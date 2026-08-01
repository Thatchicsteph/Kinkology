import React, { useEffect, useRef, useState } from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Gauge, Ruler, Waves, Move3d, Power, Square, Zap, Lock } from "lucide-react";
import { PATTERNS, cmd } from "@/lib/ossm";

const ICONS = { speed: Gauge, depth: Move3d, stroke: Ruler, sensation: Waves };

const CONTROL_DESCRIPTIONS = {
  speed: "Increases the speed of the attachment. Speed must be above 0% for the OSSM to move. When paused, increasing speed will resume movement automatically.",
  depth: "Controls the depth of penetration.",
  stroke: "Controls the length of each stroke. Low = shorter strokes. High = longer strokes.",
  sensation: "Controls the feel of the motion. Low = gentle and smooth. High = more intense and aggressive.",
};

// App-level automated motion programs. Each returns targets given elapsed seconds.
const PROGRAMS = [
  { id: "wave", name: "Wave", desc: "Smooth speed swell" },
  { id: "buildup", name: "Build-Up", desc: "Slow ramp, repeat" },
  { id: "tease", name: "Tease / Edge", desc: "Bursts then pause" },
  { id: "pulse", name: "Depth Pulse", desc: "Oscillating depth" },
  { id: "surge", name: "Surge", desc: "Fast in, slow out" },
  { id: "random", name: "Random", desc: "Shifts every few sec" },
];

