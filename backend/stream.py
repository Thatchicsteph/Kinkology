"""
OBS WHIP/WHEP video stream broker.

Accepts a single WHIP publisher (OBS 30+, GStreamer, etc.) and re-broadcasts
its video/audio tracks to multiple WHEP viewers (browser <video> tags) via
aiortc. Sub-second latency, no external media server required.

Endpoints (mounted under /api by server.py):
    POST   /api/whip         body: application/sdp (offer)  -> 201 with sdp answer
    DELETE /api/whip/{sid}   end the publisher session
    POST   /api/whep         body: application/sdp (offer)  -> 201 with sdp answer
    DELETE /api/whep/{sid}   end a viewer session
    GET    /api/stream/status                              -> {publisher, viewers, ...}

All endpoints follow the IETF WHIP/WHEP drafts closely enough for OBS's built-in
WHIP output to publish, and for a browser RTCPeerConnection to consume via WHEP.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Awaitable, Callable, Dict, List, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender
from fastapi import APIRouter, HTTPException, Request, Response

from stream_patch import rewrite_incoming_sdp

logger = logging.getLogger("ossm-bridge.stream")

# One MediaRelay is shared so a single publisher track can be subscribed to
# by any number of viewer peer connections without decode duplication.
_relay = MediaRelay()

# Server.py wires this on startup so we can check the persisted publish token
# without importing the mongo client (avoids a circular import).
_get_publish_token: Optional[Callable[[], Awaitable[str]]] = None


def set_publish_token_provider(provider: Callable[[], Awaitable[str]]) -> None:
    global _get_publish_token
    _get_publish_token = provider


async def _current_publish_token() -> str:
    if _get_publish_token is None:
        return ""
    try:
        return (await _get_publish_token()) or ""
    except Exception:  # noqa: BLE001
        return ""


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Some WHIP clients pass ?token= or ?auth= on the URL. Support both as a fallback.
    return (request.query_params.get("token") or request.query_params.get("auth") or "").strip()


class StreamHub:
    def __init__(self) -> None:
        self.publisher_pc: Optional[RTCPeerConnection] = None
        self.publisher_id: Optional[str] = None
        self.publisher_started_at: Optional[float] = None
        # We keep the ORIGINAL incoming tracks; per-viewer we subscribe via _relay.
        self.tracks: List = []                       # list of MediaStreamTrack (video/audio)
        self.viewers: Dict[str, RTCPeerConnection] = {}
        self.lock = asyncio.Lock()

    def has_publisher(self) -> bool:
        return self.publisher_pc is not None and len(self.tracks) > 0

    def status(self) -> dict:
        kinds = sorted({t.kind for t in self.tracks})
        return {
            "publisher_connected": self.has_publisher(),
            "publisher_id": self.publisher_id if self.has_publisher() else None,
            "publisher_started_at": self.publisher_started_at if self.has_publisher() else None,
            "tracks": kinds,
            "viewer_count": len(self.viewers),
        }

    async def close_publisher(self) -> None:
        pc, self.publisher_pc = self.publisher_pc, None
        self.publisher_id = None
        self.publisher_started_at = None
        self.tracks = []
        if pc is not None:
            try:
                await pc.close()
            except Exception:  # noqa: BLE001
                pass
        # Drop viewers too — nothing to serve without a source.
        viewers, self.viewers = list(self.viewers.items()), {}
        for _, vpc in viewers:
            try:
                await vpc.close()
            except Exception:  # noqa: BLE001
                pass

    async def close_viewer(self, sid: str) -> None:
        vpc = self.viewers.pop(sid, None)
        if vpc is not None:
            try:
                await vpc.close()
            except Exception:  # noqa: BLE001
                pass


hub = StreamHub()
router = APIRouter()


def _prefer_codec(pc: RTCPeerConnection, kind: str, mime: str) -> None:
    """Best-effort codec preference so viewers reliably decode H264 from OBS."""
    try:
        capabilities = RTCRtpSender.getCapabilities(kind)
        preferred = [c for c in capabilities.codecs if c.mimeType.lower() == mime.lower()]
        others = [c for c in capabilities.codecs if c.mimeType.lower() != mime.lower()]
        for transceiver in pc.getTransceivers():
            if transceiver.kind == kind:
                transceiver.setCodecPreferences(preferred + others)
    except Exception:  # noqa: BLE001
        pass


async def _read_sdp(request: Request) -> str:
    body = await request.body()
    sdp = body.decode("utf-8", errors="ignore").strip()
    if not sdp or "v=0" not in sdp:
        raise HTTPException(status_code=400, detail="Expected an SDP offer in the request body.")
    return sdp


@router.post("/whip", status_code=201)
async def whip_publish(request: Request) -> Response:
    """WHIP ingest — one publisher at a time. Replaces the current publisher.

    If an owner-set publish token is configured (`settings.stream_token`), the
    request MUST carry `Authorization: Bearer <token>` (or `?token=` as a
    fallback). Otherwise ingest is open — same behaviour as before this feature.
    """
    ip = request.client.host if request.client else "unknown"
    ct = request.headers.get("Content-Type", "?")
    has_auth = "yes" if request.headers.get("Authorization") else "no"
    logger.info("WHIP publish attempt from %s (Content-Type=%s, Authorization=%s)", ip, ct, has_auth)

    expected = await _current_publish_token()
    if expected:
        provided = _extract_bearer(request)
        if not provided or not secrets.compare_digest(provided, expected):
            logger.warning("WHIP publish from %s rejected: %s bearer token",
                           ip, "missing" if not provided else "wrong")
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid publish token. Set the WHIP bearer token in OBS.",
                headers={"WWW-Authenticate": 'Bearer realm="whip"'},
            )
    sdp = await _read_sdp(request)
    sdp = rewrite_incoming_sdp(sdp)
    logger.info("WHIP SDP offer from %s: %d bytes", ip, len(sdp))
    async with hub.lock:
        # Only one publisher slot — close the previous one on takeover.
        if hub.publisher_pc is not None:
            await hub.close_publisher()

        pc = RTCPeerConnection()
        sid = secrets.token_hex(8)
        hub.publisher_pc = pc
        hub.publisher_id = sid

        @pc.on("track")
        def on_track(track):  # noqa: ANN001
            logger.info("WHIP publisher %s added track: %s", sid, track.kind)
            hub.tracks.append(track)

            @track.on("ended")
            async def on_ended():
                if track in hub.tracks:
                    hub.tracks.remove(track)

        @pc.on("connectionstatechange")
        async def on_state():
            logger.info("WHIP publisher %s state: %s", sid, pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                if hub.publisher_pc is pc:
                    await hub.close_publisher()

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            answer = await pc.createAnswer()
        except Exception as e:  # noqa: BLE001
            logger.exception("WHIP %s failed to negotiate SDP from OBS: %s", sid, e)
            logger.info("WHIP %s offending SDP (%d bytes):\n%s", sid, len(sdp), sdp)
            await pc.close()
            hub.publisher_pc = None
            hub.publisher_id = None
            raise HTTPException(
                status_code=400,
                detail=f"aiortc rejected the SDP offer: {type(e).__name__}: {e}",
            ) from e
        await pc.setLocalDescription(answer)
        hub.publisher_started_at = time.time()

    # WHIP spec allows a relative Location, but some clients (incl. some OBS
    # builds) fail silently on relative resource URLs. Return an absolute URL
    # built from the incoming request so it works behind Caddy, ngrok, etc.
    base = str(request.base_url).rstrip("/")
    location = f"{base}/api/whip/{sid}"
    logger.info("WHIP publisher %s ready — Location=%s", sid, location)

    return Response(
        content=pc.localDescription.sdp,
        status_code=201,
        media_type="application/sdp",
        headers={
            "Location": location,
            "Access-Control-Expose-Headers": "Location, Link",
        },
    )


@router.get("/whip")
@router.head("/whip")
async def whip_probe() -> Response:
    """Reachability probe. WHIP clients that do a HEAD/GET first get a hint."""
    return Response(
        status_code=200,
        media_type="text/plain",
        content="WHIP ingest endpoint. POST an SDP offer with Content-Type: application/sdp.\n",
    )


@router.patch("/whip/{sid}")
async def whip_trickle(sid: str) -> Response:
    """Trickle-ICE PATCH from WHIP clients. aiortc gathers ICE non-trickle, so
    all candidates are already in the answer — we accept and no-op so newer
    clients (OBS 30.2+) don't error out when they try to trickle."""
    return Response(status_code=204)


