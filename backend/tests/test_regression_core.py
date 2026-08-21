"""Regression checks: setup status, admin login, overlay state, access code validation."""
import os

import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or frontend_env["REACT_APP_BACKEND_URL"]).rstrip("/")
API = f"{BASE_URL}/api"
ADMIN = {"email": "admin@ossm.local", "password": "ossm-admin-2026"}


def test_setup_status():
    r = requests.get(f"{API}/setup/status", timeout=20)
    assert r.status_code == 200, r.text
    assert r.json()["needs_setup"] is False, r.json()


def test_admin_login_returns_jwt():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=20)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    assert token and isinstance(token, str), data
    assert token.count(".") == 2, "not a JWT"


def test_overlay_state():
    r = requests.get(f"{API}/overlay/state", timeout=20)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)


def test_invalid_access_code():
    r = requests.get(f"{API}/access/NOPE1234", timeout=20)
    assert r.status_code == 200, r.text
    assert r.json()["valid"] is False
