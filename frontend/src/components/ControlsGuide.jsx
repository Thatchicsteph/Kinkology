import React, { useState } from "react";
import { BookOpen, ChevronDown, Gauge, Move3d, Waves, Ruler } from "lucide-react";

const SPEED = {
  icon: Gauge,
  title: "Speed Control",
  intro: "The left encoder always controls speed, regardless of which mode is active.",
  param: { name: "Speed", range: "0–100%", default: "0%" },
  bullets: [
    "Turn clockwise to increase speed",
    "Turn counter-clockwise to decrease speed",
    "The encoder has acceleration: faster turns make bigger changes",
  ],
  note: "Speed must be above 0% for the OSSM to move. When paused, increase speed to resume automatically.",
};

const MODES = [
  {
    icon: Move3d,
    title: "Depth Mode",
    intro: "Controls how deep the penetration reaches.",
    param: { name: "Depth", range: "0–100%", default: "10%" },
    bullets: ["Higher values = deeper penetration", "The depth is visualized on the linear rail graph"],
  },
  {
    icon: Waves,
    title: "Sensation Mode",
    intro: "Controls the intensity or “feel” of the motion.",
    param: { name: "Sensation", range: "0–100%", default: "50%" },
    bullets: ["Lower values = gentler, smoother motion", "Higher values = more intense, aggressive motion"],
  },
  {
    icon: Ruler,
    title: "Stroke Mode",
    intro: "Controls the length of each stroke.",
    param: { name: "Stroke", range: "0–100%", default: "50%" },
    bullets: ["Lower values = shorter strokes", "Higher values = longer strokes", "The stroke is visualized on the linear rail graph"],
  },
];

function ParamTable({ param }) {
  return (
    <table className="w-full font-mono-data text-xs mt-3 border border-[var(--ossm-overlay)]">
      <thead>
        <tr className="text-[var(--ossm-muted)] text-left">
          <th className="px-2 py-1 font-normal border-b border-[var(--ossm-overlay)]">Parameter</th>
          <th className="px-2 py-1 font-normal border-b border-[var(--ossm-overlay)]">Range</th>
          <th className="px-2 py-1 font-normal border-b border-[var(--ossm-overlay)]">Default</th>
        </tr>
      </thead>
      <tbody>
        <tr className="text-[var(--ossm-text-2)]">
          <td className="px-2 py-1">{param.name}</td>
          <td className="px-2 py-1">{param.range}</td>
          <td className="px-2 py-1">{param.default}</td>
        </tr>
      </tbody>
    </table>
  );
}

function Section({ item }) {
  const Icon = item.icon;
  return (
    <div>
      <div className="flex items-center gap-2">
        <Icon size={14} className="text-[var(--ossm-cyan)]" />
        <span className="font-display text-xs tracking-[0.15em] text-white">{item.title.toUpperCase()}</span>
      </div>
      <p className="text-[var(--ossm-text-2)] text-sm mt-2">{item.intro}</p>
      <ParamTable param={item.param} />
      <ul className="list-disc pl-5 mt-3 space-y-1 text-[var(--ossm-text-2)] text-sm">
        {item.bullets.map((b) => (
          <li key={b}>{b}</li>
        ))}
      </ul>
      {item.note && <p className="font-mono-data text-xs text-[var(--ossm-cyan)] mt-3">{item.note}</p>}
    </div>
  );
}

export function ControlsGuide() {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="controls-guide">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="controls-guide-toggle"
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <span className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)] flex items-center gap-2">
          <BookOpen size={14} className="text-[var(--ossm-cyan)]" /> HOW THE CONTROLS WORK
        </span>
        <ChevronDown size={16} className={`text-[var(--ossm-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="mt-5 space-y-7" data-testid="controls-guide-body">
          <Section item={SPEED} />
          <div>
            <span className="font-display text-xs tracking-[0.15em] text-white">ADJUSTMENT MODES</span>
            <p className="text-[var(--ossm-text-2)] text-sm mt-2">
              The right encoder controls one of three parameters, depending on which mode is active. Use the shoulder
              bumpers to switch modes.
            </p>
          </div>
          {MODES.map((m) => (
            <Section key={m.title} item={m} />
          ))}
          <a
            href="https://docs.researchanddesire.com/radr/guides/user-guide/ossm-controls"
            target="_blank"
            rel="noreferrer"
            className="block font-mono-data text-xs text-[var(--ossm-cyan)]"
          >
            Full OSSM controls guide →
          </a>
        </div>
      )}
    </div>
  );
}
