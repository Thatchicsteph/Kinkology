"""Persistent-queue smoke test: mid-turn backend restart must NOT hand the
guest back all their spent time. Keeps the guest WS open across the restart
window and verifies used_seconds was flushed to Mongo before the crash.
"""
import asyncio
import json
import subprocess
import time

import httpx
import websockets

BASE = "https://stream-control-hub-14.preview.emergentagent.com"
API = f"{BASE}/api"
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASS = "ossm-admin-2026"


async def login() -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
        r.raise_for_status()
        return r.json()["token"]


async def mint_code(token: str, minutes: int = 60) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{API}/codes",
            json={"minutes": minutes, "label": "PERSIST_TEST"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()["code"]


async def delete_code(token: str, code: str):
    async with httpx.AsyncClient() as c:
        await c.delete(f"{API}/codes/{code}", headers={"Authorization": f"Bearer {token}"})


async def read_used_seconds(token: str, code: str) -> int:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API}/codes", headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        entries = r.json()
        if isinstance(entries, dict):
            entries = entries.get("codes", [])
        for entry in entries:
            if entry.get("code") == code:
                return int(entry.get("used_seconds", 0))
    return -1


async def wait_for_active(ws) -> int:
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
        if msg.get("type") == "state":
            you = msg.get("you") or {}
            if you.get("status") == "active":
                return int(you.get("remaining_seconds", -1))
    raise RuntimeError("timeout waiting for active")


async def wait_backend_ready(timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            try:
                r = await c.get(f"{API}/setup/status", timeout=2)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("backend never came ready")


async def main():
    token = await login()
    code = await mint_code(token, minutes=60)
    print(f"[+] minted code={code}")

    try:
        # Phase 1: keep the WS OPEN while turn burns down so we exercise the
        # periodic flush, not the on-disconnect flush.
        url = f"{WS_BASE}/api/ws/control/{code}"
        ws = await websockets.connect(url, open_timeout=5, origin=BASE)
        r1 = await wait_for_active(ws)
        print(f"[+] R1 remaining_seconds after activate = {r1}")
        assert r1 >= 3590, f"expected fresh ~3600s turn, got {r1}"

        # Drain state frames for 12s — enough for at least 2 flushes.
        drain_end = time.monotonic() + 12
        while time.monotonic() < drain_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
                _ = json.loads(raw)
            except asyncio.TimeoutError:
                pass

        used_mid = await read_used_seconds(token, code)
        print(f"[+] used_seconds mid-turn (from Mongo) = {used_mid}")
        assert used_mid >= 5, f"expected periodic flush to have written >=5s, got {used_mid}"

        # Kill the backend WITHOUT letting the client close cleanly first
        # (simulates a crash mid-turn).
        print("[+] restarting backend WHILE ws is open…")
        subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True)
        try:
            await ws.close()
        except Exception:
            pass

        await wait_backend_ready()
        print("[+] backend up again")

        # Reconnect and confirm remaining is not 3600 (the flushed seconds
        # were preserved).
        ws2 = await websockets.connect(url, open_timeout=5, origin=BASE)
        try:
            r2 = await wait_for_active(ws2)
        finally:
            try: await ws2.close()
            except Exception: pass
        used_after = await read_used_seconds(token, code)
        drop = r1 - r2
        print(f"[+] R2 remaining_seconds after restart = {r2}")
        print(f"[+] used_seconds after restart = {used_after}")
        print(f"[+] time consumed across restart = {drop}s (expect 5..30)")

        assert r2 < 3600, "remaining_seconds reset to 3600 — persistence FAILED"
        assert 5 <= drop <= 40, f"unexpected drop {drop}s"
        print("[✓] PERSISTENT QUEUE TEST PASSED")
    finally:
        await delete_code(token, code)
        print(f"[+] cleaned up code={code}")


if __name__ == "__main__":
    asyncio.run(main())
