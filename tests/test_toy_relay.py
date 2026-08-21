"""E2E test for guest -> backend -> owner toy command relay.

Simulates the flow:
  1. Owner logs in and opens the host WebSocket.
  2. Owner announces `toys_status` with `available: true`.
  3. A guest access code is created and the guest opens ws/control/<code>.
  4. Guest sends `toy_command` messages (both vibrate and pattern).
  5. Assert backend broadcasts `toys.available=true` to the guest.
  6. Assert owner receives the exact `toy_command` payloads in order.
  7. Guest sends an INVALID toy command -> owner should NOT receive it.
"""
import asyncio
import json
import os
import websockets
import httpx

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
        r = await c.post(f"{API}/auth/login",
                         json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
        r.raise_for_status()
        token = r.json()["token"]
        # Fresh code
        r = await c.post(f"{API}/codes",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"label": "ToyTester", "minutes": 5})
        r.raise_for_status()
        code = r.json()["code"]

    # Owner host WS
    async with websockets.connect(f"{WS_BASE}/api/ws/host?token={token}") as owner:
        # First message is a state broadcast — drain it
        await asyncio.wait_for(owner.recv(), timeout=3)
        await owner.send(json.dumps({"type": "toys_status", "available": True, "pattern": None}))
        await asyncio.sleep(0.2)

        # Guest WS
        async with websockets.connect(f"{WS_BASE}/api/ws/control/{code}") as guest:
            # Wait until we see toys.available == True in a state msg
            st = await collect(
                guest,
                lambda m: m.get("type") == "state" and m.get("toys", {}).get("available") is True,
            )
            assert st["you"]["status"] == "active", st
            print("guest sees toys.available =", st["toys"])

            # Send commands from guest
            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:vibrate:60"}))
            m = await collect(owner, lambda m: m.get("type") == "toy_command")
            assert m["cmd"] == "toy:vibrate:60", m
            print("owner got:", m)

            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:pattern:pulse"}))
            m = await collect(owner, lambda m: m.get("type") == "toy_command")
            assert m["cmd"] == "toy:pattern:pulse", m
            print("owner got:", m)

            # Backend should also update snap.toys.pattern for guests
            st = await collect(guest, lambda m: m.get("type") == "state" and m.get("toys", {}).get("pattern") == "pulse")
            print("guest sees running pattern:", st["toys"])

            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:stop"}))
            m = await collect(owner, lambda m: m.get("type") == "toy_command")
            assert m["cmd"] == "toy:stop", m

            # Invalid — must be dropped silently
            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:vibrate:999"}))
            got_invalid = False
            try:
                await asyncio.wait_for(collect(owner, lambda m: m.get("type") == "toy_command"), timeout=1.0)
                got_invalid = True
            except asyncio.TimeoutError:
                pass
            assert not got_invalid, "backend should have rejected toy:vibrate:999"
            print("invalid command was correctly dropped")

            # Owner drops toys — subsequent guest toy commands must not reach owner
            await owner.send(json.dumps({"type": "toys_status", "available": False, "pattern": None}))
            await collect(guest, lambda m: m.get("type") == "state" and m.get("toys", {}).get("available") is False)
            await guest.send(json.dumps({"type": "toy_command", "cmd": "toy:vibrate:20"}))
            try:
                await asyncio.wait_for(collect(owner, lambda m: m.get("type") == "toy_command"), timeout=1.0)
                raise AssertionError("backend forwarded a toy command with no toys attached")
            except asyncio.TimeoutError:
                pass
            print("toys unavailable -> commands dropped as expected")

    print("all toy relay cases PASS")


if __name__ == "__main__":
    asyncio.run(main())
