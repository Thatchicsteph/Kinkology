import React, { useState } from "react";
import { UserCircle2 } from "lucide-react";

/**
 * One-time nickname prompt shown to guests joining an auto-labeled code so
 * their handle in chat + presence isn't just "Guest". The parent decides
 * whether to render this (based on sessionStorage per-code), and receives
 * the chosen name via onSubmit for a WS `set_nickname` push.
 */
export function NicknamePrompt({ onSubmit, onSkip }) {
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const clean = name.trim().slice(0, 24);
  const valid = clean.length >= 2 && /[a-zA-Z0-9]/.test(clean);

  const submit = async (e) => {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    try { await onSubmit(clean); } finally { setSubmitting(false); }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      data-testid="nickname-prompt"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={submit}
        className="hud-panel w-full max-w-md p-6 sm:p-7 fade-up"
      >
        <div className="flex items-center gap-2 mb-4">
          <UserCircle2 size={18} className="text-[var(--kink-purple)]" />
          <span className="font-display font-black tracking-[0.15em] text-sm">PICK A NICKNAME</span>
        </div>
        <p className="font-mono-data text-[11px] text-[var(--kink-muted)] mb-4 leading-relaxed">
          Everyone in the room sees this next to your chat lines. 2–24 characters,
          letters/numbers/spaces. You can skip and stay a plain "Guest".
        </p>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={24}
          autoFocus
          placeholder="e.g. Alex"
          data-testid="nickname-input"
          className="w-full bg-transparent border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-sm focus:outline-none focus:border-[var(--kink-purple)]/60"
        />
        <div className="flex items-center gap-2 mt-5">
          <button
            type="submit"
            disabled={!valid || submitting}
            data-testid="nickname-submit"
            className="flex-1 bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.15em] py-3 text-xs active:scale-95 transition-transform disabled:opacity-40"
          >
            {submitting ? "SAVING…" : "USE THIS NAME"}
          </button>
          <button
            type="button"
            onClick={onSkip}
            data-testid="nickname-skip"
            className="border border-[var(--kink-overlay)] px-4 py-3 font-display tracking-[0.1em] text-[10px] text-[var(--kink-text-2)] hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
          >
            SKIP
          </button>
        </div>
      </form>
    </div>
  );
}
