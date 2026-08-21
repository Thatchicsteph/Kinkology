import React, { useState, useEffect, useRef } from "react";
import { Vibrate, Square, Play, Lock } from "lucide-react";
import { VIBRATION_PATTERNS } from "@/lib/vibrationPatterns";

/**
 * Guest-facing toys control. The owner's Intiface connection lives in their
 * browser; here we just send `toy:*` command strings over the same WebSocket
 * we use for OSSM commands. The backend relays them to the owner.
 *
 * Only rendered when the backend reports `snap.toys.available === true`.
 * When `locked` is true, the owner has hit the kill switch — controls are
 * greyed out and any input is dropped by the backend anyway.
 */
export function GuestToys({ onCommand, activePattern, locked = false }) {
  const [intensity, setIntensity] = useState(0);
  // Throttle vibration nudges so we don't flood the WS with every rAF tick.
  const lastSent = useRef({ v: -1, t: 0 });

  useEffect(() => { setIntensity(0); }, []);

  const sendVibrate = (val) => {
    const now = Date.now();
    if (val === lastSent.current.v && now - lastSent.current.t < 60) return;
    lastSent.current = { v: val, t: now };
    onCommand(`toy:vibrate:${val}`);
  };

  const handleSlider = (e) => {
    const val = Math.max(0, Math.min(100, Number(e.target.value)));
    setIntensity(val);
    sendVibrate(val);
  };

  const stop = () => {
    setIntensity(0);
    onCommand("toy:stop");
  };

  return (
    <div className="pt-5 border-t border-[var(--kink-overlay)]" data-testid="guest-toys">
      <div className="flex items-center justify-between mb-3">
        <span className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)] flex items-center gap-2">
          <Vibrate size={14} className="text-[var(--kink-purple)]" /> TOYS
        </span>
        {locked ? (
          <span
            data-testid="guest-toys-locked"
            className="inline-flex items-center gap-1.5 font-mono-data text-[10px] tracking-[0.15em] px-2 py-1 border border-[var(--kink-danger)] text-[var(--kink-danger)]"
          >
            <Lock size={11} /> PAUSED BY OWNER
          </span>
        ) : (
          <button
            onClick={stop}
            data-testid="guest-toys-stop"
            className="inline-flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-1.5 font-mono-data text-[11px] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] transition-colors"
          >
            <Square size={11} /> STOP
          </button>
        )}
      </div>

      <div className={locked ? "opacity-40 pointer-events-none select-none" : ""}>
      <div className="mb-4">
        <div className="flex items-center justify-between mb-1.5">
          <label className="font-mono-data text-[10px] text-[var(--kink-muted)] uppercase tracking-wide">
            Vibration intensity
          </label>
          <span className="font-mono-data text-sm font-bold text-[var(--kink-purple)]" data-testid="guest-toys-intensity">
            {intensity}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={intensity}
          onChange={handleSlider}
          className="w-full accent-[var(--kink-purple)]"
          data-testid="guest-toys-slider"
        />
      </div>

      <div>
        <span className="font-mono-data text-[10px] text-[var(--kink-muted)] uppercase tracking-wide block mb-2">
          Patterns
        </span>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2" data-testid="guest-toys-patterns">
          {VIBRATION_PATTERNS.map((p) => {
            const active = activePattern === p.id;
            return (
              <button
                key={p.id}
                onClick={() => {
                  setIntensity(0);
                  onCommand(`toy:pattern:${p.id}`);
                }}
                data-testid={`guest-toys-pattern-${p.id}`}
                title={p.description}
                className={`flex items-center gap-1.5 border px-3 py-2 font-display text-xs tracking-[0.08em] transition-colors ${
                  active
                    ? "border-[var(--kink-purple)]/60 text-[var(--kink-purple)] glow-purple"
                    : "border-[var(--kink-overlay)] text-[var(--kink-text-2)] hover:border-[var(--kink-purple)]/40"
                }`}
              >
                {active ? <Vibrate size={12} className="pulse-dot" /> : <Play size={12} />}{" "}
                {p.label.toUpperCase()}
              </button>
            );
          })}
        </div>
      </div>
      </div>
    </div>
  );
}
