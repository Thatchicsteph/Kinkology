from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import time
import asyncio
import logging
import secrets
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
import bcrypt
import jwt
import pyotp
import qrcode
import base64
import hashlib
import hmac
import csv
import json
from io import BytesIO, StringIO

from stream import router as stream_router, shutdown as stream_shutdown, set_publish_token_provider, _log_ice_config
import stream_patch
import cloudflare_turn

# Install aioice NAT/Docker patch BEFORE any RTCPeerConnection is created.
stream_patch.apply()
_log_ice_config()

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"

app = FastAPI(title="Kinkology API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ossm-bridge")

# ------------------------------------------------------------------
# OSSM BLE command validation (mirrors firmware regex)
# ------------------------------------------------------------------
COMMAND_RE = re.compile(
    r'^(go:(simplePenetration|strokeEngine|streaming|menu)'
    r'|set:(speed|stroke|depth|sensation|buffer|pattern):(0|[1-9][0-9]?|100)'
    r'|stream:(0|[1-9][0-9]?|100):[0-9]+)$'
)

# Toy commands the active guest is allowed to send. Owner's browser translates
# these into Intiface / Buttplug calls; the backend is a dumb relay.
#   toy:vibrate:<0-100>   set intensity on every connected toy
#   toy:pattern:<slug>    start a named preset from vibrationPatterns.js
#   toy:stop              stop pattern + zero all toys
TOY_COMMAND_RE = re.compile(
    r'^(toy:vibrate:(0|[1-9][0-9]?|100)'
    r'|toy:pattern:[a-z0-9_\-]{1,32}'
    r'|toy:stop)$'
)

def is_valid_command(cmd: str) -> bool:
    return bool(COMMAND_RE.match(cmd))

def is_valid_toy_command(cmd: str) -> bool:
    return bool(TOY_COMMAND_RE.match(cmd))

# ------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

JWT_SECRET_PLACEHOLDER = "CHANGE_ME_run_openssl_rand_hex_32"

def get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret or secret == JWT_SECRET_PLACEHOLDER:
        raise RuntimeError(
            "JWT_SECRET is missing or left as the placeholder. "
            "Set a strong secret in .env (generate with: openssl rand -hex 32)."
        )
    return secret

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email,
               "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "access"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def create_mfa_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email,
               "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "mfa"}
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)

