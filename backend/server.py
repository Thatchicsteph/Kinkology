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
from pymongo import ReturnDocument
import bcrypt
import jwt
import pyotp
import qrcode
import base64
import hashlib
import hmac
from io import BytesIO

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

def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]

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

async def clear_attempts(identifier: str):
    await db.login_attempts.delete_one({"identifier": identifier})

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
        self.lock = asyncio.Lock()

    def clamp_command(self, cmd: str) -> str:
        """Enforce owner safety limits (min depth floor, max speed cap)."""
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
        # Safety: stop the device between turns
        await self.send_to_host({"type": "command", "cmd": "set:speed:0"})
        await self.send_to_host({"type": "command", "cmd": "go:menu"})
        self.active_id = None
        self.active_start = None
        self.active_remaining_start = 0

    async def add_client(self, cid: str, ws: WebSocket, code: str, label: str):
        self.clients[cid] = {"ws": ws, "code": code, "label": label}
        if cid not in self.queue and cid != self.active_id:
            self.queue.append(cid)
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
        await self.send_to_host({"type": "command", "cmd": cmd})

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
@api_router.post("/auth/login")
async def login(body: LoginInput, request: Request, response: Response):
    email = body.email.strip().lower()
    ident = f"{client_ip(request)}:login:{email}"
    await check_lockout(ident)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        await record_failure(ident)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await clear_attempts(ident)
    uid = str(user["_id"])
    if user.get("twofa_enabled"):
        return {"mfa_required": True, "mfa_token": create_mfa_token(uid, email)}
    token = create_access_token(uid, email)
    response.set_cookie("access_token", token, httponly=True, secure=True,
                        samesite="none", max_age=604800, path="/")
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
                        samesite="none", max_age=604800, path="/")
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
    return code_public(doc)

@api_router.get("/codes")
async def list_codes(user: dict = Depends(get_current_user)):
    docs = await db.access_codes.find().sort("created_at", -1).to_list(500)
    return [code_public(d) for d in docs]

@api_router.post("/codes/{code_id}/revoke")
async def revoke_code(code_id: str, user: dict = Depends(get_current_user)):
    await db.access_codes.update_one({"_id": ObjectId(code_id)}, {"$set": {"revoked": True}})
    return {"ok": True}

@api_router.post("/codes/{code_id}/add-minutes")
async def add_minutes(code_id: str, body: CodeCreate, user: dict = Depends(get_current_user)):
    await db.access_codes.update_one(
        {"_id": ObjectId(code_id)},
        {"$inc": {"granted_seconds": body.minutes * 60}, "$set": {"revoked": False}},
    )
    doc = await db.access_codes.find_one({"_id": ObjectId(code_id)})
    return code_public(doc)

@api_router.delete("/codes/{code_id}")
async def delete_code(code_id: str, user: dict = Depends(get_current_user)):
    await db.access_codes.delete_one({"_id": ObjectId(code_id)})
    return {"ok": True}

# ------------------------------------------------------------------
# Public: validate a code
# ------------------------------------------------------------------
@api_router.get("/access/{code}")
async def validate_code(code: str):
    doc = await db.access_codes.find_one({"code": code.upper()})
    if not doc or doc.get("revoked"):
        return {"valid": False}
    remaining = max(0, int(doc.get("granted_seconds", 0) - doc.get("used_seconds", 0)))
    return {"valid": remaining > 0, "label": doc.get("label", ""), "remaining_seconds": remaining}

# ------------------------------------------------------------------
# Safety settings (owner-set limits enforced server-side)
# ------------------------------------------------------------------
async def load_settings() -> dict:
    doc = await db.settings.find_one({"_id": "global"})
    if not doc:
        doc = {"_id": "global", "min_depth": 0, "max_speed": 100}
        await db.settings.insert_one(doc)
    return {"min_depth": int(doc.get("min_depth", 0)), "max_speed": int(doc.get("max_speed", 100))}

@api_router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return await load_settings()

@api_router.put("/settings")
async def put_settings(body: SettingsInput, user: dict = Depends(get_current_user)):
    data = {"min_depth": body.min_depth, "max_speed": body.max_speed}
    await db.settings.update_one({"_id": "global"}, {"$set": data}, upsert=True)
    hub.limits = data
    return data

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
    return {"ok": True}

@api_router.post("/session/skip")
async def session_skip(user: dict = Depends(get_current_user)):
    async with hub.lock:
        await hub.end_active("skipped_by_admin")
        await hub.promote()
        await hub.broadcast()
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
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if hub.host_ws is ws:
            hub.host_ws = None
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

@api_router.get("/")
async def root():
    return {"message": "OSSM Bridge API"}

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.access_codes.create_index("code", unique=True)
    await db.login_attempts.create_index("identifier", unique=True)
    hub.limits = await load_settings()
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@ossm.local").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "ossm-admin-2026")
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
