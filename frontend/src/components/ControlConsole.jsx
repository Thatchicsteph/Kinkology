import React, { useRef, useState } from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Gauge, Ruler, Waves, Move3d, Power, Square } from "lucide-react";
import { PATTERNS, cmd } from "@/lib/ossm";

const ICONS = { speed: Gauge, depth: Move3d, stroke: Ruler, sensation: Waves };

function ControlSlider({ id, label, value, onChange, disabled, danger }) {
  const Icon = ICONS[id];
  const color = danger ? "var(--ossm-danger)" : "var(--ossm-cyan)";
  return (
    <div className={`px-1 ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} style={{ color }} />
          <span className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">
            {label}
          </span>
        </div>
        <span className="font-mono-data text-xl font-bold tabular-nums" style={{ color }} data-testid={`value-${id}`}>
          {value}
        </span>
      </div>
      <SliderPrimitive.Root
        className="relative flex w-full touch-none select-none items-center h-6"
        min={0}
        max={100}
        step={1}
        value={[value]}
        onValueChange={(v) => onChange(v[0])}
        data-testid={`slider-${id}`}
      >
        <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-[var(--ossm-overlay)]">
          <SliderPrimitive.Range className="absolute h-full rounded-full" style={{ background: color, boxShadow: `0 0 10px ${color}` }} />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          className="block h-6 w-6 rounded-full border-2 bg-[var(--ossm-raised)] transition-transform hover:scale-110 focus:outline-none focus-visible:ring-2"
          style={{ borderColor: color, boxShadow: `0 0 8px ${color}` }}
        />
      </SliderPrimitive.Root>
    </div>
  );
}

export function ControlConsole({ onCommand, disabled = false }) {
  const [running, setRunning] = useState(false);
  const [state, setState] = useState({ speed: 0, depth: 60, stroke: 60, sensation: 50 });
  const [pattern, setPattern] = useState(0);
  const throttle = useRef({});

  const sendThrottled = (key, builder, v) => {
    const now = Date.now();
    if (!throttle.current[key] || now - throttle.current[key] > 90) {
      throttle.current[key] = now;
      onCommand(builder(v));
    }
  };

  const setParam = (key, builder) => (v) => {
    setState((s) => ({ ...s, [key]: v }));
    if (key === "speed" && !running) return; // speed only applied while running
    sendThrottled(key, builder, v);
  };

  const selectPattern = (idx) => {
    setPattern(idx);
    onCommand(cmd.pattern(idx));
  };

  const toggleRun = () => {
    if (running) {
      onCommand(cmd.stop());
      setState((s) => ({ ...s, speed: 0 }));
      setRunning(false);
    } else {
      onCommand(cmd.goStrokeEngine());
      onCommand(cmd.pattern(pattern));
      onCommand(cmd.depth(state.depth));
      onCommand(cmd.stroke(state.stroke));
      onCommand(cmd.sensation(state.sensation));
      const startSpeed = state.speed > 0 ? state.speed : 30;
      setState((s) => ({ ...s, speed: startSpeed }));
      onCommand(cmd.speed(startSpeed));
      setRunning(true);
    }
  };

  return (
    <div className="space-y-8" data-testid="control-console">
      <div className="space-y-7">
        <ControlSlider id="speed" label="SPEED" value={state.speed} onChange={setParam("speed", cmd.speed)} disabled={disabled} danger />
        <ControlSlider id="depth" label="DEPTH" value={state.depth} onChange={setParam("depth", cmd.depth)} disabled={disabled} />
        <ControlSlider id="stroke" label="STROKE" value={state.stroke} onChange={setParam("stroke", cmd.stroke)} disabled={disabled} />
        <ControlSlider id="sensation" label="SENSATION" value={state.sensation} onChange={setParam("sensation", cmd.sensation)} disabled={disabled} />
      </div>

      <div className={disabled ? "opacity-40 pointer-events-none" : ""}>
        <span className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)] block mb-3">PATTERN</span>
        <div className="grid grid-cols-2 gap-2">
          {PATTERNS.map((p) => (
            <button
              key={p.idx}
              onClick={() => selectPattern(p.idx)}
              data-testid={`pattern-${p.idx}`}
              className={`text-left px-3 py-2.5 border text-sm transition-colors duration-200 ${
                pattern === p.idx
                  ? "border-[var(--ossm-cyan)] bg-[var(--ossm-cyan)]/[0.08] text-white"
                  : "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-cyan)]/40"
              }`}
            >
              {p.name}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={toggleRun}
        disabled={disabled}
        data-testid="start-stop-button"
        className={`w-full h-20 flex items-center justify-center gap-3 font-display text-xl tracking-[0.2em] font-black transition-transform active:scale-95 disabled:opacity-40 ${
          running
            ? "bg-[var(--ossm-danger)] text-white pulse-danger"
            : "bg-[var(--ossm-cyan)] text-[var(--ossm-base)] glow-cyan"
        }`}
      >
        {running ? <Square size={22} fill="currentColor" /> : <Power size={22} />}
        {running ? "STOP" : "START"}
      </button>
    </div>
  );
}
