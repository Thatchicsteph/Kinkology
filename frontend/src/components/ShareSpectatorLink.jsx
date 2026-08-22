import React, { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api } from "@/lib/api";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { Eye, Copy, QrCode, ExternalLink } from "lucide-react";
import { toast } from "sonner";

/**
 * "SHARE SPECTATOR LINK" button — opens a popover with the URL, a QR code,
 * a copy button, and an open-in-new-tab shortcut. The popover only fetches
 * a code when it's opened (lazy) so the button is cheap; subsequent opens
 * reuse the same code because the backend is idempotent per label='Spectator'.
 */
export function ShareSpectatorLink({ publicUrl, onCodeCreated }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState("");
  const [url, setUrl] = useState("");

  const ensureLink = async () => {
    setLoading(true);
    try {
      const { data } = await api.post("/codes/spectator-link");
      const base = (publicUrl || window.location.origin).replace(/\/+$/, "");
      const nextUrl = `${base}/c/${data.code}`;
      setCode(data.code);
      setUrl(nextUrl);
      if (onCodeCreated) onCodeCreated();
      return nextUrl;
    } catch (e) {
      toast.error("Could not create spectator link");
      setOpen(false);
      return null;
    } finally { setLoading(false); }
  };

  const handleOpenChange = async (next) => {
    setOpen(next);
    if (next && !url) {
      await ensureLink();
    }
  };

  const copy = async () => {
    const u = url || (await ensureLink());
    if (!u) return;
    try {
      await navigator.clipboard.writeText(u);
      toast.success(`Spectator link copied — ${code}`);
    } catch (_) {
      toast.error("Clipboard blocked");
    }
  };

  const openTab = () => {
    if (url) window.open(url, "_blank", "noopener");
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="share-spectator-link"
          className="w-full mb-5 inline-flex items-center justify-center gap-2 border border-[var(--kink-purple)]/40 hover:border-[var(--kink-purple)] hover:bg-[var(--kink-purple)]/10 text-[var(--kink-purple)] font-display tracking-[0.12em] py-3 text-sm active:scale-[0.99] transition-all"
        >
          <Eye size={15} /> SHARE SPECTATOR LINK
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="center"
        sideOffset={8}
        className="w-[300px] p-0 bg-[var(--kink-base)] border border-[var(--kink-overlay)] text-[var(--kink-text)]"
        data-testid="spectator-link-popover"
      >
        <div className="px-4 py-3 border-b border-[var(--kink-overlay)] flex items-center gap-2">
          <QrCode size={14} className="text-[var(--kink-purple)]" />
          <span className="font-display text-[11px] tracking-[0.18em] text-[var(--kink-text-2)]">SPECTATOR ACCESS</span>
        </div>

        <div className="p-4 space-y-3">
          {loading && !url ? (
            <div className="h-[220px] flex items-center justify-center font-mono-data text-xs text-[var(--kink-muted)]">
              GENERATING LINK…
            </div>
          ) : (
            <>
              <div
                className="flex items-center justify-center bg-white p-3 rounded-sm"
                data-testid="spectator-qr"
              >
                <QRCodeSVG value={url || " "} size={192} level="M" includeMargin={false} />
              </div>
              <div>
                <p className="font-display text-[9px] tracking-[0.2em] text-[var(--kink-muted)] mb-1">CODE</p>
                <p
                  className="font-mono-data font-bold text-lg tracking-[0.15em] text-[var(--kink-purple)]"
                  data-testid="spectator-code"
                >
                  {code || "—"}
                </p>
              </div>
              <div>
                <p className="font-display text-[9px] tracking-[0.2em] text-[var(--kink-muted)] mb-1">URL</p>
                <p
                  className="font-mono-data text-[11px] text-[var(--kink-text-2)] break-all leading-relaxed"
                  data-testid="spectator-url"
                >
                  {url}
                </p>
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={copy}
                  data-testid="spectator-copy"
                  className="flex-1 inline-flex items-center justify-center gap-1.5 bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] py-2.5 text-xs active:scale-95 transition-transform"
                >
                  <Copy size={13} /> COPY
                </button>
                <button
                  type="button"
                  onClick={openTab}
                  data-testid="spectator-open"
                  title="Open in a new tab"
                  className="inline-flex items-center justify-center border border-[var(--kink-overlay)] px-3 hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors"
                >
                  <ExternalLink size={14} />
                </button>
              </div>
              <p className="font-mono-data text-[10px] text-[var(--kink-muted)] leading-relaxed">
                Anyone with this link watches the stream and chats — no control, no timer, no queue.
              </p>
            </>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
