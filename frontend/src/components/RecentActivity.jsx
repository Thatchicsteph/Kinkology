import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { ScrollText, ChevronRight } from "lucide-react";
import { labelFor, fmtTs, CategoryBadge } from "@/pages/Logs";

export function RecentActivity() {
  const [items, setItems] = useState([]);

  const load = async () => {
    try {
      const { data } = await api.get("/logs", { params: { limit: 6 } });
      setItems(data.items);
    } catch (e) {}
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="activity-log-card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
          <ScrollText size={18} className="text-[var(--ossm-cyan)]" /> Recent Activity
        </h2>
        <Link to="/admin/logs" data-testid="view-all-logs-link"
          className="flex items-center gap-1 font-mono-data text-xs text-[var(--ossm-text-2)] hover:text-[var(--ossm-cyan)] transition-colors">
          VIEW ALL <ChevronRight size={13} />
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="font-mono-data text-sm text-[var(--ossm-muted)] py-4 text-center">No activity yet.</p>
      ) : (
        <div className="space-y-2.5" data-testid="activity-log-list">
          {items.map((it) => (
            <div key={it.id} className="flex items-center gap-3 text-sm">
              <CategoryBadge category={it.category} />
              <span className="font-display text-white truncate flex-1 min-w-0">
                {labelFor(it.action)}
                {it.target && <span className="text-[var(--ossm-cyan)] font-mono-data ml-1.5">{it.target}</span>}
              </span>
              <span className="font-mono-data text-[11px] text-[var(--ossm-muted)] shrink-0">{fmtTs(it.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
