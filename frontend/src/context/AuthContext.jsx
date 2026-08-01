import React, { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null=checking, false=guest, obj=auth

  useEffect(() => {
    const token = localStorage.getItem("ossm_token");
    if (!token) {
      setUser(false);
      return;
    }
    api
      .get("/auth/me")
      .then(({ data }) => setUser(data))
      .catch(() => {
        localStorage.removeItem("ossm_token");
        setUser(false);
      });
  }, []);

  const checkSetup = async () => {
    const { data } = await api.get("/setup/status");
    return data.needs_setup;
  };

  const setupAdmin = async (email, password, localUrl, publicUrl) => {
    const { data } = await api.post("/setup", {
      email,
      password,
      local_url: localUrl,
      public_url: publicUrl,
    });
    localStorage.setItem("ossm_token", data.token);
    setUser(data.user);
    return data.user;
  };

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data.mfa_required) {
      return { mfaRequired: true, mfaToken: data.mfa_token };
    }
    localStorage.setItem("ossm_token", data.token);
    setUser(data.user);
    return { mfaRequired: false, user: data.user };
  };

  const verify2fa = async ({ mfaToken, code, recoveryCode }) => {
    const { data } = await api.post("/auth/2fa/login", {
      mfa_token: mfaToken,
      code: code || null,
      recovery_code: recoveryCode || null,
    });
    localStorage.setItem("ossm_token", data.token);
    setUser(data.user);
    return data.user;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (e) {}
    localStorage.removeItem("ossm_token");
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, login, verify2fa, logout, checkSetup, setupAdmin }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
