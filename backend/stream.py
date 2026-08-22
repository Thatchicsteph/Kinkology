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

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCIceCandidate
from aiortc.contrib.media import MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.sdp import candidate_from_sdp
from fastapi import APIRouter, HTTPException, Request, Response

import os

from stream_patch import rewrite_incoming_sdp

logger = logging.getLogger("ossm-bridge.stream")


def _ice_servers() -> List[RTCIceServer]:
    """Read STUN + optional TURN servers from env so aiortc can gather
    server-reflexive candidates. Without STUN, backend inside a Docker bridge
    network only ever advertises its unreachable container IP, which is why
    mobile clients over 4G/5G or on foreign networks can't complete ICE.

    Env vars (comma-separated URLs):
        STREAM_STUN_SERVERS  default: stun:stun.l.google.com:19302,stun:stun.cloudflare.com:3478
        STREAM_TURN_URL      optional, e.g. turn:turn.example.com:3478
        STREAM_TURN_USERNAME
        STREAM_TURN_PASSWORD
    """
    servers: List[RTCIceServer] = []
    stun_env = os.environ.get(
        "STREAM_STUN_SERVERS",
        "stun:stun.l.google.com:19302,stun:stun.cloudflare.com:3478",
    )
    stun_urls = [u.strip() for u in stun_env.split(",") if u.strip()]
    if stun_urls:
        servers.append(RTCIceServer(urls=stun_urls))
    turn_url = (os.environ.get("STREAM_TURN_URL") or "").strip()
    if turn_url:
        servers.append(RTCIceServer(
            urls=[turn_url],
            username=os.environ.get("STREAM_TURN_USERNAME") or None,
            credential=os.environ.get("STREAM_TURN_PASSWORD") or None,
        ))
    return servers


def _log_ice_config() -> None:
    """Print the effective ICE server list once at startup so operators
    can confirm STUN/TURN are wired up when debugging phone playback."""
    servers = _ice_servers()
    if not servers:
        logger.warning(
            "stream: NO ICE servers configured — mobile clients on foreign "
            "networks will fail. Set STREAM_STUN_SERVERS or leave the default."
        )
        return
    summary = []
    for s in servers:
        urls = s.urls if isinstance(s.urls, list) else [s.urls]
        for u in urls:
            if u.lower().startswith(("turn:", "turns:")):
                # Redact credentials so tail/journal aren't leaking secrets.
                summary.append(f"{u} (user={s.username or '-'} pass={'***' if s.credential else '-'})")
            else:
                summary.append(u)
    logger.info("stream ICE servers: %s", ", ".join(summary))


def _new_pc() -> RTCPeerConnection:
    """RTCPeerConnection with STUN/TURN pre-configured so ICE can traverse NAT."""
    return RTCPeerConnection(configuration=RTCConfiguration(iceServers=_ice_servers()))


async def _new_pc_with_extra_ice() -> RTCPeerConnection:
    """Same as _new_pc() but also injects any dynamically-configured TURN
    servers (currently: Cloudflare Calls). Awaits an async provider so a
    fresh per-session TURN credential is minted each call. Falls back to
    the static config if the provider is missing or errors."""
    servers = _ice_servers()
    if _get_extra_ice_servers is not None:
        try:
            extra = await _get_extra_ice_servers() or []
            for entry in extra:
                # entry is a dict from Cloudflare with `urls`, `username`, `credential`.
                urls = entry.get("urls")
                if not urls:
                    continue
                servers.append(RTCIceServer(
                    urls=urls if isinstance(urls, list) else [urls],
                    username=entry.get("username") or None,
                    credential=entry.get("credential") or None,
                ))
        except Exception as e:  # noqa: BLE001
            logger.warning("extra ICE provider failed: %s — using static ICE", e)
    return RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))

