"""E2E test for the WHIP publish-token feature.

Sets a token via the admin API, verifies WHIP rejects without/with-wrong token
and accepts with the correct token (real aiortc publisher), then clears it.
"""
import asyncio
import os

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api"


async def login() -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API}/auth/login", json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
        r.raise_for_status()
        return r.json()["token"]


async def publish(token: str | None) -> tuple[int, str]:
    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=purple:size=160x120:rate=10", format="lavfi", options={"framerate": "10", "video_size": "160x120"})
    if player.video:
        pc.addTrack(player.video)
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    headers = {"Content-Type": "application/sdp"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp, headers=headers)
    if r.status_code == 201:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    await pc.close()
    return r.status_code, r.headers.get("Location", "")


async def main() -> None:
    admin = await login()
    ah = {"Authorization": f"Bearer {admin}"}

    async with httpx.AsyncClient(timeout=10) as c:
        # Baseline — no token set.
        await c.delete(f"{API}/stream/token", headers=ah)
        # Set a token.
        r = await c.post(f"{API}/stream/token", json={"token": "obs-secret-token-e2e"}, headers=ah)
        assert r.status_code == 200, r.text
        assert r.json()["token"] == "obs-secret-token-e2e"

        st = (await c.get(f"{API}/stream/status")).json()
        assert st["publish_token_required"], "publish_token_required must be true after setting a token"
        print("token set, status.publish_token_required =", st["publish_token_required"])

    # WHIP without a token should be rejected.
    code, _ = await publish(None)
    assert code == 401, f"expected 401 without token, got {code}"
    print("no token -> 401 OK")

    # WHIP with a wrong token should be rejected.
    code, _ = await publish("wrong")
    assert code == 401, f"expected 401 with wrong token, got {code}"
    print("wrong token -> 401 OK")

    # WHIP with the right token should succeed.
    code, loc = await publish("obs-secret-token-e2e")
    assert code == 201, f"expected 201 with correct token, got {code}"
    assert loc.endswith("/api/whip/") or "/api/whip/" in loc, loc
    print("correct token -> 201 OK, location:", loc)
    await asyncio.sleep(0.5)

    async with httpx.AsyncClient(timeout=10) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        assert st["publisher_connected"], f"publisher should be live, got {st}"

        # Clear token — WHIP becomes open again.
        await c.delete(f"{API}/stream/token", headers=ah)
        st = (await c.get(f"{API}/stream/status")).json()
        assert not st["publish_token_required"], "should be false after clear"

    code, _ = await publish(None)
    assert code == 201, f"open ingest after clear should be 201, got {code}"
    print("open after clear -> 201 OK")

    print("all publish-token cases PASS")


if __name__ == "__main__":
    asyncio.run(main())
