"""Iteration 16 manual check: run the backend with unresolvable TURN env set and
confirm WHIP publish + WHEP view still return 201 (STUN gathering independent).

Usage: python /app/tests/probe_turn_env_whep.py
Assumes STREAM_TURN_* env vars are already exported into the backend process.
"""
import asyncio
import os
import re

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

API = os.environ.get("BACKEND_LOCAL_URL", "http://localhost:8001") + "/api"


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        if st.get("publisher_id"):
            await c.delete(f"{API}/whip/{st['publisher_id']}")

    pub = RTCPeerConnection()
    player = MediaPlayer("color=c=green:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    pub.addTrack(player.video)
    await pub.setLocalDescription(await pub.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/whip", content=pub.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    print("WHIP status:", r.status_code)
    assert r.status_code == 201, r.text[:400]
    await pub.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await asyncio.sleep(2)

    view = RTCPeerConnection()
    view.addTransceiver("video", direction="recvonly")
    view.addTransceiver("audio", direction="recvonly")
    await view.setLocalDescription(await view.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
        rv = await c.post(f"{API}/whep", content=view.localDescription.sdp,
                          headers={"Content-Type": "application/sdp",
                                   "Host": "tg30.ddns.net"})
    print("WHEP status:", rv.status_code)
    assert rv.status_code == 201, rv.text[:400]
    await view.setRemoteDescription(RTCSessionDescription(sdp=rv.text, type="answer"))
    lines = [l for l in rv.text.replace("\r\n", "\n").split("\n")
             if l.startswith("a=candidate:")]
    print("candidates:")
    for l in lines:
        print("  ", l)
    host = [l for l in lines if " typ host" in l]
    assert host, "no host candidates"
    for l in host:
        addr = l.split(" ")[4]
        assert re.match(r"^(\d{1,3}\.){3}\d{1,3}$", addr), addr
    assert rv.text.count("\n") == rv.text.count("\r\n"), "bare LF in answer"

    await view.close()
    await pub.close()
    async with httpx.AsyncClient(timeout=30) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        if st.get("publisher_id"):
            await c.delete(f"{API}/whip/{st['publisher_id']}")
    print("TURN-env probe PASS")


asyncio.run(main())