# Small TTL cache so we don't resolve the same Host header on every WHEP call.
# Value is (ip, expires_at); positive TTL 60s, negative TTL 5s so a transient
# resolver blip on our real deployment FQDN doesn't disable the IPv4 rewrite
# for a whole minute. Cache is capped to protect against attacker-supplied
# Host headers filling memory (see minor findings in iteration_15 report).
_host_ip_cache: Dict[str, tuple] = {}
_HOST_IP_TTL = 60.0
_HOST_IP_NEG_TTL = 5.0
_HOST_IP_CACHE_MAX = 256


def _cache_put(host: str, ip: Optional[str], now: float) -> None:
    ttl = _HOST_IP_TTL if ip else _HOST_IP_NEG_TTL
    if len(_host_ip_cache) >= _HOST_IP_CACHE_MAX:
        # Cheap eviction: drop all expired entries; if still full, drop oldest.
        expired = [k for k, (_ip, exp) in _host_ip_cache.items() if exp <= now]
        for k in expired:
            _host_ip_cache.pop(k, None)
        if len(_host_ip_cache) >= _HOST_IP_CACHE_MAX:
            oldest = min(_host_ip_cache.items(), key=lambda kv: kv[1][1])[0]
            _host_ip_cache.pop(oldest, None)
    _host_ip_cache[host] = (ip, now + ttl)


def _looks_like_ipv4(s: str) -> bool:
    if not s or s.count(".") != 3:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in s.split("."))
    except ValueError:
        return False


async def _resolve_viewer_host(request: Request) -> Optional[str]:
    """Pick the ICE-candidate host address for THIS viewer.

    Browsers only accept `typ host` candidates whose connection-address is an
    IP literal (RFC 8839 / draft-ietf-mmusic-mdns-ice-candidates); an FQDN like
    `tg30.ddns.net` is silently dropped, which is exactly the mobile-video
    breakage we hit. So:

      * If the Host header is an IPv4 literal (`192.168.1.42`), return it.
      * If it's `localhost` / `127.0.0.1`, return `127.0.0.1`.
      * If it's a domain, resolve it to an IPv4 with a short TTL cache
        (via loop.getaddrinfo so we don't stall other requests).
      * If resolution fails, return None so `rewrite_answer_candidates` leaves
        the answer SDP untouched (aioice's advertised IP wins).
    """
    raw = request.headers.get("host", "").strip()
    if not raw:
        return None
    # Strip port. Handles IPv6 bracket form `[::1]:8001` too.
    if raw.startswith("["):
        end = raw.find("]")
        host = raw[1:end] if end > 0 else raw
    else:
        host = raw.split(":", 1)[0]
    host = host.strip().lower()
    if not host:
        return None
    if host in ("localhost", "127.0.0.1", "::1"):
        return "127.0.0.1"
    if _looks_like_ipv4(host):
        return host
    # Cached async DNS lookup — never blocks the event loop.
    now = time.time()
    cached = _host_ip_cache.get(host)
    if cached and cached[1] > now:
        return cached[0]
    try:
        import socket as _socket
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=_socket.AF_INET, type=_socket.SOCK_DGRAM),
            timeout=1.5,
        )
        ip = infos[0][4][0] if infos else None
    except Exception:  # noqa: BLE001
        ip = None
    _cache_put(host, ip, now)
    return ip


def rewrite_answer_candidates(sdp: str, target_host: Optional[str]) -> str:
    """Rewrite every `a=candidate: ... typ host` line to advertise `target_host`.

    Only touches HOST candidates — leaves srflx / relay lines alone so their
    `raddr` and remote-transport hints stay intact. Preserves the original
    line-ending style (CRLF vs LF) since RFC 4566 mandates CRLF and strict
    SDP parsers reject LF-terminated lines. Idempotent; safe with
    `target_host=None` (returns SDP unchanged).
    """
    if not target_host:
        return sdp
    # Detect the terminator so we round-trip CRLF vs LF exactly.
    eol = "\r\n" if "\r\n" in sdp else "\n"
    parts = sdp.split(eol)
    for i, line in enumerate(parts):
        if line.startswith("a=candidate:") and " typ host" in line:
            fields = line.split(" ", 6)
            if len(fields) >= 6:
                fields[4] = target_host
                parts[i] = " ".join(fields)
    return eol.join(parts)