function ControlSlider({ id, label, value, onChange, disabled, danger, min = 0, max = 100, limitNote }) {
  const Icon = ICONS[id];
  const color = danger ? "var(--kink-danger)" : "var(--kink-purple)";
  return (
    <div className={`px-1 ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} style={{ color }} />
          <span className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)]">{label}</span>
          {limitNote && (
            <span className="flex items-center gap-1 font-mono-data text-[10px] text-[var(--kink-muted)]">
              <Lock size={10} /> {limitNote}
            </span>
          )}
        </div>
        <span className="font-mono-data text-xl font-bold tabular-nums" style={{ color }} data-testid={`value-${id}`}>
          {value}
        </span>
      </div>
      {CONTROL_DESCRIPTIONS[id] && (
        <p className="font-mono-data text-[11px] leading-snug text-[var(--kink-muted)] mb-3" data-testid={`desc-${id}`}>
          {CONTROL_DESCRIPTIONS[id]}
        </p>
      )}
      <SliderPrimitive.Root
        className="relative flex w-full touch-none select-none items-center h-6"
        min={min}
        max={max}
        step={1}
        value={[Math.min(max, Math.max(min, value))]}
        onValueChange={(v) => onChange(v[0])}
        data-testid={`slider-${id}`}
      >
        <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-[var(--kink-overlay)]">
          <SliderPrimitive.Range className="absolute h-full rounded-full" style={{ background: color, boxShadow: `0 0 10px ${color}` }} />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          className="block h-6 w-6 rounded-full border-2 bg-[var(--kink-raised)] transition-transform hover:scale-110 focus:outline-none focus-visible:ring-2"
          style={{ borderColor: color, boxShadow: `0 0 8px ${color}` }}
        />
      </SliderPrimitive.Root>
    </div>
  );
}

export function ControlConsole({ onCommand, disabled = false, limits = { min_depth: 0, max_speed: 100 } }) {
  const minDepth = limits?.min_depth ?? 0;
  const maxSpeed = limits?.max_speed ?? 100;

  const [running, setRunning] = useState(false);
  const [activeProgram, setActiveProgram] = useState(null);
  const [state, setState] = useState({ speed: 0, depth: Math.max(60, minDepth), stroke: 60, sensation: 50 });
  const [pattern, setPattern] = useState(0);
  const throttle = useRef({});
  const progRef = useRef(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const clampSpeed = (v) => Math.min(maxSpeed, Math.max(0, Math.round(v)));
  const clampDepth = (v) => Math.min(100, Math.max(minDepth, Math.round(v)));

  useEffect(() => () => { if (progRef.current) clearInterval(progRef.current); }, []);

  // keep depth above owner's min at all times
  useEffect(() => {
    if (state.depth < minDepth) setState((s) => ({ ...s, depth: minDepth }));
    // eslint-disable-next-line
  }, [minDepth]);

  const sendThrottled = (key, builder, v) => {
    const now = Date.now();
    if (!throttle.current[key] || now - throttle.current[key] > 90) {
      throttle.current[key] = now;
      onCommand(builder(v));
    }
  };

  const stopProgram = () => {
    if (progRef.current) { clearInterval(progRef.current); progRef.current = null; }
    setActiveProgram(null);
  };

  const setParam = (key, builder) => (v) => {
    if (activeProgram) stopProgram(); // manual touch takes over
    if (key === "depth") v = clampDepth(v);
    if (key === "speed") v = clampSpeed(v);
    setState((s) => ({ ...s, [key]: v }));
    if (key === "speed" && !running) return;
    sendThrottled(key, builder, v);
  };

  const selectPattern = (idx) => {
    setPattern(idx);
    onCommand(cmd.pattern(idx));
  };

  const stopAll = () => {
    stopProgram();
    onCommand(cmd.stop());
    setState((s) => ({ ...s, speed: 0 }));
    setRunning(false);
  };

  const startManual = () => {
    onCommand(cmd.goStrokeEngine());
    onCommand(cmd.pattern(pattern));
    onCommand(cmd.depth(clampDepth(state.depth)));
    onCommand(cmd.stroke(state.stroke));
    onCommand(cmd.sensation(state.sensation));
    const startSpeed = clampSpeed(state.speed > 0 ? state.speed : 30);
    setState((s) => ({ ...s, speed: startSpeed }));
    onCommand(cmd.speed(startSpeed));
    setRunning(true);
  };

  const toggleRun = () => (running ? stopAll() : startManual());

  const runProgram = (pid) => {
    if (activeProgram === pid) { stopAll(); return; }
    stopProgram();
    onCommand(cmd.goStrokeEngine());
    onCommand(cmd.pattern(pattern));
    onCommand(cmd.sensation(stateRef.current.sensation));
    setRunning(true);
    setActiveProgram(pid);
    const start = Date.now();
    const rnd = { last: 0, speed: 40, depth: Math.max(60, minDepth) };
    progRef.current = setInterval(() => {
      const t = (Date.now() - start) / 1000;
      let speed = stateRef.current.speed;
      let depth = stateRef.current.depth;
      let sendDepth = false;
      switch (pid) {
        case "wave":
          speed = 50 + 35 * Math.sin(t * 0.5); break;
        case "buildup": {
          const c = t % 22; speed = 12 + (c / 22) * 78; break;
        }
        case "tease": {
          const c = t % 9; speed = c < 5.5 ? 72 : 0; break;
        }
        case "pulse": {
          speed = 45;
          depth = minDepth + (100 - minDepth) * (0.5 + 0.5 * Math.sin(t * 0.8));
          sendDepth = true; break;
        }
        case "surge": {
          const c = (t % 4) / 4; speed = 90 * (1 - c) + 10; break;
        }
        case "random": {
          if (t - rnd.last > 3) {
            rnd.last = t;
            rnd.speed = 20 + Math.random() * 60;
            rnd.depth = minDepth + Math.random() * (100 - minDepth);
          }
          speed = rnd.speed; depth = rnd.depth; sendDepth = true; break;
        }
        default: break;
      }
      speed = clampSpeed(speed);
      depth = clampDepth(depth);
      setState((s) => ({ ...s, speed, depth }));
      onCommand(cmd.speed(speed));
      if (sendDepth) onCommand(cmd.depth(depth));
    }, 250);
  };

  const speedNote = maxSpeed < 100 ? `max ${maxSpeed}` : null;
  const depthNote = minDepth > 0 ? `min ${minDepth}` : null;

  return (
    <div className="space-y-8" data-testid="control-console">
      <div className="space-y-7">
        <ControlSlider id="speed" label="SPEED" value={state.speed} onChange={setParam("speed", cmd.speed)} disabled={disabled} danger max={maxSpeed} limitNote={speedNote} />
        <ControlSlider id="depth" label="DEPTH" value={state.depth} onChange={setParam("depth", cmd.depth)} disabled={disabled} min={minDepth} limitNote={depthNote} />
        <ControlSlider id="stroke" label="STROKE" value={state.stroke} onChange={setParam("stroke", cmd.stroke)} disabled={disabled} />
        <ControlSlider id="sensation" label="SENSATION" value={state.sensation} onChange={setParam("sensation", cmd.sensation)} disabled={disabled} />
      </div>

      <div className={disabled ? "opacity-40 pointer-events-none" : ""}>
        <span className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)] block mb-3">PATTERN</span>
        <div className="grid grid-cols-2 gap-2">
          {PATTERNS.map((p) => (
            <button
              key={p.idx}
              onClick={() => selectPattern(p.idx)}
              data-testid={`pattern-${p.idx}`}
              className={`text-left px-3 py-2.5 border text-sm transition-colors duration-200 ${
                pattern === p.idx
                  ? "border-[var(--kink-purple)] bg-[var(--kink-purple)]/[0.08] text-white"
                  : "border-[var(--kink-overlay)] text-[var(--kink-text-2)] hover:border-[var(--kink-purple)]/40"
              }`}
            >
              <span className="block text-sm font-medium">{p.name}</span>
              {p.desc && (
                <span className="block font-mono-data text-[10px] text-[var(--kink-muted)] mt-0.5">{p.desc}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className={disabled ? "opacity-40 pointer-events-none" : ""}>
        <span className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)] flex items-center gap-2 mb-3">
          <Zap size={14} className="text-[var(--kink-purple)]" /> AUTO PROGRAMS
        </span>
        <div className="grid grid-cols-2 gap-2">
          {PROGRAMS.map((p) => (
            <button
              key={p.id}
              onClick={() => runProgram(p.id)}
              data-testid={`program-${p.id}`}
              className={`text-left px-3 py-2.5 border transition-colors duration-200 ${
                activeProgram === p.id
                  ? "border-[var(--kink-purple)] bg-[var(--kink-purple)]/[0.12] text-white glow-purple"
                  : "border-[var(--kink-overlay)] text-[var(--kink-text-2)] hover:border-[var(--kink-purple)]/40"
              }`}
            >
              <span className="block text-sm font-medium">{p.name}</span>
              <span className="block font-mono-data text-[10px] text-[var(--kink-muted)] mt-0.5">{p.desc}</span>
            </button>
          ))}
        </div>
        {activeProgram && (
          <p className="font-mono-data text-xs text-[var(--kink-purple)] mt-3" data-testid="program-active-note">
            ▶ Running "{PROGRAMS.find((p) => p.id === activeProgram)?.name}" — move any slider or press STOP to take manual control.
          </p>
        )}
      </div>

      <button
        onClick={toggleRun}
        disabled={disabled}
        data-testid="start-stop-button"
        className={`w-full h-20 flex items-center justify-center gap-3 font-display text-xl tracking-[0.2em] font-black transition-transform active:scale-95 disabled:opacity-40 ${
          running
            ? "bg-[var(--kink-danger)] text-white pulse-danger"
            : "bg-[var(--kink-purple)] text-[var(--kink-base)] glow-purple"
        }`}
      >
        {running ? <Square size={22} fill="currentColor" /> : <Power size={22} />}
        {running ? "STOP" : "START"}
      </button>
    </div>
  );
}
