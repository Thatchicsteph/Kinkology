"""Iteration 20: POST /api/codes/spectator-link + guest/owner reaction relay over WS."""
import os
import asyncio
import json

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
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------------------------------------------------------------- spectator-link
class TestSpectatorLink:
    def test_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/codes/spectator-link", timeout=15)
        assert r.status_code in (401, 403), r.status_code

    def test_creates_or_reuses_view_only_code(self, auth):
        r = requests.post(f"{BASE_URL}/api/codes/spectator-link", headers=auth, timeout=20)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        d = r.json()
        assert "_id" not in d, "mongo _id leaked in response"
        assert isinstance(d["code"], str) and len(d["code"]) >= 4
        assert d["view_only"] is True
        assert d["revoked"] is False
        assert d["granted_seconds"] == 0
        assert d["remaining_seconds"] == 0
        assert isinstance(d["id"], str)

        # Idempotency: second call reuses the same (most recent, non-revoked) code
        r2 = requests.post(f"{BASE_URL}/api/codes/spectator-link", headers=auth, timeout=20)
        assert r2.status_code == 200
        assert r2.json()["code"] == d["code"], "spectator-link is not idempotent"

        # Appears in the issued codes list flagged view_only
        lst = requests.get(f"{BASE_URL}/api/codes", headers=auth, timeout=20)
        assert lst.status_code == 200
        match = [c for c in lst.json() if c["code"] == d["code"]]
        assert match, "spectator code missing from GET /api/codes"
        assert match[0]["view_only"] is True

    def test_view_only_ws_accepts_and_marks_spectator(self, auth):
        code = requests.post(f"{BASE_URL}/api/codes/spectator-link",
                             headers=auth, timeout=20).json()["code"]

        async def run():
            async with websockets.connect(f"{WS_BASE}/api/ws/control/{code}") as ws:
                msgs = []
                for _ in range(6):
                    try:
                        msgs.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))
                    except asyncio.TimeoutError:
                        break
                return msgs

        msgs = asyncio.run(run())
        types = [m.get("type") for m in msgs]
        assert "rejected" not in types, f"view-only code rejected: {msgs}"
        assert any(t in ("state", "chat_history", "presence") for t in types), types
        state = next((m for m in msgs if m.get("type") == "state"), None)
        if state is not None:
            assert state.get("you", {}).get("status") == "spectator", state


# ---------------------------------------------------------------- reactions relay
async def _owner_ws(token):
    return await websockets.connect(f"{WS_BASE}/api/ws/host?token={token}")


async def _drain(ws, seconds=1.0):
    out = []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3)))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            break
    return out


async def _collect_reactions(ws, seconds=2.0):
    out = []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.4))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            continue
        if m.get("type") == "reaction":
            out.append(m)
    return out