def hash_recovery(code: str) -> str:
    return hmac.new(get_jwt_secret().encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()

def make_recovery_codes(n: int = 10):
    return ["-".join(secrets.token_hex(2).upper() for _ in range(3)) for _ in range(n)]

def qr_data_url(uri: str) -> str:
    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

# ------------------------------------------------------------------
# Brute-force lockout / rate limiting
# ------------------------------------------------------------------
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

async def check_lockout(identifier: str):
    doc = await db.login_attempts.find_one({"identifier": identifier})
    if not doc:
        return
    locked_until = doc.get("locked_until", 0)
    now = time.time()
    if locked_until and locked_until > now:
        retry = int(locked_until - now)
        minutes = max(1, (retry + 59) // 60)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(retry)},
        )
    if locked_until and locked_until <= now:
        await db.login_attempts.delete_one({"identifier": identifier})

async def record_failure(identifier: str):
    doc = await db.login_attempts.find_one_and_update(
        {"identifier": identifier},
        {"$inc": {"count": 1}, "$set": {"updated": time.time()}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if doc.get("count", 0) >= MAX_ATTEMPTS:
        await db.login_attempts.update_one(
            {"identifier": identifier},
            {"$set": {"locked_until": time.time() + LOCKOUT_SECONDS}},
        )
        await log_event("security", "account_locked", actor=identifier,
                        detail={"attempts": doc.get("count", 0)})

async def clear_attempts(identifier: str):
    await db.login_attempts.delete_one({"identifier": identifier})

# ------------------------------------------------------------------
# Audit & activity log
# ------------------------------------------------------------------
async def log_event(category: str, action: str, actor: str = "system",
                    target: Optional[str] = None, detail=None, ip: Optional[str] = None):
    try:
        await db.audit_logs.insert_one({
            "ts": datetime.now(timezone.utc).isoformat(),
            "category": category, "action": action, "actor": actor,
            "target": target, "detail": detail, "ip": ip,
        })
    except Exception as e:
        logger.error(f"audit log failed: {e}")

def log_public(d: dict) -> dict:
    return {
        "id": str(d["_id"]), "ts": d.get("ts"), "category": d.get("category"),
        "action": d.get("action"), "actor": d.get("actor"),
        "target": d.get("target"), "detail": d.get("detail"), "ip": d.get("ip"),
    }

def decode_token(token: str) -> dict:
    return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["id"] = str(user["_id"])
        user.pop("_id", None)
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------
class LoginInput(BaseModel):
    email: str
    password: str

class SetupInput(BaseModel):
    email: str
    password: str
    local_url: Optional[str] = None
    public_url: Optional[str] = None

class TwoFAVerify(BaseModel):
    code: str

class TwoFALogin(BaseModel):
    mfa_token: str
    code: Optional[str] = None
    recovery_code: Optional[str] = None

class CodeCreate(BaseModel):
    label: str = ""
    minutes: int = Field(gt=0, le=1440)

class SettingsInput(BaseModel):
    min_depth: int = Field(ge=0, le=100)
    max_speed: int = Field(ge=0, le=100)
    hr_cutoff: int = Field(ge=0, le=300, default=0)
    toy_length_mm: int = Field(ge=0, le=2000, default=0)
    rail_travel_mm: int = Field(ge=1, le=2000, default=300)

class UrlSettingsInput(BaseModel):
    local_url: str = ""
    public_url: str = ""

def gen_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))

def compute_toy_max_depth(toy_length_mm: int, rail_travel_mm: int) -> int:
    """0 means Toy Mode is off (no cap beyond the normal 0-100 range)."""
    if not toy_length_mm or not rail_travel_mm:
        return 100
    return max(0, min(100, round((toy_length_mm / rail_travel_mm) * 100)))

# ------------------------------------------------------------------
# Realtime hub: relays control from active guest -> host (device holder)
# ------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.host_ws: Optional[WebSocket] = None
        self.clients: Dict[str, dict] = {}   # client_id -> {ws, code, label}
        self.queue: List[str] = []
        self.active_id: Optional[str] = None
        self.active_start: Optional[float] = None
        self.active_remaining_start: int = 0
        self.device_state: str = ""
        self.limits: dict = {"min_depth": 0, "max_speed": 100, "max_depth": 100}
        self.hr_cutoff: int = 0
        self.hr_over: bool = False
        self.pre_cutoff_speed: int = 0
        self.hr_target: int = 0
        self.hr_sync_enabled: bool = False
        self.overlay_ws: set = set()
        self.telemetry: dict = {"speed": 0, "stroke": 0, "depth": 0, "sensation": 0}
        self.hr: dict = {"bpm": 0, "connected": False}
        self.hr_sync: dict = {"enabled": False, "target": 120, "min_speed": 0,
                              "max_speed": 100, "response": 0.6, "ramp_up": 25.0,
                              "ramp_down": 50.0, "schedule": []}
        self.hr_sync_command: float = 0.0
        self.hr_sync_started: Optional[float] = None
        self.motion_accum: float = 0.0
        self.motion_start: Optional[float] = None
        # Toys (Lovense / Intiface) live on the owner's browser. We just track
        # what the owner reports so guests know whether the toy controls should
        # appear on their console. `toys_locked` is an owner-triggered kill
        # switch: while True, guest toy commands are dropped and the owner's
        # browser is told to stop all toys.
        self.toys_available: bool = False
        self.toys_pattern: Optional[str] = None
        self.toys_locked: bool = False
        # In-memory chat: last 50 messages, oldest first. Each entry:
        #   {"id": str, "author": str, "role": "owner"|"guest", "text": str, "ts": iso}
        self.chat_msgs: List[dict] = []
        # Per-sender rate limit: 1 message / 1s minimum gap.
        self.chat_last_sent: Dict[str, float] = {}
        self.lock = asyncio.Lock()

    def _update_motion(self, speed: int):
        now = time.monotonic()
        if speed > 0 and self.motion_start is None:
            self.motion_start = now
        elif speed == 0 and self.motion_start is not None:
            self.motion_accum += now - self.motion_start
            self.motion_start = None

    def reset_telemetry(self):
        self.telemetry = {"speed": 0, "stroke": 0, "depth": 0, "sensation": 0}
        self.motion_accum = 0.0
        self.motion_start = None

    def telemetry_frame(self) -> dict:
        now = time.monotonic()
        run = self.motion_accum + (now - self.motion_start if self.motion_start else 0)
        session = int(now - self.active_start) if (self.active_id and self.active_start) else 0
        label = self.clients[self.active_id]["label"] if (self.active_id and self.active_id in self.clients) else None
        return {
            "type": "telemetry",
            "host_connected": self.host_ws is not None,
            "controller": label,
            "running": self.telemetry["speed"] > 0,
            "run_seconds": int(run),
            "session_seconds": session,
            "hr_bpm": int(self.hr.get("bpm", 0)),
            "hr_connected": bool(self.hr.get("connected", False)),
            "hr_cutoff": int(self.hr_cutoff),
            "hr_over": bool(self.hr_over),
            "hr_target": int(self.hr_target),
            "hr_sync_enabled": bool(self.hr_sync_enabled),
            **self.telemetry,
        }

    async def push_telemetry(self):
        frame = self.telemetry_frame()
        for ws in list(self.overlay_ws):
            try:
                await ws.send_json(frame)
            except Exception:
                self.overlay_ws.discard(ws)

    async def evaluate_hr_cutoff(self):
        """Force-stop and block motion when live BPM crosses the safety cutoff;
        resume automatically once BPM drops back below it."""
        cutoff = int(self.hr_cutoff or 0)
        bpm = int(self.hr.get("bpm", 0))
        if cutoff > 0 and self.hr.get("connected") and bpm >= cutoff:
            if not self.hr_over:
                self.hr_over = True
                self.pre_cutoff_speed = int(self.telemetry.get("speed", 0))
                await self.send_to_host({"type": "command", "cmd": "set:speed:0"})
                await self.send_to_host({"type": "command", "cmd": "go:menu"})
                self.telemetry["speed"] = 0
                self._update_motion(0)
                await log_event("security", "hr_cutoff_triggered", actor="system",
                                detail={"bpm": bpm, "cutoff": cutoff})
        elif self.hr_over and self.hr.get("connected") and bpm < cutoff:
            self.hr_over = False
            await log_event("security", "hr_cutoff_cleared", actor="system",
                            detail={"bpm": bpm, "cutoff": cutoff})
            resume_speed = self.clamp_command(f"set:speed:{self.pre_cutoff_speed}")
            resume_speed = int(resume_speed.split(":")[-1])
            # The cutoff trip always sends go:menu, regardless of hr_sync_enabled,
            # so the resume must always send go:strokeEngine to bring the device
            # back — otherwise it stays parked on the menu screen forever.
            await self.send_to_host({"type": "command", "cmd": "go:strokeEngine"})
            # If HR Sync is driving speed itself, let its own control loop take it
            # from here; otherwise restore motion to where it was before the trip.
            if not self.hr_sync_enabled and resume_speed > 0:
                await self.send_to_host({"type": "command", "cmd": f"set:speed:{resume_speed}"})
                self.telemetry["speed"] = resume_speed
                self._update_motion(resume_speed)
            await log_event("security", "hr_cutoff_resumed", actor="system",
                            detail={"bpm": bpm, "cutoff": cutoff,
                                     "speed": resume_speed if not self.hr_sync_enabled else self.telemetry.get("speed", 0)})
            self.pre_cutoff_speed = 0

    def clamp_command(self, cmd: str) -> str:
        # Heart-rate safety cutoff: while over the limit, no motion is allowed.
        if self.hr_over and (cmd.startswith("set:speed:") or cmd == "go:strokeEngine"):
            return "set:speed:0"
        m = re.match(r'^set:(depth|speed):(\d+)$', cmd)
        if not m:
            return cmd
        kind, val = m.group(1), int(m.group(2))
        if kind == "depth" and val < self.limits.get("min_depth", 0):
            return f'set:depth:{self.limits["min_depth"]}'
        if kind == "depth" and val > self.limits.get("max_depth", 100):
            return f'set:depth:{self.limits["max_depth"]}'
        if kind == "speed" and val > self.limits.get("max_speed", 100):
            return f'set:speed:{self.limits["max_speed"]}'
        return cmd

    def active_remaining(self) -> int:
        if self.active_id is None or self.active_start is None:
            return 0
        elapsed = time.monotonic() - self.active_start
        return max(0, int(self.active_remaining_start - elapsed))

    async def _send(self, ws: Optional[WebSocket], payload: dict):
        if ws is None:
            return
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    async def send_to_host(self, payload: dict):
        await self._send(self.host_ws, payload)

    async def code_remaining(self, code: str) -> int:
        doc = await db.access_codes.find_one({"code": code})
        if not doc or doc.get("revoked"):
            return 0
        return max(0, int(doc.get("granted_seconds", 0) - doc.get("used_seconds", 0)))

    async def promote(self):
        """Promote next queued client to active if slot is free."""
        while self.active_id is None and self.queue:
            cid = self.queue[0]
            client = self.clients.get(cid)
            if client is None:
                self.queue.pop(0)
                continue
            remaining = await self.code_remaining(client["code"])
            if remaining <= 0:
                self.queue.pop(0)
                await self._send(client["ws"], {"type": "expired"})
                continue
            self.queue.pop(0)
            self.active_id = cid
            self.active_start = time.monotonic()
            self.active_remaining_start = remaining
            self.reset_telemetry()
            await log_event("session", "guest_active", actor=f"guest:{client['label']}",
                            target=client["code"], detail={"remaining_seconds": remaining})
            break

    async def end_active(self, reason: str = "ended"):
        if self.active_id is None:
            return
        cid = self.active_id
        client = self.clients.get(cid)
        elapsed = int(time.monotonic() - (self.active_start or time.monotonic()))
        consumed = min(elapsed, self.active_remaining_start)
        if client:
            await db.access_codes.update_one(
                {"code": client["code"]},
                {"$inc": {"used_seconds": consumed},
                 "$set": {"last_used_at": datetime.now(timezone.utc).isoformat()}},
            )
            await self._send(client["ws"], {"type": "turn_ended", "reason": reason})
            await log_event("session", "turn_ended", actor=f"guest:{client['label']}",
                            target=client["code"], detail={"reason": reason, "seconds": consumed})
        # Safety: stop the device between turns
        await self.send_to_host({"type": "command", "cmd": "set:speed:0"})
        await self.send_to_host({"type": "command", "cmd": "go:menu"})
        self.active_id = None
        self.active_start = None
        self.active_remaining_start = 0
        self.reset_telemetry()
        await self.push_telemetry()

    async def add_client(self, cid: str, ws: WebSocket, code: str, label: str, auto_label: bool = False):
        # auto_label is True when the owner didn't set a custom label for this
        # access code, so `label` fell back to the raw code itself. That raw
        # code must never be shown to *other* guests (only to the owner/host,
        # who already has it on the Manage Codes panel).
        self.clients[cid] = {"ws": ws, "code": code, "label": label, "auto_label": auto_label}
        if cid not in self.queue and cid != self.active_id:
            self.queue.append(cid)
        await log_event("session", "guest_joined", actor=f"guest:{label}", target=code)
        await self.promote()

    async def remove_client(self, cid: str):
        if cid in self.queue:
            self.queue.remove(cid)
        if self.active_id == cid:
            await self.end_active("disconnected")
            await self.promote()
        self.clients.pop(cid, None)

    async def handle_command(self, cid: str, cmd: str):
        if cid != self.active_id:
            return
        if not is_valid_command(cmd):
            return
        cmd = self.clamp_command(cmd)
        m = re.match(r'^set:(speed|stroke|depth|sensation):(\d+)$', cmd)
        if m:
            self.telemetry[m.group(1)] = int(m.group(2))
            if m.group(1) == "speed":
                self._update_motion(int(m.group(2)))
        await self.send_to_host({"type": "command", "cmd": cmd})
        await self.push_telemetry()

    async def handle_toy_command(self, cid: str, cmd: str):
        """Relay a toy command from the active guest to the owner's browser.
        Owner-side `useToys` interprets it against the actual Intiface client."""
        if cid != self.active_id:
            return
        if not is_valid_toy_command(cmd):
            return
        if not self.toys_available:
            return  # owner isn't hosting any toys — silently drop
        if self.toys_locked:
            return  # owner has paused guest toy control
        if cmd.startswith("toy:pattern:"):
            self.toys_pattern = cmd.split(":", 2)[2]
        elif cmd == "toy:stop" or cmd.startswith("toy:vibrate:"):
            # A direct nudge or stop clears the "running pattern" indicator.
            self.toys_pattern = None
        await self.send_to_host({"type": "toy_command", "cmd": cmd})

    CHAT_MAX_LEN = 250
    CHAT_HISTORY = 50
    CHAT_MIN_GAP = 1.0  # seconds between messages from the same sender

    def _rate_limit(self, sender_id: str) -> bool:
        now = time.monotonic()
        last = self.chat_last_sent.get(sender_id, 0.0)
        if now - last < self.CHAT_MIN_GAP:
            return False
        self.chat_last_sent[sender_id] = now
        return True

    async def _append_chat(self, author: str, role: str, text: str, sender_id: str) -> Optional[dict]:
        text = (text or "").strip()
        if not text:
            return None
        if len(text) > self.CHAT_MAX_LEN:
            text = text[: self.CHAT_MAX_LEN]
        if not self._rate_limit(sender_id):
            return None
        msg = {
            "id": secrets.token_hex(6),
            "author": author,
            "role": role,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.chat_msgs.append(msg)
        if len(self.chat_msgs) > self.CHAT_HISTORY:
            self.chat_msgs = self.chat_msgs[-self.CHAT_HISTORY :]
        # Fan out to everyone connected.
        frame = {"type": "chat_msg", "message": msg}
        await self.send_to_host(frame)
        for c in list(self.clients.values()):
            await self._send(c["ws"], frame)
        return msg

    async def handle_guest_chat(self, cid: str, text: str):
        client = self.clients.get(cid)
        if not client:
            return
        author = self._safe_label(client)
        await self._append_chat(author=author, role="guest", text=text, sender_id=f"g:{cid}")

    async def handle_owner_chat(self, text: str):
        await self._append_chat(author="Owner", role="owner", text=text, sender_id="owner")

    def client_status(self, cid: str) -> dict:
        if cid == self.active_id:
            return {"status": "active", "position": 0, "remaining_seconds": self.active_remaining()}
        if cid in self.queue:
            return {"status": "waiting", "position": self.queue.index(cid) + 1,
                    "remaining_seconds": 0}
        return {"status": "idle", "position": -1, "remaining_seconds": 0}

    @staticmethod
    def _safe_label(c: dict) -> str:
        # What other guests are allowed to see: never the raw access code.
        return "Guest" if c.get("auto_label") else c["label"]

    def public_state(self, for_guests: bool = False) -> dict:
        active_label = None
        if self.active_id and self.active_id in self.clients:
            active_client = self.clients[self.active_id]
            active_label = self._safe_label(active_client) if for_guests else active_client["label"]
        queue_view = []
        for i, cid in enumerate(self.queue):
            c = self.clients.get(cid)
            if c:
                label = self._safe_label(c) if for_guests else c["label"]
                queue_view.append({"label": label, "position": i + 1})
        return {
            "host_connected": self.host_ws is not None,
            "device_state": self.device_state,
            "active": {"label": active_label, "remaining_seconds": self.active_remaining()} if self.active_id else None,
            "queue": queue_view,
            "queue_length": len(self.queue),
            "limits": self.limits,
            "toys": {"available": self.toys_available, "pattern": self.toys_pattern, "locked": self.toys_locked},
        }

    async def broadcast(self):
        # host sees real labels (owner already has codes on Manage Codes)
        host_base = self.public_state(for_guests=False)
        await self.send_to_host({"type": "state", **host_base})
        # guests only ever see sanitized labels for OTHER people in the
        # queue/active slot — their own code is never echoed back to anyone
        # but themselves via the top-level "label" field below.
        guest_base = self.public_state(for_guests=True)
        for cid, c in list(self.clients.items()):
            payload = {"type": "state", **guest_base, "you": self.client_status(cid), "label": c["label"]}
            await self._send(c["ws"], payload)

    async def tick(self):
        async with self.lock:
            if self.active_id is not None and self.active_remaining() <= 0:
                await self.end_active("time_up")
                await self.promote()
            await self.broadcast()
            await self.push_telemetry()

hub = Hub()

async def ticker_loop():
    while True:
        try:
            await hub.tick()
        except Exception as e:
            logger.error(f"ticker error: {e}")
        await asyncio.sleep(1)

# ------------------------------------------------------------------
# Auth routes
# ------------------------------------------------------------------
@api_router.get("/setup/status")
async def setup_status():
    count = await db.users.count_documents({})
    return {"needs_setup": count == 0}

@api_router.post("/setup")
async def setup_admin(body: SetupInput, request: Request, response: Response):
    if await db.users.count_documents({}) > 0:
        raise HTTPException(status_code=403, detail="Setup already completed. Please sign in.")
    email = body.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    res = await db.users.insert_one({
        "email": email, "password_hash": hash_password(body.password),
        "name": "Admin", "role": "admin", "created_at": datetime.now(timezone.utc).isoformat(),
    })
    url_update = {}
    if body.local_url is not None:
        url_update["local_url"] = body.local_url.strip()
    if body.public_url is not None:
        url_update["public_url"] = body.public_url.strip()
    if url_update:
        await db.settings.update_one({"_id": "global"}, {"$set": url_update}, upsert=True)
    uid = str(res.inserted_id)
    token = create_access_token(uid, email)
    response.set_cookie("access_token", token, httponly=True, secure=True,
                        samesite="lax", max_age=604800, path="/")
    await log_event("security", "owner_created", actor=email, ip=client_ip(request))
    return {"token": token, "user": {"id": uid, "email": email, "name": "Admin"}}

@api_router.post("/auth/login")
async def login(body: LoginInput, request: Request, response: Response):
    email = body.email.strip().lower()
    ident = f"{client_ip(request)}:login:{email}"
    await check_lockout(ident)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await record_failure(ident)
        await log_event("security", "login_failed", actor=email, ip=client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await clear_attempts(ident)
    uid = str(user["_id"])
    if user.get("twofa_enabled"):
        return {"mfa_required": True, "mfa_token": create_mfa_token(uid, email)}
    token = create_access_token(uid, email)
    response.set_cookie("access_token", token, httponly=True, secure=True,
                        samesite="lax", max_age=604800, path="/")
    await log_event("security", "login_success", actor=email, ip=client_ip(request))
    return {"token": token, "user": {"id": uid, "email": email, "name": user.get("name", "Admin")}}

@api_router.post("/auth/2fa/login")
async def twofa_login(body: TwoFALogin, request: Request, response: Response):
    try:
        payload = decode_token(body.mfa_token)
        if payload.get("type") != "mfa":
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=401, detail="Your 2FA session expired. Please log in again.")
    ident = f"{client_ip(request)}:2fa:{payload.get('email','')}"
    await check_lockout(ident)
    user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    if not user or not user.get("twofa_enabled"):
        raise HTTPException(status_code=401, detail="Invalid 2FA state")
    ok = False
    if body.code:
        ok = pyotp.TOTP(user["totp_secret"]).verify(body.code.strip(), valid_window=1)
    elif body.recovery_code:
        h = hash_recovery(body.recovery_code)
        hashes = user.get("recovery_codes_hash", [])
        if h in hashes:
            ok = True
            await db.users.update_one({"_id": user["_id"]},
                                      {"$set": {"recovery_codes_hash": [x for x in hashes if x != h]}})
    if not ok:
        await record_failure(ident)
        raise HTTPException(status_code=401, detail="Invalid code. Try again.")
    await clear_attempts(ident)
    uid = str(user["_id"]); email = user["email"]
    token = create_access_token(uid, email)
    response.set_cookie("access_token", token, httponly=True, secure=True,
                        samesite="lax", max_age=604800, path="/")
    await log_event("security", "login_success",
                    actor=email, ip=client_ip(request),
                    detail={"method": "recovery_code" if body.recovery_code else "totp"})
    return {"token": token, "user": {"id": uid, "email": email, "name": user.get("name", "Admin")}}

@api_router.get("/auth/2fa/status")
async def twofa_status(user: dict = Depends(get_current_user)):
    doc = await db.users.find_one({"email": user["email"]})
    return {"enabled": bool(doc.get("twofa_enabled"))}

@api_router.post("/auth/2fa/setup/start")
async def twofa_setup_start(user: dict = Depends(get_current_user)):
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="Kinkology")
    await db.users.update_one({"email": user["email"]}, {"$set": {"twofa_pending_secret": secret}})
    return {"secret": secret, "otpauth_uri": uri, "qr_code_data_url": qr_data_url(uri)}

@api_router.post("/auth/2fa/setup/verify")
async def twofa_setup_verify(body: TwoFAVerify, request: Request, user: dict = Depends(get_current_user)):
    ident = f"{client_ip(request)}:2fa-setup:{user['email']}"
    await check_lockout(ident)
    doc = await db.users.find_one({"email": user["email"]})
    secret = doc.get("twofa_pending_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="No pending 2FA setup. Start again.")
    if not pyotp.TOTP(secret).verify(body.code.strip(), valid_window=1):
        await record_failure(ident)
        raise HTTPException(status_code=400, detail="Invalid code — check your authenticator app and try again.")
    await clear_attempts(ident)
    codes = make_recovery_codes(10)
    await db.users.update_one(
        {"email": user["email"]},
        {"$set": {"totp_secret": secret, "twofa_enabled": True,
                  "recovery_codes_hash": [hash_recovery(c) for c in codes]},
         "$unset": {"twofa_pending_secret": ""}},
    )
    await log_event("security", "twofa_enabled", actor=user["email"], ip=client_ip(request))
    return {"ok": True, "recovery_codes": codes}

@api_router.post("/auth/2fa/disable")
async def twofa_disable(body: TwoFAVerify, request: Request, user: dict = Depends(get_current_user)):
    ident = f"{client_ip(request)}:2fa-disable:{user['email']}"
    await check_lockout(ident)
    doc = await db.users.find_one({"email": user["email"]})
    secret = doc.get("totp_secret")
    ok = bool(secret) and pyotp.TOTP(secret).verify(body.code.strip(), valid_window=1)
    if not ok and hash_recovery(body.code) in doc.get("recovery_codes_hash", []):
        ok = True
    if not ok:
        await record_failure(ident)
        raise HTTPException(status_code=400, detail="Invalid code. 2FA not disabled.")
    await clear_attempts(ident)
    await db.users.update_one(
        {"email": user["email"]},
        {"$set": {"twofa_enabled": False},
         "$unset": {"totp_secret": "", "recovery_codes_hash": "", "twofa_pending_secret": ""}},
    )
    await log_event("security", "twofa_disabled", actor=user["email"], ip=client_ip(request))
    return {"ok": True}

@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}

@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user.get("name", "Admin")}

