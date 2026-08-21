"""WHIP/WHEP OBS stream broker tests (module: backend/stream.py).

Uses aiortc to simulate an OBS WHIP publisher and a browser WHEP viewer
against the public preview URL from /app/frontend/.env.
"""
import asyncio
import os

import pytest
import requests
from dotenv import dotenv_values
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"


def get_status():
    r = requests.get(f"{API}/stream/status", timeout=20)
    assert r.status_code == 200, r.text
    return r.json()


async def make_publisher():
    pc = RTCPeerConnection()
    player = MediaPlayer(
        "color=c=purple:size=320x240:rate=15",
        format="lavfi",
        options={"framerate": "15", "video_size": "320x240"},
    )
    if player.video:
        pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    r = requests.post(f"{API}/whip", data=pc.localDescription.sdp,
                      headers={"Content-Type": "application/sdp"}, timeout=30)
    assert r.status_code == 201, f"WHIP failed: {r.status_code} {r.text[:300]}"
    loc = r.headers.get("Location", "")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, loc, r


async def make_viewer():
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    got_track = asyncio.Event()
    got_frames = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        got_track.set()

        async def pump():
            try:
                for _ in range(2):
                    await asyncio.wait_for(track.recv(), timeout=20)
                got_frames.set()
            except Exception as e:  # noqa: BLE001
                print("viewer pump error:", e)

        asyncio.create_task(pump())

    await pc.setLocalDescription(await pc.createOffer())
    r = requests.post(f"{API}/whep", data=pc.localDescription.sdp,
                      headers={"Content-Type": "application/sdp"}, timeout=30)
    assert r.status_code == 201, f"WHEP failed: {r.status_code} {r.text[:300]}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, r, got_track, got_frames


# --- baseline / validation -------------------------------------------------

def test_status_idle_baseline():
    st = get_status()
    assert st["publisher_connected"] is False, st
    assert st["viewer_count"] == 0, st
    assert st["tracks"] == []
    assert st["publisher_id"] is None


def test_whep_before_publisher_returns_409():
    st = get_status()
    assert st["publisher_connected"] is False, f"not idle: {st}"
    r = requests.post(f"{API}/whep", data="v=0\r\n", headers={"Content-Type": "application/sdp"}, timeout=20)
    assert r.status_code == 409, f"{r.status_code} {r.text[:200]}"
    assert r.json()["detail"] == "No live stream is being published right now."


def test_whip_empty_body_returns_400():
    r = requests.post(f"{API}/whip", data="", headers={"Content-Type": "application/sdp"}, timeout=20)
    assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"
    assert "Expected an SDP offer" in r.json()["detail"]


def test_options_preflight():
    for path in ("whip", "whep"):
        r = requests.options(f"{API}/{path}", timeout=20)
        assert r.status_code == 204, f"{path}: {r.status_code}"


# --- full publisher -> viewer flow ----------------------------------------

def test_full_whip_whep_flow():
    async def flow():
        pub, loc, resp = await make_publisher()
        try:
            assert loc.startswith("/api/whip/"), loc
            assert resp.headers.get("content-type", "").startswith("application/sdp")
            assert "v=0" in resp.text
            await asyncio.sleep(3)
            st = get_status()
            assert st["publisher_connected"] is True, st
            assert st["tracks"] == ["video"], st
            assert st["publisher_id"] == loc.rsplit("/", 1)[-1]

            view, vresp, got_track, got_frames = await make_viewer()
            vloc = vresp.headers.get("Location", "")
            assert vloc.startswith("/api/whep/"), vloc
            await asyncio.wait_for(got_track.wait(), timeout=15)
            await asyncio.wait_for(got_frames.wait(), timeout=30)

            st = get_status()
            assert st["viewer_count"] >= 1, st

            d = requests.delete(f"{BASE_URL}{vloc}", timeout=20)
            assert d.status_code == 200, d.text
            assert d.json() == {"ok": True}
            await view.close()
            await asyncio.sleep(1)
            assert get_status()["viewer_count"] == 0, get_status()
        finally:
            requests.delete(f"{BASE_URL}{loc}", timeout=20)
            await pub.close()

        await asyncio.sleep(1)
        st = get_status()
        assert st["publisher_connected"] is False, st
        assert st["tracks"] == []
        assert st["viewer_count"] == 0

    asyncio.run(flow())


def test_second_publisher_replaces_first():
    async def flow():
        pub1, loc1, _ = await make_publisher()
        await asyncio.sleep(2)
        st = get_status()
        assert st["publisher_id"] == loc1.rsplit("/", 1)[-1], st
        pub2, loc2, _ = await make_publisher()
        await asyncio.sleep(3)
        try:
            st = get_status()
            assert loc2 != loc1
            assert st["publisher_connected"] is True, st
            assert st["publisher_id"] == loc2.rsplit("/", 1)[-1], f"publisher not replaced: {st}"
        finally:
            requests.delete(f"{BASE_URL}{loc2}", timeout=20)
            await pub1.close()
            await pub2.close()
        await asyncio.sleep(1)
        assert get_status()["publisher_connected"] is False

    asyncio.run(flow())