# One MediaRelay is shared so a single publisher track can be subscribed to
# by any number of viewer peer connections without decode duplication.
_relay = MediaRelay()

# Server.py wires this on startup so we can check the persisted publish token
# without importing the mongo client (avoids a circular import).
_get_publish_token: Optional[Callable[[], Awaitable[str]]] = None
# Same story for extra ICE servers (Cloudflare TURN configured via admin UI).
_get_extra_ice_servers: Optional[Callable[[], Awaitable[list]]] = None


def set_publish_token_provider(provider: Callable[[], Awaitable[str]]) -> None:
    global _get_publish_token
    _get_publish_token = provider


def set_extra_ice_provider(provider: Callable[[], Awaitable[list]]) -> None:
    global _get_extra_ice_servers
    _get_extra_ice_servers = provider


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


def _absolute_url(request: Request, path: str) -> str:
    """Build absolute URL for the Location header. Behind a reverse proxy like
    Caddy, uvicorn sees http:// on the internal hop, so `request.base_url` is
    wrong for HTTPS deployments. Honor X-Forwarded-Proto / X-Forwarded-Host so
    OBS's PATCH/DELETE to the returned Location URL don't get 308-redirected
    (OBS refuses to follow 308 on state-changing methods).
    """
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}{path}"


async def _current_extra_ice_servers() -> list:
    """Fetch dynamic (Cloudflare) ICE-server dicts for advertising to the client.
    Returns a list of {"urls": [...], "username": "...", "credential": "..."}.
    """
    if _get_extra_ice_servers is None:
        return []
    try:
        return (await _get_extra_ice_servers()) or []
    except Exception:  # noqa: BLE001
        return []


