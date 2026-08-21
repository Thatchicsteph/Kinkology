// OSSM BLE protocol constants (from KinkyMakers/OSSM-hardware firmware v3+)
export const OSSM = {
  SERVICE_UUID: "522b443a-4f53-534d-0001-420badbabe69",
  COMMAND_UUID: "522b443a-4f53-534d-1000-420badbabe69",
  STATE_UUID: "522b443a-4f53-534d-2000-420badbabe69",
  PATTERNS_UUID: "522b443a-4f53-534d-3000-420badbabe69",
};

// StrokeEngine pattern names (index-based), pulled from firmware Strings.h
export const PATTERNS = [
  { idx: 0, name: "Simple Stroke", desc: "Equal acceleration, coasting & deceleration. No sensation." },
  { idx: 1, name: "Teasing Pounding", desc: "Speed shifts with sensation; balances faster strokes." },
  { idx: 2, name: "Robo Stroke", desc: "Sensation varies acceleration; robotic to gradual." },
  { idx: 3, name: "Half'n'Half", desc: "Full and half depth strokes alternate." },
  { idx: 4, name: "Deeper", desc: "Stroke depth increases per cycle." },
  { idx: 5, name: "Stop'n'Go", desc: "Pauses between strokes; sensation adjusts length." },
  { idx: 6, name: "Insist", desc: "Modifies length, maintains speed." },
];

export const cmd = {
  speed: (v) => `set:speed:${clamp(v)}`,
  stroke: (v) => `set:stroke:${clamp(v)}`,
  depth: (v) => `set:depth:${clamp(v)}`,
  sensation: (v) => `set:sensation:${clamp(v)}`,
  pattern: (v) => `set:pattern:${Math.max(0, Math.floor(v))}`,
  goStrokeEngine: () => `go:strokeEngine`,
  goMenu: () => `go:menu`,
  stop: () => `set:speed:0`,
};

function clamp(v) {
  return Math.min(100, Math.max(0, Math.round(v)));
}

export const webBluetoothSupported = () =>
  typeof navigator !== "undefined" && !!navigator.bluetooth;
