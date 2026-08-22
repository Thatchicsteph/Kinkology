import React, { useEffect, useState } from "react";
import { Award, Clock, MessageSquare, Flame, Activity, X } from "lucide-react";

/**
 * Small end-of-turn recap card the guest sees when their time runs out and
 * they're demoted to spectator. Backend emits it on `session_recap`; the
 * card auto-dismisses after 14s but the guest can close it earlier.
 *
 * `recap` shape:
 *   {
 *     used_seconds, granted_seconds, chat_count,
 *     reactions_total, reactions_top: [{emoji, count}, …],
 *     avg_speed_percent, peak_speed_percent, reason
 *   }
 */
export function SessionRecap({ recap, onClose }) {
  const [countdown, setCountdown] = useState(14);

  useEffect(() => {
    if (!recap) return;
    const id = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { clearInterval(id); onClose && onClose(); return 0; }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [recap, onClose]);

  if (!recap) return null;

  const fmtMinutes = (s) => {
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return m > 0 ? `${m}m ${rem}s` : `${rem}s`;
  };
  const top = (recap.reactions_top || []).slice(0, 3);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      data-testid="session-recap"
      role="dialog"
      aria-modal="true"
    >
      <div className="hud-panel w-full max-w-md p-6 sm:p-7 fade-up relative">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close recap"
          data-testid="session-recap-close"
          className="absolute top-3 right-3 text-[var(--kink-muted)] hover:text-[var(--kink-text)] transition-colors"
        >
          <X size={16} />
        </button>

        <div className="flex items-center gap-2 mb-1">
          <Award size={16} className="text-[var(--kink-purple)]" />
          <span className="font-display font-black tracking-[0.15em] text-sm">TURN COMPLETE</span>
        </div>
        <p className="font-mono-data text-[11px] text-[var(--kink-muted)] mb-5 leading-relaxed">
          You've been moved to spectator — you can still watch and chat.
        </p>

        <div className="grid grid-cols-2 gap-3">
          <Stat
            icon={<Clock size={14} className="text-[var(--kink-purple)]" />}
            label="TIME USED"
            value={fmtMinutes(recap.used_seconds || 0)}
            sub={`of ${fmtMinutes(recap.granted_seconds || 0)}`}
            testid="recap-stat-time"
          />
          <Stat
            icon={<Activity size={14} className="text-[var(--kink-purple)]" />}
            label="AVG INTENSITY"
            value={`${recap.avg_speed_percent || 0}%`}
            sub={`peak ${recap.peak_speed_percent || 0}%`}
            testid="recap-stat-avg"
          />
          <Stat
            icon={<Flame size={14} className="text-[var(--kink-purple)]" />}
            label="REACTIONS"
            value={String(recap.reactions_total || 0)}
            sub={top.length > 0 ? top.map((r) => `${r.emoji} ${r.count}`).join("  ") : "none sent"}
            testid="recap-stat-reactions"
          />
          <Stat
            icon={<MessageSquare size={14} className="text-[var(--kink-purple)]" />}
            label="MESSAGES"
            value={String(recap.chat_count || 0)}
            sub="sent in chat"
            testid="recap-stat-chat"
          />
        </div>

        <button
          type="button"
          onClick={onClose}
          data-testid="session-recap-dismiss"
          className="mt-6 w-full bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.15em] py-2.5 text-xs active:scale-95 transition-transform"
        >
          BACK TO THE STREAM ({countdown}s)
        </button>
      </div>
    </div>
  );
}

function Stat({ icon, label, value, sub, testid }) {
  return (
    <div
      className="border border-[var(--kink-overlay)] p-3 space-y-1"
      data-testid={testid}
    >
      <div className="flex items-center gap-1.5 font-display text-[9px] tracking-[0.2em] text-[var(--kink-muted)]">
        {icon}
        {label}
      </div>
      <div className="font-mono-data font-bold text-lg text-[var(--kink-text)]">{value}</div>
      <div className="font-mono-data text-[10px] text-[var(--kink-muted)] leading-snug">{sub}</div>
    </div>
  );
}
