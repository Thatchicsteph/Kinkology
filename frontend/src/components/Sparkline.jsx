import React from "react";

// Lightweight rolling line + area chart (no chart lib). data: array of 0-100 numbers.
export function Sparkline({ data, color, height = 70, id, max = 100, refValue = null, refColor }) {
  const W = 300;
  const H = height;
  const MAX = max;
  const gradId = `grad-${id}`;
  const refY = refValue != null ? H - (Math.min(MAX, Math.max(0, refValue)) / MAX) * (H - 4) - 2 : null;

  if (!data || data.length < 2) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
        <line x1="0" y1={H - 1} x2={W} y2={H - 1} stroke={color} strokeOpacity="0.3" strokeWidth="2" />
        {refY != null && (
          <line x1="0" y1={refY} x2={W} y2={refY} stroke={refColor || color} strokeOpacity="0.7" strokeWidth="1.5" strokeDasharray="5,4" />
        )}
      </svg>
    );
  }

  const n = data.length;
  const step = W / (n - 1);
  const y = (v) => H - (Math.min(MAX, Math.max(0, v)) / MAX) * (H - 4) - 2;
  const line = data.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.45" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((g) => (
        <line key={g} x1="0" y1={H * g} x2={W} y2={H * g} stroke="#ffffff" strokeOpacity="0.05" strokeWidth="1" />
      ))}
      <polygon points={area} fill={`url(#${gradId})`} />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        style={{ filter: `drop-shadow(0 0 4px ${color})` }}
      />
      {refY != null && (
        <line x1="0" y1={refY} x2={W} y2={refY} stroke={refColor || color} strokeOpacity="0.8" strokeWidth="1.5" strokeDasharray="5,4" />
      )}
    </svg>
  );
}
