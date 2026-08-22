"""
Cloudflare Calls TURN integration.

Cloudflare Realtime TURN is a hosted TURN relay that mints short-lived per-viewer
credentials via a REST call — perfect for mobile clients on 4G/5G symmetric NAT
where direct WebRTC fails. Free tier includes 1000 GB/month.

The owner pastes their `TURN_KEY_ID` + `TURN_API_TOKEN` (from
https://dash.cloudflare.com > Calls/Realtime > TURN) once in the admin UI. We:
  * Fernet-encrypt the token at rest in `settings.cloudflare_turn` (Mongo).
  * On every WHEP session start, mint a fresh, short-lived (1h) ICE-server
    config and hand it to the viewer's browser via `GET /api/stream/ice-servers`.
  * The long-lived Cloudflare API token never leaves the backend.

Docs: https://developers.cloudflare.com/realtime/turn/generate-credentials/
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("ossm-bridge.cloudflare_turn")

DEFAULT_CF_ENDPOINT = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"


def _endpoint(key_id: str) -> str:
    """Resolved at call time so a `CF_TURN_ENDPOINT` env var set by
    load_dotenv() (which runs *after* this module is imported) is picked up."""
    tmpl = os.environ.get("CF_TURN_ENDPOINT") or DEFAULT_CF_ENDPOINT
    return tmpl.format(key_id=key_id)

DEFAULT_TTL = 3600  # 1 hour matches a typical viewer session
SETTINGS_ID = "cloudflare_turn"


# --- Fernet-based token-at-rest encryption --------------------------------
def _load_fernet() -> Fernet:
    """
    Load the encryption key from `TURN_CREDENTIAL_ENCRYPTION_KEY` env var.
    If missing (common in a fresh dev setup) we generate an ephemeral key
    and log a warning — the owner can persist it later without losing data
    by re-saving their credentials.
    """
    key = (os.environ.get("TURN_CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    if not key:
        key = Fernet.generate_key().decode()
        logger.warning(
            "TURN_CREDENTIAL_ENCRYPTION_KEY is unset — using an ephemeral key. "
            "Saved Cloudflare TURN credentials will not survive a backend restart. "
            "Set TURN_CREDENTIAL_ENCRYPTION_KEY in your .env for persistence."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def encrypt_token(token: str) -> str:
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(token_enc: str) -> Optional[str]:
    try:
        return _get_fernet().decrypt(token_enc.encode()).decode()
    except InvalidToken:
        return None


# --- Cloudflare API calls -------------------------------------------------
async def validate_credentials(key_id: str, token: str) -> bool:
    """Verify creds by attempting a short-TTL credential generation. Cloudflare
    returns 201 for success. Any other status (including 401/403) => invalid."""
    url = _endpoint(key_id)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"ttl": 300},
            )
        return r.status_code == 201
    except httpx.RequestError:
        return False


async def mint_ice_servers(key_id: str, token: str, ttl: int = DEFAULT_TTL) -> Optional[List[dict]]:
    """Mint a fresh Cloudflare TURN credential set. Returns the iceServers list
    or None on failure so the caller can fall back to STUN-only ICE."""
    ttl = max(60, min(int(ttl), 172_800))  # Cloudflare caps at 48h
    url = _endpoint(key_id)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"ttl": ttl},
            )
    except httpx.RequestError as e:
        logger.warning("Cloudflare TURN request failed: %s", e)
        return None
    if r.status_code != 201:
        logger.warning("Cloudflare TURN mint failed: HTTP %s", r.status_code)
        return None
    try:
        body = r.json()
        servers = body.get("iceServers")
        return servers if isinstance(servers, list) else None
    except Exception:  # noqa: BLE001
        return None


# --- Mongo persistence helpers -------------------------------------------
async def load_config(db) -> Optional[dict]:
    """Return `{key_id, token}` from Mongo settings, or None if unset."""
    doc = await db.settings.find_one({"_id": SETTINGS_ID})
    if not doc:
        return None
    token = decrypt_token(doc.get("token_enc") or "")
    if not token:
        # Encrypted with a different Fernet key (e.g. after key rotation) —
        # treat as unconfigured so the owner can re-save.
        return None
    return {"key_id": doc["key_id"], "token": token}


async def save_config(db, key_id: str, token: str) -> None:
    await db.settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": {
            "key_id": key_id.strip(),
            "token_enc": encrypt_token(token.strip()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def delete_config(db) -> None:
    await db.settings.delete_one({"_id": SETTINGS_ID})


def masked_key_id(key_id: str) -> str:
    if not key_id or len(key_id) <= 8:
        return "•" * len(key_id)
    return f"{key_id[:4]}…{key_id[-4:]}"


async def get_status(db) -> dict:
    doc = await db.settings.find_one({"_id": SETTINGS_ID}, {"key_id": 1, "updated_at": 1})
    if not doc:
        return {"configured": False}
    return {
        "configured": True,
        "key_id_masked": masked_key_id(doc.get("key_id") or ""),
        "updated_at": doc.get("updated_at"),
    }


async def get_ice_servers_for_viewer(db, static: List[dict]) -> List[dict]:
    """Combine static STUN entries with a freshly-minted Cloudflare TURN entry.
    Returns whatever we can gather — never raises."""
    result: List[dict] = list(static)
    cfg = await load_config(db)
    if cfg:
        servers = await mint_ice_servers(cfg["key_id"], cfg["token"])
        if servers:
            result.extend(servers)
    return result
