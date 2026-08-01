"""Backend tests for brute-force lockout / rate limiting on auth endpoints.

Covers:
- /api/auth/login lockout after 5 fails, 6th returns 429 with Retry-After
- Correct password blocked while locked
- Clear-on-success: correct login resets counter
- /api/auth/2fa/login lockout with wrong TOTP; correct TOTP resets counter
- /api/auth/2fa/setup/verify lockout with wrong codes
- /api/auth/2fa/disable lockout with wrong codes

CRITICAL: Because rate-limiter keys on client IP + email, all tests using the
real admin identifier will pollute state. Every test clears `login_attempts`
before and after, and 2FA is left DISABLED at module teardown.
"""
import os
import time
import pytest
import requests
import pyotp
from pathlib import Path
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


def _clear_attempts():
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


@pytest.fixture(autouse=True)
def _per_test_clear():
    _clear_attempts()
    yield
    _clear_attempts()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup():
    _clear_attempts()
    _reset_2fa()
    yield
    _clear_attempts()
    _reset_2fa()


def _login(email, password):
    return requests.post(f"{BASE_URL}/api/auth/login",
                         json={"email": email, "password": password})


# ---------- Scenario 1: login lockout ----------
class TestLoginLockout:
    def test_1_five_wrong_then_429_on_sixth(self):
        for i in range(5):
            r = _login(ADMIN_EMAIL, "wrong-pw")
            assert r.status_code == 401, f"attempt {i+1}: {r.status_code} {r.text}"

        r6 = _login(ADMIN_EMAIL, "wrong-pw")
        assert r6.status_code == 429, r6.text
        detail = r6.json().get("detail", "")
        assert "Too many failed attempts" in detail, f"detail={detail}"
        assert "Retry-After" in r6.headers
        retry = int(r6.headers["Retry-After"])
        assert 0 < retry <= 15 * 60

    def test_2_correct_password_blocked_while_locked(self):
        # Prime lockout
        for _ in range(5):
            _login(ADMIN_EMAIL, "wrong-pw")
        r6 = _login(ADMIN_EMAIL, "wrong-pw")
        assert r6.status_code == 429

        # Even correct password must be blocked
        r_ok = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert r_ok.status_code == 429, r_ok.text
        assert "Retry-After" in r_ok.headers


# ---------- Scenario 2: clear-on-success ----------
class TestClearOnSuccess:
    def test_3_success_resets_counter(self):
        # 3 wrong (below threshold)
        for _ in range(3):
            r = _login(ADMIN_EMAIL, "wrong-pw")
            assert r.status_code == 401

        # correct -> 200 and delete attempts doc
        ok = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert ok.status_code == 200, ok.text

        mc = _mongo()
        try:
            doc = mc[os.environ["DB_NAME"]].login_attempts.find_one(
                {"identifier": {"$regex": f":login:{ADMIN_EMAIL}$"}})
            assert doc is None, f"attempts doc not cleared: {doc}"
        finally:
            mc.close()

        # Subsequent wrong -> 401 (fresh counter), not immediate 429
        r_next = _login(ADMIN_EMAIL, "wrong-pw")
        assert r_next.status_code == 401, r_next.text