# ------------------------------------------------------------------
# Access code admin routes
# ------------------------------------------------------------------
def code_public(doc: dict) -> dict:
    granted = int(doc.get("granted_seconds", 0))
    used = int(doc.get("used_seconds", 0))
    return {
        "id": str(doc["_id"]),
        "code": doc["code"],
        "label": doc.get("label", ""),
        "granted_seconds": granted,
        "used_seconds": used,
        "remaining_seconds": max(0, granted - used),
        "revoked": bool(doc.get("revoked", False)),
        "created_at": doc.get("created_at"),
        "last_used_at": doc.get("last_used_at"),
    }

@api_router.post("/codes")
async def create_code(body: CodeCreate, user: dict = Depends(get_current_user)):
    code = gen_code()
    while await db.access_codes.find_one({"code": code}):
        code = gen_code()
    doc = {
        "code": code,
        "label": body.label.strip(),
        "granted_seconds": body.minutes * 60,
        "used_seconds": 0,
        "revoked": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
    }
    res = await db.access_codes.insert_one(doc)
    doc["_id"] = res.inserted_id
    await log_event("security", "code_created", actor=user["email"], target=code,
                    detail={"minutes": body.minutes, "label": body.label.strip()})
    return code_public(doc)

@api_router.get("/codes")
async def list_codes(user: dict = Depends(get_current_user)):
    docs = await db.access_codes.find().sort("created_at", -1).to_list(500)
    return [code_public(d) for d in docs]

