import React, { useEffect, useRef, useState, useCallback } from "react";
import { HeartPulse, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

const STORE_KEY = "ossm_hr_sync";
// Closed-loop target-HR controller: speed is nudged up/down to drive the live
// heart rate toward targetBpm, then held there.
const DEFAULTS = { targetBpm: 120, minSpeed: 0, maxSpeed: 100, response: 0.6, rampUp: 25, rampDown: 50 };
const MAXES = { targetBpm: 240, minSpeed: 100, maxSpeed: 100, response: 5, rampUp: 100, rampDown: 100 };
const TICK_MS = 1000;

const PRESET_KEYS = ["minSpeed", "maxSpeed", "response", "rampUp", "rampDown"];
const PRESETS = {
  Gentle:     { minSpeed: 0,  maxSpeed: 60,  response: 0.3, rampUp: 8,  rampDown: 30 },
  Responsive: { minSpeed: 0,  maxSpeed: 100, response: 0.6, rampUp: 20, rampDown: 40 },
  Intense:    { minSpeed: 10, maxSpeed: 100, response: 1.2, rampUp: 40, rampDown: 50 },
};

function loadCfg() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY));
    if (raw && typeof raw === "object") return { ...DEFAULTS, ...raw };
  } catch (e) {}
  return { ...DEFAULTS };
}

