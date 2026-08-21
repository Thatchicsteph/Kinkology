import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { WS_BASE, fmtTime } from "@/lib/api";
import { Sparkline } from "@/components/Sparkline";
import { Gauge, Ruler, Waves, Move3d, Activity, Heart } from "lucide-react";
import kinkologyMark from "@/assets/kinkology-mark.png";

const METRICS = [
  { key: "speed", label: "SPEED", color: "#FF2A5F", icon: Gauge },
  { key: "depth", label: "DEPTH", color: "#C7C9D1", icon: Move3d },
  { key: "stroke", label: "STROKE", color: "#FFB020", icon: Ruler },
  { key: "sensation", label: "SENSATION", color: "#A855F7", icon: Waves },
];

const CAP = 120; // rolling window points (~60s at 500ms)
const HR_COLOR = "#FF4D6D";
const HR_TARGET_COLOR = "#C7C9D1";

export default function Overlay() {
  const [params] = useSearchParams();
  const transparent = params.has("transparent");

  const [frame, setFrame] = useState({
    speed: 0, depth: 0, stroke: 0, sensation: 0,
    run_seconds: 0, session_seconds: 0, running: false,
    controller: null, host_connected: false,
    hr_bpm: 0, hr_connected: false, hr_cutoff: 0, hr_over: false,
    hr_target: 0, hr_sync_enabled: false,
  });
  const [history, setHistory] = useState({ speed: [], depth: [], stroke: [], sensation: [], hr: [] });
  const [connected, setConnected] = useState(false);
  const latest = useRef(frame);
  latest.current = frame;

  // WebSocket: receive live telemetry frames
  useEffect(() => {
    let ws;
    let retry;
    const connect = () => {
      ws = new WebSocket(`${WS_BASE}/api/ws/overlay`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); retry = setTimeout(connect, 1500); };
      ws.onerror = () => {};
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "telemetry") setFrame(msg);
        } catch (e) {}
      };
    };
    connect();
    return () => { if (retry) clearTimeout(retry); if (ws) ws.close(); };
  }, []);

  // Sample latest values into rolling history at a fixed cadence for smooth graphs
  useEffect(() => {
    const iv = setInterval(() => {
      const f = latest.current;
      setHistory((h) => {
        const push = (arr, v) => {
          const next = arr.concat(v);
          return next.length > CAP ? next.slice(next.length - CAP) : next;
        };
        return {
          speed: push(h.speed, f.speed),
          depth: push(h.depth, f.depth),
          stroke: push(h.stroke, f.stroke),
          sensation: push(h.sensation, f.sensation),
          hr: push(h.hr, f.hr_bpm || 0),
        };
      });
    }, 500);
    return () => clearInterval(iv);
  }, []);

  return (
    <div
      data-testid="overlay-root"
      className="min-h-screen w-full p-6 sm:p-8 font-sans"
      style={{ background: transparent ? "transparent" : "var(--kink-base)" }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <img src={kinkologyMark} alt="Kinkology" style={{ height: 22, width: 22 }} className="rounded-sm" />
          <span className="font-display font-black tracking-[0.25em] text-lg">KINKOLOGY LIVE</span>
        </div>
        <div className="flex items-center gap-5">
          {frame.controller && (
            <span className="font-mono-data text-sm text-[var(--kink-text-2)]" data-testid="overlay-controller">
              CTRL: <span className="text-white">{frame.controller}</span>
            </span>
          )}
          <span className="flex items-center gap-2 font-mono-data text-xs" data-testid="overlay-status">
            <span className={`h-2.5 w-2.5 rounded-full ${connected && frame.host_connected ? "bg-[var(--kink-purple)] pulse-dot" : "bg-[var(--kink-danger)]"}`} />
            {connected ? (frame.host_connected ? "LIVE" : "NO DEVICE") : "OFFLINE"}
          </span>
        </div>
      </div>

      {/* Run time banner */}
      <div className="hud-panel px-6 py-5 mb-6 flex flex-wrap items-center justify-between gap-4" style={transparent ? { background: "rgba(22,22,24,0.72)", backdropFilter: "blur(10px)" } : {}}>
        <div className="flex items-center gap-3">
          <Activity size={20} className={frame.running ? "text-[var(--kink-purple)]" : "text-[var(--kink-muted)]"} />
          <div>
            <p className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)]">RUN TIME</p>
            <p className="font-mono-data font-extrabold text-4xl sm:text-5xl tabular-nums leading-none text-[var(--kink-purple)] text-glow-purple" data-testid="overlay-runtime">
              {fmtTime(frame.run_seconds)}
            </p>
          </div>
        </div>
        <div className="text-right">
          <p className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)]">SESSION</p>
          <p className="font-mono-data font-bold text-2xl tabular-nums text-white" data-testid="overlay-session">{fmtTime(frame.session_seconds)}</p>
        </div>
      </div>

      {/* Heart rate */}
      <div
        data-testid="overlay-hr"
        className="hud-panel px-6 py-5 mb-6"
        style={transparent ? { background: "rgba(22,22,24,0.72)", backdropFilter: "blur(10px)" } : {}}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <Heart
              size={22}
              style={{ color: HR_COLOR }}
              className={frame.hr_connected && frame.hr_bpm > 0 ? "hr-pulse" : ""}
              fill={frame.hr_connected && frame.hr_bpm > 0 ? "currentColor" : "none"}
            />
            <span className="font-display text-xs tracking-[0.18em]" style={{ color: HR_COLOR }}>HEART RATE</span>
            {!frame.hr_connected && (
              <span className="font-mono-data text-[11px] text-[var(--kink-muted)]" data-testid="overlay-hr-waiting">waiting for monitor…</span>
            )}
            {frame.hr_over && (
              <span className="font-mono-data text-[11px] tracking-[0.12em] px-2 py-0.5 border border-[var(--kink-hr)] text-[var(--kink-hr)] pulse-dot" data-testid="overlay-hr-cutoff">
                CUTOFF {frame.hr_cutoff}
              </span>
            )}
            {frame.hr_sync_enabled && frame.hr_target > 0 && (
              <span
                className="font-mono-data text-[11px] tracking-[0.12em] px-2 py-0.5 border"
                style={{ borderColor: HR_TARGET_COLOR, color: HR_TARGET_COLOR }}
                data-testid="overlay-hr-target-badge"
              >
                TARGET {frame.hr_target}
              </span>
            )}
          </div>
          <div className="text-right">
            <span className="font-mono-data font-extrabold text-4xl tabular-nums" style={{ color: HR_COLOR }} data-testid="overlay-hr-value">
              {frame.hr_connected ? frame.hr_bpm : "--"}
              <span className="text-sm text-[var(--kink-muted)] ml-1">BPM</span>
            </span>
            {frame.hr_sync_enabled && frame.hr_target > 0 && (
              <p className="font-mono-data text-xs tabular-nums" style={{ color: HR_TARGET_COLOR }} data-testid="overlay-hr-target-value">
                target {frame.hr_target} BPM
              </p>
            )}
          </div>
        </div>
        <Sparkline
          data={history.hr}
          color={HR_COLOR}
          id="hr"
          height={72}
          max={200}
          refValue={frame.hr_sync_enabled && frame.hr_target > 0 ? frame.hr_target : null}
          refColor={HR_TARGET_COLOR}
        />
      </div>

      {/* Metric graphs */}
      <div className="grid sm:grid-cols-2 gap-5">
        {METRICS.map((m) => (
          <div
            key={m.key}
            data-testid={`overlay-metric-${m.key}`}
            className="hud-panel p-5"
            style={transparent ? { background: "rgba(22,22,24,0.72)", backdropFilter: "blur(10px)" } : {}}
          >
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <m.icon size={16} style={{ color: m.color }} />
                <span className="font-display text-xs tracking-[0.18em]" style={{ color: m.color }}>{m.label}</span>
              </div>
              <span className="font-mono-data font-extrabold text-3xl tabular-nums" style={{ color: m.color }} data-testid={`overlay-value-${m.key}`}>
                {frame[m.key]}
                <span className="text-sm text-[var(--kink-muted)] ml-0.5">%</span>
              </span>
            </div>
            <Sparkline data={history[m.key]} color={m.color} id={m.key} height={72} />
          </div>
        ))}
      </div>
    </div>
  );
}
