"""E2E test — Cloudflare Calls TURN one-click.

Runs a tiny mock Cloudflare endpoint on a local port, points the backend at
it via `CF_TURN_ENDPOINT` env var + supervisor restart, then drives the full
CRUD flow through the real HTTP API. Restores the original .env at the end.
"""
import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from aiohttp import web

ENV_FILE = Path("/app/backend/.env")
BACKUP_ENV = Path("/tmp/.env.backup-cf-turn-test")
MOCK_PORT = 9739


async def start_mock_cloudflare():
    async def handler(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer valid-token-abc":
            return web.json_response({"error": "invalid"}, status=401)
        ttl = 3600
        try:
            body = await request.json()
            ttl = int(body.get("ttl", 3600))
        except Exception:
            pass
        return web.json_response(
            {
                "iceServers": [
                    {"urls": ["stun:stun.cloudflare.com:3478"]},
                    {
                        "urls": [
                            "turn:turn.cloudflare.com:3478?transport=udp",
                            "turn:turn.cloudflare.com:80?transport=tcp",
                        ],
                        "username": f"expiring-user-ttl{ttl}",
                        "credential": "expiring-cred-abc",
                    },
                ]
            },
            status=201,
        )

    app = web.Application()
    app.router.add_post(r"/v1/turn/keys/{key_id}/credentials/generate-ice-servers", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", MOCK_PORT)
    await site.start()
    return runner


def restart_backend():
    subprocess.check_call(["sudo", "supervisorctl", "restart", "backend"])
    for _ in range(30):
        try:
            r = httpx.get("http://localhost:8001/api/", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("backend did not come back up")


async def main():
    if not ENV_FILE.exists():
        raise RuntimeError("expected /app/backend/.env to exist")
    shutil.copy(ENV_FILE, BACKUP_ENV)
    try:
        # Point backend at our mock Cloudflare
        with open(ENV_FILE, "a") as f:
            f.write(
                f"\nCF_TURN_ENDPOINT=http://127.0.0.1:{MOCK_PORT}"
                "/v1/turn/keys/{key_id}/credentials/generate-ice-servers\n"
            )
        runner = await start_mock_cloudflare()
        restart_backend()

        API = "http://localhost:8001/api"
        async with httpx.AsyncClient(timeout=10) as c:
            # Fresh admin login
            r = await c.post(f"{API}/auth/login",
                             json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
            r.raise_for_status()
            auth = {"Authorization": f"Bearer {r.json()['token']}"}

            # Cleanup any previous config
            await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)

            # unauthenticated 401
            r = await c.get(f"{API}/stream/turn/cloudflare")
            assert r.status_code == 401, r.text
            print("unauth -> 401 OK")

            # status: not configured
            r = await c.get(f"{API}/stream/turn/cloudflare", headers=auth)
            assert r.json() == {"configured": False}, r.text

            # bad creds -> 400
            r = await c.put(f"{API}/stream/turn/cloudflare", headers=auth,
                            json={"key_id": "abc", "token": "invalid-token"})
            assert r.status_code == 400, r.text
            print("bad creds -> 400 OK")

            # save valid creds
            r = await c.put(f"{API}/stream/turn/cloudflare", headers=auth,
                            json={"key_id": "abc123def456ghi789", "token": "valid-token-abc"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["configured"] is True and data["key_id_masked"] == "abc1…i789", data
            print("save -> OK, key_id_masked =", data["key_id_masked"])

            # status shows configured + masked (not full)
            r = await c.get(f"{API}/stream/turn/cloudflare", headers=auth)
            j = r.json()
            assert j["configured"] and j["key_id_masked"] == "abc1…i789"
            assert "token" not in j and "token_enc" not in j, j
            print("status hides token OK")

            # public ice-servers now includes Cloudflare TURN
            r = await c.get(f"{API}/stream/ice-servers")
            servers = r.json()["iceServers"]
            turn = [s for s in servers if any("turn:turn.cloudflare.com" in u for u in s.get("urls", []))]
            assert turn, f"expected turn.cloudflare.com in {servers!r}"
            assert turn[0]["username"].startswith("expiring-user-ttl"), turn[0]
            assert turn[0]["credential"] == "expiring-cred-abc"
            print("ice-servers includes Cloudflare TURN with expiring creds OK")

            # delete
            r = await c.delete(f"{API}/stream/turn/cloudflare", headers=auth)
            assert r.status_code == 200 and r.json()["configured"] is False

            # after delete, ice-servers has only static STUN
            r = await c.get(f"{API}/stream/ice-servers")
            servers = r.json()["iceServers"]
            assert not any(
                "turn:" in u for s in servers for u in (s.get("urls") if isinstance(s.get("urls"), list) else [])
            ), servers
            print("after delete -> STUN only, OK")

        await runner.cleanup()
        print("cloudflare_turn E2E PASS")
    finally:
        shutil.copy(BACKUP_ENV, ENV_FILE)
        try:
            BACKUP_ENV.unlink()
        except Exception:
            pass
        restart_backend()


if __name__ == "__main__":
    asyncio.run(main())
