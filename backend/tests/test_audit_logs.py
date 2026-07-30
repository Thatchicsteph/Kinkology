"""Audit & Activity Log backend tests.

Coverage:
- log_event helper writes on admin actions (login_success, code CRUD)
- login_failed + account_locked on brute force
- settings/urls/session events (limits_updated, urls_updated, emergency_stop, session_skipped)
- WS host & guest lifecycle events (device_connected/disconnected, guest_joined, guest_active, turn_ended)
- GET /api/logs filtering (category, q, limit/skip, total)
- GET /api/logs/export csv & json
- DELETE /api/logs clears entries
"""
import os
import asyncio
import json
import time
from pathlib import Path

import pytest
import requests
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Read BASE_URL from frontend env (external ingress URL) — same as backend_test.py
FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break

WS_BASE = "ws://localhost:8001"
ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.text}"
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


@pytest.fixture(scope="module")
def token(api):
    # extract Bearer from headers
    return api.headers["Authorization"].split(" ", 1)[1]


def _fetch_logs(api, **params):
    r = api.get(f"{BASE_URL}/api/logs", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _has_action(items, action, **match):
    for it in items:
        if it.get("action") != action:
            continue
        ok = True
        for k, v in match.items():
            if it.get(k) != v:
                ok = False
                break
        if ok:
            return it
    return None


# ---------------- Admin action events ----------------
class TestAdminActionsAuditLog:
    def test_login_and_code_lifecycle_writes_audit(self, api):
        # Login already happened in fixture — that wrote login_success.
        # Create -> extend -> revoke -> delete a code, then check /api/logs.
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_AUDIT", "minutes": 5})
        assert r.status_code == 200
        c = r.json()
        cid, code = c["id"], c["code"]

        r = api.post(f"{BASE_URL}/api/codes/{cid}/add-minutes",
                     json={"label": "", "minutes": 2})
        assert r.status_code == 200

        r = api.post(f"{BASE_URL}/api/codes/{cid}/revoke")
        assert r.status_code == 200

        r = api.delete(f"{BASE_URL}/api/codes/{cid}")
        assert r.status_code == 200

        # Give backend a moment to flush
        time.sleep(0.4)
        data = _fetch_logs(api, category="security", limit=200)
        items = data["items"]

        assert _has_action(items, "login_success", actor=ADMIN_EMAIL), \
            "login_success not recorded"
        assert _has_action(items, "code_created", actor=ADMIN_EMAIL, target=code), \
            "code_created missing/target mismatch"
        assert _has_action(items, "code_extended", actor=ADMIN_EMAIL, target=code), \
            "code_extended missing"
        assert _has_action(items, "code_revoked", actor=ADMIN_EMAIL, target=code), \
            "code_revoked missing"
        assert _has_action(items, "code_deleted", actor=ADMIN_EMAIL, target=code), \
            "code_deleted missing"

        # All returned items must be category=security
        assert all(it["category"] == "security" for it in items)

    def test_settings_urls_session_events(self, api):
        # limits_updated
        r = api.put(f"{BASE_URL}/api/settings",
                    json={"min_depth": 10, "max_speed": 90})
        assert r.status_code == 200
        # urls_updated
        r = api.put(f"{BASE_URL}/api/settings/urls",
                    json={"local_url": "http://localhost",
                          "public_url": "https://tg30.ddns.net"})
        assert r.status_code == 200
        # emergency_stop
        r = api.post(f"{BASE_URL}/api/session/stop")
        assert r.status_code == 200
        # session_skipped
        r = api.post(f"{BASE_URL}/api/session/skip")
        assert r.status_code == 200

        time.sleep(0.4)
        items = _fetch_logs(api, category="security", limit=200)["items"]
        for act in ("limits_updated", "urls_updated",
                    "emergency_stop", "session_skipped"):
            assert _has_action(items, act, actor=ADMIN_EMAIL), f"{act} missing"

        # reset defaults
        api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 100})


# ---------------- Brute-force login events ----------------
class TestLoginFailedAndLockout:
    def test_failed_and_lockout_events(self, api):
        # Use a throwaway email so we don't lock the real admin
        throwaway = f"test_lock_{int(time.time())}@example.com"
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        # 5 failed attempts
        for i in range(5):
            r = s.post(f"{BASE_URL}/api/auth/login",
                       json={"email": throwaway, "password": "bad"})
            assert r.status_code == 401, f"attempt {i+1}: {r.status_code}"
        # 6th should be 429 (locked)
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": throwaway, "password": "bad"})
        assert r.status_code == 429, f"expected lockout 429, got {r.status_code}"

        time.sleep(0.4)
        items = _fetch_logs(api, category="security",
                            q=throwaway, limit=200)["items"]
        # login_failed should appear (>=1) for this throwaway
        failed = [it for it in items
                  if it["action"] == "login_failed" and it["actor"] == throwaway]
        assert len(failed) >= 1, "no login_failed events for throwaway"
        locked = [it for it in items
                  if it["action"] == "account_locked" and throwaway in (it.get("actor") or "")]
        assert len(locked) >= 1, "account_locked not recorded"


