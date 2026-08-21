"""Keep an aiortc WHIP publisher alive for frontend live-stream testing."""
import asyncio
import os
import sys

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
from dotenv import dotenv_values

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
API = f"{BASE}/api"


async def main(duration: int = 180):
    pc = RTCPeerConnection()
    player = MediaPlayer("testsrc=size=320x240:rate=15", format="lavfi", options={"framerate": "15", "video_size": "320x240"})
    if player.video:
        pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp, headers={"Content-Type": "application/sdp"})
        print("whip status", r.status_code, r.headers.get("Location"), flush=True)
        r.raise_for_status()
        loc = r.headers.get("Location")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    print("publisher live", flush=True)
    try:
        await asyncio.sleep(duration)
    finally:
        async with httpx.AsyncClient(timeout=20) as c:
            if loc:
                await c.delete(f"{BASE}{loc}")
        await pc.close()
        print("publisher stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 180))
