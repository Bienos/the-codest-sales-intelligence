import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, setToken } from "./api";
import Today from "./pages/Today";
import { Accounts, Account } from "./pages/Accounts";
import Opportunity from "./pages/Opportunity";
import Explorer from "./pages/Explorer";
import Admin from "./pages/Admin";

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
      window.google.accounts.id.renderButton(document.getElementById("g-btn"), { theme: "outline", size: "large", text: "signin_with" });
    };
    s.onerror = () => setErr("Google Sign-In could not be loaded.");
    document.head.appendChild(s);
    return () => { s.remove(); };
  }, [cfg]);
  return (
    <div className="main">
      <h2>Revenue Intelligence</h2>
      {cfg?.google_client_id ? <><p className="sub">Sign in with your Codest Google account. Access is limited to people an administrator has added.</p><div id="g-btn" /></> :
        cfg && cfg.env !== "production" ? <><p className="sub">Development environment: paste a signed development token.</p>
          <div className="row"><input aria-label="Development token" type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder="Development token" style={{ minWidth: 320 }} />
            <button className="primary" onClick={() => { setToken(t); onDone(); }}>Continue</button></div></> :
        cfg ? <div className="err">Sign-in is not configured. An administrator must set RI_GOOGLE_CLIENT_ID.</div> : null}
      {err && <div className="err" role="alert">{err}</div>}
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [n, setN] = useState(0);
  useEffect(() => {
    api<Me>("/api/me").then((m) => { setMe(m); setNeedLogin(false); }).catch((e) => { if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setNeedLogin(true); });
  }, [n]);
  if (needLogin) return <Login onDone={() => setN(n + 1)} />;
  if (!me) return <div className="main muted">Loading…</div>;
  const can = (p: string) => me.permissions.includes(p);
  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <h1>Revenue Intelligence</h1>
        {can("today.read") && <NavLink to="/today">Today</NavLink>}
        {can("accounts.read") && <NavLink to="/accounts">Accounts</NavLink>}
        {can("explorer.read") && <NavLink to="/explorer">Market Explorer</NavLink>}
        {(can("admin.sources") || can("sources.read") || can("audit.read") || can("admin.costs")) && <NavLink to="/admin">Administration</NavLink>}
        <div className="who">{me.email}<br />{me.role.replace(/_/g, " ")}</div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={can("today.read") ? "/today" : "/admin"} replace />} />
          <Route path="/today" element={<Today />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/accounts/:id" element={<Account me={me} />} />
          <Route path="/opportunities/:id" element={<Opportunity me={me} />} />
          <Route path="/explorer" element={<Explorer />} />
          <Route path="/admin/*" element={<Admin me={me} />} />
        </Routes>
      </main>
    </div>
  );
}
