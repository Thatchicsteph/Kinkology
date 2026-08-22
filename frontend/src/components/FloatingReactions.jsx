import React, { useEffect, useRef, useState } from "react";

/**
 * Absolute-positioned emoji overlay for the stream.
 *
 * Consumers pass in a stream of reaction events ({id, emoji, author}); each
 * one is added to a local queue, rendered with a horizontal jitter, and
 * removed once its float-up animation completes (~2.4s).
 *
 * Once an id has been shown it's remembered in `seenRef` so it never gets
 * resurrected — parents can safely keep sending the last N reactions in an
 * array without re-triggering old animations.
 *
 * Parent must be `position: relative`.
 */
export function FloatingReactions({ reactions }) {
  const seenRef = useRef(new Set());
  const [visible, setVisible] = useState([]);

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
  }, [reactions]);

  const dismiss = (id) => setVisible((prev) => prev.filter((r) => r.id !== id));

  return (
    <div
      data-testid="floating-reactions-layer"
      className="absolute inset-0 pointer-events-none overflow-hidden"
      aria-hidden="true"
    >
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
