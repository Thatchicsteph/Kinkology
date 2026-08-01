import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, BACKEND_URL } from "@/lib/api";
import { toast } from "sonner";
import { ArrowLeft, Search, Download, Trash2, ShieldAlert, Activity, RefreshCw } from "lucide-react";
import kinkologyMark from "@/assets/kinkology-mark.png";

const PAGE = 50;

export const ACTION_LABELS = {
  login_success: "Login",
  login_failed: "Failed login",
  account_locked: "Account locked",
  owner_created: "Owner account created",
  twofa_enabled: "2FA enabled",
  twofa_disabled: "2FA disabled",
  code_created: "Code created",
  code_revoked: "Code revoked",
  code_extended: "Code extended",
  code_deleted: "Code deleted",
  limits_updated: "Safety limits updated",
  urls_updated: "Base URLs updated",
  emergency_stop: "Emergency stop",
  session_skipped: "Session skipped",
  logs_cleared: "Log cleared",
  guest_joined: "Guest joined queue",
  guest_active: "Guest took control",
  turn_ended: "Turn ended",
  device_connected: "Device connected",
  device_disconnected: "Device disconnected",
};

export const labelFor = (action) =>
  ACTION_LABELS[action] || (action || "").replace(/_/g, " ");

export function fmtTs(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return ts; }
}

export function detailText(detail) {
  if (detail == null || detail === "") return "";
  if (typeof detail === "string") return detail;
  return Object.entries(detail).map(([k, v]) => `${k}: ${v}`).join(" · ");
}

