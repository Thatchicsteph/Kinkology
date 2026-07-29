import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import { useBleHost } from "@/hooks/useBleHost";
import { ControlConsole } from "@/components/ControlConsole";
import { LiveQueue } from "@/components/LiveQueue";
import { TwoFactorPanel } from "@/components/TwoFactorPanel";
import { fmtTime } from "@/lib/api";
import { webBluetoothSupported } from "@/lib/ossm";
import {
  Radio, LogOut, Bluetooth, BluetoothConnected, Power, SkipForward,
  Plus, Copy, Trash2, Ban, Clock, Activity, Ticket, Sliders,
} from "lucide-react";
import { toast } from "sonner";

function StatusPill({ ok, okText, offText }) {
  return (
    <span className={`inline-flex items-center gap-2 font-mono-data text-xs px-3 py-1.5 border ${
      ok ? "border-[var(--ossm-cyan)]/40 text-[var(--ossm-cyan)]" : "border-[var(--ossm-overlay)] text-[var(--ossm-muted)]"
    }`}>
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-[var(--ossm-cyan)] pulse-dot" : "bg-[var(--ossm-muted)]"}`} />
      {ok ? okText : offText}
    </span>
  );
}

export default function AdminDashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const ble = useBleHost();

  const [codes, setCodes] = useState([]);
  const [state, setState] = useState({ active: null, queue: [], queue_length: 0, host_connected: false, device_state: "" });
  const [label, setLabel] = useState("");
  const [minutes, setMinutes] = useState(10);
  const [showTest, setShowTest] = useState(false);
  const [limits, setLimits] = useState({ min_depth: 0, max_speed: 100 });
  const [savingLimits, setSavingLimits] = useState(false);
  const pollRef = useRef(null);

  const loadCodes = async () => {
    try { const { data } = await api.get("/codes"); setCodes(data); } catch (e) {}
  };
  const loadState = async () => {
    try { const { data } = await api.get("/session/state"); setState(data); } catch (e) {}
  };
  const loadLimits = async () => {
    try { const { data } = await api.get("/settings"); setLimits(data); } catch (e) {}
  };
  const saveLimits = async () => {
    setSavingLimits(true);
    try {
      const { data } = await api.put("/settings", {
        min_depth: Number(limits.min_depth), max_speed: Number(limits.max_speed),
      });
      setLimits(data);
      toast.success("Safety limits saved — enforced for all guests");
    } catch (e) { toast.error("Could not save limits"); }
    finally { setSavingLimits(false); }
  };

  useEffect(() => {
    loadCodes();
    loadState();
    loadLimits();
    pollRef.current = setInterval(loadState, 1000);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line
  }, []);

  const createCode = async (e) => {
    e.preventDefault();
    try {
      await api.post("/codes", { label, minutes: Number(minutes) });
      setLabel("");
      setMinutes(10);
      loadCodes();
      toast.success("Access code created");
    } catch (e) { toast.error("Could not create code"); }
  };

  const revoke = async (id) => { await api.post(`/codes/${id}/revoke`); loadCodes(); };
  const addMin = async (id) => { await api.post(`/codes/${id}/add-minutes`, { minutes: 10 }); loadCodes(); toast.success("+10 minutes"); };
  const del = async (id) => { await api.delete(`/codes/${id}`); loadCodes(); };
  const copyLink = (code) => {
    navigator.clipboard.writeText(`${window.location.origin}/c/${code}`);
    toast.success("Share link copied");
  };

  const stopAll = async () => { await api.post("/session/stop"); toast("Emergency stop sent", { icon: "⛔" }); };
  const skip = async () => { await api.post("/session/skip"); loadState(); toast("Skipped to next guest"); };

  const doLogout = async () => { await logout(); navigate("/admin/login"); };

  return (
    <div className="relative z-10 min-h-screen max-w-7xl mx-auto px-5 sm:px-8 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2.5">
          <Radio className="text-[var(--ossm-cyan)]" size={22} />
          <span className="font-display font-black tracking-[0.2em] text-lg">OSSM BRIDGE</span>
          <span className="font-mono-data text-xs text-[var(--ossm-muted)] ml-2 hidden sm:inline">CONTROL DECK</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="font-mono-data text-xs text-[var(--ossm-text-2)] hidden sm:inline">{user?.email}</span>
          <button onClick={doLogout} data-testid="logout-button" className="flex items-center gap-1.5 font-mono-data text-xs text-[var(--ossm-text-2)] hover:text-[var(--ossm-danger)] transition-colors">
            <LogOut size={14} /> LOGOUT
          </button>
        </div>
      </header>

      {/* Device Host bar */}
      <div className="hud-panel p-5 sm:p-6 mb-6">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div>
            <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
              <Bluetooth size={18} className="text-[var(--ossm-cyan)]" /> Device Host
            </h2>
            <p className="text-[var(--ossm-text-2)] text-sm mt-1">
              This browser holds the Bluetooth link to your OSSM and relays guest commands.
            </p>
            <div className="flex flex-wrap items-center gap-3 mt-3">
              <StatusPill ok={ble.connected} okText={`BLE · ${ble.deviceName}`} offText="BLE DISCONNECTED" />
              <StatusPill ok={ble.wsConnected} okText="BRIDGE ONLINE" offText="BRIDGE OFFLINE" />
              {ble.connected && state.device_state && (
                <span className="font-mono-data text-xs text-[var(--ossm-muted)]">STATE: {state.device_state}</span>
              )}
            </div>
            {!webBluetoothSupported() && (
              <p className="font-mono-data text-xs text-[var(--ossm-danger)] mt-3">
                ⚠ Web Bluetooth unavailable. Use Chrome, Edge, or Opera on desktop/Android.
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {ble.connected && (
              <button onClick={() => setShowTest((s) => !s)} data-testid="toggle-test-console" className="flex items-center gap-2 border border-[var(--ossm-overlay)] px-4 py-3 font-display text-xs tracking-[0.1em] hover:border-[var(--ossm-cyan)]/40 transition-colors">
                <Sliders size={16} /> {showTest ? "HIDE" : "TEST"} CONTROLS
              </button>
            )}
            {ble.connected ? (
              <button onClick={ble.disconnect} data-testid="disconnect-device-button" className="flex items-center gap-2 bg-[var(--ossm-danger)] text-white px-5 py-3 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform">
                <Power size={16} /> DISCONNECT
              </button>
            ) : (
              <button onClick={ble.connect} data-testid="connect-device-button" className="flex items-center gap-2 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] px-5 py-3 font-display font-bold tracking-[0.1em] glow-cyan active:scale-95 transition-transform">
                <BluetoothConnected size={16} /> CONNECT DEVICE
              </button>
            )}
          </div>
        </div>

        {ble.connected && showTest && (
          <div className="mt-6 pt-6 border-t border-[var(--ossm-overlay)] max-w-md" data-testid="owner-test-console">
            <p className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)] mb-4">OWNER TEST CONTROLS — DIRECT TO DEVICE</p>
            <ControlConsole onCommand={ble.writeCommand} limits={limits} />
          </div>
        )}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Live session monitor */}
        <section className="lg:col-span-2 space-y-6">
          <div className="hud-panel p-5 sm:p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
                <Activity size={18} className="text-[var(--ossm-cyan)]" /> Live Session
              </h2>
              <div className="flex gap-2">
                <button onClick={skip} disabled={!state.active} data-testid="skip-button" className="flex items-center gap-1.5 border border-[var(--ossm-overlay)] px-3 py-2 font-mono-data text-xs hover:border-[var(--ossm-cyan)]/40 transition-colors disabled:opacity-40">
                  <SkipForward size={14} /> SKIP
                </button>
                <button onClick={stopAll} data-testid="emergency-stop-button" className="flex items-center gap-1.5 bg-[var(--ossm-danger)] text-white px-3 py-2 font-mono-data text-xs font-bold active:scale-95 transition-transform">
                  <Power size={14} /> STOP
                </button>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3 mb-6">
              <Stat label="IN CONTROL" value={state.active ? (state.active.label || "Guest") : "—"} />
              <Stat label="TIME LEFT" value={state.active ? fmtTime(state.active.remaining_seconds) : "--:--"} mono />
              <Stat label="IN QUEUE" value={String(state.queue_length)} mono />
            </div>

            <LiveQueue active={state.active} queue={state.queue} />
          </div>
        </section>

        {/* Access codes */}
        <section className="space-y-6">
          <TwoFactorPanel />
          <div className="hud-panel p-5 sm:p-6" data-testid="safety-limits-card">
            <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2 mb-2">
              <Sliders size={18} className="text-[var(--ossm-cyan)]" /> Safety Limits
            </h2>
            <p className="text-[var(--ossm-text-2)] text-sm mb-5">Enforced server-side for every guest. No one can exceed these.</p>
            <div className="space-y-5">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">MINIMUM DEPTH</label>
                  <span className="font-mono-data text-lg font-bold text-[var(--ossm-cyan)]" data-testid="limit-min-depth-value">{limits.min_depth}</span>
                </div>
                <input
                  type="range" min={0} max={100} step={1} value={limits.min_depth}
                  onChange={(e) => setLimits((l) => ({ ...l, min_depth: Number(e.target.value) }))}
                  data-testid="limit-min-depth"
                  className="w-full accent-[var(--ossm-cyan)]"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">MAXIMUM SPEED</label>
                  <span className="font-mono-data text-lg font-bold text-[var(--ossm-danger)]" data-testid="limit-max-speed-value">{limits.max_speed}</span>
                </div>
                <input
                  type="range" min={0} max={100} step={1} value={limits.max_speed}
                  onChange={(e) => setLimits((l) => ({ ...l, max_speed: Number(e.target.value) }))}
                  data-testid="limit-max-speed"
                  className="w-full accent-[var(--ossm-danger)]"
                />
              </div>
              <button
                onClick={saveLimits}
                disabled={savingLimits}
                data-testid="save-limits-button"
                className="w-full bg-[var(--ossm-cyan)] text-[var(--ossm-base)] font-display font-bold tracking-[0.1em] py-3 active:scale-95 transition-transform disabled:opacity-50"
              >
                {savingLimits ? "SAVING…" : "SAVE LIMITS"}
              </button>
            </div>
          </div>

          <div className="hud-panel p-5 sm:p-6">
            <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2 mb-5">
              <Ticket size={18} className="text-[var(--ossm-cyan)]" /> New Access Code
            </h2>
            <form onSubmit={createCode} className="space-y-4" data-testid="create-code-form">
              <div>
                <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">GUEST LABEL (OPTIONAL)</label>
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Alex" data-testid="code-label-input"
                  className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-2.5 outline-none focus:border-[var(--ossm-cyan)] transition-colors" />
              </div>
              <div>
                <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">MINUTES OF CONTROL</label>
                <input type="number" min={1} max={1440} value={minutes} onChange={(e) => setMinutes(e.target.value)} data-testid="code-minutes-input"
                  className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-2.5 font-mono-data outline-none focus:border-[var(--ossm-cyan)] transition-colors" />
              </div>
              <button type="submit" data-testid="create-code-button" className="w-full flex items-center justify-center gap-2 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] font-display font-bold tracking-[0.1em] py-3 active:scale-95 transition-transform">
                <Plus size={16} /> GENERATE CODE
              </button>
            </form>
          </div>

          <div className="hud-panel p-5 sm:p-6">
            <h3 className="font-display text-xs tracking-[0.2em] text-[var(--ossm-text-2)] mb-4">ISSUED CODES ({codes.length})</h3>
            <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1" data-testid="codes-list">
              {codes.length === 0 && <p className="font-mono-data text-sm text-[var(--ossm-muted)] py-4 text-center">No codes yet.</p>}
              {codes.map((c) => (
                <div key={c.id} data-testid={`code-${c.code}`} className={`border p-3 ${c.revoked ? "border-[var(--ossm-overlay)] opacity-50" : "border-[var(--ossm-overlay)]"}`}>
                  <div className="flex items-center justify-between">
                    <span className="font-mono-data font-bold text-xl tracking-[0.15em] text-[var(--ossm-cyan)]">{c.code}</span>
                    {c.revoked && <span className="font-mono-data text-[10px] text-[var(--ossm-danger)] border border-[var(--ossm-danger)]/40 px-2 py-0.5">REVOKED</span>}
                  </div>
                  {c.label && <p className="text-sm text-[var(--ossm-text-2)] mt-1">{c.label}</p>}
                  <div className="flex items-center gap-2 mt-2 font-mono-data text-xs text-[var(--ossm-muted)]">
                    <Clock size={12} /> {fmtTime(c.remaining_seconds)} left / {Math.round(c.granted_seconds / 60)}m granted
                  </div>
                  <div className="flex flex-wrap gap-2 mt-3">
                    <IconBtn testid={`copy-${c.code}`} onClick={() => copyLink(c.code)} icon={Copy} text="LINK" />
                    <IconBtn testid={`addmin-${c.code}`} onClick={() => addMin(c.id)} icon={Plus} text="10M" />
                    {!c.revoked && <IconBtn testid={`revoke-${c.code}`} onClick={() => revoke(c.id)} icon={Ban} text="REVOKE" danger />}
                    <IconBtn testid={`delete-${c.code}`} onClick={() => del(c.id)} icon={Trash2} text="" danger />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, mono }) {
  return (
    <div className="bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-4">
      <p className="font-display text-[10px] tracking-[0.15em] text-[var(--ossm-muted)]">{label}</p>
      <p className={`mt-1.5 truncate ${mono ? "font-mono-data" : "font-display"} font-bold text-lg text-white`}>{value}</p>
    </div>
  );
}

function IconBtn({ onClick, icon: Icon, text, danger, testid }) {
  return (
    <button onClick={onClick} data-testid={testid} className={`flex items-center gap-1.5 border px-2.5 py-1.5 font-mono-data text-[11px] transition-colors ${
      danger ? "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-danger)] hover:text-[var(--ossm-danger)]"
             : "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-cyan)]/50 hover:text-[var(--ossm-cyan)]"
    }`}>
      <Icon size={13} /> {text}
    </button>
  );
}
