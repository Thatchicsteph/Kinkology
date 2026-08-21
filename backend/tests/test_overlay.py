"""Overlay telemetry tests (public WS + GET /api/overlay/state)."""
import os
import asyncio
import json
import time
import pytest
import requests
import websockets
from pathlib import Path

FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
BASE_URL = None
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break
BASE_URL = BASE_URL or "http://localhost:8001"

WS_BASE = "ws://localhost:8001"
ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


def _login():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _create_code(token, label="TEST_OVERLAY", minutes=5):
    r = requests.post(f"{BASE_URL}/api/codes",
                      json={"label": label, "minutes": minutes},
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def _delete_code(token, cid):
    requests.delete(f"{BASE_URL}/api/codes/{cid}",
                    headers={"Authorization": f"Bearer {token}"})


def _set_limits(token, min_depth, max_speed):
    r = requests.put(f"{BASE_URL}/api/settings",
                     json={"min_depth": min_depth, "max_speed": max_speed},
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


TOKEN = None


@pytest.fixture(scope="module", autouse=True)
def module_setup():
    global TOKEN
    TOKEN = _login()
    yield
    # cleanup: reset limits
    try:
        _set_limits(TOKEN, 0, 100)
    except Exception:
        pass


# -------- Public REST --------
class TestOverlayRestPublic:
    def test_overlay_state_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/overlay/state")
        assert r.status_code == 200
        d = r.json()
        for key in ("type", "speed", "depth", "stroke", "sensation",
                    "run_seconds", "session_seconds", "running",
                    "controller", "host_connected"):
            assert key in d, f"missing {key}"
        assert d["type"] == "telemetry"


# -------- Public WS --------
class TestOverlayWebSocket:
    def test_ws_connect_and_initial_frame(self):
        async def run():
            async with websockets.connect(f"{WS_BASE}/api/ws/overlay") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                frame = json.loads(msg)
                assert frame["type"] == "telemetry"
                for k in ("speed", "depth", "stroke", "sensation",
                          "run_seconds", "session_seconds", "running",
                          "controller", "host_connected"):
                    assert k in frame
        asyncio.run(run())

    def test_telemetry_updates_via_relay_and_clamp(self):
        """Drive a guest, send set:X commands, verify overlay frames.
        Also confirms max_speed clamp is reflected in overlay values."""
        code_doc = _create_code(TOKEN, label="TEST_OV_RELAY")
        code = code_doc["code"]
        cid_db = code_doc["id"]
        # Set a max_speed cap so we can test clamping
        _set_limits(TOKEN, 0, 70)

        async def run():
            # Connect overlay WS
            overlay = await websockets.connect(f"{WS_BASE}/api/ws/overlay")
            # Consume initial frame
            await asyncio.wait_for(overlay.recv(), timeout=5)
            # Connect guest control WS
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{code}")
            # Give hub a moment to promote guest to active
            await asyncio.sleep(0.5)

            async def send_cmd(cmd):
                await guest.send(json.dumps({"type": "command", "cmd": cmd}))

            await send_cmd("set:depth:70")
            await send_cmd("set:stroke:55")
            await send_cmd("set:sensation:40")
            await send_cmd("set:speed:95")  # should clamp to 70

            # Collect frames until we see all values set OR timeout
            deadline = time.monotonic() + 5
            last = None
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(overlay.recv(), timeout=1.5)
                except asyncio.TimeoutError:
                    continue
                fr = json.loads(raw)
                if fr.get("type") != "telemetry":
                    continue
                last = fr
                if (fr["depth"] == 70 and fr["stroke"] == 55 and
                        fr["sensation"] == 40 and fr["speed"] == 70):
                    break

            assert last is not None, "no telemetry frame received"
            assert last["depth"] == 70, last
            assert last["stroke"] == 55, last
            assert last["sensation"] == 40, last
            assert last["speed"] == 70, f"expected clamped speed=70, got {last['speed']}"
            assert last["running"] is True
            assert last["controller"] == "TEST_OV_RELAY", last

            # Also test REST endpoint reflects same state
            r = requests.get(f"{BASE_URL}/api/overlay/state")
            state = r.json()
            assert state["speed"] == 70
            assert state["depth"] == 70
            assert state["controller"] == "TEST_OV_RELAY"

            # ---- Run time / session time ----
            # After a couple seconds while speed>0, run_seconds should increase
            await asyncio.sleep(2.2)
            # drain a fresh frame after tick
            fresh = None
            drain_end = time.monotonic() + 3
            while time.monotonic() < drain_end:
                try:
                    raw = await asyncio.wait_for(overlay.recv(), timeout=1.2)
                except asyncio.TimeoutError:
                    break
                d = json.loads(raw)
                if d.get("type") == "telemetry":
                    fresh = d
            assert fresh is not None
            assert fresh["run_seconds"] >= 2, fresh
            assert fresh["session_seconds"] >= 2, fresh
            assert fresh["running"] is True
            prev_run = fresh["run_seconds"]

            # Set speed:0 -> running False, run_seconds should stop increasing
            await send_cmd("set:speed:0")
            await asyncio.sleep(2.2)
            fresh2 = None
            drain_end = time.monotonic() + 3
            while time.monotonic() < drain_end:
                try:
                    raw = await asyncio.wait_for(overlay.recv(), timeout=1.2)
                except asyncio.TimeoutError:
                    break
                d = json.loads(raw)
                if d.get("type") == "telemetry":
                    fresh2 = d
            assert fresh2 is not None
            assert fresh2["running"] is False
            assert fresh2["speed"] == 0
            # run_seconds should NOT continue growing (allow +1 tolerance rounding)
            assert fresh2["run_seconds"] <= prev_run + 1, (
                f"run_seconds grew while speed=0: prev={prev_run}, now={fresh2['run_seconds']}")

            # ---- Reset on turn end: disconnect guest ----
            await guest.close()
            await asyncio.sleep(1.5)
            # drain frames
            reset_frame = None
            drain_end = time.monotonic() + 3
            while time.monotonic() < drain_end:
                try:
                    raw = await asyncio.wait_for(overlay.recv(), timeout=1.2)
                except asyncio.TimeoutError:
                    break
                d = json.loads(raw)
                if d.get("type") == "telemetry":
                    reset_frame = d
            assert reset_frame is not None
            assert reset_frame["speed"] == 0
            assert reset_frame["depth"] == 0
            assert reset_frame["stroke"] == 0
            assert reset_frame["sensation"] == 0
            assert reset_frame["controller"] is None
            assert reset_frame["run_seconds"] == 0

            await overlay.close()

        try:
            asyncio.run(run())
        finally:
            _set_limits(TOKEN, 0, 100)
            _delete_code(TOKEN, cid_db)
