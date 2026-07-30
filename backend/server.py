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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"

app = FastAPI()
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

def is_valid_command(cmd: str) -> bool:
    return bool(COMMAND_RE.match(cmd))

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

class UrlSettingsInput(BaseModel):
    local_url: str = ""
    public_url: str = ""

def gen_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))

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
        self.limits: dict = {"min_depth": 0, "max_speed": 100}
        self.hr_cutoff: int = 0
        self.hr_over: bool = False
        self.overlay_ws: set = set()
        self.telemetry: dict = {"speed": 0, "stroke": 0, "depth": 0, "sensation": 0}
        self.hr: dict = {"bpm": 0, "connected": False}
        self.motion_accum: float = 0.0
        self.motion_start: Optional[float] = None
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
        """Force-stop and block motion when live BPM crosses the safety cutoff."""
        cutoff = int(self.hr_cutoff or 0)
        bpm = int(self.hr.get("bpm", 0))
        if cutoff > 0 and self.hr.get("connected") and bpm >= cutoff:
            if not self.hr_over:
                self.hr_over = True
                await self.send_to_host({"type": "command", "cmd": "set:speed:0"})
                await self.send_to_host({"type": "command", "cmd": "go:menu"})
                self.telemetry["speed"] = 0
                self._update_motion(0)
                await log_event("security", "hr_cutoff_triggered", actor="system",
                                detail={"bpm": bpm, "cutoff": cutoff})
        elif self.hr_over and bpm < max(0, cutoff - 3):
            self.hr_over = False
            await log_event("security", "hr_cutoff_cleared", actor="system",
                            detail={"bpm": bpm, "cutoff": cutoff})

    def clamp_command(self, cmd: str) -> str:
        """Enforce owner safety limits (min depth floor, max speed cap, HR cutoff)."""
        # Heart-rate safety cutoff: while over the limit, no motion is allowed.
        if self.hr_over and (cmd.startswith("set:speed:") or cmd == "go:strokeEngine"):
            return "set:speed:0"
        m = re.match(r'^set:(depth|speed):(\d+)$', cmd)
        if not m:
            return cmd
        kind, val = m.group(1), int(m.group(2))
        if kind == "depth" and val < self.limits.get("min_depth", 0):
            return f'set:depth:{self.limits["min_depth"]}'
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

    async def add_client(self, cid: str, ws: WebSocket, code: str, label: str):
        self.clients[cid] = {"ws": ws, "code": code, "label": label}
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

    def client_status(self, cid: str) -> dict:
        if cid == self.active_id:
            return {"status": "active", "position": 0, "remaining_seconds": self.active_remaining()}
        if cid in self.queue:
            return {"status": "waiting", "position": self.queue.index(cid) + 1,
                    "remaining_seconds": 0}
        return {"status": "idle", "position": -1, "remaining_seconds": 0}

    def public_state(self) -> dict:
        active_label = None
        if self.active_id and self.active_id in self.clients:
            active_label = self.clients[self.active_id]["label"]
        queue_view = []
        for i, cid in enumerate(self.queue):
            c = self.clients.get(cid)
            if c:
                queue_view.append({"label": c["label"], "position": i + 1})
        return {
            "host_connected": self.host_ws is not None,
            "device_state": self.device_state,
            "active": {"label": active_label, "remaining_seconds": self.active_remaining()} if self.active_id else None,
            "queue": queue_view,
            "queue_length": len(self.queue),
            "limits": self.limits,
        }

    async def broadcast(self):
        base = self.public_state()
        # host
        await self.send_to_host({"type": "state", **base})
        # each client with personalized "you"
        for cid, c in list(self.clients.items()):
            payload = {"type": "state", **base, "you": self.client_status(cid), "label": c["label"]}
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
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="OSSM Bridge")
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
    return {
        "min_depth": int(doc.get("min_depth", 0)),
        "max_speed": int(doc.get("max_speed", 100)),
        "hr_cutoff": int(doc.get("hr_cutoff", 0)),
        "local_url": doc.get("local_url", "") or "",
        "public_url": doc.get("public_url", "") or "",
    }

@api_router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return await load_settings()

@api_router.put("/settings")
async def put_settings(body: SettingsInput, user: dict = Depends(get_current_user)):
    data = {"min_depth": body.min_depth, "max_speed": body.max_speed, "hr_cutoff": body.hr_cutoff}
    await db.settings.update_one({"_id": "global"}, {"$set": data}, upsert=True)
    hub.limits = {"min_depth": body.min_depth, "max_speed": body.max_speed}
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
                if sp is not None:
                    sp = max(0, min(100, sp))
                    hub.telemetry["speed"] = sp
                    hub._update_motion(sp)
                    await hub.push_telemetry()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if hub.host_ws is ws:
            hub.host_ws = None
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
    label = doc.get("label") or code
    async with hub.lock:
        await hub.add_client(cid, ws, code, label)
        await hub.broadcast()
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "command":
                async with hub.lock:
                    await hub.handle_command(cid, str(data.get("cmd", "")))
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
                    hub.hr_over = False
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
    return {"message": "OSSM Bridge API"}

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
    hub.limits = {"min_depth": s["min_depth"], "max_speed": s["max_speed"]}
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
    client.close()
