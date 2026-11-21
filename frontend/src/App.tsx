import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { ApiError, api, clearToken } from "./api";
import Today from "./pages/Today";
import { Accounts, Account } from "./pages/Accounts";
import Opportunity from "./pages/Opportunity";
import Explorer from "./pages/Explorer";
import Admin from "./pages/Admin";
import Guide from "./pages/Guide";
import Landing, { Notice } from "./pages/Landing";
import Search from "./pages/Search";
import LibraryHub from "./pages/LibraryHub";
import Knowledge from "./pages/Knowledge";
import { IconAccounts, IconAdmin, IconExplorer, IconGuide, IconKnowledge, IconLibrary, IconToday, Brand } from "./components/ui";

type Me = { email: string; role: string; permissions: string[] };

function emailFromToken(t: string | null): string | undefined {
  try { return JSON.parse(atob((t ?? "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email; } catch { return undefined; }
}

export default function App() {
  const nav = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [n, setN] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  useEffect(() => {
    api<Me>("/api/me").then((m) => { setMe(m); setNeedLogin(false); setNotice(null); }).catch((e) => {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        let t: string | null = null; try { t = sessionStorage.getItem("ri_token"); } catch { /* storage unavailable */ }
        setNotice(t ? (e.status === 403 ? { kind: "denied", email: emailFromToken(t) } : { kind: "expired" }) : null);
        clearToken(); setNeedLogin(true);
      }
    });
  }, [n]);
  if (needLogin) return <Landing onDone={() => setN(n + 1)} notice={notice} />;
  if (!me) return <div className="page muted">Loading</div>;
  const can = (p: string) => me.permissions.includes(p);
  const link = (to: string, label: string, Ico: any) => <NavLink to={to} aria-label={label} className={({ isActive }) => `navlink${isActive ? " active" : ""}`}><Ico /><span>{label}</span></NavLink>;
  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <div className="brand"><Brand /></div>
        <form className="navsearch" role="search" onSubmit={(e) => { e.preventDefault(); const v = (new FormData(e.currentTarget).get("q") as string || "").trim(); if (v) nav(`/search?q=${encodeURIComponent(v)}`); }}><input name="q" aria-label="Search everything" placeholder="Search everything" /></form>
        {can("today.read") && link("/today", "Today", IconToday)}
        {can("accounts.read") && link("/accounts", "Accounts", IconAccounts)}
        {can("explorer.read") && link("/explorer", "Market Explorer", IconExplorer)}
        {link("/library", "Library", IconLibrary)}
        {link("/knowledge", "Knowledge base", IconKnowledge)}
        {link("/guide", "How it works", IconGuide)}
        {(can("admin.sources") || can("sources.read") || can("audit.read") || can("admin.costs")) && link("/admin", "Administration", IconAdmin)}
        <div className="who"><b>{me.email}</b>{me.role.replace(/_/g, " ")}<br /><button onClick={() => { clearToken(); setMe(null); setNeedLogin(true); }}>Sign out</button></div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={can("today.read") ? "/today" : "/admin"} replace />} />
          <Route path="/today" element={<Today />} />
          <Route path="/guide" element={<Guide />} />
          <Route path="/search" element={<div className="page"><Search /></div>} />
          <Route path="/library" element={<div className="page"><LibraryHub /></div>} />
          <Route path="/knowledge/:doc?" element={<div className="page"><Knowledge /></div>} />
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
