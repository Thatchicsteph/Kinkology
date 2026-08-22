import React, { useEffect, useRef, useState } from "react";
import { Send, MessageSquare, Trash2, Users, SmilePlus } from "lucide-react";

/**
 * Whitelist of allowed chat-message reactions. Kept short so the picker
 * stays glanceable and the DOM stays cheap.
 */
const CHAT_REACTIONS = ["🔥", "💦", "😩", "👏", "😈", "💜"];

/**
 * Realtime chat panel. The parent owns the WebSocket — this component just
 * renders the message list, handles the composer, and calls `onSend(text)` /
 * `onClear()` when the user acts.
 *
 * Messages have shape:
 *   { id, author, role: "owner"|"guest", text, ts }
 *
 * `presence` (optional) is the latest server-emitted presence snapshot:
 *   { owner_online: bool, guests: [{id,label}], typing: [label,...] }
 *
 * `onTyping()` is called (throttled) whenever the user is composing so the
 * parent can send a `typing` message on the WebSocket.
 *
 * Rendered by BOTH the admin dashboard (owner) and the guest control page.
 * Guests can't clear the log — only the owner sees the trash icon.
 */
export function ChatPanel({
  messages = [],
  onSend,
  onClear,
  canClear = false,
  selfLabel = "You",
  compact = false,
  title = "CHAT",
  presence = null,
  onTyping,
  onReact,
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [pickerFor, setPickerFor] = useState(null);
  const scrollRef = useRef(null);
  const lastTypingSentAt = useRef(0);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const submit = async (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    try {
      onSend(t);
      setText("");
    } finally {
      // 800ms lockout matches backend rate limit so double-taps don't feel broken
      setTimeout(() => setSending(false), 800);
    }
  };

  const fmt = (iso) => {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_) { return ""; }
  };

  return (
    <div className={`flex flex-col ${compact ? "h-64" : "h-80"}`} data-testid="chat-panel">
      <div className="flex items-center justify-between mb-3">
        <span className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)] flex items-center gap-2">
          <MessageSquare size={14} className="text-[var(--kink-purple)]" /> {title}
        </span>
        {canClear && (
          <button
            onClick={onClear}
            data-testid="chat-clear-button"
            title="Clear chat history"
            className="p-1.5 border border-[var(--kink-overlay)] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] transition-colors"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>

      {presence && (
        <div
          data-testid="chat-presence-bar"
          className="mb-2 flex flex-wrap items-center gap-2 font-mono-data text-[10px] tracking-wide"
        >
          <Users size={11} className="text-[var(--kink-muted)]" />
          <span
            data-testid="chat-presence-owner"
            className={presence.owner_online ? "text-[var(--kink-purple)]" : "text-[var(--kink-muted)] opacity-60"}
          >
            {presence.owner_online ? "● Owner" : "○ Owner offline"}
          </span>
          {(presence.guests || []).length === 0 ? (
            <span className="text-[var(--kink-muted)] opacity-60">no guests</span>
          ) : (
            (presence.guests || []).map((g) => (
              <span key={g.id} data-testid={`chat-presence-guest-${g.id}`} className="text-[var(--kink-text-2)]">
                ● {g.label}
              </span>
            ))
          )}
        </div>
      )}

      <div
        ref={scrollRef}
        data-testid="chat-messages"
        className="flex-1 overflow-y-auto space-y-2 pr-1 bg-[var(--kink-base)] border border-[var(--kink-overlay)] p-3"
      >
        {messages.length === 0 ? (
          <p className="font-mono-data text-[11px] text-[var(--kink-muted)] py-6 text-center">
            No messages yet. Say something.
          </p>
        ) : (
          messages.map((m) => {
            const reactions = m.reactions || {};
            const entries = Object.entries(reactions).filter(([, arr]) => (arr || []).length > 0);
            const showPicker = pickerFor === m.id;
            const mineReactedTo = (emoji) => (reactions[emoji] || []).some((a) => a === selfLabel || a === "You");
            return (
              <div
                key={m.id}
                data-testid={`chat-msg-${m.id}`}
                className="text-sm leading-snug group"
              >
                <span
                  className={`font-mono-data text-[10px] tracking-wide uppercase mr-2 ${
                    m.role === "owner" ? "text-[var(--kink-purple)]" : "text-[var(--kink-text-2)]"
                  }`}
                >
                  {m.author || (m.role === "owner" ? "Owner" : "Guest")}
                </span>
                <span className="font-mono-data text-[10px] text-[var(--kink-muted)] mr-2">
                  {fmt(m.ts)}
                </span>
                <span className="text-white break-words">{m.text}</span>
                {onReact && (
                  <button
                    type="button"
                    onClick={() => setPickerFor(showPicker ? null : m.id)}
                    data-testid={`chat-react-open-${m.id}`}
                    aria-label="React"
                    className="ml-1.5 inline-flex align-middle opacity-0 group-hover:opacity-100 focus:opacity-100 text-[var(--kink-muted)] hover:text-[var(--kink-purple)] transition-opacity"
                  >
                    <SmilePlus size={12} />
                  </button>
                )}
                {(entries.length > 0 || showPicker) && (
                  <div className="flex flex-wrap items-center gap-1.5 mt-1 ml-0.5">
                    {entries.map(([emoji, authors]) => {
                      const mine = mineReactedTo(emoji);
                      return (
                        <button
                          key={emoji}
                          type="button"
                          onClick={() => onReact && onReact(m.id, emoji)}
                          disabled={!onReact}
                          data-testid={`chat-react-badge-${m.id}-${emoji}`}
                          title={authors.join(", ")}
                          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] border transition-colors ${
                            mine
                              ? "border-[var(--kink-purple)] bg-[var(--kink-purple)]/15 text-white"
                              : "border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50 text-[var(--kink-text-2)]"
                          }`}
                        >
                          <span>{emoji}</span>
                          <span className="font-mono-data text-[10px]">{authors.length}</span>
                        </button>
                      );
                    })}
                    {showPicker && onReact && (
                      <div
                        data-testid={`chat-react-picker-${m.id}`}
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-[var(--kink-purple)]/40 bg-[var(--kink-purple)]/10"
                      >
                        {CHAT_REACTIONS.map((emoji) => (
                          <button
                            key={emoji}
                            type="button"
                            onClick={() => {
                              onReact(m.id, emoji);
                              setPickerFor(null);
                            }}
                            data-testid={`chat-react-pick-${m.id}-${emoji}`}
                            className="text-base leading-none active:scale-90 transition-transform"
                          >
                            {emoji}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {presence && Array.isArray(presence.typing) && presence.typing.filter((n) => n !== selfLabel).length > 0 && (
        <div
          data-testid="chat-typing-indicator"
          className="mt-2 flex items-center gap-2 font-mono-data text-[10px] text-[var(--kink-muted)]"
        >
          <span>{presence.typing.filter((n) => n !== selfLabel).join(", ")} typing</span>
          <span className="inline-flex gap-0.5" aria-hidden="true">
            <span className="chat-typing-dot" />
            <span className="chat-typing-dot" style={{ animationDelay: "120ms" }} />
            <span className="chat-typing-dot" style={{ animationDelay: "240ms" }} />
          </span>
        </div>
      )}

      <form onSubmit={submit} className="mt-3 flex items-stretch gap-2">
        <input
          value={text}
          onChange={(e) => {
            const next = e.target.value.slice(0, 250);
            setText(next);
            // Throttle typing pings to at most 1 per 1.5s (backend TTL is 4s).
            if (next && onTyping) {
              const now = Date.now();
              if (now - lastTypingSentAt.current > 1500) {
                lastTypingSentAt.current = now;
                try { onTyping(); } catch { /* noop */ }
              }
            }
          }}
          data-testid="chat-input"
          placeholder={`Say something as ${selfLabel}…`}
          maxLength={250}
          className="flex-1 bg-transparent border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-sm focus:outline-none focus:border-[var(--kink-purple)]/50"
        />
        <button
          type="submit"
          disabled={!text.trim() || sending}
          data-testid="chat-send-button"
          className="bg-[var(--kink-purple)] text-[var(--kink-base)] px-4 font-display font-bold tracking-[0.1em] text-xs active:scale-95 transition-transform disabled:opacity-40"
        >
          <Send size={14} />
        </button>
      </form>
    </div>
  );
}