# ---------- Scenario 3: 2FA login lockout ----------
class Test2FALoginLockout:
    @pytest.fixture(autouse=True)
    def enable_2fa(self):
        _reset_2fa()
        _clear_attempts()
        # login -> bearer
        r = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert r.status_code == 200
        bearer = r.json()["token"]
        hdr = {"Authorization": f"Bearer {bearer}",
               "Content-Type": "application/json"}
        # setup start
        s = requests.post(f"{BASE_URL}/api/auth/2fa/setup/start", headers=hdr)
        assert s.status_code == 200
        secret = s.json()["secret"]
        # verify to enable
        code = pyotp.TOTP(secret).now()
        v = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                          json={"code": code}, headers=hdr)
        assert v.status_code == 200
        _clear_attempts()
        self.secret = secret
        self.hdr = hdr
        yield
        # disable 2FA
        _clear_attempts()
        try:
            # re-login through 2FA to get bearer
            r = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
            mfa_token = r.json()["mfa_token"]
            c = pyotp.TOTP(secret).now()
            r2 = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                               json={"mfa_token": mfa_token, "code": c})
            b2 = r2.json()["token"]
            h2 = {"Authorization": f"Bearer {b2}",
                  "Content-Type": "application/json"}
            c2 = pyotp.TOTP(secret).now()
            requests.post(f"{BASE_URL}/api/auth/2fa/disable",
                          json={"code": c2}, headers=h2)
        except Exception:
            pass
        _reset_2fa()
        _clear_attempts()

    def _get_mfa_token(self):
        r = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert r.status_code == 200
        data = r.json()
        assert data.get("mfa_required") is True
        return data["mfa_token"]

    def test_4_2fa_login_wrong_five_then_429(self):
        mfa = self._get_mfa_token()
        for i in range(5):
            r = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                              json={"mfa_token": mfa, "code": "000000"})
            assert r.status_code == 401, f"attempt {i+1}: {r.status_code} {r.text}"
        r6 = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                           json={"mfa_token": mfa, "code": "000000"})
        assert r6.status_code == 429, r6.text
        assert "Retry-After" in r6.headers

    def test_5_2fa_correct_totp_resets_counter(self):
        mfa = self._get_mfa_token()
        # 3 wrong
        for _ in range(3):
            r = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                              json={"mfa_token": mfa, "code": "000000"})
            assert r.status_code == 401
        # correct
        code = pyotp.TOTP(self.secret).now()
        r_ok = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                             json={"mfa_token": mfa, "code": code})
        assert r_ok.status_code == 200, r_ok.text

        # Counter cleared: 2fa identifier no longer present
        mc = _mongo()
        try:
            doc = mc[os.environ["DB_NAME"]].login_attempts.find_one(
                {"identifier": {"$regex": f":2fa:{ADMIN_EMAIL}$"}})
            assert doc is None
        finally:
            mc.close()


# ---------- Scenario 4: 2FA setup/verify + disable lockout ----------
class Test2FASetupAndDisableLockout:
    def test_6_setup_verify_wrong_code_lockout(self):
        _reset_2fa()
        _clear_attempts()
        r = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        bearer = r.json()["token"]
        hdr = {"Authorization": f"Bearer {bearer}",
               "Content-Type": "application/json"}
        # start setup
        s = requests.post(f"{BASE_URL}/api/auth/2fa/setup/start", headers=hdr)
        assert s.status_code == 200
        # 5 wrong codes -> 400
        for i in range(5):
            rv = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                               json={"code": "000000"}, headers=hdr)
            assert rv.status_code == 400, f"attempt {i+1}: {rv.status_code}"
        # 6th -> 429
        r6 = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                           json={"code": "000000"}, headers=hdr)
        assert r6.status_code == 429, r6.text
        assert "Retry-After" in r6.headers

        _clear_attempts()
        _reset_2fa()

    def test_7_disable_wrong_code_lockout(self):
        _reset_2fa()
        _clear_attempts()
        # Enable 2FA fully first
        r = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        bearer = r.json()["token"]
        hdr = {"Authorization": f"Bearer {bearer}",
               "Content-Type": "application/json"}
        s = requests.post(f"{BASE_URL}/api/auth/2fa/setup/start", headers=hdr)
        secret = s.json()["secret"]
        v = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                          json={"code": pyotp.TOTP(secret).now()},
                          headers=hdr)
        assert v.status_code == 200

        # login via 2FA to get valid bearer post-enable
        rl = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        mfa = rl.json()["mfa_token"]
        rr = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                           json={"mfa_token": mfa,
                                 "code": pyotp.TOTP(secret).now()})
        assert rr.status_code == 200
        bearer2 = rr.json()["token"]
        hdr2 = {"Authorization": f"Bearer {bearer2}",
                "Content-Type": "application/json"}
        _clear_attempts()

        # 5 wrong disable codes -> 400
        for i in range(5):
            rd = requests.post(f"{BASE_URL}/api/auth/2fa/disable",
                               json={"code": "000000"}, headers=hdr2)
            assert rd.status_code == 400, f"attempt {i+1}: {rd.status_code} {rd.text}"
        r6 = requests.post(f"{BASE_URL}/api/auth/2fa/disable",
                           json={"code": "000000"}, headers=hdr2)
        assert r6.status_code == 429, r6.text
        assert "Retry-After" in r6.headers

        # Cleanup: clear lockout then disable with valid TOTP
        _clear_attempts()
        rd_ok = requests.post(f"{BASE_URL}/api/auth/2fa/disable",
                              json={"code": pyotp.TOTP(secret).now()},
                              headers=hdr2)
        assert rd_ok.status_code == 200
        _reset_2fa()
        _clear_attempts()
