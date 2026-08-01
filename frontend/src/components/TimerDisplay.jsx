import React from "react";
import { fmtTime } from "@/lib/api";

export function TimerDisplay({ seconds, label = "TIME REMAINING" }) {
  const low = seconds <= 60;
  return (
    <div className="flex flex-col items-center" data-testid="timer-display">
      <span className="font-display text-[0.7rem] tracking-[0.25em] text-[var(--kink-muted)] mb-2">
        {label}
      </span>
      <span
        className={`font-mono-data font-extrabold tabular-nums text-6xl sm:text-7xl leading-none ${
          low ? "text-[var(--kink-danger)] text-glow-danger pulse-dot" : "text-[var(--kink-purple)] text-glow-purple"
        }`}
        data-testid="timer-value"
      >
        {fmtTime(seconds)}
      </span>
    </div>
  );
}
