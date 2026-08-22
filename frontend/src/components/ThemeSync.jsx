import { useEffect } from "react";
import { api } from "@/lib/api";

const VALID = new Set(["kink", "neon", "dungeon", "moody"]);

/**
 * Read the current room theme from the backend once on mount and paint it
 * onto <html data-theme="…"> so every viewer sees the same skin. Owner picker
 * and the WebSocket `theme` push both funnel through the same DOM write so
 * they stay in sync.
 *
 * Also exposes a live setter via CustomEvent("kinkology:theme", {detail: name})
 * so any component can request a swap without prop-drilling.
 */
export function ThemeSync() {
  useEffect(() => {
    const apply = (name) => {
      const t = (name || "").toLowerCase();
      if (!VALID.has(t)) return;
      document.documentElement.setAttribute("data-theme", t);
      try { window.localStorage.setItem("kinkology_theme", t); } catch (_) {}
    };
    // First: paint the last-known local value so the initial frame doesn't flash.
    try {
      const cached = window.localStorage.getItem("kinkology_theme");
      if (cached) apply(cached);
    } catch (_) {}
    // Then: fetch the authoritative value.
    (async () => {
      try {
        const { data } = await api.get("/settings/theme");
        apply(data?.theme);
      } catch (_) {}
    })();
    // Listen for cross-component swaps (owner picker + WS pushes).
    const onEvt = (e) => apply(e.detail);
    window.addEventListener("kinkology:theme", onEvt);
    return () => window.removeEventListener("kinkology:theme", onEvt);
  }, []);
  return null;
}

/** Fire a theme swap without importing this module directly. */
export function applyTheme(name) {
  try {
    window.dispatchEvent(new CustomEvent("kinkology:theme", { detail: name }));
  } catch (_) {}
}
