"""Iteration 21: spectator-link reuse must be filtered by label='Spectator'."""
import os

import pytest
import requests
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
_base = os.environ.get("REACT_APP_BACKEND_URL") or _env.get("REACT_APP_BACKEND_URL")
if not _base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = _base.rstrip("/")

ADMIN_EMAIL = "admin@ossm.local"
ADMIN_PASSWORD = "ossm-admin-2026"


@pytest.fixture(scope="module")
def auth():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed {r.status_code} {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def created_ids(auth):
    ids = []
    yield ids
    for cid in ids:
        requests.delete(f"{BASE_URL}/api/codes/{cid}", headers=auth, timeout=20)


class TestSpectatorLabelScoping:
    def test_named_view_only_code_not_returned_by_spectator_link(self, auth, created_ids):
        # (a) owner mints a named view-only code
        r = requests.post(f"{BASE_URL}/api/codes", headers=auth,
                          json={"minutes": 0, "label": "TEST_Alice", "view_only": True}, timeout=20)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        alice = r.json()
        created_ids.append(alice["id"])
        assert alice["view_only"] is True
        assert alice["label"] == "TEST_Alice"

        # (b) spectator-link must NOT hand back Alice's code
        s = requests.post(f"{BASE_URL}/api/codes/spectator-link", headers=auth, timeout=20)
        assert s.status_code == 200, f"{s.status_code} {s.text[:200]}"
        spec = s.json()
        assert "_id" not in spec
        assert spec["code"] != alice["code"], "spectator-link returned the owner's named code"
        assert spec["label"] == "Spectator", spec
        assert spec["view_only"] is True
        assert spec["granted_seconds"] == 0
        assert spec["revoked"] is False

        # (c) idempotent: same Spectator code on repeat call
        s2 = requests.post(f"{BASE_URL}/api/codes/spectator-link", headers=auth, timeout=20)
        assert s2.status_code == 200
        assert s2.json()["code"] == spec["code"], "spectator-link not idempotent"

        # persisted and visible in the codes list with the right label
        lst = requests.get(f"{BASE_URL}/api/codes", headers=auth, timeout=20)
        assert lst.status_code == 200
        by_code = {c["code"]: c for c in lst.json()}
        assert by_code[spec["code"]]["label"] == "Spectator"
        assert by_code[spec["code"]]["view_only"] is True
        assert by_code[alice["code"]]["label"] == "TEST_Alice"

    def test_spectator_link_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/codes/spectator-link", timeout=15)
        assert r.status_code in (401, 403), r.status_code
