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
import bcrypt
import jwt

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

class CodeCreate(BaseModel):
    label: str = ""
    minutes: int = Field(gt=0, le=1440)

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
        self.lock = asyncio.Lock()

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
async def login(body: LoginInput, response: Response):
    email = body.email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    uid = str(user["_id"])
    token = create_access_token(uid, email)
    response.set_cookie("access_token", token, httponly=True, secure=True,
                        samesite="none", max_age=604800, path="/")
    return {"token": token, "user": {"id": uid, "email": email, "name": user.get("name", "Admin")}}

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
