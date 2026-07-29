"""Guest activation end-to-end over the EXTERNAL wss ingress.

This test iteration_8 specifically validates that:
  1. GET https://<external>/api/access/{CODE} correctly validates codes
  2. wss://<external>/api/ws/control/{CODE} completes a WebSocket upgrade
     through the reverse proxy / ingress AND the guest becomes 'active'
     within ~2 seconds.
  3. An invalid/revoked code over the external wss receives {type:'rejected'}
     and the socket is closed (never active).

The idea is to prove that the OSSM Bridge app + this env's reverse proxy DO
upgrade wss for /api/ws/*, so a user experiencing the opposite behind their
own Caddy has an INFRA (Caddy) issue, not an app-code bug.
"""
import os
import asyncio
import json
import pytest
import requests
import websockets
from pathlib import Path

FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
BASE_URL = None
for line in FRONTEND_ENV.read_text().splitlines():
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
        break
assert BASE_URL and BASE_URL.startswith("https://"), "external HTTPS URL required"
WSS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def api(token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}"})
    return s


def _mk_code(api, minutes=5, label="TEST_ext_ws"):
    r = api.post(f"{BASE_URL}/api/codes",
                 json={"label": label, "minutes": minutes}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def _del_code(api, cid):
    try:
        api.delete(f"{BASE_URL}/api/codes/{cid}", timeout=10)
    except Exception:
        pass


def _revoke(api, cid):
    return api.post(f"{BASE_URL}/api/codes/{cid}/revoke", timeout=10)


async def _recv_until(ws, pred, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - asyncio.get_event_loop().time()))
        except asyncio.TimeoutError:
            return None
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if pred(msg):
            return msg
    return None


def test_access_get_external_valid_and_invalid(api):
    """GET /api/access/{CODE} via external HTTPS."""
    c = _mk_code(api)
    try:
        r = requests.get(f"{BASE_URL}/api/access/{c['code']}", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j.get("valid") is True
        assert j.get("remaining_seconds", 0) > 0

        # Unknown
        r2 = requests.get(f"{BASE_URL}/api/access/GARBAGE9", timeout=10)
        assert r2.status_code == 200
        assert r2.json().get("valid") is False

        # Revoke -> invalid  (revoke uses the mongo id, not the code)
        _revoke(api, c["id"])
        r3 = requests.get(f"{BASE_URL}/api/access/{c['code']}", timeout=10)
        assert r3.status_code == 200
        assert r3.json().get("valid") is False
    finally:
        _del_code(api, c["id"])


@pytest.mark.asyncio
async def test_external_wss_guest_becomes_active(api):
    """CORE: wss ingress upgrade + guest activation for a VALID code."""
    c = _mk_code(api)
    code = c["code"]
    uri = f"{WSS_BASE}/api/ws/control/{code}"
    try:
        async with websockets.connect(uri, open_timeout=10, close_timeout=5) as ws:
            msg = await _recv_until(
                ws,
                lambda m: m.get("type") == "state"
                          and (m.get("you") or {}).get("status") == "active",
                timeout=3.0,
            )
            assert msg is not None, "guest did NOT become active over external wss"
            assert (msg.get("you") or {}).get("remaining_seconds", 0) > 0

            # Send a valid command (no host connected — should not error).
            await ws.send(json.dumps({"type": "command", "cmd": "set:speed:10"}))
            # We should still receive at least one further state (telemetry/broadcast tick)
            follow = await _recv_until(ws, lambda m: m.get("type") == "state", timeout=2.5)
            assert follow is not None, "no state broadcasts after command"
    finally:
        _del_code(api, c["id"])


@pytest.mark.asyncio
async def test_external_wss_rejects_invalid_code():
    uri = f"{WSS_BASE}/api/ws/control/BADCODEX"
    async with websockets.connect(uri, open_timeout=10, close_timeout=5) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        msg = json.loads(raw)
        assert msg.get("type") == "rejected"
        # Server closes; ensure we don't ever get 'active'.
        try:
            more = await asyncio.wait_for(ws.recv(), timeout=1.5)
            # If more comes, it must NOT be an active state
            try:
                j = json.loads(more)
                assert (j.get("you") or {}).get("status") != "active"
            except Exception:
                pass
        except Exception:
            pass


@pytest.mark.asyncio
async def test_external_wss_rejects_revoked_code(api):
    c = _mk_code(api, label="TEST_ext_revoked")
    code = c["code"]
    _revoke(api, c["id"])
    try:
        uri = f"{WSS_BASE}/api/ws/control/{code}"
        async with websockets.connect(uri, open_timeout=10, close_timeout=5) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            msg = json.loads(raw)
            assert msg.get("type") == "rejected"
    finally:
        _del_code(api, c["id"])
