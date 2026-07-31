import React, { useState } from "react";
import { Vibrate, Link2, Unlink, Power, PlugZap } from "lucide-react";
import { DEFAULT_INTIFACE_WS } from "@/lib/buttplug";

function StatusPill({ ok, okText, offText }) {
  return (
    <span className={`inline-flex items-center gap-2 font-mono-data text-xs px-3 py-1.5 border ${
      ok ? "border-[var(--ossm-cyan)]/40 text-[var(--ossm-cyan)]" : "border-[var(--ossm-overlay)] text-[var(--ossm-muted)]"
    }`}>
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-[var(--ossm-cyan)] pulse-dot" : "bg-[var(--ossm-muted)]"}`} />
      {ok ? okText : offText}
    </span>
  );
}

export function ToysPanel({ toys }) {
  const [url, setUrl] = useState(DEFAULT_INTIFACE_WS);

  return (
    <div className="hud-panel p-5 sm:p-6 mb-6" data-testid="toys-panel">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2">
            <Vibrate size={18} className="text-[var(--ossm-cyan)]" /> Toys — Lovense & Other Bluetooth Toys
          </h2>
          <p className="text-[var(--ossm-text-2)] text-sm mt-1">
            Connects to <span className="font-mono-data">Intiface Central</span> running on this machine —
            a free local app that holds the Bluetooth link to Lovense and dozens of other toy brands.{" "}
            <a href="https://intiface.com" target="_blank" rel="noreferrer" className="underline hover:text-[var(--ossm-cyan)]">
              Get it here
            </a>, then press "Start Server" in it before connecting below. Works standalone — no OSSM required —
            or synced to the OSSM's SPEED via the toggle once connected.
          </p>
          <div className="flex flex-wrap items-center gap-3 mt-3">
            <StatusPill ok={toys.connected} okText="INTIFACE ONLINE" offText="INTIFACE OFFLINE" />
            {toys.connected && (
              <span className="font-mono-data text-xs text-[var(--ossm-muted)]" data-testid="toys-found-count">
                {toys.devices.length} toy{toys.devices.length === 1 ? "" : "s"} found
              </span>
            )}
          </div>
          {!toys.connected && (
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="mt-3 bg-transparent border border-[var(--ossm-overlay)] px-3 py-2 font-mono-data text-xs w-64 focus:outline-none focus:border-[var(--ossm-cyan)]/40"
              placeholder={DEFAULT_INTIFACE_WS}
              data-testid="intiface-url-input"
            />
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {toys.connected ? (
            <>
              <button
                onClick={() => toys.setLinked(!toys.linked)}
                data-testid="toys-link-toggle"
                className={`flex items-center gap-2 border px-4 py-3 font-display text-xs tracking-[0.1em] transition-colors ${
                  toys.linked
                    ? "border-[var(--ossm-cyan)]/50 text-[var(--ossm-cyan)]"
                    : "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-cyan)]/40"
                }`}
              >
                {toys.linked ? <Link2 size={16} /> : <Unlink size={16} />} {toys.linked ? "LINKED TO SPEED" : "MANUAL"}
              </button>
              <button
                onClick={toys.stopAllToys}
                data-testid="toys-stop-button"
                className="flex items-center gap-2 bg-[var(--ossm-danger)] text-white px-4 py-3 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform"
              >
                <Power size={16} /> STOP TOYS
              </button>
              <button
                onClick={toys.disconnect}
                data-testid="toys-disconnect-button"
                className="flex items-center gap-2 border border-[var(--ossm-overlay)] px-4 py-3 font-display text-xs tracking-[0.1em] hover:border-[var(--ossm-danger)]/50 hover:text-[var(--ossm-danger)] transition-colors"
              >
                DISCONNECT
              </button>
            </>
          ) : (
            <button
              onClick={() => toys.connect(url)}
              data-testid="toys-connect-button"
              className="flex items-center gap-2 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] px-5 py-3 font-display font-bold tracking-[0.1em] glow-cyan active:scale-95 transition-transform"
            >
              <PlugZap size={16} /> CONNECT TOYS
            </button>
          )}
        </div>
      </div>

      {toys.connected && toys.devices.length === 0 && (
        <p className="font-mono-data text-xs text-[var(--ossm-muted)] mt-4" data-testid="toys-empty-note">
          No toys found yet — put your device in pairing mode near this computer. Intiface scans automatically.
        </p>
      )}

      {toys.connected && toys.devices.length > 0 && (
        <div className="mt-6 pt-6 border-t border-[var(--ossm-overlay)] space-y-4">
          {toys.devices.map((d) => (
            <div key={d.index} className="flex items-center justify-between gap-4" data-testid={`toy-row-${d.index}`}>
              <span className="font-mono-data text-sm text-[var(--ossm-text-2)] truncate">{d.name}</span>
              {toys.linked ? (
                <span className="font-mono-data text-xs text-[var(--ossm-cyan)]">mirrors SPEED</span>
              ) : (
                <input
                  type="range"
                  min={0}
                  max={100}
                  defaultValue={0}
                  onChange={(e) => toys.setDeviceIntensity(d.index, Number(e.target.value) / 100)}
                  className="w-40 accent-[var(--ossm-cyan)]"
                  data-testid={`toy-slider-${d.index}`}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
