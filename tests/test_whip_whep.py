"""End-to-end WHIP + WHEP handshake test.

Uses aiortc to simulate an OBS publisher (WHIP) and a browser viewer (WHEP),
then verifies that a video track flows from publisher -> hub -> viewer.
"""
import asyncio
import os
import sys
import time

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

API = os.environ.get("API", "http://localhost:8001/api")


async def publish() -> tuple[RTCPeerConnection, str]:
    pc = RTCPeerConnection()
    # Generate a synthetic video source (aiortc built-in test pattern).
    player = MediaPlayer(
        "color=c=purple:size=320x240:rate=15",
        format="lavfi",
        options={"framerate": "15", "video_size": "320x240"},
    )
    if player.video:
        pc.addTrack(player.video)
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{API}/whip", content=pc.localDescription.sdp,
                              headers={"Content-Type": "application/sdp"})
        assert r.status_code == 201, f"WHIP publish failed: {r.status_code} {r.text}"
        location = r.headers.get("Location", "")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, location


async def view() -> tuple[RTCPeerConnection, asyncio.Event]:
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    got_track = asyncio.Event()
    frame_received = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        got_track.set()
        async def pump():
            try:
                # Read a couple of RTP frames to prove the pipe actually flows.
                for _ in range(3):
                    await asyncio.wait_for(track.recv(), timeout=10)
                frame_received.set()
            except Exception as e:  # noqa: BLE001
                print(f"viewer track pump error: {e}", file=sys.stderr)
        asyncio.create_task(pump())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{API}/whep", content=pc.localDescription.sdp,
                              headers={"Content-Type": "application/sdp"})
        assert r.status_code == 201, f"WHEP subscribe failed: {r.status_code} {r.text}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, got_track, frame_received


async def main() -> None:
    async with httpx.AsyncClient() as c:
        st = (await c.get(f"{API}/stream/status")).json()
        assert st["publisher_connected"] is False, f"expected clean state, got {st}"
        print("initial status:", st)

    pub_pc, loc = await publish()
    print("published to", loc)
    # Let ICE settle and tracks arrive.
    await asyncio.sleep(2)

    async with httpx.AsyncClient() as c:
        st = (await c.get(f"{API}/stream/status")).json()
        print("mid status:", st)
        assert st["publisher_connected"], "publisher should be connected"

    view_pc, got_track, got_frame = await view()
    print("viewer requested")
    await asyncio.wait_for(got_track.wait(), timeout=8)
    print("viewer got track")
    await asyncio.wait_for(got_frame.wait(), timeout=15)
    print("viewer received media frames — OK")

    async with httpx.AsyncClient() as c:
        st = (await c.get(f"{API}/stream/status")).json()
        assert st["viewer_count"] >= 1, f"viewer count should be >=1, got {st}"
        print("final status:", st)

    await view_pc.close()
    async with httpx.AsyncClient() as c:
        await c.delete(f"http://localhost:8001{loc}") if loc.startswith("/") else None
    await pub_pc.close()

    await asyncio.sleep(1)
    async with httpx.AsyncClient() as c:
        st = (await c.get(f"{API}/stream/status")).json()
        print("after teardown:", st)


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"OK in {time.time()-t0:.1f}s")
