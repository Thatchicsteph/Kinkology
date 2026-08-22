import React, { useEffect, useRef, useState } from "react";
import { Video, VideoOff, Monitor, Camera, Loader2, RadioTower, Square } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";

/**
 * Browser-based WHIP publisher. Uses the browser's native RTCPeerConnection
 * (which handles RFC 8840 Link-header ICE servers correctly) so streaming
 * works without OBS. Owner picks camera+mic OR screen share, clicks Go Live,
 * and the browser negotiates a WebRTC session with the backend.
 */
export function BrowserPublisher() {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const streamRef = useRef(null);
  const resourceUrlRef = useRef(null);

  const [source, setSource] = useState("camera"); // "camera" | "screen"
  const [status, setStatus] = useState("idle");   // idle | preparing | connecting | live | error
  const [bitrateKbps, setBitrateKbps] = useState(0);
  const bitrateTimerRef = useRef(null);
  const lastStatsRef = useRef({ bytes: 0, ts: 0 });
  const iceRefreshTimerRef = useRef(null);

  useEffect(() => {
    return () => { void stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cleanup = async () => {
    if (bitrateTimerRef.current) { clearInterval(bitrateTimerRef.current); bitrateTimerRef.current = null; }
    if (iceRefreshTimerRef.current) { clearInterval(iceRefreshTimerRef.current); iceRefreshTimerRef.current = null; }
    if (pcRef.current) {
      try { pcRef.current.getSenders().forEach(s => { try { s.track && s.track.stop(); } catch { /* noop */ } }); } catch { /* noop */ }
      try { pcRef.current.close(); } catch { /* noop */ }
      pcRef.current = null;
    }
    if (streamRef.current) {
      try { streamRef.current.getTracks().forEach(t => t.stop()); } catch { /* noop */ }
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    // Best-effort DELETE so the backend releases the publisher slot immediately.
    const url = resourceUrlRef.current;
    resourceUrlRef.current = null;
    if (url) {
      try {
        await fetch(url, { method: "DELETE", credentials: "omit" });
      } catch { /* noop */ }
    }
  };

  const stop = async () => {
    await cleanup();
    setStatus("idle");
    setBitrateKbps(0);
    lastStatsRef.current = { bytes: 0, ts: 0 };
  };

  const start = async () => {
    setStatus("preparing");
    try {
      // 1) Pull ICE servers (STUN + Cloudflare TURN if configured).
      const { data: iceCfg } = await api.get("/stream/ice-servers");
      const iceServers = (iceCfg && iceCfg.iceServers) || [];

      // 2) Pull the current WHIP publish token (auth for POST /api/whip).
      let token = "";
      try {
        const { data: tok } = await api.get("/stream/token");
        token = (tok && tok.enabled ? (tok.token || "") : "").trim();
      } catch { /* token endpoint requires auth — main dashboard is authed already */ }

      // 3) Capture media.
      const media = source === "screen"
        ? await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 30 }, audio: true })
        : await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: true });
      streamRef.current = media;
      if (videoRef.current) {
        videoRef.current.srcObject = media;
        videoRef.current.muted = true;
        try { await videoRef.current.play(); } catch { /* autoplay may reject — user gesture already occurred */ }
      }

      // 4) Peer connection with our ICE servers.
      const pc = new RTCPeerConnection({ iceServers });
      pcRef.current = pc;

      for (const track of media.getTracks()) pc.addTrack(track, media);

      pc.addEventListener("connectionstatechange", () => {
        const s = pc.connectionState;
        if (s === "connected") setStatus("live");
        else if (s === "failed" || s === "disconnected" || s === "closed") {
          if (status !== "idle") toast.error(`Stream ${s}`);
          void stop();
        }
      });

      // 5) Offer + WHIP POST.
      const offer = await pc.createOffer({ offerToReceiveAudio: false, offerToReceiveVideo: false });
      await pc.setLocalDescription(offer);
      setStatus("connecting");

      // Wait for ICE gathering to complete (2s max) — non-trickle mode so all
      // candidates are in the offer we POST.
      await waitForIceGathering(pc, 2000);

      const backendBase = process.env.REACT_APP_BACKEND_URL || window.location.origin;
      const headers = { "Content-Type": "application/sdp" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const resp = await fetch(`${backendBase.replace(/\/+$/, "")}/api/whip`, {
        method: "POST",
        headers,
        body: pc.localDescription.sdp,
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(`WHIP ${resp.status}: ${detail.slice(0, 200) || resp.statusText}`);
      }
      resourceUrlRef.current = resp.headers.get("Location") || null;
      const answerSdp = await resp.text();
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

      // 6) Poll outbound video bitrate.
      lastStatsRef.current = { bytes: 0, ts: performance.now() };
      bitrateTimerRef.current = setInterval(async () => {
        if (!pcRef.current) return;
        const stats = await pcRef.current.getStats();
        let bytes = 0;
        stats.forEach((r) => { if (r.type === "outbound-rtp" && r.kind === "video") bytes += r.bytesSent || 0; });
        const now = performance.now();
        const last = lastStatsRef.current;
        if (last.ts) {
          const kbps = Math.max(0, ((bytes - last.bytes) * 8) / (now - last.ts));
          setBitrateKbps(Math.round(kbps));
        }
        lastStatsRef.current = { bytes, ts: now };
      }, 1000);

      // Cloudflare TURN allocations expire at ~600s and creds are typically
      // valid for 1h. Refresh both every 9 minutes and trigger an ICE restart
      // so long-running sessions never drop when the current allocation dies.
      iceRefreshTimerRef.current = setInterval(async () => {
        try {
          if (!pcRef.current) return;
          const { data } = await api.get("/stream/ice-servers");
          const fresh = (data && data.iceServers) || [];
          if (!fresh.length) return;
          pcRef.current.setConfiguration({ iceServers: fresh });
          pcRef.current.restartIce();
          // Push the renegotiated offer back to the WHIP resource so aiortc
          // sees the new ufrag/pwd and swaps its ICE agent state.
          const offer = await pcRef.current.createOffer({ iceRestart: true });
          await pcRef.current.setLocalDescription(offer);
          await waitForIceGathering(pcRef.current, 2000);
          const url = resourceUrlRef.current;
          if (url) {
            await fetch(url, {
              method: "PATCH",
              headers: { "Content-Type": "application/sdp" },
              body: pcRef.current.localDescription.sdp,
            }).catch(() => { /* server may not support offer PATCH — best-effort */ });
          }
        } catch (e) {
          console.warn("ICE refresh failed:", e);
        }
      }, 9 * 60 * 1000);

      toast.success("Live from browser");
    } catch (err) {
      console.error("browser publisher failed:", err);
      toast.error(err.message || "Could not start stream");
      await cleanup();
      setStatus("error");
    }
  };

  const busy = status === "preparing" || status === "connecting";
  const live = status === "live" || status === "connecting" || status === "preparing";

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="browser-publisher-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
          <RadioTower size={18} className="text-[var(--kink-purple)]" /> Publish From This Browser
        </h2>
        <span
          data-testid="browser-publisher-status"
          className={`inline-flex items-center gap-1.5 font-mono-data text-[10px] tracking-[0.15em] px-2 py-1 border ${
            status === "live"
              ? "border-[var(--kink-success,#4ade80)]/50 text-[var(--kink-success,#4ade80)]"
              : status === "error"
                ? "border-[var(--kink-danger)]/50 text-[var(--kink-danger)]"
                : "border-[var(--kink-overlay)] text-[var(--kink-muted)]"
          }`}
        >
          {status.toUpperCase()}
        </span>
      </div>

      <p className="text-[var(--kink-text-2)] text-sm mb-4">
        No OBS needed. Grants camera + mic (or a screen share) and pushes straight to the control page over WebRTC.
      </p>

      <div className="rounded overflow-hidden border border-[var(--kink-overlay)] mb-4 aspect-video bg-black">
        <video
          ref={videoRef}
          data-testid="browser-publisher-preview"
          className="w-full h-full object-contain"
          playsInline
          muted
        />
      </div>

      {!live && (
        <div className="flex gap-2 mb-3">
          <button
            type="button"
            onClick={() => setSource("camera")}
            data-testid="browser-publisher-source-camera"
            className={`flex-1 inline-flex items-center justify-center gap-1.5 border px-3 py-2 font-mono-data text-[11px] transition-colors ${
              source === "camera"
                ? "border-[var(--kink-purple)] text-[var(--kink-purple)]"
                : "border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50"
            }`}
          >
            <Camera size={13} /> CAMERA + MIC
          </button>
          <button
            type="button"
            onClick={() => setSource("screen")}
            data-testid="browser-publisher-source-screen"
            className={`flex-1 inline-flex items-center justify-center gap-1.5 border px-3 py-2 font-mono-data text-[11px] transition-colors ${
              source === "screen"
                ? "border-[var(--kink-purple)] text-[var(--kink-purple)]"
                : "border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50"
            }`}
          >
            <Monitor size={13} /> SCREEN + AUDIO
          </button>
        </div>
      )}

      {!live ? (
        <button
          onClick={start}
          disabled={busy}
          data-testid="browser-publisher-start"
          className="w-full inline-flex items-center justify-center gap-2 bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] py-2.5 active:scale-95 transition-transform disabled:opacity-40"
        >
          {busy ? <><Loader2 className="animate-spin" size={14} /> STARTING…</> : <><Video size={14} /> GO LIVE</>}
        </button>
      ) : (
        <button
          onClick={stop}
          data-testid="browser-publisher-stop"
          className="w-full inline-flex items-center justify-center gap-2 border border-[var(--kink-danger)] text-[var(--kink-danger)] font-display font-bold tracking-[0.1em] py-2.5 active:scale-95 transition-transform"
        >
          <Square size={14} /> STOP STREAM
        </button>
      )}

      {status === "live" && (
        <div className="mt-3 flex items-center justify-between font-mono-data text-[11px] text-[var(--kink-muted)]">
          <span>Bitrate</span>
          <span data-testid="browser-publisher-bitrate" className="text-[var(--kink-text-2)]">{bitrateKbps} kbps</span>
        </div>
      )}

      {status === "error" && (
        <p className="mt-3 font-mono-data text-[11px] text-[var(--kink-danger)] flex items-center gap-1.5">
          <VideoOff size={12} /> Could not start. Check camera permission + try again.
        </p>
      )}
    </div>
  );
}

function waitForIceGathering(pc, timeoutMs) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (done) return; done = true; pc.removeEventListener("icegatheringstatechange", onChange); resolve(); };
    const onChange = () => { if (pc.iceGatheringState === "complete") finish(); };
    pc.addEventListener("icegatheringstatechange", onChange);
    setTimeout(finish, timeoutMs);
  });
}
