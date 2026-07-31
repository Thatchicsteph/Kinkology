import React, { useState } from "react";
import { Vibrate, Link2, Unlink, Power, PlugZap, Square, Play } from "lucide-react";
import { DEFAULT_INTIFACE_WS } from "@/lib/buttplug";
import { VIBRATION_PATTERNS } from "@/lib/vibrationPatterns";

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
          <p className="text-[var(--ossm-text-2)] text-sm mt-2">
            <span className="font-mono-data text-xs uppercase tracking-wide text-[var(--ossm-muted)]">MuSe / Love Spouse toys</span> —
            these don't speak Bluetooth GATT (they only listen for BLE broadcasts), so neither a browser nor Intiface
            can talk to one directly. A cheap ESP32 flashed with an open-source gateway firmware (e.g.{" "}
            <a href="https://github.com/Fi0nee/LS-Buttplug" target="_blank" rel="noreferrer" className="underline hover:text-[var(--ossm-cyan)]">
              LS-Buttplug
            </a>{" "}
            or{" "}
            <a href="https://github.com/IngeniousKink/LVS-Gateway" target="_blank" rel="noreferrer" className="underline hover:text-[var(--ossm-cyan)]">
              LVS-Gateway
            </a>
            ) sits in between: it broadcasts the Love Spouse commands on one side and shows up in Intiface Central as
            a regular Lovense toy on the other. Once that's flashed and Intiface sees it, it works here exactly like
            any other toy below — no separate setup in this app.
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
        <div className="mt-6 pt-6 border-t border-[var(--ossm-overlay)]">
          <div className="flex items-center justify-between gap-3 mb-3">
            <span className="font-mono-data text-xs uppercase tracking-wide text-[var(--ossm-muted)]">
              Vibration Patterns
            </span>
            {toys.activePattern && (
              <button
                onClick={toys.stopPattern}
                data-testid="toys-pattern-stop-button"
                className="flex items-center gap-1.5 border border-[var(--ossm-overlay)] px-3 py-1.5 font-mono-data text-xs hover:border-[var(--ossm-danger)]/50 hover:text-[var(--ossm-danger)] transition-colors"
              >
                <Square size={12} /> STOP PATTERN
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2" data-testid="toys-pattern-list">
            {VIBRATION_PATTERNS.map((p) => {
              const active = toys.activePattern === p.id;
              return (
                <button
                  key={p.id}
                  onClick={() => toys.startPattern(p.id)}
                  data-testid={`toys-pattern-${p.id}`}
                  title={p.description}
                  className={`flex items-center gap-1.5 border px-3 py-2 font-display text-xs tracking-[0.08em] transition-colors ${
                    active
                      ? "border-[var(--ossm-cyan)]/60 text-[var(--ossm-cyan)] glow-cyan"
                      : "border-[var(--ossm-overlay)] text-[var(--ossm-text-2)] hover:border-[var(--ossm-cyan)]/40"
                  }`}
                >
                  {active ? <Vibrate size={12} className="pulse-dot" /> : <Play size={12} />} {p.label.toUpperCase()}
                </button>
              );
            })}
          </div>
          {toys.linked && (
            <p className="font-mono-data text-[11px] text-[var(--ossm-muted)] mt-2">
              Starting a pattern switches toys out of LINKED TO SPEED and into manual mode.
            </p>
          )}
        </div>
      )}

      {toys.connected && toys.devices.length > 0 && (
        <div className="mt-6 pt-6 border-t border-[var(--ossm-overlay)] space-y-4">
          {toys.devices.map((d) => (
            <div key={d.index} className="flex items-center justify-between gap-4" data-testid={`toy-row-${d.index}`}>
              <span className="font-mono-data text-sm text-[var(--ossm-text-2)] truncate">{d.name}</span>
              {toys.linked ? (
                <span className="font-mono-data text-xs text-[var(--ossm-cyan)]">mirrors SPEED</span>
              ) : toys.activePattern ? (
                <span className="font-mono-data text-xs text-[var(--ossm-cyan)]">
                  running {toys.activePattern}
                </span>
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