function matchPreset(cfg) {
  for (const [name, p] of Object.entries(PRESETS)) {
    if (PRESET_KEYS.every((k) => Number(cfg[k]) === p[k])) return name;
  }
  return null;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function HeartRateSync({ hr, ble, maxCap, cutoff = 0 }) {
  const [cfg, setCfg] = useState(loadCfg);
  const [enabled, setEnabled] = useState(false);
  const [applied, setApplied] = useState(0);

  const ready = hr.connected && ble.connected;
  const overCutoff = cutoff > 0 && hr.connected && hr.bpm >= cutoff;
  const activePreset = matchPreset(cfg);

  useEffect(() => { localStorage.setItem(STORE_KEY, JSON.stringify(cfg)); }, [cfg]);

  const bpmRef = useRef(hr.bpm); bpmRef.current = hr.bpm;
  const cfgRef = useRef(cfg); cfgRef.current = cfg;
  const maxCapRef = useRef(maxCap); maxCapRef.current = maxCap;
  const cutoffRef = useRef(cutoff); cutoffRef.current = cutoff;
  const hrConnRef = useRef(hr.connected); hrConnRef.current = hr.connected;
  const commandRef = useRef(0);
  const lastSentRef = useRef(-1);

  const setField = (k, v) =>
    setCfg((c) => ({ ...c, [k]: clamp(Number(v) || 0, 0, MAXES[k] ?? 100) }));

  const applySpeed = useCallback((v) => {
    const iv = clamp(Math.round(v), 0, 100);
    if (iv !== lastSentRef.current) {
      lastSentRef.current = iv;
      ble.writeCommand(`set:speed:${iv}`);
      ble.sendHostMessage?.({ type: "owner_telemetry", speed: iv });
      setApplied(iv);
    }
  }, [ble]);

  const stopDevice = useCallback(() => {
    commandRef.current = 0;
    lastSentRef.current = -1;
    setApplied(0);
    if (ble.connected) {
      ble.writeCommand("set:speed:0");
      ble.sendHostMessage?.({ type: "owner_telemetry", speed: 0 });
    }
  }, [ble]);

  // Control loop: nudge speed to drive live BPM toward targetBpm, then hold.
  useEffect(() => {
    if (!enabled || !ready) return;
    const dt = TICK_MS / 1000;
    const id = setInterval(() => {
      const c = cfgRef.current;
      const over = cutoffRef.current > 0 && hrConnRef.current && bpmRef.current >= cutoffRef.current;
      if (over) { commandRef.current = 0; applySpeed(0); return; } // safety: instant stop
      const error = Number(c.targetBpm) - bpmRef.current;        // >0 => below target, speed up
      const delta = clamp(Number(c.response) * error * dt, -Number(c.rampDown) * dt, Number(c.rampUp) * dt);
      const ceil = Math.min(Number(c.maxSpeed), Math.max(0, maxCapRef.current));
      commandRef.current = clamp(commandRef.current + delta, Number(c.minSpeed), ceil);
      applySpeed(commandRef.current);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [enabled, ready, applySpeed]);

  // Fail-safe: monitor or device dropped while running
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
      commandRef.current = 0;
      lastSentRef.current = -1;
      setApplied(0);
      setEnabled(true);
      toast.success(`Targeting ${cfg.targetBpm} BPM — speed will adjust to reach and hold it`);
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
      <p className="text-[var(--ossm-text-2)] text-sm mb-3">Speeds up to reach your target heart rate, then eases off to hold it. Always capped by Max Speed.</p>

      <div className="flex gap-2 mb-4" data-testid="hr-sync-presets">
        {Object.keys(PRESETS).map((name) => {
          const active = activePreset === name;
          return (
            <button
              key={name}
              onClick={() => setCfg((c) => ({ ...c, ...PRESETS[name] }))}
              data-testid={`hr-sync-preset-${name.toLowerCase()}`}
              className={`flex-1 font-display text-xs tracking-[0.1em] py-2 border transition-colors ${
                active
                  ? "border-[var(--ossm-hr)] text-[var(--ossm-hr)] bg-[var(--ossm-hr)]/10"
                  : "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-hr)]/50 hover:text-[var(--ossm-hr)]"
              }`}
            >
              {name.toUpperCase()}
            </button>
          );
        })}
      </div>

      <div className="mb-3">
        <label className="font-display text-[10px] tracking-[0.15em] text-[var(--ossm-text-2)]">TARGET HEART RATE (BPM)</label>
        <input
          type="number" value={cfg.targetBpm} onChange={(e) => setField("targetBpm", e.target.value)} data-testid="hr-sync-target-bpm"
          className="w-full mt-1.5 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-2.5 font-mono-data text-lg text-[var(--ossm-hr)] outline-none focus:border-[var(--ossm-hr)] transition-colors"
        />
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <NumField label="MIN SPEED" testid="hr-sync-minspeed" value={cfg.minSpeed} onChange={(v) => setField("minSpeed", v)} />
        <NumField label="MAX SPEED" testid="hr-sync-maxspeed" value={cfg.maxSpeed} onChange={(v) => setField("maxSpeed", v)} />
        <NumField label="RESPONSE (%/S·BPM)" testid="hr-sync-response" value={cfg.response} onChange={(v) => setField("response", v)} step="0.1" />
        <div />
        <NumField label="RAMP UP (%/S)" testid="hr-sync-rampup" value={cfg.rampUp} onChange={(v) => setField("rampUp", v)} />
        <NumField label="RAMP DOWN (%/S)" testid="hr-sync-rampdown" value={cfg.rampDown} onChange={(v) => setField("rampDown", v)} />
      </div>

      <div className="flex items-center justify-between bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3">
        <span className="font-mono-data text-xs text-[var(--ossm-text-2)]" data-testid="hr-sync-target">
          {hr.connected ? `${hr.bpm}` : "--"}<span className="text-[var(--ossm-muted)]"> / {cfg.targetBpm} BPM</span>
        </span>
        <div className="text-right leading-tight">
          <span className="font-mono-data font-bold text-2xl text-[var(--ossm-hr)]" data-testid="hr-sync-applied">
            {enabled && ready ? applied : "--"}<span className="text-xs text-[var(--ossm-muted)] ml-0.5">%</span>
          </span>
          <span className="block font-mono-data text-[11px] text-[var(--ossm-muted)]">speed</span>
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

function NumField({ label, value, onChange, testid, step }) {
  return (
    <div>
      <label className="font-display text-[10px] tracking-[0.15em] text-[var(--ossm-text-2)]">{label}</label>
      <input
        type="number" step={step} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid}
        className="w-full mt-1.5 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-3 py-2 font-mono-data text-sm outline-none focus:border-[var(--ossm-hr)] transition-colors"
      />
    </div>
  );
}