def parse_object_id(code_id: str) -> ObjectId:
    try:
        return ObjectId(code_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Not found")

@api_router.post("/codes/{code_id}/revoke")
async def revoke_code(code_id: str, user: dict = Depends(get_current_user)):
    doc = await db.access_codes.find_one_and_update(
        {"_id": parse_object_id(code_id)}, {"$set": {"revoked": True}})
    if doc:
        await log_event("security", "code_revoked", actor=user["email"], target=doc.get("code"))
    return {"ok": True}

@api_router.post("/codes/{code_id}/add-minutes")
async def add_minutes(code_id: str, body: CodeCreate, user: dict = Depends(get_current_user)):
    oid = parse_object_id(code_id)
    await db.access_codes.update_one(
        {"_id": oid},
        {"$inc": {"granted_seconds": body.minutes * 60}, "$set": {"revoked": False}},
    )
    doc = await db.access_codes.find_one({"_id": oid})
    await log_event("security", "code_extended", actor=user["email"],
                    target=doc.get("code") if doc else None, detail={"minutes": body.minutes})
    return code_public(doc)

@api_router.delete("/codes/{code_id}")
async def delete_code(code_id: str, user: dict = Depends(get_current_user)):
    oid = parse_object_id(code_id)
    doc = await db.access_codes.find_one({"_id": oid})
    await db.access_codes.delete_one({"_id": oid})
    if doc:
        await log_event("security", "code_deleted", actor=user["email"], target=doc.get("code"))
    return {"ok": True}

# ------------------------------------------------------------------
# Public: validate a code
# ------------------------------------------------------------------
@api_router.get("/access/{code}")
async def validate_code(code: str, request: Request):
    ident = f"{client_ip(request)}:access"
    await check_lockout(ident)
    doc = await db.access_codes.find_one({"code": code.upper()})
    if not doc or doc.get("revoked"):
        await record_failure(ident)
        return {"valid": False}
    remaining = max(0, int(doc.get("granted_seconds", 0) - doc.get("used_seconds", 0)))
    if remaining <= 0:
        await record_failure(ident)
        return {"valid": False, "label": doc.get("label", ""), "remaining_seconds": 0}
    await clear_attempts(ident)
    return {"valid": True, "label": doc.get("label", ""), "remaining_seconds": remaining}

# ------------------------------------------------------------------
# Safety settings (owner-set limits enforced server-side)
# ------------------------------------------------------------------
async def load_settings() -> dict:
    doc = await db.settings.find_one({"_id": "global"})
    if not doc:
        doc = {"_id": "global", "min_depth": 0, "max_speed": 100, "local_url": "", "public_url": ""}
        await db.settings.insert_one(doc)
    toy_length_mm = int(doc.get("toy_length_mm", 0))
    rail_travel_mm = int(doc.get("rail_travel_mm", 300))
    return {
        "min_depth": int(doc.get("min_depth", 0)),
        "max_speed": int(doc.get("max_speed", 100)),
        "hr_cutoff": int(doc.get("hr_cutoff", 0)),
        "toy_length_mm": toy_length_mm,
        "rail_travel_mm": rail_travel_mm,
        "max_depth": compute_toy_max_depth(toy_length_mm, rail_travel_mm),
        "local_url": doc.get("local_url", "") or "",
        "public_url": doc.get("public_url", "") or "",
    }

@api_router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return await load_settings()

@api_router.put("/settings")
async def put_settings(body: SettingsInput, user: dict = Depends(get_current_user)):
    data = {
        "min_depth": body.min_depth, "max_speed": body.max_speed, "hr_cutoff": body.hr_cutoff,
        "toy_length_mm": body.toy_length_mm, "rail_travel_mm": body.rail_travel_mm,
    }
    await db.settings.update_one({"_id": "global"}, {"$set": data}, upsert=True)
    hub.limits = {
        "min_depth": body.min_depth, "max_speed": body.max_speed,
        "max_depth": compute_toy_max_depth(body.toy_length_mm, body.rail_travel_mm),
    }
    hub.hr_cutoff = body.hr_cutoff
    await log_event("security", "limits_updated", actor=user["email"], detail=data)
    return await load_settings()

@api_router.put("/settings/urls")
async def put_url_settings(body: UrlSettingsInput, user: dict = Depends(get_current_user)):
    data = {"local_url": body.local_url.strip(), "public_url": body.public_url.strip()}
    await db.settings.update_one({"_id": "global"}, {"$set": data}, upsert=True)
    await log_event("security", "urls_updated", actor=user["email"], detail=data)
    return await load_settings()

# ------------------------------------------------------------------
# Admin session control
# ------------------------------------------------------------------
@api_router.get("/session/state")
async def session_state(user: dict = Depends(get_current_user)):
    return hub.public_state()

@api_router.post("/session/stop")
async def session_stop(user: dict = Depends(get_current_user)):
    await hub.send_to_host({"type": "command", "cmd": "set:speed:0"})
    await hub.send_to_host({"type": "command", "cmd": "go:menu"})
    await log_event("security", "emergency_stop", actor=user["email"])
    return {"ok": True}

@api_router.post("/session/skip")
async def session_skip(user: dict = Depends(get_current_user)):
    async with hub.lock:
        await hub.end_active("skipped_by_admin")
        await hub.promote()
        await hub.broadcast()
    await log_event("security", "session_skipped", actor=user["email"])
    return {"ok": True}

@api_router.post("/session/toys/lock")
async def toys_lock(user: dict = Depends(get_current_user)):
    """Kill switch: immediately stop toys on the owner's browser AND block
    every subsequent guest toy command until the owner unlocks."""
    async with hub.lock:
        hub.toys_locked = True
        hub.toys_pattern = None
        await hub.send_to_host({"type": "toy_command", "cmd": "toy:stop"})
        await hub.send_to_host({"type": "toys_lock", "locked": True})
        await hub.broadcast()
    await log_event("security", "toys_locked", actor=user["email"])
    return {"ok": True, "locked": True}


@api_router.post("/session/toys/unlock")
async def toys_unlock(user: dict = Depends(get_current_user)):
    async with hub.lock:
        hub.toys_locked = False
        await hub.send_to_host({"type": "toys_lock", "locked": False})
        await hub.broadcast()
    await log_event("security", "toys_unlocked", actor=user["email"])
    return {"ok": True, "locked": False}


@api_router.delete("/session/chat")
async def clear_chat(user: dict = Depends(get_current_user)):
    async with hub.lock:
        hub.chat_msgs = []
        frame = {"type": "chat_cleared"}
        await hub.send_to_host(frame)
        for c in list(hub.clients.values()):
            await hub._send(c["ws"], frame)
    return {"ok": True}

# ------------------------------------------------------------------
# WebSockets
# ------------------------------------------------------------------
@app.websocket("/api/ws/host")
async def ws_host(ws: WebSocket):
    token = ws.query_params.get("token", "")
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.host_ws = ws
    await log_event("session", "device_connected", actor="owner")
    # Sync current lock state and chat history to the owner's newly-opened WS.
    await hub._send(ws, {"type": "toys_lock", "locked": hub.toys_locked})
    await hub._send(ws, {"type": "chat_history", "messages": hub.chat_msgs})
    await hub.broadcast()
    try:
        while True:
            data = await ws.receive_json()
            t = data.get("type")
            if t == "device_state":
                hub.device_state = str(data.get("state", ""))[:200]
            elif t == "ble_status":
                if not data.get("connected"):
                    hub.device_state = "disconnected"
            elif t == "owner_telemetry":
                try:
                    sp = int(data.get("speed"))
                except (TypeError, ValueError):
                    sp = None
                changed = False
                if sp is not None:
                    sp = max(0, min(100, sp))
                    hub.telemetry["speed"] = sp
                    hub._update_motion(sp)
                    changed = True
                for field in ("stroke", "depth", "sensation"):
                    if field in data:
                        try:
                            val = max(0, min(100, int(data.get(field))))
                        except (TypeError, ValueError):
                            continue
                        hub.telemetry[field] = val
                        changed = True
                if "hr_target" in data:
                    try:
                        hub.hr_target = max(0, min(240, int(data.get("hr_target"))))
                        changed = True
                    except (TypeError, ValueError):
                        pass
                if "hr_sync_enabled" in data:
                    hub.hr_sync_enabled = bool(data.get("hr_sync_enabled"))
                    changed = True
                if changed:
                    await hub.push_telemetry()
            elif t == "toys_status":
                hub.toys_available = bool(data.get("available"))
                hub.toys_pattern = (data.get("pattern") or None)
                await hub.broadcast()
            elif t == "chat":
                async with hub.lock:
                    await hub.handle_owner_chat(str(data.get("text", "")))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if hub.host_ws is ws:
            hub.host_ws = None
            hub.toys_available = False
            hub.toys_pattern = None
            await log_event("session", "device_disconnected", actor="owner")
        await hub.broadcast()

@app.websocket("/api/ws/control/{code}")
async def ws_control(ws: WebSocket, code: str):
    code = code.upper()
    remaining = await hub.code_remaining(code)
    doc = await db.access_codes.find_one({"code": code})
    if not doc or doc.get("revoked") or remaining <= 0:
        await ws.accept()
        await ws.send_json({"type": "rejected", "reason": "invalid_or_expired"})
        await ws.close()
        return
    await ws.accept()
    cid = secrets.token_hex(8)
    owner_label = doc.get("label")
    label = owner_label or code
    async with hub.lock:
        await hub.add_client(cid, ws, code, label, auto_label=not owner_label)
        await hub.broadcast()
    # Seed the newly-joined guest with the current chat history so they don't
    # arrive to an empty pane while everyone else is mid-conversation.
    await hub._send(ws, {"type": "chat_history", "messages": hub.chat_msgs})
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "command":
                async with hub.lock:
                    await hub.handle_command(cid, str(data.get("cmd", "")))
            elif data.get("type") == "toy_command":
                async with hub.lock:
                    await hub.handle_toy_command(cid, str(data.get("cmd", "")))
            elif data.get("type") == "chat":
                async with hub.lock:
                    await hub.handle_guest_chat(cid, str(data.get("text", "")))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        async with hub.lock:
            await hub.remove_client(cid)
            await hub.broadcast()

@app.websocket("/api/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    hub.overlay_ws.add(ws)
    await hub._send(ws, hub.telemetry_frame())
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        hub.overlay_ws.discard(ws)

@app.websocket("/api/ws/hr")
async def ws_hr(ws: WebSocket):
    token = ws.query_params.get("token", "")
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.hr["connected"] = True
    await hub.push_telemetry()
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "hr":
                try:
                    bpm = int(data.get("bpm", 0))
                except (TypeError, ValueError):
                    bpm = 0
                hub.hr["bpm"] = max(0, min(300, bpm))
                hub.hr["connected"] = True
                await hub.evaluate_hr_cutoff()
                await hub.push_telemetry()
            elif data.get("type") == "hr_status":
                hub.hr["connected"] = bool(data.get("connected"))
                if not hub.hr["connected"]:
                    hub.hr["bpm"] = 0
                    # NOTE: intentionally do NOT clear hr_over here. If the cutoff
                    # was tripped, the device stays safely stopped while the strap
                    # is disconnected (no live BPM to confirm it's safe to resume).
                    # hr_over only clears via evaluate_hr_cutoff() once a real BPM
                    # reading below the cutoff comes back in, which also fires the
                    # actual go:strokeEngine/set:speed resume commands. Clearing it
                    # here left hr_over permanently False with the device parked at
                    # speed 0 and no way to ever trigger the resume path again.
                await hub.push_telemetry()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.hr["connected"] = False
        hub.hr["bpm"] = 0
        hub.hr_over = False
        await hub.push_telemetry()

@api_router.get("/overlay/state")
async def overlay_state():
    return hub.telemetry_frame()

# ------------------------------------------------------------------
# Audit & activity log routes
# ------------------------------------------------------------------
def _log_query(category, q, start, end):
    query = {}
    if category in ("security", "session"):
        query["category"] = category
    if start or end:
        ts = {}
        if start:
            ts["$gte"] = start
        if end:
            ts["$lte"] = end
        query["ts"] = ts
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"action": rx}, {"actor": rx}, {"target": rx}]
    return query

