"""
aioice runtime patch for Docker/NAT deployments.

WebRTC over Docker's default bridge network is broken because aioice:
  1. Gathers ICE candidates using the container's private IP (172.x.x.x), which
     the outside world can't reach.
  2. Binds host candidates on random ephemeral UDP ports, which we can't publish
     ahead of time in docker-compose.

This module fixes both by:
  * `STREAM_UDP_MIN` / `STREAM_UDP_MAX` (ints) — force aioice to bind host
    candidates within this UDP port range. Set the SAME range in docker-compose
    `ports:` (as UDP) so OBS on the host can reach them.
  * `STREAM_PUBLIC_IP` (str) — advertise this IP in host candidates instead of
    the container's private IP. For local OBS on the same Mac as Docker,
    `127.0.0.1` works. For LAN OBS, use the Mac's LAN IP. For remote OBS behind
    a domain, use the domain / public IP.

Apply once at startup by calling `stream_patch.apply()` BEFORE creating any
RTCPeerConnection.

If the env vars are unset the module is a no-op — preview / non-Docker deploys
keep working unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import socket

import aioice.ice as _ice
import aioice.turn as _turn
from aioice.candidate import Candidate, candidate_foundation, candidate_priority

logger = logging.getLogger("ossm-bridge.stream_patch")


def _int_env(name: str) -> int:
    try:
        return int(os.environ.get(name) or 0)
    except ValueError:
        return 0


UDP_MIN = _int_env("STREAM_UDP_MIN")
UDP_MAX = _int_env("STREAM_UDP_MAX")
PUBLIC_IP = (os.environ.get("STREAM_PUBLIC_IP") or "").strip()


async def _patched_get_component_candidates(self, component, addresses, timeout=5):
    """
    Drop-in replacement for `aioice.ice.Connection.get_component_candidates`
    that (a) binds host candidates to a UDP port in [UDP_MIN, UDP_MAX] and
    (b) advertises PUBLIC_IP as the candidate host if set.

    Falls back to aioice's default behaviour on any address where we run out
    of ports in the configured range.
    """
    candidates = []
    loop = asyncio.get_event_loop()
    host_protocols = []
    used_range = UDP_MIN > 0 and UDP_MAX >= UDP_MIN

    for address in addresses:
        transport = None
        protocol = None
        if used_range:
            ports = list(range(UDP_MIN, UDP_MAX + 1))
            random.shuffle(ports)
            last_err = None
            for port in ports:
                try:
                    transport, protocol = await loop.create_datagram_endpoint(
                        lambda: _ice.StunProtocol(self), local_addr=(address, port),
                    )
                    break
                except OSError as e:  # port busy / address invalid
                    last_err = e
                    continue
            if transport is None:
                self.__dict__.setdefault("_patch_bind_errors", []).append(str(last_err))
                logger.warning(
                    "aioice patch: could not bind %s in %d..%d (%s), falling back to random",
                    address, UDP_MIN, UDP_MAX, last_err,
                )
        if transport is None:
            try:
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: _ice.StunProtocol(self), local_addr=(address, 0),
                )
            except OSError as exc:
                logger.info("Could not bind to %s - %s", address, exc)
                continue

        sock = transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, _turn.UDP_SOCKET_BUFFER_SIZE,
            )
        host_protocols.append(protocol)

        bind_addr = protocol.transport.get_extra_info("sockname")
        advertised_host = PUBLIC_IP if PUBLIC_IP else bind_addr[0]
        protocol.local_candidate = Candidate(
            foundation=candidate_foundation("host", "udp", advertised_host),
            component=component,
            transport="udp",
            priority=candidate_priority(component, "host"),
            host=advertised_host,
            port=bind_addr[1],
            type="host",
        )
        if self._transport_policy == _ice.TransportPolicy.ALL:
            candidates.append(protocol.local_candidate)

    self._protocols += host_protocols

    # STUN / TURN path — verbatim from aioice so we don't regress those flows.
    tasks = []
    if self.stun_server:
        for protocol in host_protocols:
            import ipaddress
            if ipaddress.ip_address(bind_addr[0]).version == 4:
                tasks.append(asyncio.create_task(
                    _ice.server_reflexive_candidate(protocol, self.stun_server)))
    if self.turn_server:
        tasks.append(asyncio.create_task(_ice.relayed_candidate(
            component=component,
            protocol_factory=lambda: _ice.StunProtocol(self),
            turn_server=self.turn_server,
            turn_username=self.turn_username,
            turn_password=self.turn_password,
            turn_ssl=self.turn_ssl,
            turn_transport=self.turn_transport,
        )))
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            if task.exception() is None:
                candidate, protocol = task.result()
                candidates.append(candidate)
                if protocol is not None:
                    self._protocols.append(protocol)
        for task in pending:
            task.cancel()

    return candidates


_applied = False


def apply() -> None:
    """Install the patch. Safe to call more than once."""
    global _applied
    if _applied:
        return
    # H.264 codec widening is ALWAYS applied — it doesn't depend on the docker
    # NAT env vars and fixes the "Failed to set remote video description send
    # parameters" error we see with OBS Apple VT / high-profile publishers.
    widen_h264_codecs()

    if UDP_MIN <= 0 and UDP_MAX <= 0 and not PUBLIC_IP:
        logger.info("stream_patch: no STREAM_UDP_MIN/MAX/STREAM_PUBLIC_IP set — leaving aioice untouched")
        _applied = True
        return
    _ice.Connection.get_component_candidates = _patched_get_component_candidates
    logger.info(
        "stream_patch active: udp_port_range=%s..%s public_ip=%s",
        UDP_MIN or "-", UDP_MAX or "-", PUBLIC_IP or "-",
    )
    _applied = True


def rewrite_incoming_sdp(sdp: str) -> str:
    """
    Rewrite an incoming SDP offer's `127.0.0.1` candidates to a peer-reachable
    address, so the backend (inside Docker) can send its ICE checks back to
    OBS on the host.

    Controlled via env var `STREAM_PEER_IP`. If unset, returns SDP unchanged.
    """
    peer_ip = (os.environ.get("STREAM_PEER_IP") or "").strip()
    if not peer_ip:
        return sdp
    # Only touch host candidate lines (a=candidate: ... typ host) to avoid
    # accidentally breaking srflx/relay lines that legitimately carry 127.0.0.1
    # as related-address.
    out_lines = []
    for line in sdp.splitlines():
        if line.startswith("a=candidate:") and " typ host" in line and " 127.0.0.1 " in line:
            line = line.replace(" 127.0.0.1 ", f" {peer_ip} ", 1)
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if sdp.endswith("\n") else "")


def widen_h264_codecs() -> None:
    """
    aiortc only advertises H.264 Constrained Baseline profiles (`42001f`,
    `42e01f`). Real-world publishers (OBS's Apple VideoToolbox on macOS, the
    NVENC encoder, most browsers) frequently emit Main (`4d*`) or High
    (`640c*` / `64*`) profile SDPs, and negotiation then fails with:

        OperationError: Failed to set remote video description send parameters

    Since aiortc's H.264 decode path is ffmpeg, it can decode any profile
    fine — we just need to advertise the extra profile-level-ids so
    `find_common_codecs` succeeds. This adds Main and High baseline-3.1
    variants (both with matching RTX entries) to `aiortc.codecs.CODECS`.
    """
    try:
        from aiortc import codecs as _codecs
        from aiortc.rtcrtpparameters import RTCRtpCodecParameters
    except Exception:  # noqa: BLE001
        return

    video = _codecs.CODECS.get("video")
    if not video:
        return

    # Extra profile-level-ids to accept from remote peers.
    #   4d001f — Main       @ Level 3.1  (OBS x264 "main" profile, some NVENC)
    #   640c1f — Constrained High @ Level 3.1  (OBS Apple VT default on macOS)
    #   64001f — High       @ Level 3.1
    extra_profiles = ["4d001f", "640c1f", "64001f"]

    existing_ids = {
        str(c.parameters.get("profile-level-id", "")).lower()
        for c in video
        if c.mimeType.lower() == "video/h264"
    }

    next_pt = max((c.payloadType for c in video), default=100) + 1
    added = []
    for prof in extra_profiles:
        if prof.lower() in existing_ids:
            continue
        h264 = RTCRtpCodecParameters(
            mimeType="video/H264",
            clockRate=90000,
            payloadType=next_pt,
            parameters={
                "level-asymmetry-allowed": "1",
                "packetization-mode": "1",
                "profile-level-id": prof,
            },
        )
        video.append(h264)
        rtx = RTCRtpCodecParameters(
            mimeType="video/rtx",
            clockRate=90000,
            payloadType=next_pt + 1,
            parameters={"apt": next_pt},
        )
        video.append(rtx)
        added.append(prof)
        next_pt += 2

    if added:
        logger.info("stream_patch: added H.264 profile-level-ids to aiortc CODECS: %s", added)