def _build_ice_link_headers(extra: list) -> List[str]:
    """Build a list of RFC 8840 `Link:` header VALUES advertising ICE servers
    to WHIP clients. Per the WHIP draft §4.4.4 each ICE server must be its own
    separate `Link:` header field (NOT comma-joined) — OBS's libwebrtc parser
    rejects comma-separated Link values.

    Includes static STUN plus any dynamic (Cloudflare) TURN/STUN URLs.

    Without these headers OBS gathers only its private LAN host candidates and
    can never connect through the backend's TURN relay.
    """
    entries: List[str] = []

    def _add(url: str, username: str = "", credential: str = "") -> None:
        # Escape any embedded quotes per RFC 8288.
        safe_u = username.replace('"', '\\"') if username else ""
        safe_c = credential.replace('"', '\\"') if credential else ""
        parts = [f'<{url}>', 'rel="ice-server"']
        if username or credential:
            parts.append(f'username="{safe_u}"')
            parts.append(f'credential="{safe_c}"')
            parts.append('credential-type="password"')
        entries.append("; ".join(parts))

    # Static STUN — same defaults as _ice_servers().
    stun_env = os.environ.get(
        "STREAM_STUN_SERVERS",
        "stun:stun.l.google.com:19302,stun:stun.cloudflare.com:3478",
    )
    for u in (s.strip() for s in stun_env.split(",")):
        if u:
            _add(u)

    # Dynamic (Cloudflare) TURN — one Link entry per URL sharing the credential.
    for srv in extra:
        urls = srv.get("urls") if isinstance(srv, dict) else None
        if not urls:
            continue
        username = srv.get("username") or ""
        credential = srv.get("credential") or ""
        for u in (urls if isinstance(urls, list) else [urls]):
            _add(u, username, credential)

    return entries


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

        pc = await _new_pc_with_extra_ice()
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
    # Honor X-Forwarded-Proto so PATCH/DELETE from OBS don't hit a Caddy 308
    # HTTPS-redirect (OBS refuses to follow 308 on state-changing methods,
    # which silently drops every trickle-ICE candidate).
    location = _absolute_url(request, f"/api/whip/{sid}")
    # Advertise ICE candidates at the exact host the client used to reach us,
    # so OBS on the Mac gets `127.0.0.1` and LAN/remote clients get their host.
    answer_sdp = rewrite_answer_candidates(pc.localDescription.sdp, await _resolve_viewer_host(request))
    _cands = [ln for ln in answer_sdp.splitlines() if ln.startswith("a=candidate:")]
    logger.info("WHIP publisher %s answer SDP candidates (%d):\n  %s", sid, len(_cands), "\n  ".join(_cands) or "(none)")
    logger.info("WHIP publisher %s ready — Location=%s", sid, location)

    # Advertise ICE servers back to OBS via RFC 8840 Link headers. Without this,
    # OBS libwebrtc only gathers its own LAN host candidates and can never reach
    # the backend's TURN relay candidate. Per WHIP draft §4.4.4 each server must
    # be its own Link header — OBS rejects comma-joined values.
    link_values = _build_ice_link_headers(await _current_extra_ice_servers())

    resp = Response(
        content=answer_sdp,
        status_code=201,
        media_type="application/sdp",
        headers={
            "Location": location,
            "Access-Control-Expose-Headers": "Location, Link",
        },
    )
    for v in link_values:
        resp.raw_headers.append((b"link", v.encode("latin-1")))
    return resp


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
async def whip_trickle(sid: str, request: Request) -> Response:
    """Trickle-ICE PATCH from WHIP clients. OBS 30.2+ sends its offer with
    no ICE candidates and trickles them here as an SDP fragment. Without
    processing these the backend never learns OBS's srflx/relay candidates
    and every ICE check pair fails."""
    body = (await request.body()).decode("utf-8", errors="ignore")
    if not body.strip():
        return Response(status_code=204)

    async with hub.lock:
        if hub.publisher_id != sid or hub.publisher_pc is None:
            raise HTTPException(status_code=404, detail="Unknown publisher session")
        pc = hub.publisher_pc

    current_mid: Optional[str] = None
    media_index = -1
    added = 0
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            media_index += 1
            current_mid = None
        elif line.startswith("a=mid:"):
            current_mid = line[6:].strip()
        elif line.startswith("a=end-of-candidates"):
            continue
        elif line.startswith("a=candidate:"):
            try:
                cand = candidate_from_sdp(line[2:])  # strip leading "a="
                cand.sdpMid = current_mid
                if media_index >= 0:
                    cand.sdpMLineIndex = media_index
                await pc.addIceCandidate(cand)
                added += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("WHIP %s trickle candidate rejected (%s): %s", sid, e, line)
    if added:
        logger.info("WHIP %s trickled %d ICE candidate(s)", sid, added)
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

    pc = await _new_pc_with_extra_ice()
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
    answer_sdp = rewrite_answer_candidates(pc.localDescription.sdp, await _resolve_viewer_host(request))
    link_values = _build_ice_link_headers(await _current_extra_ice_servers())
    resp = Response(
        content=answer_sdp,
        status_code=201,
        media_type="application/sdp",
        headers={
            "Location": _absolute_url(request, f"/api/whep/{sid}"),
            "Access-Control-Expose-Headers": "Location, Link",
        },
    )
    for v in link_values:
        resp.raw_headers.append((b"link", v.encode("latin-1")))
    return resp


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
    """Preflight for WHIP/WHEP clients (incl. OBS). Advertises ICE servers via
    RFC 8840 Link headers so clients can configure their PeerConnection ICE
    servers BEFORE the offer/answer round-trip. Without these headers OBS
    only gathers its private LAN host candidates."""
    link_values = _build_ice_link_headers(await _current_extra_ice_servers())
    resp = Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Methods": "POST, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Expose-Headers": "Location, Link",
        },
    )
    for v in link_values:
        resp.raw_headers.append((b"link", v.encode("latin-1")))
    return resp


@router.get("/stream/status")
async def stream_status() -> dict:
    st = hub.status()
    st["publish_token_required"] = bool(await _current_publish_token())
    return st


async def shutdown() -> None:
    async with hub.lock:
        await hub.close_publisher()
