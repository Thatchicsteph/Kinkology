import React, { useEffect, useRef, useState } from "react";
import { Volume2, VolumeX } from "lucide-react";

/**
 * Web Audio "pop" synth used when a floating emoji appears.
 * Single shared AudioContext lazily created on first play so we don't
 * violate browser autoplay policies before a user gesture. Each pop is a
 * short pluck: sine tone → ~700Hz down to ~200Hz over 120ms with an
 * exponential gain fall-off so it stays subtle over voice/music.
 */
let _sharedCtx = null;
function playPop() {
  try {
    if (!_sharedCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      _sharedCtx = new AC();
    }
    const ctx = _sharedCtx;
    if (ctx.state === "suspended") { ctx.resume().catch(() => {}); }
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(700, now);
    osc.frequency.exponentialRampToValueAtTime(200, now + 0.12);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.16, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.15);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.17);
  } catch (_) { /* audio best-effort */ }
}

const SOUND_KEY = "kinkology_reaction_sound";

function readSoundPref() {
  try {
    const v = window.localStorage.getItem(SOUND_KEY);
    // Default OFF so we never surprise a viewer with sudden audio.
    return v === "on";
  } catch (_) { return false; }
}

/**
 * Absolute-positioned emoji overlay for the stream.
 *
 * Consumers pass in a stream of reaction events ({id, emoji, author}); each
 * new one is added to a local queue, rendered with horizontal jitter, and
 * removed once its float-up animation completes (~2.4s).
 *
 * A `seenRef` Set makes sure a re-sent parent array can't resurrect an
 * already-dismissed emoji. A small speaker toggle in the corner lets each
 * viewer decide whether to hear a soft pop per reaction (persisted in
 * localStorage, default off).
 *
 * Parent must be `position: relative`.
 */
export function FloatingReactions({ reactions }) {
  const seenRef = useRef(new Set());
  const primedRef = useRef(false);
  const [visible, setVisible] = useState([]);
  const [soundOn, setSoundOn] = useState(readSoundPref);

  useEffect(() => {
    if (!reactions || reactions.length === 0) return;
    const additions = [];
    for (const r of reactions) {
      if (!r || !r.id) continue;
      if (seenRef.current.has(r.id)) continue;
      seenRef.current.add(r.id);
      additions.push({ ...r, left: 15 + Math.random() * 70 });
    }
    if (additions.length === 0) return;
    setVisible((prev) => [...prev, ...additions]);
    if (soundOn && primedRef.current) {
      // One pop per burst — a flood of 5 emojis in one tick shouldn't sound like a machine gun.
      playPop();
    }
  }, [reactions, soundOn]);

  const dismiss = (id) => setVisible((prev) => prev.filter((r) => r.id !== id));

  const toggleSound = () => {
    setSoundOn((cur) => {
      const next = !cur;
      try { window.localStorage.setItem(SOUND_KEY, next ? "on" : "off"); } catch (_) {}
      // First toggle counts as the user gesture that primes the AudioContext.
      primedRef.current = true;
      if (next) playPop();
      return next;
    });
  };

  return (
    <div
      data-testid="floating-reactions-layer"
      className="absolute inset-0 pointer-events-none overflow-hidden"
      aria-hidden="true"
    >
      <button
        type="button"
        onClick={toggleSound}
        title={soundOn ? "Mute reaction sounds" : "Enable reaction sounds"}
        data-testid="reaction-sound-toggle"
        data-sound={soundOn ? "on" : "off"}
        className="pointer-events-auto absolute top-3 right-3 w-9 h-9 flex items-center justify-center rounded-full bg-[rgba(0,0,0,0.55)] backdrop-blur-sm border border-[rgba(255,255,255,0.15)] text-white/90 hover:bg-[rgba(168,85,247,0.35)] hover:border-[var(--kink-purple)]/60 transition-all"
      >
        {soundOn ? <Volume2 size={15} /> : <VolumeX size={15} />}
      </button>
      {visible.map((r) => (
        <span
          key={r.id}
          className="reaction-float"
          style={{ left: `${r.left}%` }}
          onAnimationEnd={() => dismiss(r.id)}
          data-testid={`floating-reaction-${r.id}`}
        >
          {r.emoji}
          {r.author && <span className="reaction-author">{r.author.toUpperCase()}</span>}
        </span>
      ))}
    </div>
  );
}
