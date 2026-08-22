"""Sanity check on the real public preview URL: WHEP through the ingress must
return 201 and IP-literal `typ host` candidates (this is what a mobile browser
actually receives)."""
import asyncio
import os
import re
import socket

import httpx
import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
from dotenv import dotenv_values

PUBLIC = (os.environ.get("REACT_APP_BACKEND_URL")
          or dotenv_values("/app/frontend/.env").get("REACT_APP_BACKEND_URL")).rstrip("/")
LOCAL_API = "http://localhost:8001/api"
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


@pytest.mark.asyncio
async def test_whep_via_public_url_returns_ip_literal_candidates():
    # clear + publish locally
    async with httpx.AsyncClient(timeout=20) as c:
        st = (await c.get(f"{LOCAL_API}/stream/status")).json()
        if st.get("publisher_id"):
            await c.delete(f"{LOCAL_API}/whip/{st['publisher_id']}")
    pub = RTCPeerConnection()
    player = MediaPlayer("color=c=blue:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    pub.addTrack(player.video)
    await pub.setLocalDescription(await pub.createOffer())
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{LOCAL_API}/whip", content=pub.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    assert r.status_code == 201, r.text[:300]
    await pub.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await asyncio.sleep(2)

    view = RTCPeerConnection()
    view.addTransceiver("video", direction="recvonly")
    view.addTransceiver("audio", direction="recvonly")
    await view.setLocalDescription(await view.createOffer())
    try:
        async with httpx.AsyncClient(timeout=30, verify=True) as c:
            vr = await c.post(f"{PUBLIC}/api/whep", content=view.localDescription.sdp,
                              headers={"Content-Type": "application/sdp"})
        assert vr.status_code == 201, f"{vr.status_code} {vr.text[:300]}"
        answer = vr.text
        addrs = [l.split(" ")[4] for l in answer.replace("\r\n", "\n").split("\n")
                 if l.startswith("a=candidate:") and " typ host" in l]
        assert addrs, f"no host candidates in public-URL answer:\n{answer}"
        host_name = PUBLIC.split("//", 1)[1]
        for a in addrs:
            assert IPV4_RE.match(a), f"not an IP literal: {a!r}"
            assert a != host_name
        # NOTE: the backend's resolver may return a different A record than the
        # test's (split-horizon / anycast), so only assert it is an IP literal
        # and that all candidates agree.
        print(f"public-URL host candidates: {addrs} "
              f"(test resolver says "
              f"{socket.getaddrinfo(host_name, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]})")
        assert len(set(addrs)) == 1, f"inconsistent host candidates: {addrs}"
        assert answer.count("\n") == answer.count("\r\n"), "public answer has bare LF"
        await view.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
    finally:
        await view.close()
        async with httpx.AsyncClient(timeout=20) as c:
            st = (await c.get(f"{LOCAL_API}/stream/status")).json()
            if st.get("publisher_id"):
                await c.delete(f"{LOCAL_API}/whip/{st['publisher_id']}")
        await pub.close()