@api_router.get("/logs")
async def list_logs(
    user: dict = Depends(get_current_user),
    category: Optional[str] = None,
    q: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
):
    limit = max(1, min(500, limit))
    query = _log_query(category, q, start, end)
    total = await db.audit_logs.count_documents(query)
    docs = await db.audit_logs.find(query).sort("ts", -1).skip(max(0, skip)).limit(limit).to_list(limit)
    return {"items": [log_public(d) for d in docs], "total": total, "limit": limit, "skip": skip}

@api_router.delete("/logs")
async def clear_logs(user: dict = Depends(get_current_user)):
    res = await db.audit_logs.delete_many({})
    await log_event("security", "logs_cleared", actor=user["email"],
                    detail={"deleted": res.deleted_count})
    return {"ok": True, "deleted": res.deleted_count}

@api_router.get("/logs/export")
async def export_logs(
    user: dict = Depends(get_current_user),
    format: str = "csv",
    category: Optional[str] = None,
    q: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    query = _log_query(category, q, start, end)
    docs = await db.audit_logs.find(query).sort("ts", -1).to_list(100000)
    items = [log_public(d) for d in docs]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if format == "json":
        return Response(
            content=json.dumps(items, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="ossm-audit-{stamp}.json"'},
        )
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "category", "action", "actor", "target", "detail", "ip"])
    for it in items:
        detail = it.get("detail")
        detail = json.dumps(detail) if isinstance(detail, (dict, list)) else (detail or "")
        writer.writerow([it.get("ts", ""), it.get("category", ""), it.get("action", ""),
                         it.get("actor", ""), it.get("target", "") or "", detail, it.get("ip", "") or ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ossm-audit-{stamp}.csv"'},
    )

@api_router.get("/")
async def root():
    return {"message": "Kinkology API"}

# ------------------------------------------------------------------
# OBS/WHIP publish token — optional bearer secret that OBS must send
# in `Authorization: Bearer <token>` before it's allowed to publish.
# ------------------------------------------------------------------
async def _get_stream_publish_token() -> str:
    doc = await db.settings.find_one({"_id": "global"}) or {}
    return (doc.get("stream_token") or "").strip()

set_publish_token_provider(_get_stream_publish_token)


class StreamTokenInput(BaseModel):
    token: Optional[str] = None  # None => server generates one


@api_router.get("/stream/token")
async def get_stream_token(user: dict = Depends(get_current_user)):
    token = await _get_stream_publish_token()
    return {"token": token, "enabled": bool(token)}


@api_router.post("/stream/token")
async def set_stream_token(body: StreamTokenInput, user: dict = Depends(get_current_user)):
    token = (body.token or "").strip() if body.token is not None else secrets.token_urlsafe(24)
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty. Use DELETE to disable auth.")
    if len(token) < 8:
        raise HTTPException(status_code=400, detail="Token must be at least 8 characters.")
    await db.settings.update_one({"_id": "global"}, {"$set": {"stream_token": token}}, upsert=True)
    await log_event("security", "stream_token_set", actor=user["email"])
    return {"token": token, "enabled": True}


@api_router.delete("/stream/token")
async def clear_stream_token(user: dict = Depends(get_current_user)):
    await db.settings.update_one({"_id": "global"}, {"$unset": {"stream_token": ""}}, upsert=True)
    await log_event("security", "stream_token_cleared", actor=user["email"])
    return {"token": "", "enabled": False}


api_router.include_router(stream_router)


# ------------------------------------------------------------------
# Cloudflare Calls TURN — one-click TURN for mobile viewers on
# symmetric NAT / CGNAT. Owner pastes their TURN Key ID + API Token in
# the admin UI; backend Fernet-encrypts the token in Mongo and mints
# fresh, short-lived ICE credentials per viewer session.
# ------------------------------------------------------------------
class CloudflareTurnInput(BaseModel):
    key_id: str = Field(min_length=1, max_length=200)
    token: str = Field(min_length=1, max_length=2000)


@api_router.get("/stream/turn/cloudflare")
async def get_cloudflare_turn(user: dict = Depends(get_current_user)):
    return await cloudflare_turn.get_status(db)


@api_router.put("/stream/turn/cloudflare")
async def put_cloudflare_turn(body: CloudflareTurnInput, user: dict = Depends(get_current_user)):
    ok = await cloudflare_turn.validate_credentials(body.key_id.strip(), body.token.strip())
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Cloudflare rejected the credentials. Double-check the TURN Key ID and API Token in dash.cloudflare.com → Realtime → TURN.",
        )
    await cloudflare_turn.save_config(db, body.key_id, body.token)
    await log_event("security", "cloudflare_turn_configured", actor=user["email"])
    return {"ok": True, **await cloudflare_turn.get_status(db)}


