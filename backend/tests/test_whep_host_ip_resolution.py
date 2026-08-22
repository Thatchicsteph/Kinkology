"""Iteration 14: validate that WHIP/WHEP SDP answers advertise `typ host` ICE
candidates as IP LITERALS (never FQDNs), which is what mobile browsers require
per RFC 8839 / draft-ietf-mmusic-mdns-ice-candidates.

Covers:
  * FQDN Host header  -> resolved IPv4 literal (tg30.ddns.net, google.com)
  * Literal IPv4 Host -> passthrough, port stripped
  * localhost / 127.0.0.1 / [::1]:8001 -> 127.0.0.1
  * CRLF preservation in the answer SDP (RFC 4566)
  * srflx candidates untouched
  * Unresolvable Host -> 201, candidates left untouched (no 400/500)
  * WHIP answer honours the Host header too (must run LAST: takes publisher slot)
"""
import asyncio
import os
import re
import socket

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
    """A live synthetic WHIP publisher so /api/whep returns 201."""
    await _clear_publisher()
    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=purple:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    assert player.video is not None
    pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    offer_sdp = pc.localDescription.sdp
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whip", content=offer_sdp,
                         headers={"Content-Type": "application/sdp"})
    assert r.status_code == 201, f"WHIP publish failed: {r.status_code} {r.text[:300]}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await asyncio.sleep(2)
    yield {"pc": pc, "offer": offer_sdp, "answer": r.text,
           "location": r.headers.get("Location", "")}
    try:
        await pc.close()
    except Exception:
        pass
    await _clear_publisher()


async def whep(host_header: str):
    """POST a fresh WHEP offer with the given Host header; return (resp, offer_sdp)."""
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    offer = pc.localDescription.sdp
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/whep", content=offer,
                             headers={"Content-Type": "application/sdp", "Host": host_header})
        if r.status_code == 201:
            # answer must still be parseable by a real ICE stack
            await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
        return r, offer
    finally:
        try:
            await pc.close()
        except Exception:
            pass


# ------------------------------------------------------- FQDN -> IPv4 literal
class TestFqdnResolution:
    @pytest.mark.parametrize("fqdn", ["tg30.ddns.net", "google.com"])
    @pytest.mark.asyncio
    async def test_fqdn_host_becomes_ipv4_literal(self, publisher, fqdn):
        expected = resolve4(fqdn)
        r, _ = await whep(fqdn)
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        lines = host_candidate_lines(r.text)
        assert lines, f"answer has no `typ host` candidates:\n{r.text}"
        addrs = host_candidate_addrs(r.text)
        for a in addrs:
            assert IPV4_RE.match(a), f"host candidate address is not an IPv4 literal: {a!r}"
        assert all(a == expected for a in addrs), f"expected {expected}, got {addrs}"
        for l in lines:
            assert fqdn not in l, f"FQDN leaked into host candidate: {l}"

    @pytest.mark.asyncio
    async def test_no_fqdn_anywhere_in_host_candidates(self, publisher):
        r, _ = await whep("tg30.ddns.net")
        assert r.status_code == 201
        assert "tg30.ddns.net" not in "\n".join(host_candidate_lines(r.text))


# ------------------------------------------------------- literal passthrough
class TestLiteralAndLocalhost:
    @pytest.mark.parametrize("host_header,expected", [
        ("192.168.1.42", "192.168.1.42"),
        ("10.0.0.5:8080", "10.0.0.5"),
        ("localhost", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]:8001", "127.0.0.1"),
    ])
    @pytest.mark.asyncio
    async def test_host_rewrite(self, publisher, host_header, expected):
        r, _ = await whep(host_header)
        assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
        addrs = host_candidate_addrs(r.text)
        assert addrs, "no host candidates"
        assert all(a == expected for a in addrs), f"expected {expected}, got {addrs}"


# ------------------------------------------------------- CRLF / srflx / errors
class TestSdpHygiene:
    @pytest.mark.asyncio
    async def test_answer_uses_crlf_only(self, publisher):
        r, offer = await whep("tg30.ddns.net")
        assert r.status_code == 201
        answer = r.text
        assert offer.count("\r\n") > 0, "offer itself had no CRLF (test premise)"
        assert answer.count("\r\n") > 0, "answer SDP has no CRLF line endings"
        assert answer.count("\n") == answer.count("\r\n"), (
            f"answer has bare LF lines: total_lf={answer.count(chr(10))} "
            f"crlf={answer.count(chr(13)+chr(10))}")

    @pytest.mark.asyncio
    async def test_srflx_candidates_untouched(self, publisher):
        r, _ = await whep("192.168.77.77")
        assert r.status_code == 201
        srflx = [l for l in r.text.replace("\r\n", "\n").split("\n")
                 if l.startswith("a=candidate:") and " typ host" not in l]
        for line in srflx:
            assert "192.168.77.77" not in line, f"non-host candidate rewritten: {line}"
        if not srflx:
            pytest.skip("no srflx/relay candidates in this environment (expected without STUN)")

    @pytest.mark.asyncio
    async def test_unresolvable_host_leaves_candidates_untouched(self, publisher):
        bad = "nx.definitely-not-real-example.invalid"
        r, _ = await whep(bad)
        assert r.status_code == 201, f"unresolvable Host must not error: {r.status_code} {r.text[:300]}"
        addrs = host_candidate_addrs(r.text)
        assert addrs, "no host candidates"
        for a in addrs:
            assert IPV4_RE.match(a), f"candidate is not an IPv4 literal: {a!r}"
            assert bad not in a
        assert container_ip() in addrs, \
            f"expected raw aioice candidate ({container_ip()}) to survive, got {addrs}"


# ------------------------------------------------------- WHIP (runs LAST)
class TestWhipHostRewriteLast:
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
                                 headers={"Content-Type": "application/sdp", "Host": "192.168.5.5"})
            assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
            addrs = host_candidate_addrs(r.text)
            assert addrs, "no host candidates in WHIP answer"
            assert all(a == "192.168.5.5" for a in addrs), addrs
            assert r.text.count("\n") == r.text.count("\r\n"), "WHIP answer has bare LF lines"
            sid = r.headers.get("Location", "").rsplit("/", 1)[-1]
            async with httpx.AsyncClient(timeout=20) as c:
                d = await c.delete(f"{API}/whip/{sid}")
                assert d.status_code == 200, d.text
        finally:
            try:
                await pc.close()
            except Exception:
                pass
