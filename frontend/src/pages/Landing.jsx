import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ShieldCheck, Timer, Bluetooth } from "lucide-react";
import kinkologyMark from "@/assets/kinkology-mark.png";

export default function Landing() {
  const [code, setCode] = useState("");
  const navigate = useNavigate();

  const enter = (e) => {
    e.preventDefault();
    if (code.trim()) navigate(`/c/${code.trim().toUpperCase()}`);
  };

  return (
    <div className="relative min-h-screen z-10 flex flex-col">
      <header className="flex items-center justify-between px-6 sm:px-10 py-6">
        <div className="flex items-center gap-2.5">
          <img src={kinkologyMark} alt="Kinkology" className="h-7 w-7 rounded-sm" />
          <span className="font-display font-black tracking-[0.2em] text-lg">KINKOLOGY</span>
        </div>
        <a
          href="/admin"
          data-testid="admin-link"
          className="font-mono-data text-xs text-[var(--kink-muted)] hover:text-[var(--kink-purple)] transition-colors"
        >
          OWNER ACCESS
        </a>
      </header>

      <main className="flex-1 flex flex-col justify-center max-w-5xl w-full mx-auto px-6 sm:px-10 py-12">
        <div className="fade-up max-w-3xl">
          <span className="font-mono-data text-xs tracking-[0.2em] text-[var(--kink-purple)] border border-[var(--kink-purple)]/30 px-3 py-1.5">
            REMOTE DEVICE CONTROL BRIDGE
          </span>
          <h1 className="font-display font-black uppercase tracking-[0.03em] text-4xl sm:text-5xl lg:text-6xl mt-8 leading-[1.05]">
            Hand over the<br />
            <span className="text-[var(--kink-purple)] text-glow-purple">controls</span>, on your terms.
          </h1>
          <p className="text-[var(--kink-text-2)] text-base sm:text-lg mt-6 max-w-xl leading-relaxed">
            A private bridge from the internet to your OSSM device over Bluetooth. Grant a guest a set
            amount of time, hold the queue, and pull the plug instantly, anytime.
          </p>
        </div>

        <form onSubmit={enter} className="fade-up hud-panel mt-12 p-6 sm:p-8 max-w-xl" style={{ animationDelay: "0.1s" }}>
          <label className="font-display text-xs tracking-[0.2em] text-[var(--kink-text-2)]">
            ENTER YOUR ACCESS CODE
          </label>
          <div className="flex flex-col sm:flex-row gap-3 mt-4">
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="e.g. K7M2QX"
              maxLength={12}
              data-testid="access-code-input"
              className="flex-1 bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-4 py-3.5 font-mono-data text-xl tracking-[0.3em] uppercase outline-none focus:border-[var(--kink-purple)] transition-colors placeholder:text-[var(--kink-muted)] placeholder:tracking-normal placeholder:text-base"
            />
            <button
              type="submit"
              data-testid="enter-code-button"
              className="bg-[var(--kink-purple)] text-[var(--kink-base)] font-display font-bold tracking-[0.15em] px-6 py-3.5 flex items-center justify-center gap-2 active:scale-95 transition-transform"
            >
              CONNECT <ArrowRight size={18} />
            </button>
          </div>
        </form>

        <div className="grid sm:grid-cols-3 gap-4 mt-12 max-w-3xl">
          {[
            { icon: Timer, t: "Timed control", d: "Every guest gets exactly the minutes you grant." },
            { icon: ShieldCheck, t: "Owner override", d: "Emergency stop and skip, always in your hands." },
            { icon: Bluetooth, t: "Local BLE", d: "Commands relay to your device over Web Bluetooth." },
          ].map((f) => (
            <div key={f.t} className="hud-panel p-5">
              <f.icon className="text-[var(--kink-purple)]" size={20} />
              <h3 className="font-display tracking-[0.1em] text-sm mt-3">{f.t}</h3>
              <p className="text-[var(--kink-text-2)] text-sm mt-1.5 leading-relaxed">{f.d}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
