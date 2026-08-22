"""Unit tests for stream._new_pc_with_extra_ice() — backend aiortc PC must pick
up dynamically-provided (Cloudflare) TURN servers, and degrade gracefully.

Also re-verifies GET /api/stream/ice-servers is public.
"""
import os
import sys

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")

import stream  # noqa: E402

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or frontend_env.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")


def _ice_urls(pc):
    """Introspect aiortc's private configuration to list configured ICE urls."""
    cfg = getattr(pc, "_RTCPeerConnection__configuration", None)
    assert cfg is not None, "could not introspect RTCPeerConnection configuration"
    urls = []
    for s in cfg.iceServers or []:
        u = s.urls if isinstance(s.urls, list) else [s.urls]
        urls.extend(u)
    return urls, cfg.iceServers or []


class TestNewPcWithExtraIce:
    """stream._new_pc_with_extra_ice()"""

    @pytest.fixture(autouse=True)
    def restore_provider(self):
        original = stream._get_extra_ice_servers
        yield
        stream._get_extra_ice_servers = original

    @pytest.mark.asyncio
    async def test_provider_turn_is_injected_alongside_static_stun(self):
        async def provider():
            return [{"urls": ["turn:test.example:3478"], "username": "u", "credential": "c"}]

        stream.set_extra_ice_provider(provider)
        pc = await stream._new_pc_with_extra_ice()
        try:
            urls, servers = _ice_urls(pc)
            assert "turn:test.example:3478" in urls, urls
            # static defaults still present
            assert any(u.startswith("stun:") for u in urls), urls
            turn_entry = [s for s in servers
                          if "turn:test.example:3478" in (s.urls if isinstance(s.urls, list) else [s.urls])]
            assert turn_entry and turn_entry[0].username == "u"
            assert turn_entry[0].credential == "c"
        finally:
            await pc.close()

    @pytest.mark.asyncio
    async def test_provider_raising_falls_back_to_static(self):
        async def bad_provider():
            raise RuntimeError("cloudflare down")

        stream.set_extra_ice_provider(bad_provider)
        pc = await stream._new_pc_with_extra_ice()
        try:
            urls, _ = _ice_urls(pc)
            static_urls, _ = _ice_urls(stream._new_pc())
            assert urls == static_urls, (urls, static_urls)
            assert not any(u.startswith("turn") for u in urls), urls
        finally:
            await pc.close()

    @pytest.mark.asyncio
    async def test_provider_empty_list_matches_new_pc(self):
        async def empty_provider():
            return []

        stream.set_extra_ice_provider(empty_provider)
        pc = await stream._new_pc_with_extra_ice()
        plain = stream._new_pc()
        try:
            assert _ice_urls(pc)[0] == _ice_urls(plain)[0]
        finally:
            await pc.close()
            await plain.close()

    @pytest.mark.asyncio
    async def test_provider_none_returns_static_pc(self):
        stream._get_extra_ice_servers = None
        pc = await stream._new_pc_with_extra_ice()
        try:
            assert _ice_urls(pc)[0] == _ice_urls(stream._new_pc())[0]
        finally:
            await pc.close()

    @pytest.mark.asyncio
    async def test_provider_entry_without_urls_is_skipped(self):
        async def provider():
            return [{"username": "u", "credential": "c"}, {"urls": []}]

        stream.set_extra_ice_provider(provider)
        pc = await stream._new_pc_with_extra_ice()
        try:
            assert _ice_urls(pc)[0] == _ice_urls(stream._new_pc())[0]
        finally:
            await pc.close()


class TestIceServersEndpoint:
    """GET /api/stream/ice-servers (public)"""

    def test_public_no_auth_static_stun(self):
        r = requests.get(f"{BASE_URL}/api/stream/ice-servers", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "iceServers" in data and isinstance(data["iceServers"], list)
        urls = [u for s in data["iceServers"] for u in (s.get("urls") or [])]
        assert any(u.startswith("stun:") for u in urls), urls
