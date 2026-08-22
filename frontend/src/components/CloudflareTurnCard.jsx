import React, { useEffect, useState } from "react";
import { Cloud, CloudOff, ShieldCheck, Trash2, Loader2, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";

/**
 * Cloudflare Calls TURN one-click config. Owner pastes the two credentials
 * from dash.cloudflare.com → Realtime → TURN; backend validates against
 * Cloudflare's `/credentials/generate-ice-servers` endpoint and stores the
 * token Fernet-encrypted.
 *
 * When configured, `/api/stream/ice-servers` returns fresh short-lived TURN
 * credentials to every WHEP viewer's browser — mobile clients on 4G/5G
 * symmetric NAT will now actually connect.
 */
export function CloudflareTurnCard() {
  const [status, setStatus] = useState({ configured: false });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [keyId, setKeyId] = useState("");
  const [token, setToken] = useState("");

  const load = async () => {
    try {
      const { data } = await api.get("/stream/turn/cloudflare");
      setStatus(data);
    } catch (_) {
      setStatus({ configured: false });
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const save = async (e) => {
    e.preventDefault();
    const kid = keyId.trim();
    const tok = token.trim();
    if (!kid || !tok) return;
    setSaving(true);
    try {
      const { data } = await api.put("/stream/turn/cloudflare", { key_id: kid, token: tok });
      setStatus(data);
      setToken(""); // never keep the secret in component state longer than needed
      setKeyId("");
      toast.success("Cloudflare TURN active — mobile viewers can now connect over 4G/5G");
    } catch (err) {
      const msg = err?.response?.data?.detail || "Cloudflare rejected the credentials";
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!window.confirm("Remove Cloudflare TURN? Mobile viewers behind carrier NAT will fail again.")) return;
    setSaving(true);
    try {
      await api.delete("/stream/turn/cloudflare");
      setStatus({ configured: false });
      toast("Cloudflare TURN disabled", { icon: "⚠" });
    } catch (_) {
      toast.error("Could not remove");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="cf-turn-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
          <Cloud size={18} className="text-[var(--kink-purple)]" /> Cloudflare TURN
        </h2>
        <span
          data-testid="cf-turn-status"
          className={`inline-flex items-center gap-1.5 font-mono-data text-[10px] tracking-[0.15em] px-2 py-1 border ${
            status.configured
              ? "border-[var(--kink-purple)]/50 text-[var(--kink-purple)]"
              : "border-[var(--kink-overlay)] text-[var(--kink-muted)]"
          }`}
        >
          {status.configured ? <><ShieldCheck size={11} /> ACTIVE</> : <><CloudOff size={11} /> NOT CONFIGURED</>}
        </span>
      </div>

      <p className="text-[var(--kink-text-2)] text-sm mb-4">
        Free TURN relay for mobile viewers behind 4G/5G / symmetric NAT. Cloudflare gives 1000 GB/month free.
      </p>

      {loading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="animate-spin text-[var(--kink-purple)]" size={20} />
        </div>
      ) : status.configured ? (
        <div className="space-y-4">
          <div>
            <span className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] block mb-1">TURN KEY ID</span>
            <code className="block bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-sm text-[var(--kink-text-2)]" data-testid="cf-turn-key-id">
              {status.key_id_masked}
            </code>
          </div>
          <button
            onClick={remove}
            disabled={saving}
            data-testid="cf-turn-remove"
            className="inline-flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-[11px] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] transition-colors disabled:opacity-40"
          >
            <Trash2 size={13} /> REMOVE
          </button>
        </div>
      ) : (
        <form onSubmit={save} className="space-y-3" data-testid="cf-turn-form">
          <a
            href="https://dash.cloudflare.com/?to=/:account/calls"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[var(--kink-purple)] text-xs hover:underline mb-2"
          >
            Open Cloudflare Dashboard <ExternalLink size={11} />
          </a>
          <div>
            <label htmlFor="cf-turn-key-input" className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] block mb-1">
              TURN KEY ID
            </label>
            <input
              id="cf-turn-key-input"
              data-testid="cf-turn-key-input"
              value={keyId}
              onChange={(e) => setKeyId(e.target.value)}
              placeholder="abc123def456..."
              autoComplete="off"
              className="w-full bg-transparent border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-sm focus:outline-none focus:border-[var(--kink-purple)]/50"
              required
            />
          </div>
          <div>
            <label htmlFor="cf-turn-token-input" className="font-display text-[10px] tracking-[0.2em] text-[var(--kink-muted)] block mb-1">
              TURN API TOKEN
            </label>
            <input
              id="cf-turn-token-input"
              data-testid="cf-turn-token-input"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="paste your TURN API token"
              autoComplete="off"
              className="w-full bg-transparent border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-sm focus:outline-none focus:border-[var(--kink-purple)]/50"
              required
            />
          </div>
          <button
            type="submit"
            disabled={saving || !keyId.trim() || !token.trim()}
            data-testid="cf-turn-save"
            className="w-full bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.1em] py-2.5 active:scale-95 transition-transform disabled:opacity-40 inline-flex items-center justify-center gap-2"
          >
            {saving ? <><Loader2 className="animate-spin" size={14} /> TESTING…</> : <><ShieldCheck size={14} /> SAVE & TEST</>}
          </button>
        </form>
      )}
    </div>
  );
}