# ---------------- WebSocket session events ----------------
@pytest.mark.asyncio
async def test_ws_session_events(api, token):
    # Ensure defaults so guest auto-activates
    api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 100})
    r = api.post(f"{BASE_URL}/api/codes",
                 json={"label": "TEST_WS_AUDIT", "minutes": 5})
    assert r.status_code == 200
    c = r.json()
    code = c["code"]

    host_uri = f"{WS_BASE}/api/ws/host?token={token}"
    ctrl_uri = f"{WS_BASE}/api/ws/control/{code}"
    try:
        async with websockets.connect(host_uri) as host_ws:
            # small wait for device_connected persistence
            await asyncio.sleep(0.4)
            async with websockets.connect(ctrl_uri) as ctrl_ws:
                # wait until active
                end = time.monotonic() + 5
                active_seen = False
                while time.monotonic() < end:
                    try:
                        raw = await asyncio.wait_for(ctrl_ws.recv(), timeout=end - time.monotonic())
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "state" and (msg.get("you") or {}).get("status") == "active":
                        active_seen = True
                        break
                assert active_seen, "guest never became active"
            # ctrl_ws closed -> turn_ended
            await asyncio.sleep(0.5)
        # host_ws closed -> device_disconnected
        await asyncio.sleep(0.5)

        items = _fetch_logs(api, category="session", limit=200)["items"]
        actions = {it["action"] for it in items}
        for act in ("device_connected", "guest_joined",
                    "guest_active", "turn_ended", "device_disconnected"):
            assert act in actions, f"session action missing: {act}; got {actions}"
        # verify target=code appears on guest_joined
        gj = _has_action(items, "guest_joined", target=code)
        assert gj is not None, "guest_joined target != code"
        # Category enforced
        assert all(it["category"] == "session" for it in items)
    finally:
        api.delete(f"{BASE_URL}/api/codes/{c['id']}")


# ---------------- Filtering / pagination ----------------
class TestLogFiltering:
    def test_category_filter(self, api):
        sec = _fetch_logs(api, category="security", limit=50)
        sess = _fetch_logs(api, category="session", limit=50)
        assert all(it["category"] == "security" for it in sec["items"])
        assert all(it["category"] == "session" for it in sess["items"])

    def test_q_filter_matches_target(self, api):
        # create a code and search for it
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_Q", "minutes": 5})
        c = r.json()
        try:
            time.sleep(0.3)
            data = _fetch_logs(api, q=c["code"], limit=50)
            assert data["total"] >= 1
            assert any(it.get("target") == c["code"] for it in data["items"])
        finally:
            api.delete(f"{BASE_URL}/api/codes/{c['id']}")

    def test_pagination_total_and_skip(self, api):
        d1 = _fetch_logs(api, limit=1, skip=0)
        d2 = _fetch_logs(api, limit=1, skip=1)
        assert d1["limit"] == 1 and d1["skip"] == 0
        assert d2["limit"] == 1 and d2["skip"] == 1
        assert d1["total"] == d2["total"]  # same filtered universe
        if d1["total"] >= 2:
            assert d1["items"][0]["id"] != d2["items"][0]["id"]


# ---------------- Export ----------------
class TestExport:
    def test_csv_export(self, api):
        r = api.get(f"{BASE_URL}/api/logs/export", params={"format": "csv"})
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "").lower()
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd.lower()
        first_line = r.text.splitlines()[0]
        assert first_line == "timestamp,category,action,actor,target,detail,ip"

    def test_json_export(self, api):
        r = api.get(f"{BASE_URL}/api/logs/export", params={"format": "json"})
        assert r.status_code == 200
        assert "application/json" in r.headers.get("content-type", "").lower()
        data = r.json()
        assert isinstance(data, list)
        if data:
            for k in ("ts", "category", "action", "actor"):
                assert k in data[0]


# ---------------- Clear all ----------------
class TestClearLogs:
    def test_delete_clears_and_writes_logs_cleared(self, api):
        # Count before
        before = _fetch_logs(api, limit=1)["total"]
        r = api.delete(f"{BASE_URL}/api/logs")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("deleted") == before

        time.sleep(0.3)
        after = _fetch_logs(api, limit=10)
        # after clear we expect small total (just logs_cleared + maybe subsequent logins in fixture)
        assert after["total"] <= 5, f"expected small total after clear, got {after['total']}"
        actions = {it["action"] for it in after["items"]}
        assert "logs_cleared" in actions
