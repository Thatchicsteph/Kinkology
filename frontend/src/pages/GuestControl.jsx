import React, { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, WS_BASE, fmtTime } from "@/lib/api";
import { ControlConsole } from "@/components/ControlConsole";
import { TimerDisplay } from "@/components/TimerDisplay";
import { Radio, Loader2, XCircle, Clock, Users } from "lucide-react";
import { toast } from "sonner";

export default function GuestControl() {
  const { code } = useParams();
  const navigate = useNavigate();
  const [phase, setPhase] = useState("checking"); // checking|invalid|connecting|waiting|active|ended
  const [meta, setMeta] = useState(null);
  const [snap, setSnap] = useState({ you: null, active: null, queue: [], host_connected: false });
  const wsRef = useRef(null);

  useEffect(() => {
    let active = true;
    api.get(`/access/${code}`).then(({ data }) => {
      if (!active) return;
      if (!data.valid) { setPhase("invalid"); return; }
      setMeta(data);
      connectWs();
    }).catch(() => active && setPhase("invalid"));
    return () => { active = false; if (wsRef.current) wsRef.current.close(); };
    // eslint-disable-next-line
  }, [code]);

  const connectWs = () => {
    setPhase("connecting");
    const ws = new WebSocket(`${WS_BASE}/api/ws/control/${code}`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "rejected") { setPhase("invalid"); return; }
      if (msg.type === "turn_ended" || msg.type === "expired") {
        toast.info(msg.reason === "time_up" ? "Your time is up." : "Your turn has ended.");
        setPhase("ended");
        return;
      }
      if (msg.type === "state") {
        setSnap(msg);
        setPhase(msg.you?.status === "active" ? "active" : "waiting");
      }
    };
    ws.onclose = () => setPhase((p) => (p === "ended" || p === "invalid" ? p : "ended"));
    ws.onerror = () => {};
  };

  const sendCommand = (cmd) => {
    if (wsRef.current && wsRef.current.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "command", cmd }));
    }
  };

  const Shell = ({ children }) => (
    <div className="relative z-10 min-h-screen flex flex-col max-w-lg mx-auto px-5 py-6">
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <Radio className="text-[var(--ossm-cyan)]" size={18} />
          <span className="font-display font-black tracking-[0.2em] text-sm">OSSM BRIDGE</span>
        </div>
        <span className="font-mono-data text-xs text-[var(--ossm-muted)]">CODE {code}</span>
      </header>
      {children}
    </div>
  );

  if (phase === "checking" || phase === "connecting") {
    return (
      <Shell>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-[var(--ossm-text-2)]">
          <Loader2 className="animate-spin text-[var(--ossm-cyan)]" size={32} />
          <p className="font-mono-data text-sm">Connecting to the bridge…</p>
        </div>
      </Shell>
    );
  }

  if (phase === "invalid") {
    return (
      <Shell>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center" data-testid="invalid-state">
          <XCircle className="text-[var(--ossm-danger)]" size={40} />
          <h2 className="font-display font-black uppercase tracking-[0.05em] text-xl">Code not valid</h2>
          <p className="text-[var(--ossm-text-2)] text-sm max-w-xs">
            This access code is invalid, revoked, or out of time. Ask the owner for a new one.
          </p>
          <button onClick={() => navigate("/")} className="mt-2 font-mono-data text-xs text-[var(--ossm-cyan)]">← BACK</button>
        </div>
      </Shell>
    );
  }

  if (phase === "ended") {
    return (
      <Shell>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center" data-testid="ended-state">
          <Clock className="text-[var(--ossm-cyan)]" size={40} />
          <h2 className="font-display font-black uppercase tracking-[0.05em] text-xl">Session ended</h2>
          <p className="text-[var(--ossm-text-2)] text-sm max-w-xs">Your control time has ended.</p>
          <button onClick={() => window.location.reload()} data-testid="reconnect-button" className="mt-2 font-mono-data text-xs text-[var(--ossm-cyan)]">RECONNECT →</button>
        </div>
      </Shell>
    );
  }

  if (phase === "waiting") {
    const pos = snap.you?.position ?? 0;
    return (
      <Shell>
        <div className="flex-1 flex flex-col items-center justify-center gap-6 text-center" data-testid="waiting-state">
          <div className="hud-panel px-8 py-10 w-full">
            <Users className="text-[var(--ossm-cyan)] mx-auto" size={32} />
            <p className="font-display text-xs tracking-[0.2em] text-[var(--ossm-text-2)] mt-4">YOU ARE IN THE QUEUE</p>
            <p className="font-mono-data font-extrabold text-6xl text-[var(--ossm-cyan)] text-glow-cyan mt-3" data-testid="queue-position">#{pos}</p>
            <p className="text-[var(--ossm-text-2)] text-sm mt-4">
              {snap.active ? (
                <>In control now: <span className="text-white">{snap.active.label}</span> · {fmtTime(snap.active.remaining_seconds)} left</>
              ) : (
                "Waiting for the device host…"
              )}
            </p>
            {!snap.host_connected && (
              <p className="font-mono-data text-xs text-[var(--ossm-danger)] mt-4">⚠ Device host offline</p>
            )}
          </div>
        </div>
      </Shell>
    );
  }

  // active
  return (
    <Shell>
      <div className="fade-up">
        <div className="hud-panel p-6 flex flex-col items-center">
          <TimerDisplay seconds={snap.you?.remaining_seconds ?? 0} />
          {!snap.host_connected && (
            <p className="font-mono-data text-xs text-[var(--ossm-danger)] mt-3">⚠ Device host offline — commands may not apply</p>
          )}
        </div>
        <div className="hud-panel p-6 mt-4">
          <ControlConsole onCommand={sendCommand} disabled={false} />
        </div>
      </div>
    </Shell>
  );
}