@api_router.delete("/stream/turn/cloudflare")
async def delete_cloudflare_turn(user: dict = Depends(get_current_user)):
    await cloudflare_turn.delete_config(db)
    await log_event("security", "cloudflare_turn_removed", actor=user["email"])
    return {"ok": True, "configured": False}


@api_router.get("/stream/ice-servers")
async def get_ice_servers():
    """Public endpoint: returns a fresh set of ICE servers the browser should
    plug into `new RTCPeerConnection({iceServers:[...]})` before starting the
    WHEP handshake. Always includes public STUN; adds Cloudflare TURN if the
    owner configured it."""
    static = [
        {"urls": ["stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478"]},
    ]
    servers = await cloudflare_turn.get_ice_servers_for_viewer(db, static)
    return {"iceServers": servers}


app.include_router(api_router)

# CORS: default to same-origin only (the documented Caddy/Docker setup is single-origin).
# Set CORS_ORIGINS to a comma-separated allowlist to enable credentialed cross-origin
# requests. A literal '*' is allowed but forces credentials OFF (browsers reject '*'
# with credentials, and reflecting arbitrary origins with credentials is a CSRF risk).
_cors_env = os.environ.get('CORS_ORIGINS', '').strip()
if _cors_env == '*':
    _cors_origins, _cors_credentials = ['*'], False
