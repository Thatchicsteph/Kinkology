import React, { useEffect, useRef, useState } from "react";
import { api, API } from "@/lib/api";
import { Video, VideoOff, Volume2, VolumeX, Maximize2, Loader2, Copy, RefreshCw, ShieldCheck, ShieldOff, Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";

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
  const [iceState, setIceState] = useState("");
  const [needsTap, setNeedsTap] = useState(false); // iOS Safari autoplay-blocked
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
    setNeedsTap(false);
    setIceState("");
    setState("connecting");
    // Ask the backend for a fresh ICE server config (public STUN + Cloudflare
    // TURN if the owner has enabled it). Falls back to a static STUN pair so
    // the connect flow keeps working even if the endpoint is unreachable.
    let iceServers = [
      { urls: ["stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478"] },
    ];
    try {
      const r = await fetch(`${API}/stream/ice-servers`);
      if (r.ok) {
        const j = await r.json();
        if (Array.isArray(j.iceServers) && j.iceServers.length) {
          iceServers = j.iceServers;
        }
      }
    } catch (_) { /* keep static fallback */ }
    const pc = new RTCPeerConnection({
      iceServers,
      bundlePolicy: "max-bundle",
    });
    pcRef.current = pc;

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    const stream = new MediaStream();
    pc.ontrack = (ev) => {
      stream.addTrack(ev.track);
      const el = videoRef.current;
      if (el && el.srcObject !== stream) {
        el.srcObject = stream;
        // iOS Safari refuses to autoplay a media element that receives its
        // srcObject after mount unless we explicitly kick .play() AND it's
        // muted. Enforce both on the DOM node.
        el.muted = true;
        el.setAttribute("playsinline", "");
        el.playsInline = true;
        const p = el.play();
        if (p && typeof p.catch === "function") {
          p.catch(() => {
            // Autoplay blocked — most commonly iOS Safari. Show a Tap-to-Play
            // overlay so the next user gesture starts playback.
            setNeedsTap(true);
          });
        }
      }
    };
    pc.oniceconnectionstatechange = () => {
      setIceState(pc.iceConnectionState);
      if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
        setState("live");
      } else if (pc.iceConnectionState === "failed") {
        setState("waiting");
        setError("ICE could not reach the media server. If you're on cellular or a different network, the host may need to forward the WebRTC UDP ports (default 50000-50099).");
      }
    };
    pc.onconnectionstatechange = () => {
      if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        setState((s) => (s === "live" ? "waiting" : s));
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
      // Fallback: if ICE hasn't connected within 12s, surface the failure so
      // the user sees a useful message instead of an infinite "buffering".
      setTimeout(() => {
        if (pcRef.current === pc && pc.iceConnectionState !== "connected" && pc.iceConnectionState !== "completed") {
          setState("waiting");
          setError((prev) => prev || `ICE timed out in state \"${pc.iceConnectionState}\". If you're on cellular, the host must open UDP 50000-50099.`);
        }
      }, 12000);
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
      const el = videoRef.current;
      if (el) el.muted = !m ? true : false;
      // ^ inversion: new muted state = !current-muted
      return !m;
    });
  };
  const fullscreen = () => {
    const el = videoRef.current;
    if (!el) return;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitEnterFullscreen) el.webkitEnterFullscreen();
  };

  const kickPlay = () => {
    const el = videoRef.current;
    if (!el) return;
    el.muted = true; // stays muted so iOS accepts the gesture-driven play
    const p = el.play();
    if (p && typeof p.catch === "function") p.catch(() => {});
    setNeedsTap(false);
  };

  const label = (() => {
    if (needsTap) return "TAP TO PLAY";
    if (state === "live") return "LIVE";
    if (state === "connecting") return "CONNECTING…";
    if (iceState && ["checking", "new"].includes(iceState)) return "ICE " + iceState.toUpperCase();
    if (state === "error") return "ERROR";
    if (status.publisher_connected) return "STARTING…";
    return "OFFLINE";
  })();
  const live = state === "live" && !needsTap;

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
          webkit-playsinline="true"
          muted
          onClick={needsTap ? kickPlay : undefined}
          className="w-full h-full object-contain"
        />
        {needsTap && (
          <button
            onClick={kickPlay}
            data-testid="obs-stream-tap"
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center bg-[rgba(0,0,0,0.7)] hover:bg-[rgba(0,0,0,0.55)] transition-colors"
          >
            <div className="h-14 w-14 rounded-full bg-[var(--kink-purple)] flex items-center justify-center">
              <Video size={26} className="text-[var(--kink-base)]" />
            </div>
            <span className="font-display text-xs tracking-[0.15em] text-white">TAP TO PLAY</span>
            <span className="font-mono-data text-[10px] text-[var(--kink-muted)]">iOS blocks WebRTC autoplay</span>
          </button>
        )}
        {!live && !needsTap && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-6 bg-[rgba(0,0,0,0.6)]">
            {state === "connecting" || (status.publisher_connected && iceState && iceState !== "failed") ? (
              <Loader2 className="animate-spin text-[var(--kink-purple)]" size={26} />
            ) : (
              <VideoOff className="text-[var(--kink-muted)]" size={26} />
            )}
            <p className="font-mono-data text-xs text-[var(--kink-text-2)] max-w-xs">
              {state === "error" || (state === "waiting" && error)
                ? error
                : status.publisher_connected
                  ? (iceState ? `Negotiating (${iceState})…` : "Buffering the live feed…")
                  : "No OBS stream is being sent yet."}
            </p>
            {iceState && !live && (
              <span data-testid="obs-stream-ice-state" className="font-mono-data text-[10px] text-[var(--kink-muted)]">
                ICE: {iceState}
              </span>
            )}
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
 * Owner-facing helper card: shows the WHIP endpoint to paste into OBS, an
 * optional bearer publish token (view/generate/rotate/clear), and copy shortcuts.
 */
export function ObsStreamSetup({ localUrl }) {
  const base = (localUrl || (typeof window !== "undefined" ? window.location.origin : "")).replace(/\/+$/, "");
  const whipUrl = `${base}/api/whip`;

  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const { data } = await api.get("/stream/token");
      setToken(data.token || "");
      setEnabled(!!data.enabled);
    } catch (_) { /* not logged in yet */ }
  };
  useEffect(() => { load(); }, []);

  const generate = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/stream/token", {});
      setToken(data.token);
      setEnabled(true);
      setReveal(true);
      toast.success("New WHIP publish token generated");
    } catch (e) {
      toast.error("Could not generate token");
    } finally { setBusy(false); }
  };
  const disable = async () => {
    setBusy(true);
    try {
      await api.delete("/stream/token");
      setToken("");
      setEnabled(false);
      toast("Publish auth disabled", { icon: "⚠" });
    } catch (e) {
      toast.error("Could not disable token");
    } finally { setBusy(false); }
  };

  const copy = async (text, label = "Copied") => {
    try { await navigator.clipboard.writeText(text); toast.success(label); }
    catch (_) { toast.error("Clipboard blocked"); }
  };
  const mask = (t) => (t.length <= 6 ? "•".repeat(t.length) : `${t.slice(0, 3)}${"•".repeat(t.length - 6)}${t.slice(-3)}`);

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="obs-setup-card">
      <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2 mb-2">
        <Video size={18} className="text-[var(--kink-purple)]" /> OBS Stream Setup
      </h2>
      <p className="text-[var(--kink-text-2)] text-sm mb-4">
        Sub-second WebRTC ingest. In OBS 30+ pick <span className="text-white">Settings → Stream → Service: WHIP</span>, paste the URL below, and the bearer token if you turn auth on.
      </p>

      <label className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] block mb-1.5">WHIP URL</label>
      <div className="flex items-stretch gap-2 mb-4">
        <code
          data-testid="obs-whip-url"
          className="flex-1 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-xs sm:text-sm text-[var(--kink-text-2)] break-all"
        >
          {whipUrl}
        </code>
        <button
          onClick={() => copy(whipUrl, "WHIP URL copied")}
          data-testid="obs-copy-whip"
          className="bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] px-4 text-xs active:scale-95 transition-transform inline-flex items-center gap-1.5"
        >
          <Copy size={13} /> COPY
        </button>
      </div>

      {/* Publish token controls */}
      <div className="border-t border-[var(--kink-overlay)] pt-4 mt-2" data-testid="obs-token-section">
        <div className="flex items-center justify-between mb-3">
          <label className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] flex items-center gap-1.5">
            {enabled ? <ShieldCheck size={12} className="text-[var(--kink-purple)]" /> : <ShieldOff size={12} />}
            PUBLISH TOKEN
          </label>
          <span
            className={`font-mono-data text-[10px] tracking-[0.15em] px-2 py-0.5 border ${
              enabled
                ? "border-[var(--kink-purple)]/50 text-[var(--kink-purple)]"
                : "border-[var(--kink-overlay)] text-[var(--kink-muted)]"
            }`}
            data-testid="obs-token-status"
          >
            {enabled ? "AUTH ON" : "AUTH OFF"}
          </span>
        </div>

        {enabled ? (
          <>
            <div className="flex items-stretch gap-2 mb-3">
              <code
                data-testid="obs-token-value"
                className="flex-1 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-xs text-[var(--kink-text-2)] break-all"
              >
                {reveal ? token : mask(token)}
              </code>
              <button
                onClick={() => setReveal((r) => !r)}
                data-testid="obs-token-reveal"
                title={reveal ? "Hide" : "Reveal"}
                className="border border-[var(--kink-overlay)] px-3 hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
              >
                {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
              <button
                onClick={() => copy(token, "Publish token copied")}
                data-testid="obs-token-copy"
                title="Copy token"
                className="border border-[var(--kink-overlay)] px-3 hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
              >
                <Copy size={14} />
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={generate}
                disabled={busy}
                data-testid="obs-token-rotate"
                className="inline-flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-[11px] hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors disabled:opacity-40"
              >
                <RefreshCw size={13} /> ROTATE
              </button>
              <button
                onClick={disable}
                disabled={busy}
                data-testid="obs-token-disable"
                className="inline-flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-[11px] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] transition-colors disabled:opacity-40"
              >
                <ShieldOff size={13} /> DISABLE AUTH
              </button>
            </div>
            <p className="font-mono-data text-[11px] text-[var(--kink-muted)] mt-3">
              In OBS 30+: <span className="text-[var(--kink-text-2)]">Settings → Stream → Bearer Token</span> — paste the value above. Rotating invalidates the old token instantly.
            </p>
          </>
        ) : (
          <>
            <p className="font-mono-data text-[11px] text-[var(--kink-muted)] mb-3">
              Anyone who can reach this URL can publish. Turn on auth so only OBS with your bearer token gets in.
            </p>
            <button
              onClick={generate}
              disabled={busy}
              data-testid="obs-token-enable"
              className="w-full bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] py-2.5 active:scale-95 transition-transform disabled:opacity-50 inline-flex items-center justify-center gap-2"
            >
              <ShieldCheck size={14} /> GENERATE PUBLISH TOKEN
            </button>
          </>
        )}
      </div>

      <ul className="font-mono-data text-[11px] text-[var(--kink-muted)] space-y-1 list-disc list-inside mt-4">
        <li>Encoder <span className="text-[var(--kink-text-2)]">H.264 (x264/NVENC)</span>, keyframe interval ≤ 2s.</li>
        <li>~2500-5000 kbps is plenty; opus audio optional.</li>
        <li>Only one publisher at a time — a new stream replaces the old.</li>
      </ul>
    </div>
  );
}
