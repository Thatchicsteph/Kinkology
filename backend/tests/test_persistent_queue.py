"""Iteration 22: persistent active-turn clock (used_seconds flushed to Mongo every 5s).

A backend restart mid-turn must not hand the guest back all their spent time.
"""
import os
import asyncio
import json
import subprocess
import time

import pytest
import requests
import websockets
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
_base = os.environ.get("REACT_APP_BACKEND_URL") or _env.get("REACT_APP_BACKEND_URL")
if not _base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing from env and /app/frontend/.env")
BASE_URL = _base.rstrip("/")
WS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def auth():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    tok = r.json().get("token")
    assert tok
    return {"Authorization": f"Bearer {tok}"}


async def _first_state(code, hold=0.0, timeout=12.0):
    """Connect, read messages until a `state` frame arrives, optionally hold open."""
    async with websockets.connect(f"{WS_BASE}/api/ws/control/{code}") as ws:
        state = None
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except asyncio.TimeoutError:
                continue
            if m.get("type") == "state" and m.get("you", {}).get("status") == "active":
                state = m
                break
        if hold and state is not None:
            end = asyncio.get_event_loop().time() + hold
            while asyncio.get_event_loop().time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.5))
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    continue
                if m.get("type") == "state" and m.get("you", {}).get("status") == "active":
                    state = m
        return state


def _wait_backend_up(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(f"{BASE_URL}/api/setup/status", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


class TestPersistentQueue:
    @pytest.fixture(scope="class")
    def code(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        hdr = {"Authorization": f"Bearer {r.json()['token']}"}
        c = requests.post(f"{BASE_URL}/api/codes", headers=hdr,
                          json={"minutes": 60, "label": "TEST_QUEUE", "view_only": False}, timeout=20)
        assert c.status_code == 200, f"{c.status_code} {c.text[:300]}"
        d = c.json()
        yield d
        # cleanup with a fresh token (backend restarted in between)
        r2 = requests.post(f"{BASE_URL}/api/auth/login",
                           json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
        if r2.status_code == 200:
            h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
            requests.post(f"{BASE_URL}/api/codes/{d['id']}/revoke", headers=h2, timeout=20)
            requests.delete(f"{BASE_URL}/api/codes/{d['id']}", headers=h2, timeout=20)

    def test_active_time_survives_backend_restart(self, code):
        c = code["code"]
        assert code["granted_seconds"] == 3600, code

        # 1st session: become active, hold ~12s so >=2 flushes happen
        s1 = asyncio.run(_first_state(c, hold=12.0))
        assert s1 is not None, "never became active on first connect"
        r1_start = 3600
        r1 = int(s1["you"]["remaining_seconds"])
        assert 3560 <= r1 <= 3600, f"unexpected initial remaining {r1}"

        # restart backend
        subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True,
                       capture_output=True, timeout=90)
        assert _wait_backend_up(), "backend did not come back up after restart"
        time.sleep(2)

        s2 = asyncio.run(_first_state(c))
        assert s2 is not None, "never became active after restart"
        r2 = int(s2["you"]["remaining_seconds"])

        drop = r1_start - r2
        assert r2 != 3600, "remaining_seconds reset to full grant after restart (no flush persisted)"
        assert 5 <= drop <= 40, f"flushed drop out of range: r1={r1} r2={r2} drop={drop}"

    def test_used_seconds_persisted_in_mongo(self, auth, code):
        lst = requests.get(f"{BASE_URL}/api/codes", headers=auth, timeout=20)
        assert lst.status_code == 200, lst.text[:300]
        match = [x for x in lst.json() if x["code"] == code["code"]]
        assert match, "test code missing from GET /api/codes"
        used = int(match[0]["used_seconds"])
        assert used >= 5, f"used_seconds not flushed to Mongo: {used}"
        assert "_id" not in match[0]
