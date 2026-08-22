"""Iteration 15 (unit): prove `_resolve_viewer_host` yields the event loop.

The HTTP-level concurrency test can only observe fast NXDOMAIN responses, so
this suite patches `socket.getaddrinfo` (which `loop.getaddrinfo` dispatches to
a thread-pool executor) with a deliberately SLOW implementation and asserts:

  1. A background "ticker" coroutine keeps running while the lookup is pending
     -> the event loop is NOT blocked.
  2. 5 concurrent resolutions of a 1.0 s lookup complete in ~1 s, not ~5 s.
  3. The 1.5 s `asyncio.wait_for` timeout is enforced and returns None.
  4. Negative results are cached (a second call does not re-issue the lookup).
"""
import asyncio
import importlib
import os
import socket
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
stream = importlib.import_module("stream")


class FakeRequest:
    """Minimal stand-in exposing only `.headers` like starlette's Request."""

    def __init__(self, host: str):
        self.headers = {"host": host}


@pytest.fixture(autouse=True)
def clear_cache():
    stream._host_ip_cache.clear()
    yield
    stream._host_ip_cache.clear()


def _slow_getaddrinfo(delay: float, counter: dict):
    real = socket.getaddrinfo

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        counter["calls"] = counter.get("calls", 0) + 1
        time.sleep(delay)
        return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("203.0.113.7", 0))]

    fake.real = real
    return fake


class TestEventLoopNotBlocked:
    @pytest.mark.asyncio
    async def test_loop_keeps_ticking_during_slow_lookup(self, monkeypatch):
        counter = {}
        monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo(1.0, counter))
        ticks = {"n": 0}
        stop = False

        async def ticker():
            while not stop:
                ticks["n"] += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        ip = await stream._resolve_viewer_host(FakeRequest("slow.example.test"))
        stop = True
        await asyncio.sleep(0.02)
        task.cancel()

        print(f"[unit] ticks during 1.0s lookup = {ticks['n']}, ip={ip}")
        assert ip == "203.0.113.7", ip
        assert ticks["n"] > 20, (
            f"event loop was BLOCKED during DNS resolution: only {ticks['n']} "
            "ticker iterations ran in ~1s (expected ~100)")

    @pytest.mark.asyncio
    async def test_five_concurrent_slow_lookups_are_parallel(self, monkeypatch):
        counter = {}
        monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo(1.0, counter))
        t0 = time.monotonic()
        results = await asyncio.gather(*[
            stream._resolve_viewer_host(FakeRequest(f"slow-{i}.example.test"))
            for i in range(5)])
        elapsed = time.monotonic() - t0
        print(f"[unit] 5 x 1.0s concurrent lookups took {elapsed:.2f}s "
              f"({counter.get('calls')} resolver calls)")
        assert all(r == "203.0.113.7" for r in results), results
        assert elapsed < 2.0, (
            f"lookups serialised ({elapsed:.2f}s) — resolver is blocking")


class TestTimeoutAndCache:
    @pytest.mark.asyncio
    async def test_timeout_returns_none_and_does_not_hang(self, monkeypatch):
        counter = {}
        monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo(5.0, counter))
        t0 = time.monotonic()
        ip = await stream._resolve_viewer_host(FakeRequest("veryslow.example.test"))
        elapsed = time.monotonic() - t0
        print(f"[unit] timeout path returned {ip!r} after {elapsed:.2f}s")
        assert ip is None, f"expected None on timeout, got {ip!r}"
        assert 1.3 < elapsed < 2.5, f"wait_for(1.5) not honoured: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_positive_result_is_cached(self, monkeypatch):
        counter = {}
        monkeypatch.setattr(socket, "getaddrinfo", _slow_getaddrinfo(0.05, counter))
        first = await stream._resolve_viewer_host(FakeRequest("cached.example.test"))
        second = await stream._resolve_viewer_host(FakeRequest("cached.example.test:9000"))
        assert first == second == "203.0.113.7"
        assert counter["calls"] == 1, \
            f"expected 1 DNS call (2nd from cache), got {counter['calls']}"

    @pytest.mark.asyncio
    async def test_negative_result_is_cached(self, monkeypatch):
        counter = {"calls": 0}

        def boom(*a, **k):
            counter["calls"] += 1
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        assert await stream._resolve_viewer_host(FakeRequest("nx.example.test")) is None
        assert await stream._resolve_viewer_host(FakeRequest("nx.example.test")) is None
        assert counter["calls"] == 1, \
            f"negative result not cached: {counter['calls']} DNS calls"

    @pytest.mark.asyncio
    async def test_literals_never_hit_dns(self, monkeypatch):
        counter = {"calls": 0}

        def boom(*a, **k):
            counter["calls"] += 1
            raise AssertionError("DNS must not be used for IP literals/localhost")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        assert await stream._resolve_viewer_host(FakeRequest("192.168.1.42")) == "192.168.1.42"
        assert await stream._resolve_viewer_host(FakeRequest("10.0.0.5:8080")) == "10.0.0.5"
        assert await stream._resolve_viewer_host(FakeRequest("localhost")) == "127.0.0.1"
        assert await stream._resolve_viewer_host(FakeRequest("[::1]:8001")) == "127.0.0.1"
        assert await stream._resolve_viewer_host(FakeRequest("")) is None
        assert counter["calls"] == 0
