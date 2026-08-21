import React, { useEffect, useRef, useState } from "react";
import { Send, MessageSquare, Trash2 } from "lucide-react";

/**
 * Realtime chat panel. The parent owns the WebSocket — this component just
 * renders the message list, handles the composer, and calls `onSend(text)` /
 * `onClear()` when the user acts.
 *
 * Messages have shape:
 *   { id, author, role: "owner"|"guest", text, ts }
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
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

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
          messages.map((m) => (
            <div key={m.id} data-testid={`chat-msg-${m.id}`} className="text-sm leading-snug">
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
            </div>
          ))
        )}
      </div>

      <form onSubmit={submit} className="mt-3 flex items-stretch gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, 250))}
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
