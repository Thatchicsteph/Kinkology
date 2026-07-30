import React, { useEffect, useRef, useState, useCallback } from "react";
import { HeartPulse, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

const STORE_KEY = "ossm_hr_sync";
const DEFAULTS = { restingBpm: 60, peakBpm: 150, minSpeed: 10, maxSpeed: 100, rampUp: 25, rampDown: 50 };
const MAXES = { restingBpm: 240, peakBpm: 240, minSpeed: 100, maxSpeed: 100, rampUp: 100, rampDown: 100 };
const TICK_MS = 150;

function loadCfg() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY));
    if (raw && typeof raw === "object") return { ...DEFAULTS, ...raw };
  } catch (e) {}
  return { ...DEFAULTS };
}

export function bpmToSpeed(bpm, cfg, maxCap) {
  const { restingBpm, peakBpm, minSpeed, maxSpeed } = cfg;
  const span = peakBpm - restingBpm;
  let t = span === 0 ? 0 : (bpm - restingBpm) / span;
  t = Math.max(0, Math.min(1, t));
  let sp = Math.round(minSpeed + t * (maxSpeed - minSpeed));
  sp = Math.max(0, Math.min(100, sp));
  return Math.min(sp, Math.max(0, maxCap));
}

export function HeartRateSync({ hr, ble, maxCap, cutoff = 0 }) {
  const [cfg, setCfg] = useState(loadCfg);
  const [enabled, setEnabled] = useState(false);
  const [applied, setApplied] = useState(0);

  const ready = hr.connected && ble.connected;
  const overCutoff = cutoff > 0 && hr.connected && hr.bpm >= cutoff;
  const target = overCutoff ? 0 : bpmToSpeed(hr.bpm, cfg, maxCap);

  useEffect(() => { localStorage.setItem(STORE_KEY, JSON.stringify(cfg)); }, [cfg]);

  // Latest values for the ramp ticker (avoid stale closures)
  const bpmRef = useRef(hr.bpm); bpmRef.current = hr.bpm;
  const cfgRef = useRef(cfg); cfgRef.current = cfg;
  const maxCapRef = useRef(maxCap); maxCapRef.current = maxCap;
  const cutoffRef = useRef(cutoff); cutoffRef.current = cutoff;
  const hrConnRef = useRef(hr.connected); hrConnRef.current = hr.connected;
  const currentRef = useRef(0);
  const lastSentRef = useRef(-1);

  const setField = (k, v) =>
    setCfg((c) => ({ ...c, [k]: Math.max(0, Math.min(MAXES[k] ?? 100, Number(v) || 0)) }));

  const applySpeed = useCallback((v) => {
    const iv = Math.max(0, Math.min(100, Math.round(v)));
    if (iv !== lastSentRef.current) {
      lastSentRef.current = iv;
      ble.writeCommand(`set:speed:${iv}`);
      ble.sendHostMessage?.({ type: "owner_telemetry", speed: iv });
      setApplied(iv);
    }
  }, [ble]);

  const stopDevice = useCallback(() => {
    currentRef.current = 0;
    lastSentRef.current = -1;
    setApplied(0);
    if (ble.connected) {
      ble.writeCommand("set:speed:0");
      ble.sendHostMessage?.({ type: "owner_telemetry", speed: 0 });
    }
  }, [ble]);

  // Ramp ticker: eases the applied speed toward the BPM-derived goal.
  useEffect(() => {
    if (!enabled || !ready) return;
    const id = setInterval(() => {
      const c = cfgRef.current;
      const over = cutoffRef.current > 0 && hrConnRef.current && bpmRef.current >= cutoffRef.current;
      if (over) { currentRef.current = 0; applySpeed(0); return; } // safety: instant stop
      const goal = bpmToSpeed(bpmRef.current, c, maxCapRef.current);
      const cur = currentRef.current;
      const rate = goal > cur ? (Number(c.rampUp) || 100) : (Number(c.rampDown) || 100);
      const step = rate * (TICK_MS / 1000);
      const next = Math.abs(goal - cur) <= step ? goal : cur + (goal > cur ? step : -step);
      currentRef.current = next;
      applySpeed(next);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [enabled, ready, applySpeed]);

  // Fail-safe: monitor or device dropped while syncing
  useEffect(() => {
    if (enabled && (!hr.connected || !ble.connected)) {
      stopDevice();
      setEnabled(false);
      toast.error(!hr.connected ? "Heart rate lost — sync stopped" : "Device disconnected — sync stopped");
    }
  }, [enabled, hr.connected, ble.connected, stopDevice]);

  const toggle = () => {
    if (!enabled) {
      if (!ready) { toast.error("Connect a heart rate monitor and the device first."); return; }
      currentRef.current = 0;
      lastSentRef.current = -1;
      setApplied(0);
      setEnabled(true);
      toast.success("Heart rate sync ON — speed ramps with your BPM");
    } else {
      setEnabled(false);
      stopDevice();
      toast("Heart rate sync OFF");
    }
  };

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="hr-sync-card">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
          <HeartPulse size={18} className="text-[var(--ossm-hr)]" /> Heart Rate Sync
        </h2>
        <button
          onClick={toggle}
          data-testid="hr-sync-toggle"
          role="switch"
          aria-checked={enabled}
          disabled={!enabled && !ready}
          className={`relative h-7 w-12 rounded-full transition-colors disabled:opacity-40 ${enabled ? "bg-[var(--ossm-hr)]" : "bg-[var(--ossm-overlay)]"}`}
        >
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white transition-all ${enabled ? "left-6" : "left-1"}`} />
        </button>
      </div>
      <p className="text-[var(--ossm-text-2)] text-sm mb-4">Device speed ramps toward your live BPM. Always capped by your Max Speed limit.</p>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <NumField label="RESTING BPM" testid="hr-sync-resting" value={cfg.restingBpm} onChange={(v) => setField("restingBpm", v)} />
        <NumField label="PEAK BPM" testid="hr-sync-peak" value={cfg.peakBpm} onChange={(v) => setField("peakBpm", v)} />
        <NumField label="MIN SPEED" testid="hr-sync-minspeed" value={cfg.minSpeed} onChange={(v) => setField("minSpeed", v)} />
        <NumField label="MAX SPEED" testid="hr-sync-maxspeed" value={cfg.maxSpeed} onChange={(v) => setField("maxSpeed", v)} />
        <NumField label="RAMP UP (%/S)" testid="hr-sync-rampup" value={cfg.rampUp} onChange={(v) => setField("rampUp", v)} />
        <NumField label="RAMP DOWN (%/S)" testid="hr-sync-rampdown" value={cfg.rampDown} onChange={(v) => setField("rampDown", v)} />
      </div>

      <div className="flex items-center justify-between bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3">
        <span className="font-mono-data text-xs text-[var(--ossm-text-2)]">
          {hr.connected ? `${hr.bpm} BPM` : "HR OFF"}
        </span>
        <div className="text-right leading-tight">
          <span className="font-mono-data font-bold text-2xl text-[var(--ossm-hr)]" data-testid="hr-sync-applied">
            {enabled && ready ? applied : "--"}<span className="text-xs text-[var(--ossm-muted)] ml-0.5">%</span>
          </span>
          <span className="block font-mono-data text-[11px] text-[var(--ossm-muted)]" data-testid="hr-sync-target">
            → target {enabled && ready ? target : "--"}
          </span>
        </div>
      </div>

      {overCutoff && (
        <p className="flex items-center gap-1.5 font-mono-data text-xs text-[var(--ossm-hr)] mt-3 pulse-dot" data-testid="hr-sync-cutoff-warning">
          <AlertTriangle size={14} className="shrink-0" /> HR CUTOFF ({cutoff}) reached — device stopped.
        </p>
      )}
      {maxCap < cfg.maxSpeed && (
        <p className="flex items-start gap-1.5 font-mono-data text-[11px] text-amber-300 mt-3">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" /> Max Speed limit ({maxCap}) caps sync below your {cfg.maxSpeed} setting.
        </p>
      )}
      {!ready && (
        <p className="font-mono-data text-[11px] text-[var(--ossm-muted)] mt-3">
          Requires a connected heart rate monitor and device.
        </p>
      )}
    </div>
  );
}

function NumField({ label, value, onChange, testid }) {
  return (
    <div>
      <label className="font-display text-[10px] tracking-[0.15em] text-[var(--ossm-text-2)]">{label}</label>
      <input
        type="number" value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid}
        className="w-full mt-1.5 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-2 font-mono-data text-sm outline-none focus:border-[var(--ossm-hr)] transition-colors"
      />
    </div>
  );
}
