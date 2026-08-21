import React, { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { ShieldCheck, ShieldAlert, Copy, Loader2, Check } from "lucide-react";
import { toast } from "sonner";

export function TwoFactorPanel() {
  const [enabled, setEnabled] = useState(null); // null=loading
  const [step, setStep] = useState("idle"); // idle | setup | codes | disable
  const [setupData, setSetupData] = useState(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadStatus = async () => {
    try { const { data } = await api.get("/auth/2fa/status"); setEnabled(data.enabled); }
    catch (e) { setEnabled(false); }
  };
  useEffect(() => { loadStatus(); }, []);

  const startSetup = async () => {
    setBusy(true); setError("");
    try {
      const { data } = await api.post("/auth/2fa/setup/start");
      setSetupData(data);
      setStep("setup");
      setCode("");
    } catch (e) { toast.error("Could not start 2FA setup"); }
    finally { setBusy(false); }
  };

  const verifySetup = async () => {
    setBusy(true); setError("");
    try {
      const { data } = await api.post("/auth/2fa/setup/verify", { code });
      setRecoveryCodes(data.recovery_codes);
      setStep("codes");
      setEnabled(true);
      toast.success("2FA enabled");
    } catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail) || e.message); }
    finally { setBusy(false); }
  };

  const disable2fa = async () => {
    setBusy(true); setError("");
    try {
      await api.post("/auth/2fa/disable", { code });
      setEnabled(false); setStep("idle"); setCode("");
      toast.success("2FA disabled");
    } catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail) || e.message); }
    finally { setBusy(false); }
  };

  const copyCodes = () => {
    navigator.clipboard.writeText(recoveryCodes.join("\n"));
    toast.success("Recovery codes copied");
  };

  return (
    <div className="hud-panel p-5 sm:p-6" data-testid="twofa-panel">
      <h2 className="font-display font-black uppercase tracking-[0.08em] text-lg flex items-center gap-2 mb-2">
        {enabled ? <ShieldCheck size={18} className="text-[var(--kink-purple)]" /> : <ShieldAlert size={18} className="text-[var(--kink-danger)]" />}
        Two-Factor Auth
      </h2>

      {enabled === null && <Loader2 className="animate-spin text-[var(--kink-purple)] mt-2" size={20} />}

      {/* ENABLED, idle */}
      {enabled && step !== "codes" && step !== "disable" && (
        <>
          <p className="text-[var(--kink-text-2)] text-sm" data-testid="twofa-status">
            <span className="text-[var(--kink-purple)] font-mono-data">● ACTIVE</span> — your login is protected by an authenticator app.
          </p>
          <button onClick={() => { setStep("disable"); setCode(""); setError(""); }} data-testid="disable-2fa-toggle"
            className="mt-4 border border-[var(--kink-overlay)] text-[var(--kink-text-2)] hover:border-[var(--kink-danger)] hover:text-[var(--kink-danger)] px-4 py-2.5 font-display text-xs tracking-[0.1em] transition-colors">
            DISABLE 2FA
          </button>
        </>
      )}

      {/* DISABLED, idle */}
      {enabled === false && step === "idle" && (
        <>
          <p className="text-[var(--kink-text-2)] text-sm" data-testid="twofa-status">
            <span className="text-[var(--kink-danger)] font-mono-data">● OFF</span> — add an extra step at login with an authenticator app.
          </p>
          <button onClick={startSetup} disabled={busy} data-testid="enable-2fa-button"
            className="mt-4 bg-[var(--kink-purple)] text-[var(--kink-base)] px-5 py-2.5 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform disabled:opacity-50">
            {busy ? "…" : "ENABLE 2FA"}
          </button>
        </>
      )}

      {/* SETUP: show QR + code entry */}
      {step === "setup" && setupData && (
        <div className="mt-2">
          <p className="text-[var(--kink-text-2)] text-sm mb-4">Scan with Google Authenticator, Authy, or 1Password, then enter the 6-digit code.</p>
          <div className="flex flex-col sm:flex-row gap-5">
            <img src={setupData.qr_code_data_url} alt="2FA QR code" data-testid="twofa-qr" className="h-44 w-44 bg-white p-2 rounded" />
            <div className="flex-1">
              <span className="font-display text-[10px] tracking-[0.15em] text-[var(--kink-muted)]">OR ENTER KEY MANUALLY</span>
              <p className="font-mono-data text-xs break-all text-[var(--kink-text-2)] mt-1 mb-4 bg-[var(--kink-base)] border border-[var(--kink-overlay)] p-2" data-testid="twofa-secret">{setupData.secret}</p>
              {error && <p className="text-[var(--kink-danger)] text-xs mb-2" data-testid="twofa-setup-error">{error}</p>}
              <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="123456" data-testid="twofa-setup-code"
                className="w-full bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-lg tracking-[0.2em] text-center outline-none focus:border-[var(--kink-purple)]" />
              <div className="flex gap-2 mt-3">
                <button onClick={verifySetup} disabled={busy || code.length !== 6} data-testid="twofa-verify-button"
                  className="flex-1 bg-[var(--kink-purple)] text-[var(--kink-base)] py-2.5 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform disabled:opacity-50">
                  {busy ? "…" : "VERIFY & ENABLE"}
                </button>
                <button onClick={() => { setStep("idle"); setError(""); }} className="border border-[var(--kink-overlay)] px-4 font-mono-data text-xs text-[var(--kink-text-2)]">CANCEL</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* RECOVERY CODES */}
      {step === "codes" && (
        <div className="mt-2" data-testid="recovery-codes">
          <p className="text-sm text-[var(--kink-purple)] flex items-center gap-2"><Check size={16} /> 2FA is on. Save these backup codes now.</p>
          <p className="text-[var(--kink-text-2)] text-xs mt-1 mb-3">Each works once if you lose your authenticator. They won't be shown again.</p>
          <div className="grid grid-cols-2 gap-2 bg-[var(--kink-base)] border border-[var(--kink-overlay)] p-3">
            {recoveryCodes.map((c) => (
              <span key={c} className="font-mono-data text-sm text-white tracking-wide">{c}</span>
            ))}
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={copyCodes} data-testid="copy-recovery-codes" className="flex items-center gap-1.5 border border-[var(--kink-overlay)] px-3 py-2 font-mono-data text-xs hover:border-[var(--kink-purple)]/50 hover:text-[var(--kink-purple)] transition-colors">
              <Copy size={13} /> COPY
            </button>
            <button onClick={() => setStep("idle")} data-testid="recovery-done-button" className="flex-1 bg-[var(--kink-purple)] text-[var(--kink-base)] py-2 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform">
              I'VE SAVED THEM
            </button>
          </div>
        </div>
      )}

      {/* DISABLE confirm */}
      {step === "disable" && (
        <div className="mt-2">
          <p className="text-[var(--kink-text-2)] text-sm mb-3">Enter a current authenticator code (or a recovery code) to turn 2FA off.</p>
          {error && <p className="text-[var(--kink-danger)] text-xs mb-2" data-testid="disable-2fa-error">{error}</p>}
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456 or backup code" data-testid="disable-2fa-code"
            className="w-full bg-[var(--kink-base)] border border-[var(--kink-overlay)] px-3 py-2.5 font-mono-data text-center tracking-[0.1em] outline-none focus:border-[var(--kink-danger)]" />
          <div className="flex gap-2 mt-3">
            <button onClick={disable2fa} disabled={busy || !code} data-testid="disable-2fa-button"
              className="flex-1 bg-[var(--kink-danger)] text-white py-2.5 font-display font-bold tracking-[0.1em] active:scale-95 transition-transform disabled:opacity-50">
              {busy ? "…" : "CONFIRM DISABLE"}
            </button>
            <button onClick={() => { setStep("idle"); setError(""); }} className="border border-[var(--kink-overlay)] px-4 font-mono-data text-xs text-[var(--kink-text-2)]">CANCEL</button>
          </div>
        </div>
      )}
    </div>
  );
}
