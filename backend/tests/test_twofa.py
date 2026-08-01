"""Backend tests for TOTP 2FA feature.

Serially covers: status baseline, setup start/verify (bad+good), step-up login,
recovery code usage + reuse rejection, disable. Always resets state at end.
"""
import os
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


def _reset_2fa_state():
    """Force-clear 2FA fields in Mongo so admin is usable."""
    mc = MongoClient(os.environ["MONGO_URL"])
    try:
        mc[os.environ["DB_NAME"]].users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"twofa_enabled": False},
             "$unset": {"totp_secret": "", "recovery_codes_hash": "",
                        "twofa_pending_secret": ""}},
        )
    finally:
        mc.close()


@pytest.fixture(scope="module", autouse=True)
def clean_state():
    _reset_2fa_state()
    yield
    _reset_2fa_state()


@pytest.fixture(scope="module")
def bearer():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("mfa_required") is not True
    return data["token"]


@pytest.fixture(scope="module")
def hdr(bearer):
    return {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}


class TestTwoFA:
    state = {}

    # --- baseline ---
    def test_1_status_initially_disabled(self, hdr):
        r = requests.get(f"{BASE_URL}/api/auth/2fa/status", headers=hdr)
        assert r.status_code == 200
        assert r.json() == {"enabled": False}

    # --- enroll ---
    def test_2_setup_start_returns_secret_and_qr(self, hdr):
        r = requests.post(f"{BASE_URL}/api/auth/2fa/setup/start", headers=hdr)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d["secret"], str) and len(d["secret"]) >= 16
        assert d["otpauth_uri"].startswith("otpauth://totp/")
        assert d["qr_code_data_url"].startswith("data:image/png;base64,")
        TestTwoFA.state["secret"] = d["secret"]

    def test_3_verify_wrong_code_400(self, hdr):
        r = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                          json={"code": "000000"}, headers=hdr)
        assert r.status_code == 400

    def test_4_verify_correct_code_enables(self, hdr):
        code = pyotp.TOTP(TestTwoFA.state["secret"]).now()
        r = requests.post(f"{BASE_URL}/api/auth/2fa/setup/verify",
                          json={"code": code}, headers=hdr)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert isinstance(d["recovery_codes"], list) and len(d["recovery_codes"]) == 10
        for c in d["recovery_codes"]:
            assert len(c) == 14 and c.count("-") == 2
        TestTwoFA.state["recovery_codes"] = d["recovery_codes"]

        s = requests.get(f"{BASE_URL}/api/auth/2fa/status", headers=hdr)
        assert s.json() == {"enabled": True}

    # --- step-up login ---
    def test_5_login_returns_mfa_required(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        d = r.json()
        assert d.get("mfa_required") is True
        assert isinstance(d.get("mfa_token"), str) and len(d["mfa_token"]) > 10
        assert "token" not in d
        # cookie must NOT be a full access_token
        assert "access_token" not in r.cookies
        TestTwoFA.state["mfa_token"] = d["mfa_token"]

    def test_6_2fa_login_wrong_code_401(self):
        r = requests.post(f"{BASE_URL}/api/auth/2fa/login", json={
            "mfa_token": TestTwoFA.state["mfa_token"], "code": "000000"})
        assert r.status_code == 401

    def test_7_2fa_login_correct_totp(self):
        code = pyotp.TOTP(TestTwoFA.state["secret"]).now()
        r = requests.post(f"{BASE_URL}/api/auth/2fa/login", json={
            "mfa_token": TestTwoFA.state["mfa_token"], "code": code})
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d.get("token"), str)
        assert d["user"]["email"] == ADMIN_EMAIL

    def test_8_recovery_code_works_and_is_single_use(self):
        # fresh mfa_token
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        mfa_token = r.json()["mfa_token"]
        rc = TestTwoFA.state["recovery_codes"][0]

        r1 = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                           json={"mfa_token": mfa_token, "recovery_code": rc})
        assert r1.status_code == 200, r1.text
        assert "token" in r1.json()

        # reuse -> 401
        r2 = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        mfa_token2 = r2.json()["mfa_token"]
        r3 = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                           json={"mfa_token": mfa_token2, "recovery_code": rc})
        assert r3.status_code == 401

    # --- disable ---
    def test_9_disable_with_totp(self, hdr):
        # Need a fresh bearer via 2FA login (module-level bearer was pre-enable)
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        mfa_token = r.json()["mfa_token"]
        code = pyotp.TOTP(TestTwoFA.state["secret"]).now()
        r2 = requests.post(f"{BASE_URL}/api/auth/2fa/login",
                           json={"mfa_token": mfa_token, "code": code})
        assert r2.status_code == 200
        bearer = r2.json()["token"]
        h = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}

        code2 = pyotp.TOTP(TestTwoFA.state["secret"]).now()
        d = requests.post(f"{BASE_URL}/api/auth/2fa/disable",
                          json={"code": code2}, headers=h)
        assert d.status_code == 200, d.text
        assert d.json() == {"ok": True}

        s = requests.get(f"{BASE_URL}/api/auth/2fa/status", headers=h)
        assert s.json() == {"enabled": False}

    def test_10_login_works_without_mfa_after_disable(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        d = r.json()
        assert d.get("mfa_required") is not True
        assert isinstance(d.get("token"), str)
