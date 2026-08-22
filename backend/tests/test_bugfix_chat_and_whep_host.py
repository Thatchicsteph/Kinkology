"""Bug-fix validation tests.

BUG 1: admin chat must work right after page refresh with no BLE/toys setup
       -> host WS must accept {type:"chat"} immediately after connect and
          broadcast chat_msg to host + guest.
BUG 2: WHEP/WHIP SDP answers must rewrite `typ host` ICE candidates to the
       Host header the client used (so LAN mobile phones get a reachable host).

Plus regression checks on /api/whip, /api/stream/status, /api/session/toys/lock.
"""
import asyncio
import json
import os
import re

import httpx
import pytest
import pytest_asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

BASE = os.environ.get("BACKEND_LOCAL_URL", "http://localhost:8001")
API = f"{BASE}/api"
WS_BASE = BASE.replace("http", "ws", 1)

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


# --------------------------------------------------------------- helpers
async def login() -> str:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, f"login failed {r.status_code} {r.text[:300]}"
        token = r.json().get("token")
        assert isinstance(token, str) and token
        return token


async def collect(ws, predicate, timeout=5):
    async def loop():
        while True:
            msg = json.loads(await ws.recv())
            if predicate(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout=timeout)


def _resolve4(host: str) -> str:
    import socket
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]


def host_candidate_addrs(sdp: str):
    """Return the connection addresses of every `typ host` candidate line."""
    addrs = []
    for line in sdp.splitlines():
        if line.startswith("a=candidate:") and " typ host" in line:
            parts = line.split(" ")
            # a=candidate:<foundation> <comp> <transport> <prio> <addr> <port> typ host
            addrs.append(parts[4])
    return addrs


@pytest_asyncio.fixture(scope="module")
async def token():
    return await login()


@pytest_asyncio.fixture(scope="module")
async def publisher():
    """Publish a synthetic stream via WHIP so /api/whep returns 201."""
    pc = RTCPeerConnection()
    player = MediaPlayer(
        "color=c=purple:size=320x240:rate=15",
        format="lavfi",
        options={"framerate": "15", "video_size": "320x240"},
    )
    assert player.video is not None
    pc.addTrack(player.video)
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    assert r.status_code == 201, f"WHIP publish failed: {r.status_code} {r.text[:300]}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await asyncio.sleep(2)  # let tracks arrive
    yield {"pc": pc, "location": r.headers.get("Location", ""), "answer": r.text}
    try:
        await pc.close()
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=20) as c:
        loc = r.headers.get("Location", "")
        if loc.startswith("http"):
            await c.delete(loc)


# --------------------------------------------------------------- BUG 2: SDP host rewrite
class TestWhepHostRewrite:
    @pytest.mark.asyncio
    async def test_whip_returns_201_absolute_location(self, publisher):
        loc = publisher["location"]
        assert loc.startswith("http"), f"Location must be absolute, got {loc!r}"
        assert "/api/whip/" in loc

    @pytest.mark.asyncio
    async def test_stream_status_reports_publisher(self, publisher):
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{API}/stream/status")
        assert r.status_code == 200
        st = r.json()
        assert st["publisher_connected"] is True
        assert "video" in st["tracks"]
        assert isinstance(st["viewer_count"], int)
        assert st["publisher_id"]

    @pytest.mark.parametrize("host_header,expected", [
        ("192.168.99.99", "192.168.99.99"),
        ("localhost", "127.0.0.1"),
        # FQDN Host headers are now resolved to an IPv4 literal (mobile browsers
        # drop `typ host` candidates whose address is a hostname).
        ("tg30.ddns.net", _resolve4("tg30.ddns.net")),
        ("192.168.1.42:8080", "192.168.1.42"),
    ])
    @pytest.mark.asyncio
    async def test_whep_rewrites_host_candidates(self, publisher, host_header, expected):
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/whep", content=pc.localDescription.sdp,
                                 headers={"Content-Type": "application/sdp", "Host": host_header})
            assert r.status_code == 201, f"WHEP failed: {r.status_code} {r.text[:300]}"
            sdp = r.text
            addrs = host_candidate_addrs(sdp)
            assert addrs, f"answer contained no `typ host` candidates:\n{sdp}"
            assert all(a == expected for a in addrs), \
                f"expected all host candidates == {expected}, got {addrs}"
            # sanity: answer still parseable by aiortc
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
            loc = r.headers.get("Location", "")
            assert "/api/whep/" in loc
        finally:
            try:
                await pc.close()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_srflx_candidates_untouched(self, publisher):
        """Non-host candidates must not be rewritten."""
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/whep", content=pc.localDescription.sdp,
                                 headers={"Content-Type": "application/sdp", "Host": "192.168.99.99"})
            assert r.status_code == 201
            for line in r.text.splitlines():
                if line.startswith("a=candidate:") and " typ host" not in line:
                    assert "192.168.99.99" not in line, f"non-host candidate rewritten: {line}"
        finally:
            try:
                await pc.close()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_whip_answer_rewrites_host_candidates(self):
        """WHIP answer must also honour the Host header."""
        pc = RTCPeerConnection()
        player = MediaPlayer("color=c=red:size=160x120:rate=10", format="lavfi",
                             options={"framerate": "10", "video_size": "160x120"})
        pc.addTrack(player.video)
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                                 headers={"Content-Type": "application/sdp", "Host": "10.10.10.10"})
            assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
            addrs = host_candidate_addrs(r.text)
            assert addrs, "no host candidates in WHIP answer"
            assert all(a == "10.10.10.10" for a in addrs), addrs
            # This publish took over the publisher slot — release it so the
            # hub doesn't keep a stale publisher for the next test/UI session.
            # NOTE: Location is built from the (spoofed) Host header, so we
            # must re-target the DELETE at the real backend address.
            loc = r.headers.get("Location", "")
            assert loc.startswith("http://10.10.10.10/api/whip/"), loc
            sid = loc.rsplit("/", 1)[-1]
            async with httpx.AsyncClient(timeout=20) as c:
                d = await c.delete(f"{API}/whip/{sid}")
                assert d.status_code == 200, d.text
        finally:
            try:
                await pc.close()
            except Exception:
                pass


