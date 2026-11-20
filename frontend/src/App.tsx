import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, setToken } from "./api";
import Today from "./pages/Today";
import { Accounts, Account } from "./pages/Accounts";
import Opportunity from "./pages/Opportunity";
import Explorer from "./pages/Explorer";
import Admin from "./pages/Admin";

type Me = { email: string; role: string; permissions: string[] };

function Login({ onDone }: { onDone: () => void }) {
  const [t, setT] = useState("");
  return (
    <div className="main">
      <h2>Sign in</h2>
      <p className="sub">Production uses your organisation's SSO via the authenticating proxy. For a development environment, paste a signed development token.</p>
      <div className="row"><input aria-label="Development token" type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder="Development token" style={{ minWidth: 320 }} />
        <button className="primary" onClick={() => { setToken(t); onDone(); }}>Continue</button></div>
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
