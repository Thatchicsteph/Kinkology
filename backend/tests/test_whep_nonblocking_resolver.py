"""Iteration 15: verify `_resolve_viewer_host` is now NON-BLOCKING
(`loop.getaddrinfo` + `asyncio.wait_for(timeout=1.5)`) while all the
iteration-14 host-rewrite guarantees still hold.

Covers:
  * EVENT LOOP NOT BLOCKED: 5 concurrent WHEP posts with distinct unresolvable
    Host headers must complete well under the serialised 5 * 1.5 s = 7.5 s.
  * DNS CACHE HIT: two WHEP posts with the same resolvable Host inside the
    60 s TTL yield the same IPv4.
  * Unresolvable Host still fails safe (201, IPv4 literal candidates).
  * FQDN -> IPv4 literal, literal passthrough, localhost fallback, CRLF.
"""
import asyncio
import os
import re
import socket
import time

import httpx
import pytest
import pytest_asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

BASE = os.environ.get("BACKEND_LOCAL_URL", "http://localhost:8001")
API = f"{BASE}/api"

IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def resolve4(host: str) -> str:
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]


def container_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def host_candidate_lines(sdp: str):
    return [l for l in sdp.replace("\r\n", "\n").split("\n")
            if l.startswith("a=candidate:") and " typ host" in l]


def host_candidate_addrs(sdp: str):
    return [l.split(" ")[4] for l in host_candidate_lines(sdp)]


async def _clear_publisher():
    async with httpx.AsyncClient(timeout=20) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        pid = st.get("publisher_id")
        if pid:
            await c.delete(f"{API}/whip/{pid}")


@pytest_asyncio.fixture(scope="module")
async def publisher():
    """A live synthetic WHIP publisher so /api/whep returns 201, not 409."""
    await _clear_publisher()
    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=purple:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    assert player.video is not None
    pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    assert r.status_code == 201, f"WHIP publish failed: {r.status_code} {r.text[:300]}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await asyncio.sleep(2)
    yield pc
    try:
        await pc.close()
    except Exception:
        pass
    await _clear_publisher()


async def whep(host_header: str, validate: bool = True):
    """POST a fresh WHEP offer with the given Host header; return (resp, offer)."""
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    offer = pc.localDescription.sdp
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{API}/whep", content=offer,
                             headers={"Content-Type": "application/sdp", "Host": host_header})
        if validate and r.status_code == 201:
            # the answer must remain parseable by a real ICE stack
            await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
        return r, offer
    finally:
        try:
            await pc.close()
        except Exception:
            pass


# ------------------------------------------------- non-blocking event loop
class TestResolverNonBlocking:
    @pytest.mark.asyncio
    async def test_five_concurrent_unresolvable_hosts_under_3_5s(self, publisher):
        """5 * 1.5 s serialised = 7.5 s if blocking. Concurrent must be < 3.5 s."""
        hosts = [f"nx-{i}.invalid" for i in range(5)]
        # Pre-build offers so SDP/ICE gathering time is NOT counted in wall clock.
        pcs = []
        offers = []
        for _ in hosts:
            pc = RTCPeerConnection()
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")
            await pc.setLocalDescription(await pc.createOffer())
            pcs.append(pc)
            offers.append(pc.localDescription.sdp)

        async def fire(client, host, offer):
            return await client.post(f"{API}/whep", content=offer,
                                     headers={"Content-Type": "application/sdp",
                                              "Host": host})
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                t0 = time.monotonic()
                results = await asyncio.gather(
                    *[fire(c, h, o) for h, o in zip(hosts, offers)])
                elapsed = time.monotonic() - t0
        finally:
            for pc in pcs:
                try:
                    await pc.close()
                except Exception:
                    pass

        codes = [r.status_code for r in results]
        print(f"[non-blocking] 5 concurrent unresolvable-Host WHEP posts "
              f"took {elapsed:.2f}s, codes={codes}")
        assert all(code == 201 for code in codes), f"expected all 201, got {codes}"
        assert elapsed < 3.5, (
            f"resolver appears to BLOCK the event loop: 5 concurrent requests took "
            f"{elapsed:.2f}s (serialised 1.5s timeouts would be ~7.5s)")

    @pytest.mark.asyncio
    async def test_unresolvable_host_fails_safe(self, publisher):
        bad = "nx.definitely-not-real-example.invalid"
        r, _ = await whep(bad)
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        addrs = host_candidate_addrs(r.text)
        assert addrs, "no host candidates in answer"
        for a in addrs:
            assert IPV4_RE.match(a), f"candidate not an IPv4 literal: {a!r}"
            assert bad not in a
        assert container_ip() in addrs, \
            f"expected raw aioice candidate ({container_ip()}) to survive, got {addrs}"

    @pytest.mark.asyncio
    async def test_concurrent_resolvable_and_unresolvable_mix(self, publisher):
        """A slow/unresolvable lookup must not delay a cached/literal lookup."""
        t0 = time.monotonic()
        results = await asyncio.gather(
            whep("nx-mix-a.invalid"), whep("192.168.9.9"), whep("localhost"))
        elapsed = time.monotonic() - t0
        print(f"[non-blocking] mixed batch took {elapsed:.2f}s")
        assert all(r.status_code == 201 for r, _ in results)
        assert all(a == "192.168.9.9" for a in host_candidate_addrs(results[1][0].text))
        assert all(a == "127.0.0.1" for a in host_candidate_addrs(results[2][0].text))