class TestReactions:
    @pytest.fixture(scope="class")
    def control_code(self, auth):
        r = requests.post(f"{BASE_URL}/api/codes", headers=auth,
                          json={"minutes": 10, "label": "TEST_React", "view_only": False}, timeout=20)
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        d = r.json()
        yield d["code"]
        requests.delete(f"{BASE_URL}/api/codes/{d['id']}", headers=auth, timeout=20)

    def test_guest_reaction_bounces_to_owner_and_guest(self, admin_token, control_code):
        async def run():
            owner = await _owner_ws(admin_token)
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{control_code}")
            try:
                await _drain(owner, 1.0)
                await _drain(guest, 1.0)
                await guest.send(json.dumps({"type": "reaction", "emoji": "🔥"}))
                o, g = await asyncio.gather(_collect_reactions(owner, 2.5),
                                            _collect_reactions(guest, 2.5))
                return o, g
            finally:
                await owner.close()
                await guest.close()

        o, g = asyncio.run(run())
        assert len(o) >= 1, "owner did not receive the guest reaction"
        assert len(g) >= 1, "guest did not receive its own reaction echo"
        assert o[0]["emoji"] == "🔥"
        assert isinstance(o[0]["id"], str) and len(o[0]["id"]) > 0
        assert o[0]["author"]
        assert g[0]["id"] == o[0]["id"], "reaction ids differ between owner and guest"

    def test_rate_limit_and_whitelist(self, admin_token, control_code):
        async def run():
            owner = await _owner_ws(admin_token)
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{control_code}")
            try:
                await _drain(owner, 1.0)
                await _drain(guest, 1.0)
                # burst of 3 within <400ms -> only 1 should pass
                for _ in range(3):
                    await guest.send(json.dumps({"type": "reaction", "emoji": "💦"}))
                burst = await _collect_reactions(owner, 1.5)
                # non-whitelisted emoji dropped
                await guest.send(json.dumps({"type": "reaction", "emoji": "🤖"}))
                bad = await _collect_reactions(owner, 1.2)
                # after the gap a new one goes through
                await asyncio.sleep(0.6)
                await guest.send(json.dumps({"type": "reaction", "emoji": "😩"}))
                after = await _collect_reactions(owner, 2.0)
                return burst, bad, after
            finally:
                await owner.close()
                await guest.close()

        burst, bad, after = asyncio.run(run())
        assert len(burst) == 1, f"rate limit not enforced, got {len(burst)} reactions"
        assert bad == [], f"non-whitelisted emoji relayed: {bad}"
        assert len(after) == 1 and after[0]["emoji"] == "😩", after

    def test_owner_reaction_reaches_guest(self, admin_token, control_code):
        async def run():
            owner = await _owner_ws(admin_token)
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{control_code}")
            try:
                await _drain(owner, 1.0)
                await _drain(guest, 1.0)
                await owner.send(json.dumps({"type": "reaction", "emoji": "👏"}))
                return await _collect_reactions(guest, 2.5)
            finally:
                await owner.close()
                await guest.close()

        got = asyncio.run(run())
        assert len(got) >= 1, "owner reaction never reached the guest"
        assert got[0]["emoji"] == "👏"
        assert got[0]["author"] == "Owner", got[0]

    def test_view_only_guest_can_react(self, admin_token, auth):
        code = requests.post(f"{BASE_URL}/api/codes/spectator-link",
                             headers=auth, timeout=20).json()["code"]

        async def run():
            owner = await _owner_ws(admin_token)
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{code}")
            try:
                await _drain(owner, 1.0)
                await _drain(guest, 1.0)
                await asyncio.sleep(0.6)
                await guest.send(json.dumps({"type": "reaction", "emoji": "💜"}))
                return await _collect_reactions(owner, 2.5)
            finally:
                await owner.close()
                await guest.close()

        got = asyncio.run(run())
        assert len(got) >= 1, "spectator (view-only) reaction did not reach owner"
        assert got[0]["emoji"] == "💜"


    def test_per_emoji_rate_limit(self, admin_token, control_code):
        """Switching emojis back-to-back should get through both — only same
        emoji within 400ms is throttled.
        """
        async def run():
            owner = await _owner_ws(admin_token)
            guest = await websockets.connect(f"{WS_BASE}/api/ws/control/{control_code}")
            try:
                await _drain(owner, 1.0)
                await _drain(guest, 1.0)
                # 🔥 then 💦 in the same tick — both should pass because
                # rate-limit key includes the emoji.
                await guest.send(json.dumps({"type": "reaction", "emoji": "🔥"}))
                await guest.send(json.dumps({"type": "reaction", "emoji": "💦"}))
                # …but a second 🔥 within 400ms should be dropped.
                await guest.send(json.dumps({"type": "reaction", "emoji": "🔥"}))
                return await _collect_reactions(owner, 2.0)
            finally:
                await owner.close()
                await guest.close()

        got = asyncio.run(run())
        emojis = [r["emoji"] for r in got]
        assert emojis.count("🔥") == 1, f"expected one 🔥, got {emojis}"
        assert emojis.count("💦") == 1, f"expected one 💦, got {emojis}"
