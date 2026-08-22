"""Iteration 16: verify STUN/TURN ICE configuration is wired into aiortc.

Covers:
  * stream._ice_servers() env parsing (defaults, STREAM_STUN_SERVERS, TURN triple)
  * stream._new_pc() returns a live RTCPeerConnection with iceServers applied
  * POST /api/whep still returns 201 with valid host candidates (srflx optional)
  * CRLF hygiene + FQDN->IPv4 regression with STUN enabled
  * 5 concurrent WHEP with unresolvable Host headers stay non-blocking (<3.5s)
"""
import asyncio
import importlib
import os
import re
import sys
import time

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")

import stream  # noqa: E402
from aiortc import RTCPeerConnection, RTCSessionDescription  # noqa: E402
from aiortc.contrib.media import MediaPlayer  # noqa: E402

BASE = os.environ.get("BACKEND_LOCAL_URL", "http://localhost:8001")
API = f"{BASE}/api"
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

DEFAULT_STUN = ["stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478"]
ICE_ENV_KEYS = ["STREAM_STUN_SERVERS", "STREAM_TURN_URL",
                "STREAM_TURN_USERNAME", "STREAM_TURN_PASSWORD"]


@pytest.fixture
def clean_ice_env():
    """Snapshot/restore the ICE env vars around a test."""
    saved = {k: os.environ.get(k) for k in ICE_ENV_KEYS}
    for k in ICE_ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _urls(server):
    u = server.urls
    return [u] if isinstance(u, str) else list(u)


# ------------------------------------------------- _ice_servers() env parsing
class TestIceServersParsing:
    def test_defaults_contain_both_stun_urls(self, clean_ice_env):
        servers = stream._ice_servers()
        assert len(servers) == 1, f"expected only STUN by default, got {servers}"
        urls = _urls(servers[0])
        for expected in DEFAULT_STUN:
            assert expected in urls, f"{expected} missing from default STUN urls {urls}"

    def test_custom_stun_env_parsed(self, clean_ice_env):
        os.environ["STREAM_STUN_SERVERS"] = "stun:foo:3478,stun:bar:3478"
        servers = stream._ice_servers()
        assert len(servers) == 1
        assert _urls(servers[0]) == ["stun:foo:3478", "stun:bar:3478"]

    def test_custom_stun_env_whitespace_and_blanks(self, clean_ice_env):
        os.environ["STREAM_STUN_SERVERS"] = " stun:a:1 , ,stun:b:2 ,"
        servers = stream._ice_servers()
        assert _urls(servers[0]) == ["stun:a:1", "stun:b:2"]

    def test_empty_stun_env_yields_no_stun_server(self, clean_ice_env):
        os.environ["STREAM_STUN_SERVERS"] = ""
        assert stream._ice_servers() == []

    def test_turn_env_adds_second_server(self, clean_ice_env):
        os.environ["STREAM_TURN_URL"] = "turn:t.example:3478"
        os.environ["STREAM_TURN_USERNAME"] = "u"
        os.environ["STREAM_TURN_PASSWORD"] = "p"
        servers = stream._ice_servers()
        assert len(servers) == 2, f"expected STUN + TURN, got {servers}"
        turn = servers[1]
        assert _urls(turn) == ["turn:t.example:3478"]
        assert turn.username == "u"
        assert turn.credential == "p"

    def test_turn_without_credentials_still_added(self, clean_ice_env):
        os.environ["STREAM_TURN_URL"] = "turn:t.example:3478"
        servers = stream._ice_servers()
        assert len(servers) == 2
        assert servers[1].username is None
        assert servers[1].credential is None


# ------------------------------------------------------------ _new_pc()
class TestNewPc:
    @pytest.mark.asyncio
    async def test_new_pc_has_ice_servers(self, clean_ice_env):
        pc = stream._new_pc()
        try:
            assert isinstance(pc, RTCPeerConnection)
            cfg = pc.getConfiguration() if hasattr(pc, "getConfiguration") \
                else getattr(pc, "_RTCPeerConnection__configuration")
            urls = []
            for s in cfg.iceServers or []:
                urls.extend(_urls(s))
            for expected in DEFAULT_STUN:
                assert expected in urls, f"{expected} not in pc config urls {urls}"
        finally:
            await pc.close()

    @pytest.mark.asyncio
    async def test_new_pc_with_turn_env_does_not_raise(self, clean_ice_env):
        os.environ["STREAM_TURN_URL"] = "turn:t.example:3478"
        os.environ["STREAM_TURN_USERNAME"] = "u"
        os.environ["STREAM_TURN_PASSWORD"] = "p"
        pc = stream._new_pc()
        try:
            pc.addTransceiver("video", direction="recvonly")
            await pc.setLocalDescription(await pc.createOffer())
            assert "a=candidate:" in pc.localDescription.sdp
        finally:
            await pc.close()

    def test_call_sites_use_new_pc(self):
        src = open("/app/backend/stream.py").read()
        assert "RTCPeerConnection()" not in src, \
            "a bare RTCPeerConnection() (no STUN config) still exists in stream.py"
        # WHIP/WHEP handlers now use the async variant `_new_pc_with_extra_ice()`
        # (injects Cloudflare TURN); both variants configure ICE servers.
        assert src.count("_new_pc()") + src.count("_new_pc_with_extra_ice()") >= 3
        assert "await _new_pc_with_extra_ice()" in src, \
            "WHIP/WHEP handlers must build the PC via _new_pc_with_extra_ice()"