# ------------------------------------------------- DNS cache
class TestDnsCache:
    @pytest.mark.asyncio
    async def test_cache_hit_same_ip_within_ttl(self, publisher):
        expected = resolve4("google.com")
        r1, _ = await whep("google.com")
        assert r1.status_code == 201, f"{r1.status_code} {r1.text[:300]}"
        t0 = time.monotonic()
        r2, _ = await whep("google.com")
        second_call = time.monotonic() - t0
        assert r2.status_code == 201
        a1 = host_candidate_addrs(r1.text)
        a2 = host_candidate_addrs(r2.text)
        assert a1 and a2, (a1, a2)
        assert set(a1) == {expected}, f"first answer {a1} != {expected}"
        assert set(a2) == set(a1), f"cache miss/mismatch: {a1} vs {a2}"
        print(f"[cache] second google.com WHEP round-trip {second_call:.2f}s, ip={a2[0]}")


# ------------------------------------------------- iteration-14 regressions
class TestHostRewriteStillHolds:
    @pytest.mark.asyncio
    async def test_fqdn_becomes_ipv4_literal(self, publisher):
        expected = resolve4("tg30.ddns.net")
        r, _ = await whep("tg30.ddns.net")
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        lines = host_candidate_lines(r.text)
        assert lines, f"no `typ host` candidates:\n{r.text}"
        for l in lines:
            assert "tg30.ddns.net" not in l, f"FQDN leaked: {l}"
        addrs = host_candidate_addrs(r.text)
        for a in addrs:
            assert IPV4_RE.match(a), f"not an IPv4 literal: {a!r}"
        assert set(addrs) == {expected}, f"expected {expected}, got {addrs}"

    @pytest.mark.parametrize("host_header,expected", [
        ("192.168.1.42", "192.168.1.42"),
        ("10.0.0.5:8080", "10.0.0.5"),
        ("localhost", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]:8001", "127.0.0.1"),
    ])
    @pytest.mark.asyncio
    async def test_literal_and_localhost_rewrite(self, publisher, host_header, expected):
        r, _ = await whep(host_header)
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        addrs = host_candidate_addrs(r.text)
        assert addrs, "no host candidates"
        assert set(addrs) == {expected}, f"expected {expected}, got {addrs}"

    @pytest.mark.asyncio
    async def test_answer_crlf_preserved(self, publisher):
        r, offer = await whep("tg30.ddns.net")
        assert r.status_code == 201
        answer = r.text
        assert offer.count("\r\n") > 0, "offer had no CRLF (test premise broken)"
        assert answer.count("\r\n") > 0, "answer SDP has no CRLF"
        assert answer.count("\n") == answer.count("\r\n"), "answer has bare LF lines"


# ------------------------------------------------- WHIP (runs LAST)
class TestWhipRewriteLast:
    @pytest.mark.asyncio
    async def test_whip_answer_host_rewrite(self, publisher):
        await _clear_publisher()
        pc = RTCPeerConnection()
        player = MediaPlayer("color=c=red:size=160x120:rate=10", format="lavfi",
                             options={"framerate": "10", "video_size": "160x120"})
        pc.addTrack(player.video)
        await pc.setLocalDescription(await pc.createOffer())
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                                 headers={"Content-Type": "application/sdp",
                                          "Host": "192.168.5.5"})
            assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
            addrs = host_candidate_addrs(r.text)
            assert addrs, "no host candidates in WHIP answer"
            assert set(addrs) == {"192.168.5.5"}, addrs
            assert r.text.count("\n") == r.text.count("\r\n"), "WHIP answer has bare LF"
            sid = r.headers.get("Location", "").rsplit("/", 1)[-1]
            async with httpx.AsyncClient(timeout=20) as c:
                d = await c.delete(f"{API}/whip/{sid}")
                assert d.status_code == 200, d.text
        finally:
            try:
                await pc.close()
            except Exception:
                pass
