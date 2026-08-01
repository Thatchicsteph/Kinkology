"""Backend regression tests for Kinkology.

Covers:
- Admin auth (login success/failure, cookie set, /auth/me)
- Access code CRUD (create, list, revoke, add-minutes, delete)
- Public code validation
- Session control endpoints (state, stop, skip)
- WebSocket flows: host relay + command validation, queue promotion, time expiry

Assumes backend reachable at REACT_APP_BACKEND_URL for HTTP (via /api prefix)
and uses local ws://localhost:8001 for WS testing (external ingress may not
support WS reliably in test env).
"""
import os
import asyncio
import json
import time
import pytest
import requests
import websockets
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "http://localhost:8001"
# Frontend .env has REACT_APP_BACKEND_URL, but our tests read from backend env if set.
FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break

WS_BASE = "ws://localhost:8001"
ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth(api):
    r = api.post(f"{BASE_URL}/api/auth/login",
                 json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data["token"]
    api.headers.update({"Authorization": f"Bearer {token}"})
    return {"token": token, "user": data["user"]}


# ---------- Auth ----------
class TestAuth:
    def test_login_invalid(self, api):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_login_success_and_cookie(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data and isinstance(data["token"], str)
        assert data["user"]["email"] == ADMIN_EMAIL
        # cookie should be set
        assert "access_token" in r.cookies or any(
            c.name == "access_token" for c in r.cookies)

    def test_me_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_me_with_bearer(self, api, auth):
        r = api.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL


# ---------- Access codes ----------
class TestCodes:
    created_ids = []

    def test_create_code(self, api, auth):
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_A", "minutes": 5})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["label"] == "TEST_A"
        assert d["granted_seconds"] == 300
        assert d["remaining_seconds"] == 300
        assert d["revoked"] is False
        assert len(d["code"]) == 6
        TestCodes.created_ids.append((d["id"], d["code"]))

    def test_list_contains_created(self, api, auth):
        r = api.get(f"{BASE_URL}/api/codes")
        assert r.status_code == 200
        codes = [c["code"] for c in r.json()]
        assert TestCodes.created_ids[0][1] in codes

    def test_validate_public_valid(self, api, auth):
        code = TestCodes.created_ids[0][1]
        r = requests.get(f"{BASE_URL}/api/access/{code}")
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is True
        assert d["remaining_seconds"] == 300

    def test_validate_unknown(self):
        r = requests.get(f"{BASE_URL}/api/access/ZZZZZZ")
        assert r.status_code == 200
        assert r.json()["valid"] is False

    def test_add_minutes(self, api, auth):
        cid, code = TestCodes.created_ids[0]
        r = api.post(f"{BASE_URL}/api/codes/{cid}/add-minutes",
                     json={"label": "", "minutes": 10})
        assert r.status_code == 200, r.text
        assert r.json()["granted_seconds"] == 300 + 600

    def test_revoke(self, api, auth):
        cid, code = TestCodes.created_ids[0]
        r = api.post(f"{BASE_URL}/api/codes/{cid}/revoke")
        assert r.status_code == 200
        v = requests.get(f"{BASE_URL}/api/access/{code}")
        assert v.json()["valid"] is False

    def test_add_minutes_unrevokes(self, api, auth):
        cid, code = TestCodes.created_ids[0]
        r = api.post(f"{BASE_URL}/api/codes/{cid}/add-minutes",
                     json={"label": "", "minutes": 2})
        assert r.status_code == 200
        v = requests.get(f"{BASE_URL}/api/access/{code}")
        assert v.json()["valid"] is True

    def test_delete(self, api, auth):
        cid, code = TestCodes.created_ids[0]
        r = api.delete(f"{BASE_URL}/api/codes/{cid}")
        assert r.status_code == 200
        v = requests.get(f"{BASE_URL}/api/access/{code}")
        assert v.json()["valid"] is False

    def test_minutes_validation(self, api, auth):
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_bad", "minutes": 0})
        assert r.status_code == 422


# ---------- Session ----------
class TestSession:
    def test_state_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/session/state")
        assert r.status_code == 401

    def test_state(self, api, auth):
        r = api.get(f"{BASE_URL}/api/session/state")
        assert r.status_code == 200
        d = r.json()
        assert "host_connected" in d
        assert "queue" in d

    def test_stop(self, api, auth):
        r = api.post(f"{BASE_URL}/api/session/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_skip(self, api, auth):
        r = api.post(f"{BASE_URL}/api/session/skip")
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------- WebSockets ----------
async def _create_code(api, auth, minutes=5, label="TEST_WS"):
    r = api.post(f"{BASE_URL}/api/codes", json={"label": label, "minutes": minutes})
    assert r.status_code == 200
    return r.json()


async def _cleanup_code(api, cid):
    try:
        api.delete(f"{BASE_URL}/api/codes/{cid}")
    except Exception:
        pass


async def _recv_until(ws, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        msg = json.loads(raw)
        if predicate(msg):
            return msg
    return None


@pytest.mark.asyncio
async def test_ws_relay_and_validation(api, auth):
    """Host receives valid commands; invalid ones are dropped."""
    code = await _create_code(api, auth, minutes=5, label="TEST_RELAY")
    token = auth["token"]

    host_uri = f"{WS_BASE}/api/ws/host?token={token}"
    ctrl_uri = f"{WS_BASE}/api/ws/control/{code['code']}"
    try:
        async with websockets.connect(host_uri) as host_ws:
            # Wait for initial state
            await _recv_until(host_ws, lambda m: m.get("type") == "state", 3)
            async with websockets.connect(ctrl_uri) as ctrl_ws:
                # Wait until this client is active
                got_active = await _recv_until(
                    ctrl_ws,
                    lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "active",
                    5,
                )
                assert got_active is not None, "guest did not become active"

                # Send valid command
                await ctrl_ws.send(json.dumps({"type": "command", "cmd": "set:speed:42"}))
                relayed = await _recv_until(
                    host_ws,
                    lambda m: m.get("type") == "command" and m.get("cmd") == "set:speed:42",
                    3,
                )
                assert relayed is not None, "valid command not relayed to host"

                # Send invalid command; must NOT be relayed
                await ctrl_ws.send(json.dumps({"type": "command", "cmd": "set:speed:250"}))
                await ctrl_ws.send(json.dumps({"type": "command", "cmd": "arbitrary garbage"}))
                bad = await _recv_until(
                    host_ws,
                    lambda m: m.get("type") == "command" and m.get("cmd") in ("set:speed:250", "arbitrary garbage"),
                    2,
                )
                assert bad is None, "invalid command was relayed"
    finally:
        await _cleanup_code(api, code["id"])


@pytest.mark.asyncio
async def test_ws_queue_promotion(api, auth):
    """Second guest is queued; when first disconnects, second becomes active."""
    code_a = await _create_code(api, auth, minutes=5, label="TEST_QA")
    code_b = await _create_code(api, auth, minutes=5, label="TEST_QB")
    try:
        uri_a = f"{WS_BASE}/api/ws/control/{code_a['code']}"
        uri_b = f"{WS_BASE}/api/ws/control/{code_b['code']}"

        ws_a = await websockets.connect(uri_a)
        try:
            got_a = await _recv_until(
                ws_a,
                lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "active",
                5,
            )
            assert got_a is not None, "A did not become active"

            ws_b = await websockets.connect(uri_b)
            try:
                got_b_wait = await _recv_until(
                    ws_b,
                    lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "waiting",
                    5,
                )
                assert got_b_wait is not None, "B was not queued as waiting"
                assert got_b_wait["you"]["position"] == 1

                # A disconnects -> B should be promoted
                await ws_a.close()
                got_b_active = await _recv_until(
                    ws_b,
                    lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "active",
                    5,
                )
                assert got_b_active is not None, "B not promoted after A disconnect"
            finally:
                await ws_b.close()
        finally:
            try:
                await ws_a.close()
            except Exception:
                pass
    finally:
        await _cleanup_code(api, code_a["id"])
        await _cleanup_code(api, code_b["id"])


@pytest.mark.asyncio
async def test_ws_time_expiry(api, auth):
    """Setting used_seconds close to granted_seconds -> turn ends when time runs out."""
    code = await _create_code(api, auth, minutes=1, label="TEST_EXP")
    # Set used_seconds so remaining is ~3s via Mongo direct
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    mclient = AsyncIOMotorClient(mongo_url)
    try:
        await mclient[db_name].access_codes.update_one(
            {"code": code["code"]}, {"$set": {"used_seconds": 60 - 3}}
        )
        uri = f"{WS_BASE}/api/ws/control/{code['code']}"
        async with websockets.connect(uri) as ws:
            # Wait for active
            active = await _recv_until(
                ws,
                lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "active",
                5,
            )
            assert active is not None
            # Wait for turn_ended
            ended = await _recv_until(
                ws,
                lambda m: m.get("type") == "turn_ended",
                8,
            )
            assert ended is not None, "turn did not end when time reached 0"
    finally:
        mclient.close()
        await _cleanup_code(api, code["id"])


@pytest.mark.asyncio
async def test_ws_rejects_invalid_code():
    uri = f"{WS_BASE}/api/ws/control/BADCODE"
    async with websockets.connect(uri) as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=3)
        m = json.loads(msg)
        assert m.get("type") == "rejected"


# ---------- Safety limits (settings) ----------
class TestSettings:
    def test_get_settings_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/settings")
        assert r.status_code == 401

    def test_get_default_settings(self, api, auth):
        r = api.get(f"{BASE_URL}/api/settings")
        assert r.status_code == 200
        d = r.json()
        assert "min_depth" in d and "max_speed" in d
        assert isinstance(d["min_depth"], int)
        assert isinstance(d["max_speed"], int)

    def test_put_settings_persists_and_reflects_in_session_state(self, api, auth):
        r = api.put(f"{BASE_URL}/api/settings", json={"min_depth": 40, "max_speed": 70})
        assert r.status_code == 200
        d = r.json()
        assert d["min_depth"] == 40 and d["max_speed"] == 70

        # GET reflects
        r2 = api.get(f"{BASE_URL}/api/settings")
        j2 = r2.json()
        assert j2["min_depth"] == 40 and j2["max_speed"] == 70

        # session/state includes limits
        r3 = api.get(f"{BASE_URL}/api/session/state")
        assert r3.status_code == 200
        assert r3.json().get("limits") == {"min_depth": 40, "max_speed": 70}

    def test_put_settings_validation(self, api, auth):
        r = api.put(f"{BASE_URL}/api/settings", json={"min_depth": -1, "max_speed": 50})
        assert r.status_code == 422
        r = api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 150})
        assert r.status_code == 422

    def test_reset_settings(self, api, auth):
        r = api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 100})
        assert r.status_code == 200
        d = r.json()
        assert d["min_depth"] == 0 and d["max_speed"] == 100


