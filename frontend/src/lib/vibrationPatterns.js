// Vibration pattern presets for toy playback.
//
// Each pattern is a pure function of elapsed time (ms) -> intensity (0..1).
// Keeping them stateless functions of `t` means the engine that drives them
// (see useToys.startPattern) can be a dumb setInterval loop: it just feeds
// in `Date.now() - startTime` and forwards whatever comes out to every
// connected toy. Pause/resume, and switching patterns, both fall out for
// free since nothing but `t` is remembered between ticks.

export const VIBRATION_PATTERNS = [
  {
    id: "pulse",
    label: "Pulse",
    description: "Sharp on/off pulses",
    tickMs: 150,
    intensityAt: (t) => ((t % 800) < 400 ? 0.9 : 0),
  },
  {
    id: "wave",
    label: "Wave",
    description: "Smooth rise and fall",
    tickMs: 150,
    intensityAt: (t) => {
      const phase = (t % 3000) / 3000;
      return 0.2 + 0.75 * (0.5 - 0.5 * Math.cos(phase * 2 * Math.PI));
    },
  },
  {
    id: "escalation",
    label: "Escalation",
    description: "Steady climb, then reset",
    tickMs: 150,
    intensityAt: (t) => Math.min(1, (t % 8000) / 8000),
  },
  {
    id: "heartbeat",
    label: "Heartbeat",
    description: "Lub-dub double pulse",
    tickMs: 100,
    intensityAt: (t) => {
      const phase = t % 1200;
      if (phase < 150) return 0.9;
      if (phase < 300) return 0.15;
      if (phase < 420) return 0.7;
      return 0.1;
    },
  },
  {
    id: "rolling",
    label: "Rolling",
    description: "Fast triangle ramp",
    tickMs: 150,
    intensityAt: (t) => {
      const phase = (t % 1000) / 1000;
      return phase < 0.5 ? phase * 2 : 2 - phase * 2;
    },
  },
  {
    id: "earthquake",
    label: "Earthquake",
    description: "Random jitter bursts",
    tickMs: 250,
    // Not actually a function of t (it's randomized each tick), but keeping
    // the same signature means the engine doesn't need a special case.
    intensityAt: () => 0.3 + Math.random() * 0.7,
  },
];

export function getPattern(id) {
  return VIBRATION_PATTERNS.find((p) => p.id === id) || null;
}
