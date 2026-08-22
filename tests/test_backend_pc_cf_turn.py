"""E2E — the BACKEND aiortc PeerConnection must be built with Cloudflare TURN.

Runs a mock Cloudflare TURN endpoint that counts POST hits, configures CF via
PUT /api/stream/turn/cloudflare, then does a WHIP publish + WHEP subscribe and
asserts the mock was hit for each backend PC creation (plus the browser's
/api/stream/ice-servers call).
"""
import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

ENV_FILE = Path("/app/backend/.env")
BACKUP_ENV = Path("/tmp/.env.backup-cf-backendpc-test")
MOCK_PORT = 9739
API = "http://localhost:8001/api"

HITS = []  # timestamps of mock CF POSTs


async def start_mock_cloudflare():
    async def handler(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer valid-token-abc":
            return web.json_response({"error": "invalid"}, status=401)
        HITS.append(time.time())
        return web.json_response(
            {"iceServers": [
                {"urls": ["stun:stun.cloudflare.com:3478"]},
                {"urls": ["turn:turn.cloudflare.com:3478?transport=udp"],
                 "username": "expiring-user", "credential": "expiring-cred-abc"},
            ]}, status=201)

    app = web.Application()
    app.router.add_post(r"/v1/turn/keys/{key_id}/credentials/generate-ice-servers", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", MOCK_PORT).start()
    return runner


def restart_backend():
    subprocess.check_call(["sudo", "supervisorctl", "restart", "backend"])
    for _ in range(40):
        try:
            if httpx.get(f"{API}/", timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("backend did not come back up")


async def publish():
    pc = RTCPeerConnection()
    player = MediaPlayer("color=c=purple:size=320x240:rate=15", format="lavfi",
                         options={"framerate": "15", "video_size": "320x240"})
    if player.video:
        pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
        assert r.status_code == 201, f"WHIP failed: {r.status_code} {r.text}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, r.headers.get("Location", "")


async def view():
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    got = asyncio.Event()
    pc.on("track", lambda t: got.set())
    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/whep", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
        assert r.status_code == 201, f"WHEP failed: {r.status_code} {r.text}"
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, got


async def main():
    shutil.copy(ENV_FILE, BACKUP_ENV)
    runner = None
    try:
        with open(ENV_FILE, "a") as f:
            f.write(f"\nCF_TURN_ENDPOINT=http://127.0.0.1:{MOCK_PORT}"
                    "/v1/turn/keys/{key_id}/credentials/generate-ice-servers\n")
        runner = await start_mock_cloudflare()
        restart_backend()

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/auth/login",
                             json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
            r.raise_for_status()
            auth = {"Authorization": f"Bearer {r.json()['token']}"}
            await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)
            r = await c.put(f"{API}/stream/turn/cloudflare", headers=auth,
                            json={"key_id": "abc123def456ghi789", "token": "valid-token-abc"})
            assert r.status_code == 200, r.text
            print("CF configured OK")

        HITS.clear()
        t_whip = time.time()
        pub_pc, loc = await publish()
        print(f"WHIP published; mock hits after whip = {len(HITS)}")
        assert len(HITS) >= 1, "backend PC did not fetch Cloudflare TURN during /api/whip"

        await asyncio.sleep(1)
        async with httpx.AsyncClient(timeout=20) as c:
            st = (await c.get(f"{API}/stream/status")).json()
            assert st["publisher_connected"], st
            r = await c.get(f"{API}/stream/ice-servers")
            servers = r.json()["iceServers"]
            assert any("turn:turn.cloudflare.com" in u for s in servers for u in (s.get("urls") or [])), servers
        print(f"mock hits after ice-servers = {len(HITS)}")
        assert len(HITS) >= 2, f"expected >=2 CF mints per publish+view cycle, got {len(HITS)}"
        assert max(HITS) - t_whip <= 10, f"CF mints took too long: {max(HITS) - t_whip:.1f}s"

        hits_before_whep = len(HITS)
        view_pc, got = await view()
        await asyncio.wait_for(got.wait(), timeout=10)
        print(f"WHEP viewer got track; mock hits after whep = {len(HITS)}")
        assert len(HITS) > hits_before_whep, "backend PC did not fetch Cloudflare TURN during /api/whep"

        await view_pc.close()
        if loc.startswith("/"):
            async with httpx.AsyncClient(timeout=10) as c:
                await c.delete(f"http://localhost:8001{loc}")
        await pub_pc.close()

        # cleanup CF config
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/auth/login",
                             json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
            auth = {"Authorization": f"Bearer {r.json()['token']}"}
            await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)
        print("backend-PC-uses-CF-TURN E2E PASS")
    finally:
        if runner:
            await runner.cleanup()
        shutil.copy(BACKUP_ENV, ENV_FILE)
        try:
            BACKUP_ENV.unlink()
        except Exception:
            pass
        restart_backend()


if __name__ == "__main__":
    asyncio.run(main())