# --------------------------------------------------------------- BUG 1: chat right after connect
class TestHostWsChatAfterRefresh:
    @pytest.mark.asyncio
    async def test_host_ws_sends_chat_history_and_toys_lock_on_connect(self, token):
        import websockets
        async with httpx.AsyncClient(timeout=20) as c:
            await c.delete(f"{API}/session/chat", headers={"Authorization": f"Bearer {token}"})
            await c.post(f"{API}/session/toys/unlock", headers={"Authorization": f"Bearer {token}"})
        async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as host:
            # Collect what the server pushes in the first 3s (order agnostic).
            # NOTE: the hub ticker pushes a `state` frame every 1s forever, so
            # this loop must be deadline-bounded, not silence-bounded.
            seen = {}
            deadline = asyncio.get_event_loop().time() + 3
            try:
                while asyncio.get_event_loop().time() < deadline:
                    remaining = deadline - asyncio.get_event_loop().time()
                    m = json.loads(await asyncio.wait_for(host.recv(), timeout=max(0.1, remaining)))
                    seen.setdefault(m.get("type"), m)
                    if "chat_history" in seen and "toys_lock" in seen:
                        break
            except asyncio.TimeoutError:
                pass
            assert "chat_history" in seen, f"no chat_history on connect, got {list(seen)}"
            assert isinstance(seen["chat_history"]["messages"], list)
            assert "toys_lock" in seen, f"no toys_lock on connect, got {list(seen)}"
            assert seen["toys_lock"]["locked"] is False

    @pytest.mark.asyncio
    async def test_host_chat_immediately_after_connect_broadcasts(self, token):
        """No toys_status / ble_status / BLE setup — chat must just work."""
        import websockets
        async with httpx.AsyncClient(timeout=20) as c:
            auth = {"Authorization": f"Bearer {token}"}
            await c.delete(f"{API}/session/chat", headers=auth)
            r = await c.post(f"{API}/codes", headers=auth, json={"label": "TEST_ChatGuest", "minutes": 5})
            assert r.status_code in (200, 201), r.text
            code = r.json()["code"]
            code_id = r.json().get("id")

        try:
            async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as host:
                async with websockets.connect(f"{WS_BASE}/api/ws/control/{code}") as guest:
                    await collect(guest, lambda m: m.get("type") == "chat_history", timeout=5)
                    # Host sends chat with zero prior setup
                    await host.send(json.dumps({"type": "chat", "text": "refresh chat test"}))
                    m_host = await collect(host, lambda m: m.get("type") == "chat_msg", timeout=5)
                    assert m_host["message"]["text"] == "refresh chat test"
                    assert m_host["message"]["role"] == "owner"
                    m_guest = await collect(guest, lambda m: m.get("type") == "chat_msg", timeout=5)
                    assert m_guest["message"]["text"] == "refresh chat test"
                    assert m_guest["message"]["author"] == "Owner"

                # Persistence: reconnecting host gets the message in chat_history
                await asyncio.sleep(0.3)
            async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as host2:
                hist = await collect(host2, lambda m: m.get("type") == "chat_history", timeout=3)
                assert any(x["text"] == "refresh chat test" for x in hist["messages"]), hist
        finally:
            async with httpx.AsyncClient(timeout=20) as c:
                auth = {"Authorization": f"Bearer {token}"}
                await c.delete(f"{API}/session/chat", headers=auth)
                if code_id:
                    await c.delete(f"{API}/codes/{code_id}", headers=auth)


# --------------------------------------------------------------- Regression: kill switch
class TestToysLockRegression:
    @pytest.mark.asyncio
    async def test_lock_fires_toy_stop_and_sets_locked(self, token):
        import websockets
        auth = {"Authorization": f"Bearer {token}"}
        async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as host:
            await asyncio.sleep(0.5)
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/session/toys/lock", headers=auth)
                assert r.status_code == 200, r.text
                assert r.json()["locked"] is True
            await collect(host, lambda m: m.get("type") == "toy_command" and m.get("cmd") == "toy:stop", timeout=5)
            await collect(host, lambda m: m.get("type") == "toys_lock" and m.get("locked") is True, timeout=5)

            async with httpx.AsyncClient(timeout=20) as c:
                r2 = await c.get(f"{API}/session/state", headers=auth)
                assert r2.status_code == 200
                assert r2.json().get("toys", {}).get("locked") is True, r2.json()

            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/session/toys/unlock", headers=auth)
                assert r.status_code == 200 and r.json()["locked"] is False
            await collect(host, lambda m: m.get("type") == "toys_lock" and m.get("locked") is False, timeout=5)

    @pytest.mark.asyncio
    async def test_no_mongo_object_id_leak(self, token):
        auth = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=20) as c:
            for path in ("/session/state", "/codes", "/settings", "/stream/status"):
                r = await c.get(f"{API}{path}", headers=auth)
                assert r.status_code == 200, f"{path} -> {r.status_code}"
                assert '"_id"' not in r.text, f"{path} leaks mongo _id"
