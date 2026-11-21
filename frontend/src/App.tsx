import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, clearToken, setToken } from "./api";
import Today from "./pages/Today";
import { Accounts, Account } from "./pages/Accounts";
import Opportunity from "./pages/Opportunity";
import Explorer from "./pages/Explorer";
import Admin from "./pages/Admin";
import Guide from "./pages/Guide";
import { IconAccounts, IconAdmin, IconExplorer, IconGuide, IconToday } from "./components/ui";

type Me = { email: string; role: string; permissions: string[] };
declare global { interface Window { google?: any } }

function Login({ onDone }: { onDone: () => void }) {
  const [cfg, setCfg] = useState<any>(null);
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { fetch("/api/config").then((r) => r.json()).then(setCfg).catch(() => setErr("Cannot reach the server.")); }, []);
  useEffect(() => {
    if (!cfg?.google_client_id) return;
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client"; s.async = true;
    s.onload = () => {
      window.google.accounts.id.initialize({ client_id: cfg.google_client_id, callback: (r: any) => { setToken(r.credential); onDone(); } });
      window.google.accounts.id.renderButton(document.getElementById("g-btn"), { theme: "outline", size: "large", text: "signin_with", shape: "rectangular", width: 320 });
    };
    s.onerror = () => setErr("Google Sign-In could not be loaded.");
    document.head.appendChild(s);
    return () => { s.remove(); };
  }, [cfg]);
  return (
    <div className="login">
      <div className="login-l">
        <div className="brand" style={{ border: 0, padding: 0, margin: 0 }}><span className="brand-mark" /><div><div className="brand-name">The Codest</div><div className="brand-sub">Revenue Intelligence</div></div></div>
        <div><h1>See which engineering work is real, and who owns it.</h1><p style={{ marginTop: 22 }}>Every recommendation shows its evidence, what is still unknown, and one specific next step. Nothing is sent or changed in your CRM without approval.</p></div>
        <div className="small" style={{ color: "#B9BCE3", position: "relative", zIndex: 1 }}>Access is limited to people an administrator has added.</div>
      </div>
      <div className="login-r"><div className="login-card">
        <div className="eyebrow">Sign in</div><h2 style={{ fontSize: 24, marginBottom: 18 }}>Welcome back</h2>
        {cfg?.google_client_id ? <div id="g-btn" /> :
          cfg && cfg.env !== "production" ? <><p className="sub" style={{ marginBottom: 14 }}>Development environment. Paste a signed development token.</p>
            <input aria-label="Development token" type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder="Development token" style={{ width: "100%", marginBottom: 14 }} />
            <button className="primary" onClick={() => { setToken(t); onDone(); }}>Continue</button></> :
          cfg ? <div className="err">Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID.</div> : <span className="muted">Loading</span>}
        {err && <div className="err" role="alert">{err}</div>}
      </div></div>
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [n, setN] = useState(0);
  useEffect(() => {
    api<Me>("/api/me").then((m) => { setMe(m); setNeedLogin(false); }).catch((e) => { if (e instanceof ApiError && (e.status === 401 || e.status === 403)) { clearToken(); setNeedLogin(true); } });
  }, [n]);
  if (needLogin) return <Login onDone={() => setN(n + 1)} />;
  if (!me) return <div className="page muted">Loading</div>;
  const can = (p: string) => me.permissions.includes(p);
  const link = (to: string, label: string, Ico: any) => <NavLink to={to} aria-label={label} className={({ isActive }) => `navlink${isActive ? " active" : ""}`}><Ico /><span>{label}</span></NavLink>;
  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <div className="brand"><span className="brand-mark" /><div><div className="brand-name">The Codest</div><div className="brand-sub">Revenue Intelligence</div></div></div>
        {can("today.read") && link("/today", "Today", IconToday)}
        {can("accounts.read") && link("/accounts", "Accounts", IconAccounts)}
        {can("explorer.read") && link("/explorer", "Market Explorer", IconExplorer)}
        {link("/guide", "How it works", IconGuide)}
        {(can("admin.sources") || can("sources.read") || can("audit.read") || can("admin.costs")) && link("/admin", "Administration", IconAdmin)}
        <div className="who"><b>{me.email}</b>{me.role.replace(/_/g, " ")}<br /><button onClick={() => { clearToken(); setMe(null); setNeedLogin(true); }}>Sign out</button></div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={can("today.read") ? "/today" : "/admin"} replace />} />
          <Route path="/today" element={<Today />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/accounts" element={<div className="page"><Accounts /></div>} />
          <Route path="/accounts/:id" element={<div className="page"><Account me={me} /></div>} />
          <Route path="/opportunities/:id" element={<div className="page"><Opportunity me={me} /></div>} />
          <Route path="/explorer" element={<div className="page"><Explorer /></div>} />
          <Route path="/admin/*" element={<div className="page"><Admin me={me} /></div>} />
        </Routes>
      </main>
    </div>
  );
}
