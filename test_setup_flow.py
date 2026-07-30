import os, asyncio
from pymongo import MongoClient

os.environ.pop("ADMIN_EMAIL", None)
os.environ.pop("ADMIN_PASSWORD", None)

import server
from motor.motor_asyncio import AsyncIOMotorClient
from httpx import AsyncClient, ASGITransport

async def main():
    sync = MongoClient(os.environ["MONGO_URL"])
    sync.drop_database("setup_flow_test")
    # bind a fresh motor client to THIS running loop
    server.db = AsyncIOMotorClient(os.environ["MONGO_URL"])["setup_flow_test"]

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/setup/status")
        print("status(empty):", r.json()); assert r.json()["needs_setup"] is True

        r = await c.post("/api/setup", json={"email": "owner@test.com", "password": "short"})
        print("weak-pass:", r.status_code); assert r.status_code == 400

        r = await c.post("/api/setup", json={"email": "Owner@Test.com", "password": "supersecret1", "local_url": "http://localhost", "public_url": "https://tg30.ddns.net"})
        print("setup ok:", r.status_code, r.json().get("user")); assert r.status_code == 200 and r.json()["user"]["email"] == "owner@test.com"

        r = await c.get("/api/setup/status")
        print("status(after):", r.json()); assert r.json()["needs_setup"] is False

        r = await c.post("/api/setup", json={"email": "b@b.com", "password": "anotherpass1"})
        print("setup blocked:", r.status_code); assert r.status_code == 403

        r = await c.post("/api/auth/login", json={"email": "owner@test.com", "password": "supersecret1"})
        print("login:", r.status_code, "token" in r.json()); assert r.status_code == 200 and "token" in r.json()
        token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"}

        r = await c.get("/api/settings", headers=h)
        print("settings:", r.json())
        assert r.json()["local_url"] == "http://localhost" and r.json()["public_url"] == "https://tg30.ddns.net"

        r = await c.put("/api/settings/urls", headers=h, json={"local_url": "http://localhost:8080", "public_url": "https://new.example.com"})
        print("urls updated:", r.json())
        assert r.json()["public_url"] == "https://new.example.com"

    sync.drop_database("setup_flow_test")
    print("\nALL SETUP-FLOW CHECKS PASSED")

asyncio.run(main())
