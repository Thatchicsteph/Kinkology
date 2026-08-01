"""Iteration 12 security hardening tests.

Coverage:
- Malformed ObjectId on /api/codes/{id} revoke, add-minutes, delete -> 404 (not 500)
- Public /api/access/{code} brute-force lockout (5 fails -> 6th 429), valid clears
- Regression: /api/setup returns 403 cleanly when admin exists (no 500 crash)
- Regression: /api/auth/2fa/setup/start reachable with Bearer + returns provisioning
- Regression: login sets cookie with SameSite=lax
- get_jwt_secret failure mode is exercised indirectly (login must not 500)

NOTE: The public /api/access lockout is IP-keyed. External ingress will collapse
all callers to a single X-Forwarded-For, so we clean the login_attempts collection
before and after to avoid leaking state to other test files or the running app.
"""
import os
import time
from pathlib import Path

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


def _mongo():
    return MongoClient(os.environ["MONGO_URL"])


def _clear_access_attempts():
    """Clear only :access identifiers to avoid breaking login lockout tests."""
    mc = _mongo()
    try:
        mc[os.environ["DB_NAME"]].login_attempts.delete_many(
            {"identifier": {"$regex": ":access$"}})
    finally:
        mc.close()


def _clear_all_attempts():
    mc = _mongo()
    try:
        mc[os.environ["DB_NAME"]].login_attempts.delete_many({})
    finally:
        mc.close()


def _reset_2fa():
    mc = _mongo()
    try:
        mc[os.environ["DB_NAME"]].users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"twofa_enabled": False},
             "$unset": {"totp_secret": "", "recovery_codes_hash": "",
                        "twofa_pending_secret": ""}},
        )
    finally:
        mc.close()


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    _clear_all_attempts()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ---------------- Regression: setup returns 403 cleanly ----------------
class TestSetupRegression:
    def test_setup_status_no_setup_needed(self):
        r = requests.get(f"{BASE_URL}/api/setup/status")
        assert r.status_code == 200
        assert r.json() == {"needs_setup": False}

    def test_setup_post_returns_403_not_500(self):
        r = requests.post(f"{BASE_URL}/api/setup",
                          json={"email": "new@x.com", "password": "password1234"})
        # The critical assertion: NOT 500 (was crashing on NameError request)
        assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


