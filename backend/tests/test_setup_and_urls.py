"""Tests for new first-run setup + Base URLs endpoints.

Preview DB already has a seeded admin, so:
- /api/setup/status -> needs_setup:false
- POST /api/setup -> 403
Also verifies /api/settings returns/persists local_url and public_url.
"""
import os
import requests
import pytest
from pathlib import Path

FRONTEND_ENV = Path(__file__).parent.parent.parent / "frontend" / ".env"
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or "http://localhost:8001"
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


def test_setup_status_needs_setup_false():
    r = requests.get(f"{BASE_URL}/api/setup/status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": False}


def test_setup_post_forbidden_when_admin_exists():
    r = requests.post(f"{BASE_URL}/api/setup",
                      json={"email": "new@x.com", "password": "password1234"})
    assert r.status_code == 403
    detail = (r.json().get("detail") or "").lower()
    assert "setup" in detail and "completed" in detail


def test_login_returns_token_and_cookie():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d.get("token"), str) and len(d["token"]) > 10
    assert d["user"]["email"] == ADMIN_EMAIL
    assert any(c.name == "access_token" for c in r.cookies)


def test_get_settings_has_url_fields(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/settings")
    assert r.status_code == 200
    d = r.json()
    for k in ("min_depth", "max_speed", "local_url", "public_url"):
        assert k in d, f"missing field {k}"
    assert isinstance(d["local_url"], str)
    assert isinstance(d["public_url"], str)


def test_put_settings_urls_persists(auth_session):
    # Set to test values first
    r = auth_session.put(f"{BASE_URL}/api/settings/urls",
                         json={"local_url": "http://test-local", "public_url": "https://test-public"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["local_url"] == "http://test-local"
    assert d["public_url"] == "https://test-public"

    # Reflected on GET
    r2 = auth_session.get(f"{BASE_URL}/api/settings")
    assert r2.json()["local_url"] == "http://test-local"
    assert r2.json()["public_url"] == "https://test-public"

    # Set to final expected values
    r3 = auth_session.put(f"{BASE_URL}/api/settings/urls",
                          json={"local_url": "http://localhost", "public_url": "https://tg30.ddns.net"})
    assert r3.status_code == 200
    assert r3.json()["local_url"] == "http://localhost"
    assert r3.json()["public_url"] == "https://tg30.ddns.net"

    r4 = auth_session.get(f"{BASE_URL}/api/settings")
    assert r4.json()["local_url"] == "http://localhost"
    assert r4.json()["public_url"] == "https://tg30.ddns.net"


def test_put_settings_still_returns_url_fields(auth_session):
    r = auth_session.put(f"{BASE_URL}/api/settings",
                         json={"min_depth": 0, "max_speed": 100})
    assert r.status_code == 200
    d = r.json()
    assert d["min_depth"] == 0 and d["max_speed"] == 100
    # URL fields present
    assert "local_url" in d and "public_url" in d
    # And still equal to what we saved
    assert d["local_url"] == "http://localhost"
    assert d["public_url"] == "https://tg30.ddns.net"


def test_settings_urls_requires_auth():
    r = requests.put(f"{BASE_URL}/api/settings/urls",
                     json={"local_url": "x", "public_url": "y"})
    assert r.status_code == 401


def test_overlay_state_endpoint():
    r = requests.get(f"{BASE_URL}/api/overlay/state")
    assert r.status_code == 200
    d = r.json()
    # Should have telemetry-like structure
    assert isinstance(d, dict)