# --------------------------------------------------------- live WHEP flow
async def _clear_publisher():
    async with httpx.AsyncClient(timeout=20) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        pid = st.get("publisher_id")
        if pid:
            await c.delete(f"{API}/whip/{pid}")


@pytest_asyncio.fixture(scope="module")
async def publisher():
    await _clear_publisher()
    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=blue:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    assert player.video is not None
    pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
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


async def whep(host_header: str):
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{API}/whep", content=pc.localDescription.sdp,
                             headers={"Content-Type": "application/sdp",
                                      "Host": host_header})
        if r.status_code == 201:
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=r.text, type="answer"))
        return r
    finally:
        try:
            await pc.close()
        except Exception:
            pass


def cands(sdp, typ=None):
    lines = [l for l in sdp.replace("\r\n", "\n").split("\n")
             if l.startswith("a=candidate:")]
    if typ:
        lines = [l for l in lines if f" typ {typ}" in l]
    return lines


class TestWhepWithStun:
    @pytest.mark.asyncio
    async def test_whep_returns_201_with_host_candidates(self, publisher):
        r = await whep("tg30.ddns.net")
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        host = cands(r.text, "host")
        assert host, f"no typ host candidates:\n{r.text}"
        for line in host:
            addr = line.split(" ")[4]
            assert IPV4_RE.match(addr), f"host candidate not IPv4 literal: {addr!r}"
            assert "tg30.ddns.net" not in line, f"FQDN leaked: {line}"

    @pytest.mark.asyncio
    async def test_whep_srflx_candidates_reported(self, publisher):
        r = await whep("192.168.1.42")
        assert r.status_code == 201
        srflx = cands(r.text, "srflx")
        print(f"srflx candidates in answer: {srflx}")
        if not srflx:
            pytest.skip("no srflx candidates - outbound STUN/UDP likely blocked "
                        "in this pod; host-only ICE is the graceful fallback")
        for line in srflx:
            assert "192.168.1.42" not in line, f"srflx wrongly rewritten: {line}"

    @pytest.mark.asyncio
    async def test_literal_host_passthrough(self, publisher):
        r = await whep("192.168.1.42")
        assert r.status_code == 201
        addrs = [l.split(" ")[4] for l in cands(r.text, "host")]
        assert addrs and all(a == "192.168.1.42" for a in addrs), addrs

    @pytest.mark.asyncio
    async def test_localhost_maps_to_loopback(self, publisher):
        r = await whep("localhost")
        assert r.status_code == 201
        addrs = [l.split(" ")[4] for l in cands(r.text, "host")]
        assert addrs and all(a == "127.0.0.1" for a in addrs), addrs

    @pytest.mark.asyncio
    async def test_answer_crlf_only(self, publisher):
        r = await whep("tg30.ddns.net")
        assert r.status_code == 201
        a = r.text
        assert a.count("\r\n") > 0
        assert a.count("\n") == a.count("\r\n"), "answer has bare LF lines"

    @pytest.mark.asyncio
    async def test_concurrent_unresolvable_hosts_non_blocking(self, publisher):
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[whep(f"nx{i}.definitely-not-real-example.invalid") for i in range(5)])
        elapsed = time.perf_counter() - t0
        for r in results:
            assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        print(f"5 concurrent WHEP wall clock: {elapsed:.2f}s")
        assert elapsed < 3.5, f"concurrent WHEP took {elapsed:.2f}s (blocking?)"


# --------------------------------------------- docker-compose UDP range bump
class TestComposeUdpRange:
    def test_udp_range_widened(self):
        src = open("/app/docker-compose.yml").read()
        assert "50019" not in src, "old narrow UDP range (50019) still present"
        assert "STREAM_UDP_MAX: ${STREAM_UDP_MAX:-50099}" in src, \
            "STREAM_UDP_MAX default not bumped to 50099"
        assert '"${STREAM_UDP_MIN:-50000}-${STREAM_UDP_MAX:-50099}:' in src, \
            "published UDP ports: range not bumped to match STREAM_UDP_MAX"
