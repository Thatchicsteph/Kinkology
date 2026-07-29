import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { formatApiErrorDetail } from "@/lib/api";
import { Radio, Lock, ShieldCheck, KeyRound } from "lucide-react";

export default function AdminLogin() {
  const { login, verify2fa } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 2FA step
  const [mfaToken, setMfaToken] = useState(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await login(email, password);
      if (res.mfaRequired) {
        setMfaToken(res.mfaToken);
      } else {
        navigate("/admin");
      }
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const submit2fa = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await verify2fa({
        mfaToken,
        code: useRecovery ? null : code,
        recoveryCode: useRecovery ? code : null,
      });
      navigate("/admin");
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      if (err.response?.status === 401 && String(err.response?.data?.detail || "").includes("expired")) {
        setMfaToken(null);
        setCode("");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative z-10 min-h-screen flex items-center justify-center px-6">
      {!mfaToken ? (
        <form onSubmit={submit} className="hud-panel w-full max-w-md p-8 fade-up" data-testid="login-form">
          <div className="flex items-center gap-2.5 mb-1">
            <Radio className="text-[var(--ossm-cyan)]" size={22} />
            <span className="font-display font-black tracking-[0.2em] text-lg">OSSM BRIDGE</span>
          </div>
          <h1 className="font-display font-black uppercase tracking-[0.05em] text-2xl mt-6 flex items-center gap-2">
            <Lock size={20} className="text-[var(--ossm-cyan)]" /> Owner Access
          </h1>
          <p className="text-[var(--ossm-text-2)] text-sm mt-2">Sign in to manage your device and access codes.</p>

          {error && (
            <div className="mt-5 border border-[var(--ossm-danger)]/40 bg-[var(--ossm-danger)]/10 text-[var(--ossm-danger)] text-sm px-4 py-3" data-testid="login-error">
              {error}
            </div>
          )}

          <div className="mt-6 space-y-4">
            <div>
              <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">EMAIL</label>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="login-email" required
                className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3 outline-none focus:border-[var(--ossm-cyan)] transition-colors" />
            </div>
            <div>
              <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">PASSWORD</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="login-password" required
                className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3 outline-none focus:border-[var(--ossm-cyan)] transition-colors" />
            </div>
          </div>

          <button type="submit" disabled={loading} data-testid="login-submit"
            className="w-full mt-7 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] font-display font-bold tracking-[0.15em] py-3.5 active:scale-95 transition-transform disabled:opacity-50">
            {loading ? "AUTHENTICATING…" : "SIGN IN"}
          </button>
        </form>
      ) : (
        <form onSubmit={submit2fa} className="hud-panel w-full max-w-md p-8 fade-up" data-testid="twofa-form">
          <div className="flex items-center gap-2.5 mb-1">
            <Radio className="text-[var(--ossm-cyan)]" size={22} />
            <span className="font-display font-black tracking-[0.2em] text-lg">OSSM BRIDGE</span>
          </div>
          <h1 className="font-display font-black uppercase tracking-[0.05em] text-2xl mt-6 flex items-center gap-2">
            <ShieldCheck size={20} className="text-[var(--ossm-cyan)]" /> Two-Factor
          </h1>
          <p className="text-[var(--ossm-text-2)] text-sm mt-2">
            {useRecovery ? "Enter one of your backup recovery codes." : "Enter the 6-digit code from your authenticator app."}
          </p>

          {error && (
            <div className="mt-5 border border-[var(--ossm-danger)]/40 bg-[var(--ossm-danger)]/10 text-[var(--ossm-danger)] text-sm px-4 py-3" data-testid="twofa-error">
              {error}
            </div>
          )}

          <input
            value={code}
            onChange={(e) => setCode(useRecovery ? e.target.value.toUpperCase() : e.target.value.replace(/\D/g, "").slice(0, 6))}
            data-testid="twofa-code"
            autoFocus
            placeholder={useRecovery ? "XXXX-XXXX-XXXX" : "123456"}
            className="w-full mt-6 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3.5 font-mono-data text-2xl tracking-[0.3em] text-center outline-none focus:border-[var(--ossm-cyan)] transition-colors placeholder:text-[var(--ossm-muted)] placeholder:text-lg"
          />

          <button type="submit" disabled={loading || !code} data-testid="twofa-submit"
            className="w-full mt-6 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] font-display font-bold tracking-[0.15em] py-3.5 active:scale-95 transition-transform disabled:opacity-50">
            {loading ? "VERIFYING…" : "VERIFY"}
          </button>

          <button type="button" onClick={() => { setUseRecovery((v) => !v); setCode(""); setError(""); }}
            data-testid="twofa-toggle-recovery"
            className="w-full mt-4 flex items-center justify-center gap-2 font-mono-data text-xs text-[var(--ossm-text-2)] hover:text-[var(--ossm-cyan)] transition-colors">
            <KeyRound size={13} /> {useRecovery ? "Use authenticator code instead" : "Use a backup recovery code"}
          </button>
        </form>
      )}
    </div>
  );
}
