import React from "react";
import { fmtTime } from "@/lib/api";

export function LiveQueue({ active, queue, you }) {
  const rows = [];
  if (active) {
    rows.push({ key: "active", label: active.label || "Guest", isActive: true, sub: fmtTime(active.remaining_seconds) });
  }
  (queue || []).forEach((q, i) =>
    rows.push({ key: `q-${i}`, label: q.label || "Guest", isActive: false, sub: `#${q.position} in line` })
  );

  return (
    <div className="space-y-2" data-testid="live-queue">
      {rows.length === 0 && (
        <p className="font-mono-data text-sm text-[var(--ossm-muted)] py-6 text-center">
          No one connected.
        </p>
      )}
      {rows.map((r) => (
        <div
          key={r.key}
          data-testid={`queue-item-${r.key}`}
          className={`flex items-center justify-between px-4 py-3 border transition-colors duration-200 ${
            r.isActive
              ? "border-[var(--ossm-cyan)]/50 bg-[var(--ossm-cyan)]/[0.06]"
              : "border-[var(--ossm-overlay)] bg-[var(--ossm-base)]"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span
              className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                r.isActive ? "bg-[var(--ossm-cyan)] pulse-dot glow-cyan" : "bg-[var(--ossm-muted)]"
              }`}
            />
            <span className={`truncate font-medium ${r.isActive ? "text-white" : "text-[var(--ossm-text-2)]"}`}>
              {r.label}
            </span>
          </div>
          <span className={`font-mono-data text-sm tabular-nums ${r.isActive ? "text-[var(--ossm-cyan)]" : "text-[var(--ossm-muted)]"}`}>
            {r.sub}
          </span>
        </div>
      ))}
    </div>
  );
}