# ---------- WebSocket server-side limit clamping ----------
@pytest.mark.asyncio
async def test_ws_clamp_depth_and_speed(api, auth):
    """With limits min_depth=40, max_speed=70:
       set:depth:10 -> set:depth:40, set:depth:80 unchanged
       set:speed:95 -> set:speed:70, set:speed:50 unchanged
    """
    # Set limits
    r = api.put(f"{BASE_URL}/api/settings", json={"min_depth": 40, "max_speed": 70})
    assert r.status_code == 200

    code = await _create_code(api, auth, minutes=5, label="TEST_CLAMP")
    token = auth["token"]
    host_uri = f"{WS_BASE}/api/ws/host?token={token}"
    ctrl_uri = f"{WS_BASE}/api/ws/control/{code['code']}"
    try:
        async with websockets.connect(host_uri) as host_ws:
            await _recv_until(host_ws, lambda m: m.get("type") == "state", 3)
            async with websockets.connect(ctrl_uri) as ctrl_ws:
                got_active = await _recv_until(
                    ctrl_ws,
                    lambda m: m.get("type") == "state" and (m.get("you") or {}).get("status") == "active",
                    5,
                )
                assert got_active is not None

                cases = [
                    ("set:depth:10", "set:depth:40"),   # clamped up
                    ("set:depth:80", "set:depth:80"),   # unchanged
                    ("set:speed:95", "set:speed:70"),   # clamped down
                    ("set:speed:50", "set:speed:50"),   # unchanged
                ]
                for sent, expected in cases:
                    await ctrl_ws.send(json.dumps({"type": "command", "cmd": sent}))
                    relayed = await _recv_until(
                        host_ws,
                        lambda m: m.get("type") == "command",
                        3,
                    )
                    assert relayed is not None, f"no relay for {sent}"
                    assert relayed["cmd"] == expected, f"sent {sent} expected {expected} got {relayed['cmd']}"
    finally:
        await _cleanup_code(api, code["id"])
        # Reset limits to defaults
        api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 100})
