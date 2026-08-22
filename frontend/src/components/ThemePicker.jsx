import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { applyTheme } from "@/components/ThemeSync";
import { toast } from "sonner";
import { Palette } from "lucide-react";

const THEMES = [
  { id: "kink",    label: "KINK",    hint: "Default — purple + slate" },
  { id: "neon",    label: "NEON",    hint: "Magenta + cyan on deep indigo" },
  { id: "dungeon", label: "DUNGEON", hint: "Red + amber on charred black" },
  { id: "moody",   label: "MOODY",   hint: "Blue + rose on midnight" },
];

/**
 * Owner-only theme picker. Persists the choice to backend settings so guests
 * see the same skin on next connect; pushes an immediate WS broadcast so
 * anyone already connected reskins live.
 */
export function ThemePicker() {
  const [current, setCurrent] = useState("kink");
  const [saving, setSaving] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/settings/theme");
        if (data?.theme) setCurrent(data.theme);
      } catch (_) {}
    })();
  }, []);

  const pick = async (id) => {
    if (id === current || saving) return;
    setSaving(id);
    // Optimistic local swap so the click feels instant.
    applyTheme(id);
    try {
      await api.put("/settings/theme", { theme: id });
      setCurrent(id);
      toast.success(`Room theme → ${id.toUpperCase()}`);
    } catch (e) {
      // Roll back to whatever we knew was correct.
      applyTheme(current);
      toast.error("Could not change theme");
    } finally { setSaving(null); }
  };

  return (
    <div className="hud-panel p-4 sm:p-5" data-testid="theme-picker">
      <div className="flex items-center gap-2 mb-3">
        <Palette size={14} className="text-[var(--kink-purple)]" />
        <span className="font-display font-black tracking-[0.15em] text-sm">ROOM THEME</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => pick(t.id)}
            disabled={saving !== null}
            data-testid={`theme-option-${t.id}`}
            data-active={current === t.id ? "true" : "false"}
            className={`text-left px-3 py-2.5 border transition-all ${
              current === t.id
                ? "border-[var(--kink-purple)] bg-[var(--kink-purple)]/15"
                : "border-[var(--kink-overlay)] hover:border-[var(--kink-purple)]/50 hover:bg-[var(--kink-purple)]/5"
            } disabled:opacity-50`}
          >
            <div className="font-display font-bold text-xs tracking-[0.15em] text-[var(--kink-text)]">
              {t.label}
            </div>
            <div className="font-mono-data text-[10px] text-[var(--kink-muted)] mt-0.5 leading-snug">
              {t.hint}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
