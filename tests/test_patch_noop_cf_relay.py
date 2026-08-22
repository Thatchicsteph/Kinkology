"""E2E (iteration 19) — aioice patch no-op by default + Cloudflare TURN relay.

Part A: with NO STREAM_UDP_* / STREAM_PUBLIC_IP (default), configure Cloudflare
        via a hit-counting mock, then WHIP publish + WHEP subscribe. Assert both
        return 201, CF mock is hit at least once per backend PC creation, and the
        answer SDPs still carry native aioice `candidate:` lines incl. host.
Part B: regression — POST /api/whep with Host: 192.168.99.99 rewrites
        `typ host` candidates to that IP.
Part C: opt-in — write STREAM_UDP_MIN/MAX/STREAM_PUBLIC_IP to backend/.env,
        restart, and check the patch installs (log line + candidate ports).
Restores /app/backend/.env at the end.
"""
import asyncio
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

ENV_FILE = Path("/app/backend/.env")
BACKUP_ENV = Path("/tmp/.env.backup-iter19")
MOCK_PORT = 9739
API = "http://localhost:8001/api"
LOG = Path("/var/log/supervisor/backend.err.log")

HITS = []
FAILURES = []


def check(cond, msg):
    if cond:
        print(f"PASS: {msg}")
    else:
        print(f"FAIL: {msg}")
        FAILURES.append(msg)


async def start_mock_cloudflare():
    async def handler(request):
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
    for _ in range(60):
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
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/whip", content=pc.localDescription.sdp,
                         headers={"Content-Type": "application/sdp"})
    check(r.status_code == 201, f"/api/whip -> 201 (got {r.status_code})")
    if r.status_code != 201:
        raise RuntimeError(f"WHIP failed: {r.text[:300]}")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
    return pc, r.headers.get("Location", ""), r.text


async def view(headers=None):
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    h = {"Content-Type": "application/sdp"}
    h.update(headers or {})
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/whep", content=pc.localDescription.sdp, headers=h)
    return pc, r


def candidates(sdp):
    return [l for l in sdp.splitlines() if "candidate:" in l]


async def configure_cf():
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/auth/login",
                         json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
        r.raise_for_status()
        auth = {"Authorization": f"Bearer {r.json()['token']}"}
        await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)
        r = await c.put(f"{API}/stream/turn/cloudflare", headers=auth,
                        json={"key_id": "abc123def456ghi789", "token": "valid-token-abc"})
        check(r.status_code == 200, f"CF creds saved (got {r.status_code})")
        return auth


async def cleanup_cf():
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/auth/login",
                         json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
        auth = {"Authorization": f"Bearer {r.json()['token']}"}
        await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)


async def part_a_and_b():
    print("\n=== PART A: patch disabled (default) + Cloudflare TURN ===")
    await configure_cf()
    HITS.clear()
    pub_pc, loc, whip_answer = await publish()
    hits_after_whip = len(HITS)
    check(hits_after_whip >= 1, f"CF minted during /api/whip (hits={hits_after_whip})")
    whip_cands = candidates(whip_answer)
    check(any(" typ host" in l for l in whip_cands),
          f"WHIP answer has host candidates ({len(whip_cands)} candidate lines)")

    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=20) as c:
        st = (await c.get(f"{API}/stream/status")).json()
        check(st.get("publisher_connected") is True, f"publisher connected ({st})")

    hits_before_whep = len(HITS)
    view_pc, r = await view()
    check(r.status_code == 201, f"/api/whep -> 201 (got {r.status_code})")
    check(len(HITS) > hits_before_whep,
          f"CF minted during /api/whep (total hits={len(HITS)})")
    check(len(HITS) >= 2, f"CF mock hit at least twice (once per PC), got {len(HITS)}")
    whep_cands = candidates(r.text)
    check(len(whep_cands) > 0, f"WHEP answer has candidate: lines ({len(whep_cands)})")
    check(any(" typ host" in l for l in whep_cands), "WHEP answer has a host candidate")
    print("WHEP candidates:", whep_cands)
    await view_pc.close()

    print("\n=== PART B: SDP host rewrite regression (Host: 192.168.99.99) ===")
    view_pc2, r2 = await view({"Host": "192.168.99.99"})
    check(r2.status_code == 201, f"/api/whep with custom Host -> 201 (got {r2.status_code})")
    host_lines = [l for l in candidates(r2.text) if " typ host" in l]
    check(bool(host_lines) and all(" 192.168.99.99 " in l for l in host_lines),
          f"host candidates rewritten to 192.168.99.99: {host_lines}")
    await view_pc2.close()

    if loc.startswith("/"):
        async with httpx.AsyncClient(timeout=10) as c:
            await c.delete(f"http://localhost:8001{loc}")
    await pub_pc.close()
    await cleanup_cf()


async def part_c():
    print("\n=== PART C: opt-in via backend/.env ===")
    with open(ENV_FILE, "a") as f:
        f.write("\nSTREAM_UDP_MIN=50000\nSTREAM_UDP_MAX=50003\nSTREAM_PUBLIC_IP=127.0.0.1\n")
    log_before = LOG.stat().st_size if LOG.exists() else 0
    restart_backend()
    await asyncio.sleep(1)
    log_tail = ""
    if LOG.exists():
        with open(LOG, errors="ignore") as f:
            f.seek(log_before)
            log_tail = f.read()
    active = "stream_patch active" in log_tail
    untouched = "leaving aioice untouched" in log_tail
    print(f"log: stream_patch active={active} untouched={untouched}")
    check(active, "patch installs when STREAM_* set in backend/.env (log 'stream_patch active')")

    pub_pc, loc, answer = await publish()
    cands = [l for l in candidates(answer) if " typ host" in l]
    print("WHIP host candidates (opt-in):", cands)
    ok = bool(cands)
    for l in cands:
        m = re.search(r"candidate:\S+ \d+ udp \d+ (\S+) (\d+) typ host", l)
        if not m:
            ok = False
            continue
        host, port = m.group(1), int(m.group(2))
        if host != "127.0.0.1" or not (50000 <= port <= 50003):
            ok = False
    check(ok, f"opt-in host candidates use 127.0.0.1 and port 50000-50003: {cands}")
    if loc.startswith("/"):
        async with httpx.AsyncClient(timeout=10) as c:
            await c.delete(f"http://localhost:8001{loc}")
    await pub_pc.close()


async def main():
    shutil.copy(ENV_FILE, BACKUP_ENV)
    runner = None
    try:
        with open(ENV_FILE, "a") as f:
            f.write(f"\nCF_TURN_ENDPOINT=http://127.0.0.1:{MOCK_PORT}"
                    "/v1/turn/keys/{key_id}/credentials/generate-ice-servers\n")
        runner = await start_mock_cloudflare()
        restart_backend()
        await part_a_and_b()
        await part_c()
    finally:
        if runner:
            await runner.cleanup()
        shutil.copy(BACKUP_ENV, ENV_FILE)
        BACKUP_ENV.unlink(missing_ok=True)
        restart_backend()

    print("\n==== SUMMARY ====")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES:")
        for f in FAILURES:
            print(" -", f)
        raise SystemExit(1)
    print("iteration19 patch-noop E2E PASS")


if __name__ == "__main__":
    asyncio.run(main())
