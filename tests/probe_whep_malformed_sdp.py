"""Ad-hoc probe: does a malformed-but-non-empty WHEP offer 500 (as seen once in
backend.err.log: `ValueError: ICE username fragment or password is missing`)?
Runs with a LIVE publisher so we get past the 409 guard.
"""
import asyncio

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

API = "http://localhost:8001/api"

BAD_NO_UFRAG = (
    "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:96 VP8/90000\r\na=recvonly\r\na=mid:0\r\n"
)
BAD_GARBAGE = "this is not sdp at all\r\n"
BAD_ONLY_V = "v=0\r\n"


async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        if st.get("publisher_id"):
            await c.delete(f"{API}/whip/{st['publisher_id']}")

    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=blue:size=160x120:rate=10", format="lavfi",
                         options={"framerate": "10", "video_size": "160x120"})
    pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    print("publish:", r.status_code)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    sid = r.headers.get("Location", "").rsplit("/", 1)[-1]
    await asyncio.sleep(1.5)

    for name, body in (("no-ufrag", BAD_NO_UFRAG), ("garbage", BAD_GARBAGE),
                       ("only-v", BAD_ONLY_V)):
        async with httpx.AsyncClient(timeout=20) as c:
            rr = await c.post(f"{API}/whep", content=body,
                              headers={"Content-Type": "application/sdp",
                                       "Host": "tg30.ddns.net"})
        print(f"WHEP[{name}] -> {rr.status_code} {rr.text[:120]!r}")

    async with httpx.AsyncClient(timeout=20) as c:
        await c.delete(f"{API}/whip/{sid}")
    await pc.close()


asyncio.run(main())