@router.delete("/whip/{sid}")
async def whip_stop(sid: str) -> dict:
    async with hub.lock:
        if hub.publisher_id == sid:
            await hub.close_publisher()
    return {"ok": True}


@router.post("/whep", status_code=201)
async def whep_view(request: Request) -> Response:
    """WHEP viewer — subscribe to the currently ingested stream."""
    if not hub.has_publisher():
        raise HTTPException(status_code=409, detail="No live stream is being published right now.")
    sdp = await _read_sdp(request)
    sdp = rewrite_incoming_sdp(sdp)

    pc = RTCPeerConnection()
    sid = secrets.token_hex(8)
    hub.viewers[sid] = pc

    # Push a relayed subscription for each incoming publisher track.
    for src in list(hub.tracks):
        try:
            pc.addTrack(_relay.subscribe(src, buffered=False))
        except Exception as e:  # noqa: BLE001
            logger.warning("could not attach track to viewer %s: %s", sid, e)

    # Prefer H264 so browsers/OBS agree on a codec that decodes everywhere.
    _prefer_codec(pc, "video", "video/H264")

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await hub.close_viewer(sid)

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
    except (ValueError, TypeError) as e:
        await hub.close_viewer(sid)
        raise HTTPException(status_code=400, detail=f"Invalid SDP offer: {e}") from e

    base = str(request.base_url).rstrip("/")
    return Response(
        content=pc.localDescription.sdp,
        status_code=201,
        media_type="application/sdp",
        headers={
            "Location": f"{base}/api/whep/{sid}",
            "Access-Control-Expose-Headers": "Location, Link",
        },
    )


@router.patch("/whep/{sid}")
async def whep_trickle(sid: str) -> Response:
    return Response(status_code=204)


@router.delete("/whep/{sid}")
async def whep_stop(sid: str) -> dict:
    await hub.close_viewer(sid)
    return {"ok": True}


@router.options("/whip")
@router.options("/whep")
async def stream_options() -> Response:
    # Some WHIP clients (incl. OBS) pre-flight the ingest URL.
    return Response(status_code=204)


@router.get("/stream/status")
async def stream_status() -> dict:
    st = hub.status()
    st["publish_token_required"] = bool(await _current_publish_token())
    return st


async def shutdown() -> None:
    async with hub.lock:
        await hub.close_publisher()