# ---------------- Regression: login + cookie samesite=lax ----------------
class TestLoginCookie:
    def test_login_sets_access_token_cookie(self):
        # Query the backend directly (localhost) so the ingress/CDN doesn't
        # rewrite the Set-Cookie attributes (Cloudflare adds Partitioned+SameSite=none
        # on cross-site preview responses). The backend itself must emit SameSite=lax.
        r = requests.post("http://localhost:8001/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        cookie_header = r.headers.get("set-cookie", "").lower()
        assert "access_token=" in cookie_header, f"no access_token cookie: {cookie_header}"
        assert "samesite=lax" in cookie_header, \
            f"expected SameSite=lax from backend, got: {cookie_header}"
        assert "httponly" in cookie_header
        assert "secure" in cookie_header


# ---------------- Malformed ObjectId returns 404 ----------------
class TestMalformedObjectId:
    BAD_IDS = ["NOT_AN_OID", "zzz", "123", "!!!", ""]

    def test_revoke_malformed_returns_404(self, api):
        for bad in self.BAD_IDS:
            if not bad:  # empty would hit different route
                continue
            r = api.post(f"{BASE_URL}/api/codes/{bad}/revoke")
            assert r.status_code == 404, \
                f"revoke {bad} -> {r.status_code}: {r.text}"

    def test_add_minutes_malformed_returns_404(self, api):
        for bad in self.BAD_IDS:
            if not bad:
                continue
            r = api.post(f"{BASE_URL}/api/codes/{bad}/add-minutes",
                         json={"label": "", "minutes": 5})
            assert r.status_code == 404, \
                f"add-minutes {bad} -> {r.status_code}: {r.text}"

    def test_delete_malformed_returns_404(self, api):
        for bad in self.BAD_IDS:
            if not bad:
                continue
            r = api.delete(f"{BASE_URL}/api/codes/{bad}")
            assert r.status_code == 404, \
                f"delete {bad} -> {r.status_code}: {r.text}"


# ---------------- Access code brute-force lockout ----------------
class TestAccessLockout:
    """IP-keyed brute-force lockout on GET /api/access/{code}.
    5 invalid -> 6th returns 429; a valid code clears attempts (only if it
    fires BEFORE the 5th failure — check_lockout is invoked first on the 6th)."""

    def setup_method(self):
        _clear_access_attempts()

    def teardown_method(self):
        _clear_access_attempts()

    def test_1_valid_code_returns_valid(self, api):
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_LOCK_VALID", "minutes": 5})
        assert r.status_code == 200
        c = r.json()
        try:
            v = requests.get(f"{BASE_URL}/api/access/{c['code']}")
            assert v.status_code == 200
            assert v.json()["valid"] is True
        finally:
            api.delete(f"{BASE_URL}/api/codes/{c['id']}")

    def test_2_valid_code_clears_prior_failures(self, api):
        """4 invalid (below threshold), then valid clears counter,
        then 4 more invalid still allowed (no immediate 429)."""
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_LOCK_CLR", "minutes": 5})
        c = r.json()
        try:
            for i in range(4):
                bad = requests.get(f"{BASE_URL}/api/access/ZZZZZ{i}")
                assert bad.status_code == 200, f"invalid attempt {i}: {bad.status_code}"
                assert bad.json()["valid"] is False
            # Valid -> clears
            ok = requests.get(f"{BASE_URL}/api/access/{c['code']}")
            assert ok.status_code == 200
            assert ok.json()["valid"] is True
            # DB check: :access identifier removed
            mc = _mongo()
            try:
                doc = mc[os.environ["DB_NAME"]].login_attempts.find_one(
                    {"identifier": {"$regex": ":access$"}})
                assert doc is None, f"attempts not cleared: {doc}"
            finally:
                mc.close()
            # Now 4 more invalids allowed without 429
            for i in range(4):
                bad = requests.get(f"{BASE_URL}/api/access/YYYYY{i}")
                assert bad.status_code == 200
                assert bad.json()["valid"] is False
        finally:
            api.delete(f"{BASE_URL}/api/codes/{c['id']}")

    def test_3_five_invalid_then_429_on_sixth(self, api):
        _clear_access_attempts()
        for i in range(5):
            r = requests.get(f"{BASE_URL}/api/access/BAD{i:03d}")
            assert r.status_code == 200, f"attempt {i+1}: {r.status_code} {r.text}"
            assert r.json()["valid"] is False
        r6 = requests.get(f"{BASE_URL}/api/access/BADBAD")
        assert r6.status_code == 429, r6.text
        assert "Retry-After" in r6.headers
        retry = int(r6.headers["Retry-After"])
        assert 0 < retry <= 15 * 60
        # Even a valid code is blocked while locked
        rc = api.post(f"{BASE_URL}/api/codes",
                      json={"label": "TEST_LOCK_BLOCKED", "minutes": 5})
        c = rc.json()
        try:
            r_valid_blocked = requests.get(f"{BASE_URL}/api/access/{c['code']}")
            assert r_valid_blocked.status_code == 429, \
                f"valid code should be blocked during lockout: {r_valid_blocked.status_code}"
        finally:
            api.delete(f"{BASE_URL}/api/codes/{c['id']}")


# ---------------- Regression: access code CRUD ----------------
class TestCodeCrudRegression:
    def test_full_crud_lifecycle(self, api):
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_REG_CRUD", "minutes": 3})
        assert r.status_code == 200
        c = r.json()
        cid, code = c["id"], c["code"]
        assert c["granted_seconds"] == 180
        # list
        lst = api.get(f"{BASE_URL}/api/codes").json()
        assert any(x["code"] == code for x in lst)
        # add-minutes
        r2 = api.post(f"{BASE_URL}/api/codes/{cid}/add-minutes",
                      json={"label": "", "minutes": 2})
        assert r2.status_code == 200
        assert r2.json()["granted_seconds"] == 180 + 120
        # revoke
        r3 = api.post(f"{BASE_URL}/api/codes/{cid}/revoke")
        assert r3.status_code == 200
        v = requests.get(f"{BASE_URL}/api/access/{code}")
        assert v.json()["valid"] is False
        # delete
        r4 = api.delete(f"{BASE_URL}/api/codes/{cid}")
        assert r4.status_code == 200


# ---------------- Regression: settings + urls with bearer ----------------
class TestSettingsRegression:
    def test_get_put_settings(self, api):
        r = api.get(f"{BASE_URL}/api/settings")
        assert r.status_code == 200
        d = r.json()
        for k in ("min_depth", "max_speed", "local_url", "public_url"):
            assert k in d
        # hr_cutoff optional per PRD; check presence tolerantly
        # PUT settings
        r2 = api.put(f"{BASE_URL}/api/settings",
                     json={"min_depth": 5, "max_speed": 80})
        assert r2.status_code == 200
        assert r2.json()["min_depth"] == 5
        # PUT urls
        r3 = api.put(f"{BASE_URL}/api/settings/urls",
                     json={"local_url": "http://localhost",
                           "public_url": "https://tg30.ddns.net"})
        assert r3.status_code == 200
        # reset
        api.put(f"{BASE_URL}/api/settings", json={"min_depth": 0, "max_speed": 100})


# ---------------- Regression: 2FA enroll endpoint reachable ----------------
class TestTwoFAEnrollReachable:
    """Do NOT complete enrollment — just verify the endpoint is reachable and
    returns a provisioning secret/QR under the new SameSite=lax cookie."""

    def teardown_method(self):
        _reset_2fa()
        _clear_all_attempts()

    def test_setup_start_returns_provisioning(self, api):
        r = api.post(f"{BASE_URL}/api/auth/2fa/setup/start")
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d.get("secret"), str) and len(d["secret"]) >= 16
        assert d.get("otpauth_uri", "").startswith("otpauth://totp/")
        assert d.get("qr_code_data_url", "").startswith("data:image/png;base64,")
        # DO NOT call /verify - leave 2FA disabled

    def test_2fa_status_disabled(self, api):
        r = api.get(f"{BASE_URL}/api/auth/2fa/status")
        assert r.status_code == 200
        assert r.json() == {"enabled": False}


# ---------------- Regression: audit logs ----------------
class TestAuditLogsRegression:
    def test_logs_return_and_category_filter(self, api):
        # trigger some events
        r = api.post(f"{BASE_URL}/api/codes",
                     json={"label": "TEST_AUDIT_REG", "minutes": 1})
        c = r.json()
        api.delete(f"{BASE_URL}/api/codes/{c['id']}")
        time.sleep(0.3)
        r = api.get(f"{BASE_URL}/api/logs", params={"limit": 50})
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "total" in d
        assert d["total"] >= 1
        # category filter
        r2 = api.get(f"{BASE_URL}/api/logs",
                     params={"category": "security", "limit": 50})
        assert r2.status_code == 200
        items = r2.json()["items"]
        assert all(it["category"] == "security" for it in items)
        # login_success must be present in security category (from module fixture)
        actions = {it["action"] for it in items}
        assert "login_success" in actions
