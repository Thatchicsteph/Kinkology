import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Landing from "@/pages/Landing";
import AdminLogin from "@/pages/AdminLogin";
import AdminDashboard from "@/pages/AdminDashboard";
import GuestControl from "@/pages/GuestControl";
import Overlay from "@/pages/Overlay";
import { Loader2 } from "lucide-react";

function ProtectedRoute({ children }) {
  const { user } = useAuth();
  if (user === null) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="animate-spin text-[var(--ossm-cyan)]" size={28} />
      </div>
    );
  }
  if (!user) return <Navigate to="/admin/login" replace />;
  return children;
}

function App() {
  return (
    <AuthProvider>
      <Toaster theme="dark" position="top-center" toastOptions={{ style: { background: "#161618", border: "1px solid #222225", color: "#fff", fontFamily: "IBM Plex Sans" } }} />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/admin/login" element={<AdminLogin />} />
          <Route path="/admin" element={<ProtectedRoute><AdminDashboard /></ProtectedRoute>} />
          <Route path="/c/:code" element={<GuestControl />} />
          <Route path="/overlay" element={<Overlay />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