export function CategoryBadge({ category }) {
  const security = category === "security";
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono-data text-[10px] tracking-[0.1em] px-2 py-1 border ${
      security ? "border-[var(--kink-purple)]/40 text-[var(--kink-purple)]" : "border-amber-400/40 text-amber-300"
    }`} data-testid={`log-cat-${category}`}>
      {security ? <ShieldAlert size={11} /> : <Activity size={11} />}
      {security ? "SECURITY" : "SESSION"}
    </span>
  );
}

export default function Logs() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  const params = useCallback((extra = {}) => {
    const p = { limit: PAGE, ...extra };
    if (category) p.category = category;
    if (q.trim()) p.q = q.trim();
    if (start) p.start = `${start}T00:00:00`;
    if (end) p.end = `${end}T23:59:59`;
    return p;
  }, [category, q, start, end]);

  const load = useCallback(async (reset = true) => {
    setLoading(true);
    try {
      const nextSkip = reset ? 0 : skip + PAGE;
      const { data } = await api.get("/logs", { params: params({ skip: nextSkip }) });
      setTotal(data.total);
      setSkip(nextSkip);
      setItems((prev) => (reset ? data.items : [...prev, ...data.items]));
    } catch (e) {
      toast.error("Could not load activity log");
    } finally {
      setLoading(false);
    }
  }, [skip, params]);

  useEffect(() => { load(true); /* eslint-disable-next-line */ }, []);

  const applyFilters = () => load(true);

  const clearAll = async () => {
    if (!window.confirm("Delete ALL activity log entries? This cannot be undone.")) return;
    try {
      await api.delete("/logs");
      toast.success("Activity log cleared");
      load(true);
    } catch (e) { toast.error("Could not clear log"); }
  };

  const exportLog = async (format) => {
    try {
      const res = await api.get("/logs/export", { params: params({ format }), responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `ossm-audit.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success(`Exported ${format.toUpperCase()}`);
    } catch (e) { toast.error("Export failed"); }
  };

  return (
    <div className="relative z-10 min-h-screen max-w-5xl mx-auto px-5 sm:px-8 py-6" data-testid="logs-page">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-2.5">
          <img src={kinkologyMark} alt="Kinkology" style={{ height: 22, width: 22 }} className="rounded-sm" />
          <span className="font-display font-black tracking-[0.2em] text-lg">KINKOLOGY</span>
          <span className="font-mono-data text-xs text-[var(--kink-muted)] ml-2 hidden sm:inline">ACTIVITY LOG</span>
        </div>
        <button onClick={() => navigate("/admin")} data-testid="logs-back-button"
          className="flex items-center gap-1.5 font-mono-data text-xs text-[var(--kink-text-2)] hover:text-[var(--kink-purple)] transition-colors">
          <ArrowLeft size={14} /> DASHBOARD
        </button>
      </header>

      <div className="hud-panel p-5 sm:p-6 mb-6">
        <div className="flex flex-col lg:flex-row lg:items-end gap-4">
          <div className="flex-1">
            <label className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)]">SEARCH</label>
            <div className="relative mt-2">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--kink-muted)]" />
              <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && applyFilters()}
                data-testid="log-search-input" placeholder="action, actor or code"
                className="w-full bg-[var(--kink-base)] border border-[var(--kink-overlay)] pl-9 pr-3 py-2.5 font-mono-data text-sm outline-none focus:border-[var(--kink-purple)] transition-colors" />
            </div>
          </div>
          <div>
            <label className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)]">CATEGORY</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)} data-testid="log-filter-category"
              className="w-full mt-2 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-sm outline-none focus:border-[var(--kink-purple)] transition-colors">
              <option value="">All</option>
              <option value="security">Security</option>
              <option value="session">Session</option>
            </select>
          </div>
          <div>
            <label className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)]">FROM</label>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} data-testid="log-filter-start"
              className="w-full mt-2 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-sm outline-none focus:border-[var(--kink-purple)] transition-colors" />
          </div>
          <div>
            <label className="font-display text-xs tracking-[0.15em] text-[var(--kink-text-2)]">TO</label>
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} data-testid="log-filter-end"
              className="w-full mt-2 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-sm outline-none focus:border-[var(--kink-purple)] transition-colors" />
          </div>
          <button onClick={applyFilters} data-testid="log-filter-apply"
            className="flex items-center justify-center gap-2 bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold text-xs tracking-[0.1em] px-5 py-3 active:scale-95 transition-transform">
            <RefreshCw size={14} /> APPLY
          </button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 mt-5 pt-5 border-t border-[var(--kink-overlay)]">
          <span className="font-mono-data text-xs text-[var(--kink-muted)]" data-testid="log-total">{total} event{total !== 1 ? "s" : ""}</span>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => exportLog("csv")} data-testid="log-export-csv"
              className="flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-xs hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors">
              <Download size={13} /> CSV
            </button>
            <button onClick={() => exportLog("json")} data-testid="log-export-json"
              className="flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-xs hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors">
              <Download size={13} /> JSON
            </button>
            <button onClick={clearAll} data-testid="log-clear-button"
              className="flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-xs text-[var(--kink-text-2)] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] transition-colors">
              <Trash2 size={13} /> CLEAR ALL
            </button>
          </div>
        </div>
      </div>

      <div className="hud-panel overflow-hidden" data-testid="logs-table">
        {items.length === 0 && !loading && (
          <p className="font-mono-data text-sm text-[var(--kink-muted)] py-16 text-center">No activity yet.</p>
        )}
        <div className="divide-y divide-[var(--kink-overlay)]">
          {items.map((it) => (
            <div key={it.id} data-testid={`log-row-${it.action}`} className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 px-4 sm:px-6 py-3.5 hover:bg-[var(--kink-base)]/40 transition-colors">
              <span className="font-mono-data text-xs text-[var(--kink-muted)] w-40 shrink-0">{fmtTs(it.ts)}</span>
              <CategoryBadge category={it.category} />
              <span className="font-display text-sm text-white flex-1 min-w-0 truncate">
                {labelFor(it.action)}
                {it.target && <span className="text-[var(--kink-purple)] font-mono-data ml-2">{it.target}</span>}
              </span>
              <span className="font-mono-data text-xs text-[var(--kink-text-2)] truncate max-w-[220px]">{it.actor}</span>
              {detailText(it.detail) && (
                <span className="font-mono-data text-[11px] text-[var(--kink-muted)] truncate max-w-[220px]">{detailText(it.detail)}</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {items.length < total && (
        <div className="flex justify-center mt-6">
          <button onClick={() => load(false)} disabled={loading} data-testid="log-load-more"
            className="border border-[var(--kink-overlay)] px-6 py-3 font-mono-data text-xs hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors disabled:opacity-50">
            {loading ? "LOADING…" : `LOAD MORE (${total - items.length} left)`}
          </button>
        </div>
      )}
    </div>
  );
}
