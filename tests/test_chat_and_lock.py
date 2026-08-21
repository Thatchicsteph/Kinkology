"""E2E test for the toys kill switch AND multi-guest chat features."""
import asyncio
import json
import os

import httpx
import websockets

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api"
WS_BASE = BASE.replace("http", "ws", 1)


async def collect(ws, predicate, timeout=5):
    async def loop():
        while True:
            msg = json.loads(await ws.recv())
            if predicate(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout=timeout)


async def main():
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{API}/auth/login",
            json={"email": "admin@ossm.local", "password": "ossm-admin-2026"},
        )
        r.raise_for_status()
        token = r.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        # Clear any leftover chat / lockouts / codes.
        await c.delete(f"{API}/session/chat", headers=auth)
        await c.post(f"{API}/session/toys/unlock", headers=auth)

        r = await c.post(
            f"{API}/codes",
            headers=auth,
            json={"label": "ChatGuest", "minutes": 5},
        )
        r.raise_for_status()
        code = r.json()["code"]

    async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as owner:
        # owner receives toys_lock (initial state) + chat_history + state
        await asyncio.sleep(0.5)
        # Drain owner buffer
        try:
            while True:
                await asyncio.wait_for(owner.recv(), timeout=0.2)
        except asyncio.TimeoutError:
            pass

        await owner.send(json.dumps({"type": "toys_status", "available": True, "pattern": None}))
        await asyncio.sleep(0.3)

        async with websockets.connect(f"{WS_BASE}/api/ws/control/{code}") as guest:
            # 1. Guest gets chat history on connect (empty)
            hist = await collect(guest, lambda m: m.get("type") == "chat_history")
            assert hist["messages"] == [], hist
            print("guest chat_history on connect:", hist["messages"])

            # 2. Guest sends chat -> owner + guest both see chat_msg
            await guest.send(json.dumps({"type": "chat", "text": "hello owner"}))
            m = await collect(guest, lambda m: m.get("type") == "chat_msg")
            assert m["message"]["text"] == "hello owner", m
            assert m["message"]["role"] == "guest", m
            print("guest sees own msg:", m["message"])
            m2 = await collect(owner, lambda m: m.get("type") == "chat_msg")
            assert m2["message"]["text"] == "hello owner", m2
            print("owner sees guest msg:", m2["message"])

            # 3. Rate limit: sending immediately again should be dropped
            await guest.send(json.dumps({"type": "chat", "text": "spam1"}))
            await guest.send(json.dumps({"type": "chat", "text": "spam2"}))
            got = 0
            try:
                while True:
                    await asyncio.wait_for(
                        collect(guest, lambda m: m.get("type") == "chat_msg"),
                        timeout=0.4,
                    )
                    got += 1
            except asyncio.TimeoutError:
                pass
            assert got == 0, f"rate limit failed — got {got} extra msgs"
            print("rate limit dropped duplicate messages OK")

            # 4. Owner replies
            await owner.send(json.dumps({"type": "chat", "text": "hi guest"}))
            m3 = await collect(guest, lambda m: m.get("type") == "chat_msg" and m["message"]["role"] == "owner")
            assert m3["message"]["author"] == "Owner", m3
            assert m3["message"]["text"] == "hi guest", m3
            print("guest sees owner msg:", m3["message"])

            # 5. Kill switch — owner hits /session/toys/lock
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{API}/session/toys/lock", headers=auth)
                assert r.status_code == 200 and r.json()["locked"] is True
            # Owner side gets toy:stop AND toys_lock=True
            stop = await collect(owner, lambda m: m.get("type") == "toy_command" and m.get("cmd") == "toy:stop")
            lock = await collect(owner, lambda m: m.get("type") == "toys_lock" and m.get("locked") is True)
            print("owner got kill switch stop+lock:", stop, lock)
            # Guest sees toys.locked = True in state broadcast
            st = await collect(guest, lambda m: m.get("type") == "state" and m.get("toys", {}).get("locked") is True)
            print("guest sees toys.locked =", st["toys"])

            # 6. While locked, guest toy commands must be dropped
            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:vibrate:80"}))
            try:
                await asyncio.wait_for(
                    collect(owner, lambda m: m.get("type") == "toy_command" and m.get("cmd") == "toy:vibrate:80"),
                    timeout=1.0,
                )
                raise AssertionError("locked toys must not forward guest commands")
            except asyncio.TimeoutError:
                print("locked -> guest toy command dropped OK")

            # 7. Unlock
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{API}/session/toys/unlock", headers=auth)
                assert r.status_code == 200 and r.json()["locked"] is False
            await collect(owner, lambda m: m.get("type") == "toys_lock" and m.get("locked") is False)
            await collect(guest, lambda m: m.get("type") == "state" and m.get("toys", {}).get("locked") is False)
            await asyncio.sleep(1.2)  # let rate limiter reset

            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:vibrate:80"}))
            m = await collect(owner, lambda m: m.get("type") == "toy_command" and m.get("cmd") == "toy:vibrate:80")
            print("unlocked -> guest toy command flows again:", m)

            # 8. Chat clear from owner API
            async with httpx.AsyncClient() as c:
                r = await c.delete(f"{API}/session/chat", headers=auth)
                assert r.status_code == 200
            await collect(guest, lambda m: m.get("type") == "chat_cleared")
            await collect(owner, lambda m: m.get("type") == "chat_cleared")
            print("chat_cleared broadcast OK")

    print("all kill-switch + chat cases PASS")


if __name__ == "__main__":
    asyncio.run(main())
