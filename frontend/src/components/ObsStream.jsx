import React, { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";
import { Video, VideoOff, Volume2, VolumeX, Maximize2, Loader2 } from "lucide-react";

/**
 * WHEP-based OBS stream viewer.
 *
 * Polls /api/stream/status until a publisher is live, then opens a WebRTC
 * subscription against POST /api/whep. Automatically retries on network drops
 * and cleans up its RTCPeerConnection + Location resource on unmount.
 */
export function ObsStream({ compact = false }) {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const locationRef = useRef(null);
  const [status, setStatus] = useState({ publisher_connected: false, viewer_count: 0, tracks: [] });
  const [state, setState] = useState("idle"); // idle | connecting | live | error | waiting
  const [muted, setMuted] = useState(true);
  const [error, setError] = useState("");

  // Poll publisher presence
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${API}/stream/status`);
        const s = await r.json();
        if (cancelled) return;
        setStatus(s);
      } catch (_) { /* status polls fail silently */ }
    };
    tick();
    const iv = setInterval(tick, 2500);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const teardown = async () => {
    try {
      if (locationRef.current) {
        await fetch(locationRef.current, { method: "DELETE" }).catch(() => {});
        locationRef.current = null;
      }
    } catch (_) { /* teardown errors are safe to ignore */ }
    if (pcRef.current) {
      try { pcRef.current.close(); } catch (_) { /* pc already closed */ }
      pcRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  const connect = async () => {
    await teardown();
    setError("");
    setState("connecting");
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
    pcRef.current = pc;

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    const stream = new MediaStream();
    pc.ontrack = (ev) => {
      stream.addTrack(ev.track);
      if (videoRef.current && videoRef.current.srcObject !== stream) {
        videoRef.current.srcObject = stream;
      }
      setState("live");
    };
    pc.onconnectionstatechange = () => {
      if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        setState("waiting");
      }
    };

    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const res = await fetch(`${API}/whep`, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: offer.sdp,
      });
      if (!res.ok) {
        if (res.status === 409) {
          setState("waiting");
          setError("No live stream yet. Start OBS to begin.");
        } else {
          setState("error");
          setError(`WHEP handshake failed (${res.status})`);
        }
        await teardown();
        return;
      }
      const answer = await res.text();
      const loc = res.headers.get("Location");
      if (loc) locationRef.current = loc.startsWith("http") ? loc : `${window.location.origin}${loc}`;
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
    } catch (e) {
      setState("error");
      setError(e.message || "Could not connect to stream.");
      await teardown();
    }
  };

  // Auto-connect when publisher goes live, and drop when it goes offline.
  useEffect(() => {
    if (status.publisher_connected && state === "idle") {
      connect();
    }
    if (!status.publisher_connected && (state === "live" || state === "connecting")) {
      teardown();
      setState("idle");
    }
  }, [status.publisher_connected]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { teardown(); }, []);

  const toggleMute = () => {
    setMuted((m) => {
      if (videoRef.current) videoRef.current.muted = !m;
      return !m;
    });
  };
  const fullscreen = () => {
    const el = videoRef.current;
    if (!el) return;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitEnterFullscreen) el.webkitEnterFullscreen();
  };

  const label = (() => {
    if (state === "live") return "LIVE";
    if (state === "connecting") return "CONNECTING…";
    if (state === "error") return "ERROR";
    if (status.publisher_connected) return "STARTING…";
    return "OFFLINE";
  })();
  const live = state === "live";

  return (
    <div className={`hud-panel overflow-hidden ${compact ? "" : ""}`} data-testid="obs-stream-card">
      <div className="flex items-center justify-between px-4 pt-4">
        <h3 className="font-display font-black uppercase tracking-[0.08em] text-sm flex items-center gap-2">
          <Video size={16} className="text-[var(--kink-purple)]" /> OBS Stream
        </h3>
        <span
          data-testid="obs-stream-status"
          className={`inline-flex items-center gap-1.5 font-mono-data text-[10px] tracking-[0.15em] px-2 py-1 border ${
            live
              ? "border-[var(--kink-purple)]/50 text-[var(--kink-purple)]"
              : "border-[var(--kink-overlay)] text-[var(--kink-muted)]"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-[var(--kink-purple)] pulse-dot" : "bg-[var(--kink-muted)]"}`} />
          {label}
        </span>
      </div>

      <div className="relative mt-3 aspect-video bg-black">
        <video
          ref={videoRef}
          data-testid="obs-stream-video"
          autoPlay
          playsInline
          muted={muted}
          className="w-full h-full object-contain"
        />
        {!live && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6 bg-[rgba(0,0,0,0.6)]">
            {state === "connecting" ? (
              <Loader2 className="animate-spin text-[var(--kink-purple)]" size={26} />
            ) : (
              <VideoOff className="text-[var(--kink-muted)]" size={26} />
            )}
            <p className="font-mono-data text-xs text-[var(--kink-text-2)] max-w-xs">
              {state === "error"
                ? (error || "Could not connect to stream.")
                : status.publisher_connected
                  ? "Buffering the live feed…"
                  : "No OBS stream is being sent yet."}
            </p>
            {(state === "error" || state === "waiting") && (
              <button
                onClick={connect}
                data-testid="obs-stream-retry"
                className="font-mono-data text-[11px] tracking-[0.15em] border border-[var(--kink-overlay)] px-3 py-1.5 hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
              >
                RETRY
              </button>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <span className="font-mono-data text-[11px] text-[var(--kink-muted)] truncate">
          {live ? (
            <>tracks: <span className="text-[var(--kink-text-2)]">{status.tracks.join(", ") || "video"}</span> · viewers: <span className="text-[var(--kink-text-2)]">{status.viewer_count}</span></>
          ) : (
            "Waiting for a publisher…"
          )}
        </span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={toggleMute}
            data-testid="obs-stream-mute"
            title={muted ? "Unmute" : "Mute"}
            className="p-1.5 border border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
          >
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
          </button>
          <button
            onClick={fullscreen}
            data-testid="obs-stream-fullscreen"
            title="Fullscreen"
            className="p-1.5 border border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
          >
            <Maximize2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Owner-facing helper card: shows the WHIP endpoint to paste into OBS and
 * lets the owner copy it in one tap.
 */
export function ObsStreamSetup({ localUrl }) {
  const base = (localUrl || (typeof window !== "undefined" ? window.location.origin : "")).replace(/\/+$/, "");
  const whipUrl = `${base}/api/whip`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(whipUrl);
      if (window?.dispatchEvent) window.dispatchEvent(new CustomEvent("kinkology-toast", { detail: "WHIP URL copied" }));
    } catch (_) { /* clipboard may be blocked in insecure contexts */ }
  };
  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="obs-setup-card">
      <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2 mb-2">
        <Video size={18} className="text-[var(--kink-purple)]" /> OBS Stream Setup
      </h2>
      <p className="text-[var(--kink-text-2)] text-sm mb-4">
        Sub-second WebRTC ingest. In OBS 30+ pick <span className="text-white">Settings → Stream → Service: WHIP</span>, then paste the URL below. Leave the bearer token empty.
      </p>
      <div className="flex items-stretch gap-2 mb-3">
        <code
          data-testid="obs-whip-url"
          className="flex-1 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-xs sm:text-sm text-[var(--kink-text-2)] break-all"
        >
          {whipUrl}
        </code>
        <button
          onClick={copy}
          data-testid="obs-copy-whip"
          className="bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] px-4 text-xs active:scale-95 transition-transform"
        >
          COPY
        </button>
      </div>
      <ul className="font-mono-data text-[11px] text-[var(--kink-muted)] space-y-1 list-disc list-inside">
        <li>Use encoder <span className="text-[var(--kink-text-2)]">H.264 (x264 or NVENC)</span>, keyframe interval ≤ 2s.</li>
        <li>Bitrate ~2500-5000 kbps is plenty; opus audio is optional.</li>
        <li>Only one publisher at a time — starting a new stream replaces the previous.</li>
      </ul>
    </div>
  );
}
