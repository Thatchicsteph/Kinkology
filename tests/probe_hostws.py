import asyncio, json, time, os
import httpx, websockets

BASE = "http://localhost:8001"
API = BASE + "/api"
WS = "ws://localhost:8001"


async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{API}/auth/login", json={"email": "admin@ossm.local", "password": "ossm-admin-2026"})
        token = r.json()["token"]
    t0 = time.time()
    print("connecting host ws...")
    async with websockets.connect(f"{WS}/api/ws/host?token={token}", open_timeout=10) as ws:
        print(f"connected in {time.time()-t0:.2f}s")
        seen = {}
        deadline = time.time() + 3
        try:
            while time.time() < deadline and not ("chat_history" in seen and "toys_lock" in seen):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time())))
                if m.get("type") != "state":
                    print(f"  +{time.time()-t0:.2f}s {m.get('type')}")
                seen.setdefault(m.get("type"), m)
        except asyncio.TimeoutError:
            print("drain done")
        print("types:", list(seen))
        assert "chat_history" in seen and "toys_lock" in seen, seen
        await ws.send(json.dumps({"type": "chat", "text": "hi from host"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if m.get("type") == "chat_msg":
                print("echo:", m["message"])
                assert m["message"]["author"] == "Owner" and m["message"]["role"] == "owner"
                assert m["message"]["text"] == "hi from host"
                break
    print("PROBE OK", f"{time.time()-t0:.2f}s")


asyncio.run(main())