elif _cors_env:
    _cors_origins, _cors_credentials = [o.strip() for o in _cors_env.split(',') if o.strip()], True
else:
    _cors_origins, _cors_credentials = [], True
app.add_middleware(
    CORSMiddleware,
    allow_credentials=_cors_credentials,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.access_codes.create_index("code", unique=True)
    await db.audit_logs.create_index([("ts", -1)])
    await db.audit_logs.create_index("category")
    await db.login_attempts.create_index("identifier", unique=True)
    s = await load_settings()
    hub.limits = {"min_depth": s["min_depth"], "max_speed": s["max_speed"], "max_depth": s["max_depth"]}
    hub.hr_cutoff = s["hr_cutoff"]
    # Optional pre-seed for dev/preview when ADMIN_EMAIL + ADMIN_PASSWORD are set.
    # In self-hosted Docker these are left unset, so the owner creates the admin
    # account via the first-run setup flow (/api/setup).
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password:
        admin_email = admin_email.lower()
        existing = await db.users.find_one({"email": admin_email})
        if existing is None:
            await db.users.insert_one({
                "email": admin_email, "password_hash": hash_password(admin_password),
                "name": "Admin", "role": "admin", "created_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Seeded admin user")
        elif not verify_password(admin_password, existing["password_hash"]):
            await db.users.update_one({"email": admin_email},
                                      {"$set": {"password_hash": hash_password(admin_password)}})
    asyncio.create_task(ticker_loop())

@app.on_event("shutdown")
async def shutdown_db_client():
    await stream_shutdown()
    client.close()
