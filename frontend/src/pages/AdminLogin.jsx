import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { formatApiErrorDetail } from "@/lib/api";
import { Radio, Lock } from "lucide-react";

export default function AdminLogin() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/admin");
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative z-10 min-h-screen flex items-center justify-center px-6">
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
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="login-email"
              required
              className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3 outline-none focus:border-[var(--ossm-cyan)] transition-colors"
            />
          </div>
          <div>
            <label className="font-display text-xs tracking-[0.15em] text-[var(--ossm-text-2)]">PASSWORD</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login-password"
              required
              className="w-full mt-2 bg-[var(--ossm-base)] border border-[var(--ossm-overlay)] px-4 py-3 outline-none focus:border-[var(--ossm-cyan)] transition-colors"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          data-testid="login-submit"
          className="w-full mt-7 bg-[var(--ossm-cyan)] text-[var(--ossm-base)] font-display font-bold tracking-[0.15em] py-3.5 active:scale-95 transition-transform disabled:opacity-50"
        >
          {loading ? "AUTHENTICATING…" : "SIGN IN"}
        </button>
      </form>
    </div>
  );
}
