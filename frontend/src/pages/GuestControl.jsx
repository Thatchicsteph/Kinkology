import React, { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, WS_BASE, fmtTime } from "@/lib/api";
import { ControlConsole } from "@/components/ControlConsole";
import { TimerDisplay } from "@/components/TimerDisplay";
import { ObsStream } from "@/components/ObsStream";
import { GuestToys } from "@/components/GuestToys";
import { Loader2, XCircle, Clock, Users } from "lucide-react";
import kinkologyMark from "@/assets/kinkology-mark.png";
import { toast } from "sonner";

const Shell = ({ code, wide = false, children }) => (
  <div
    className={`relative z-10 min-h-screen flex flex-col mx-auto px-4 sm:px-6 py-5 sm:py-8 ${
      wide ? "max-w-6xl w-full" : "max-w-lg"
    }`}
  >
    <header className="flex items-center justify-between mb-5 sm:mb-8">
      <div className="flex items-center gap-2">
        <img src={kinkologyMark} alt="Kinkology" style={{ height: 18, width: 18 }} className="rounded-sm" />
        <span className="font-display font-black tracking-[0.2em] text-sm">KINKOLOGY</span>
      </div>
      <span className="font-mono-data text-xs text-[var(--kink-muted)]">CODE {code}</span>
    </header>
    {children}
  </div>
);

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

  const sendToyCommand = (cmd) => {
    if (wsRef.current && wsRef.current.readyState === 1) {
      wsRef.current.send(JSON.stringify({ type: "toy_command", cmd }));
    }
  };

  if (phase === "checking" || phase === "connecting") {
    return (
      <Shell code={code}>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-[var(--kink-text-2)]">
          <Loader2 className="animate-spin text-[var(--kink-purple)]" size={32} />
          <p className="font-mono-data text-sm">Connecting to the bridge…</p>
        </div>
      </Shell>
    );
  }

  if (phase === "invalid") {
    return (
      <Shell code={code}>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center" data-testid="invalid-state">
          <XCircle className="text-[var(--kink-danger)]" size={40} />
          <h2 className="font-display font-black uppercase tracking-[0.05em] text-xl">Code not valid</h2>
          <p className="text-[var(--kink-text-2)] text-sm max-w-xs">
            This access code is invalid, revoked, or out of time. Ask the owner for a new one.
          </p>
          <button onClick={() => navigate("/")} className="mt-2 font-mono-data text-xs text-[var(--kink-purple)]">← BACK</button>
        </div>
      </Shell>
    );
  }

  if (phase === "ended") {
    return (
      <Shell code={code}>
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center" data-testid="ended-state">
          <Clock className="text-[var(--kink-purple)]" size={40} />
          <h2 className="font-display font-black uppercase tracking-[0.05em] text-xl">Session ended</h2>
          <p className="text-[var(--kink-text-2)] text-sm max-w-xs">Your control time has ended.</p>
          <button onClick={() => window.location.reload()} data-testid="reconnect-button" className="mt-2 font-mono-data text-xs text-[var(--kink-purple)]">RECONNECT →</button>
        </div>
      </Shell>
    );
  }

  if (phase === "waiting") {
    const pos = snap.you?.position ?? 0;
    return (
      <Shell code={code} wide>
        <div
          className="flex-1 grid gap-5 lg:gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:items-start"
          data-testid="waiting-state"
        >
          <ObsStream />
          <div className="hud-panel px-6 sm:px-10 py-10 sm:py-14 w-full text-center lg:sticky lg:top-6">
            <Users className="text-[var(--kink-purple)] mx-auto" size={32} />
            <p className="font-display text-xs tracking-[0.2em] text-[var(--kink-text-2)] mt-4">YOU ARE IN THE QUEUE</p>
            <p
              className="font-mono-data font-extrabold text-6xl sm:text-7xl lg:text-8xl text-[var(--kink-purple)] text-glow-purple mt-3"
              data-testid="queue-position"
            >
              #{pos}
            </p>
            <p className="text-[var(--kink-text-2)] text-sm mt-4">
              {snap.active ? (
                <>In control now: <span className="text-white">{snap.active.label}</span> · {fmtTime(snap.active.remaining_seconds)} left</>
              ) : (
                "Waiting for the device host…"
              )}
            </p>
            {!snap.host_connected && (
              <p className="font-mono-data text-xs text-[var(--kink-danger)] mt-4">⚠ Device host offline</p>
            )}
          </div>
        </div>
      </Shell>
    );
  }

  // active
  return (
    <Shell code={code} wide>
      <div className="fade-up grid gap-4 lg:gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:items-start">
        <div className="space-y-4 lg:sticky lg:top-6">
          <ObsStream />
          <div className="hud-panel p-5 sm:p-6 flex flex-col items-center">
            <TimerDisplay seconds={snap.you?.remaining_seconds ?? 0} />
            {!snap.host_connected && (
              <p className="font-mono-data text-xs text-[var(--kink-danger)] mt-3 text-center">
                ⚠ Device host offline — commands may not apply
              </p>
            )}
          </div>
        </div>
        <div className="hud-panel p-5 sm:p-6 space-y-5">
          <ControlConsole onCommand={sendCommand} disabled={false} autoStart limits={snap.limits} />
          {snap.toys?.available && (
            <GuestToys onCommand={sendToyCommand} activePattern={snap.toys?.pattern || null} />
          )}
        </div>
      </div>
    </Shell>
  );
}
