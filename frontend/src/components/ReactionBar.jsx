import React, { useRef } from "react";

/**
 * Row of one-tap reaction emoji buttons.
 *
 * Per-emoji 400ms client throttle — switching from 🔥 to 💦 back-to-back
 * no longer swallows the second click. Aligns with the backend's per-sender
 * rate limit but doesn't punish users for varying their reactions.
 *
 * Server whitelist: 🔥 💦 😩 👏 😈 💜 🍑 ❤️
 */
const REACTIONS = ["🔥", "💦", "😩", "👏", "😈", "💜"];

export function ReactionBar({ onReact, disabled = false, className = "" }) {
  const lastRef = useRef(new Map());

  const handle = (emoji) => {
    if (disabled) return;
    const now = Date.now();
    const last = lastRef.current.get(emoji) || 0;
    if (now - last < 400) return;
    lastRef.current.set(emoji, now);
    try { onReact(emoji); } catch (_) {}
  };

  return (
    <div
      data-testid="reaction-bar"
      className={`hud-panel px-3 py-2.5 sm:px-4 sm:py-3 flex items-center justify-between gap-2 ${className}`}
    >
      <span
        className="hidden sm:inline font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] pl-1"
        aria-hidden="true"
      >
        REACT
      </span>
      <div className="flex-1 flex items-center justify-around sm:justify-end gap-1 sm:gap-2">
        {REACTIONS.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={() => handle(emoji)}
            disabled={disabled}
            data-testid={`reaction-btn-${emoji}`}
            aria-label={`React with ${emoji}`}
            className="w-11 h-11 sm:w-12 sm:h-12 flex items-center justify-center text-2xl sm:text-[26px] rounded-full border border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/60 hover:bg-[var(--kink-purple)]/10 active:scale-90 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {emoji}
          </button>
        ))}
      </div>
    </div>
  );
}
