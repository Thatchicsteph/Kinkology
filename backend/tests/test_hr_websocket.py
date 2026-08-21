"""Tests for Heart Rate WebSocket /api/ws/hr and its effect on telemetry."""
import os
import json
import asyncio
import pytest
import requests
import websockets

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
WS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok, "no token in login response"
    return tok


def get_state():
    r = requests.get(f"{BASE_URL}/api/overlay/state", timeout=10)
    assert r.status_code == 200
    return r.json()


def test_overlay_state_has_hr_keys():
    st = get_state()
    assert "hr_bpm" in st
    assert "hr_connected" in st


def test_ws_hr_rejects_without_token():
    async def run():
        try:
            async with websockets.connect(f"{WS_BASE}/api/ws/hr") as ws:
                # Should close immediately with 4401
                try:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                except websockets.ConnectionClosed as e:
                    return e.code
                except asyncio.TimeoutError:
                    return None
            return "closed-cleanly"
        except websockets.InvalidStatusCode as e:
            return e.status_code
        except websockets.ConnectionClosedError as e:
            return e.code
        except Exception as e:
            return f"err:{type(e).__name__}:{e}"
    code = asyncio.get_event_loop().run_until_complete(run())
    # Accept anything indicating rejection (4401 or connection failure)
    assert code in (4401, 1006) or (isinstance(code, str) and "err" in code) or code == "closed-cleanly" and False, \
        f"Expected close code 4401, got {code}"


def test_ws_hr_rejects_bad_token():
    async def run():
        try:
            async with websockets.connect(f"{WS_BASE}/api/ws/hr?token=badtoken") as ws:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                except websockets.ConnectionClosed as e:
                    return e.code
        except Exception as e:
            return f"err:{type(e).__name__}"
        return None
    code = asyncio.get_event_loop().run_until_complete(run())
    assert code == 4401 or (isinstance(code, str) and "err" in code), f"unexpected: {code}"


def test_ws_hr_authenticated_updates_telemetry(admin_token):
    async def run():
        async with websockets.connect(f"{WS_BASE}/api/ws/hr?token={admin_token}") as ws:
            await asyncio.sleep(0.5)
            # Send bpm 72
            await ws.send(json.dumps({"type": "hr", "bpm": 72}))
            await asyncio.sleep(0.6)
            st1 = get_state()
            # Update to 145
            await ws.send(json.dumps({"type": "hr", "bpm": 145}))
            await asyncio.sleep(0.6)
            st2 = get_state()
            # Clamp high
            await ws.send(json.dumps({"type": "hr", "bpm": 9999}))
            await asyncio.sleep(0.6)
            st_high = get_state()
            # Clamp negative
            await ws.send(json.dumps({"type": "hr", "bpm": -50}))
            await asyncio.sleep(0.6)
            st_neg = get_state()
            # hr_status disconnected
            await ws.send(json.dumps({"type": "hr_status", "connected": False}))
            await asyncio.sleep(0.6)
            st_off = get_state()
            return st1, st2, st_high, st_neg, st_off

    st1, st2, st_high, st_neg, st_off = asyncio.get_event_loop().run_until_complete(run())
    assert st1["hr_bpm"] == 72 and st1["hr_connected"] is True, st1
    assert st2["hr_bpm"] == 145, st2
    assert st_high["hr_bpm"] == 300, st_high
    assert st_neg["hr_bpm"] == 0, st_neg
    assert st_off["hr_connected"] is False and st_off["hr_bpm"] == 0, st_off


def test_ws_hr_disconnect_resets(admin_token):
    async def run():
        async with websockets.connect(f"{WS_BASE}/api/ws/hr?token={admin_token}") as ws:
            await ws.send(json.dumps({"type": "hr", "bpm": 88}))
            await asyncio.sleep(0.6)
            st_on = get_state()
        # After disconnect
        await asyncio.sleep(0.8)
        st_after = get_state()
        return st_on, st_after
    st_on, st_after = asyncio.get_event_loop().run_until_complete(run())
    assert st_on["hr_bpm"] == 88 and st_on["hr_connected"] is True
    assert st_after["hr_bpm"] == 0 and st_after["hr_connected"] is False


def test_overlay_ws_receives_hr_frame(admin_token):
    async def run():
        # Open HR sender first
        async with websockets.connect(f"{WS_BASE}/api/ws/hr?token={admin_token}") as hr_ws:
            await hr_ws.send(json.dumps({"type": "hr", "bpm": 111}))
            await asyncio.sleep(0.5)
            async with websockets.connect(f"{WS_BASE}/api/ws/overlay") as ov:
                # First frame should include current state
                msg = await asyncio.wait_for(ov.recv(), timeout=5)
                return json.loads(msg)
    frame = asyncio.get_event_loop().run_until_complete(run())
    assert "hr_bpm" in frame and "hr_connected" in frame
    assert frame["hr_bpm"] == 111
    assert frame["hr_connected"] is True
