import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, clearToken } from "./api";
import { LanguageSwitcher, tr, useLanguage } from "./locale";
import Today from "./pages/Today";
import { Accounts } from "./pages/Accounts";
import { Account } from "./pages/AccountDetail";
import Opportunity from "./pages/Opportunity";
import Admin from "./pages/Admin";
import Guide from "./pages/Guide";
import Landing, { Notice } from "./pages/Landing";
import Search from "./pages/Search";
import Feed from "./pages/Feed";
import LibraryHub from "./pages/LibraryHub";
import Knowledge from "./pages/Knowledge";
import People from "./pages/People";
import Tenders from "./pages/Tenders";
import { CampaignDetail, CampaignList, NewCampaign } from "./pages/Campaigns";
import { Err, Skeleton, lab, IconAccounts, IconAdmin, IconFeed, IconGuide, IconKnowledge, IconLibrary, IconToday, IconPeople, IconCampaign, IconTender, Brand } from "./components/ui";

type Me = { email: string; role: string; permissions: string[] };

function emailFromToken(t: string | null): string | undefined {
  try { return JSON.parse(atob((t ?? "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email; } catch { return undefined; }
}

export default function App() {
  useLanguage();
  const nav = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [n, setN] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  const [menu, setMenu] = useState(false);
  const loc = useLocation();
  useEffect(() => { setMenu(false); window.scrollTo(0, 0); }, [loc.pathname]);
  const [signInError, setSignInError] = useState<unknown>(null);
  useEffect(() => {
    setSignInError(null);
    api<Me>("/api/me").then((m) => { setMe(m); setNeedLogin(false); setNotice(null); }).catch((e) => {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        let t: string | null = null; try { t = sessionStorage.getItem("ri_token"); } catch { /* storage unavailable */ }
        setNotice(t ? (e.status === 403 ? { kind: "denied", email: emailFromToken(t) } : { kind: "expired" }) : null);
        clearToken(); setNeedLogin(true);
      } else { setNeedLogin(false); setSignInError(e); }
    });
  }, [n]);
  const [waiting, setWaiting] = useState(0);
  const refreshApprovals = () => {
    if (!me?.permissions.some((p) => p === "approvals.read_own" || p === "approvals.read_all")) {
      setWaiting(0);
      return;
    }
    api<{ items: { status: string }[] }>("/api/approvals")
      .then((d) => setWaiting(d.items.filter((a) => a.status === "pending").length))
      .catch(() => setWaiting(0));
  };
  useEffect(refreshApprovals, [me, loc.pathname]);
  if (needLogin) return <Landing onDone={() => setN(n + 1)} notice={notice} />;
  if (!me) return <div className="shell">
    <div className="nav"><Brand /><LanguageSwitcher /></div>
    <main className="page">
      <h1>{tr("Signing you in")}</h1>
      {signInError ? <Err e={signInError} onRetry={() => setN((value) => value + 1)} /> : <Skeleton label={tr("Signing you in")} />}
    </main>
  </div>;
  const can = (p: string) => me.permissions.includes(p);
  const link = (to: string, label: string, Ico: any) => <NavLink to={to} aria-label={label} className={({ isActive }) => `navlink${isActive ? " active" : ""}`}><Ico /><span>{label}</span></NavLink>;
  return (
    <div className="shell">
      <nav className={`nav${menu ? " open" : ""}`} aria-label={tr("Primary")}>
        <div className="nav-top">
          <div className="brand"><Brand /></div>
          <button className="nav-toggle" aria-expanded={menu} aria-controls="nav-panel" onClick={() => setMenu(!menu)}>{menu ? tr("Close") : "Menu"}</button>
        </div>
        <div className="nav-panel" id="nav-panel">
          <form className="navsearch" role="search" aria-label={tr("Navigation search")} onSubmit={(e) => { e.preventDefault(); const v = (new FormData(e.currentTarget).get("q") as string || "").trim(); if (v) nav(`/search?q=${encodeURIComponent(v)}`); }}><input name="q" aria-label={tr("Search the workspace")} placeholder={tr("Search the workspace")} /></form>
          <LanguageSwitcher />
          <span className="nav-group">{tr("Work")}</span>
          {can("today.read") && link("/today", tr("Today"), IconToday)}
          {can("accounts.read") && link("/accounts", tr("Accounts & leads"), IconAccounts)}
          {can("contacts.read") && link("/people", tr("People"), IconPeople)}
          {can("draft.create") && link("/campaigns", tr("Campaigns"), IconCampaign)}
          {can("explorer.read") && link("/feed", tr("All findings"), IconFeed)}
          {(can("today.read") || can("explorer.read")) && link("/tenders", tr("Open tenders"), IconTender)}
          <span className="nav-group">{tr("Look up")}</span>
          {link("/library", tr("Library"), IconLibrary)}
          {link("/knowledge", tr("Knowledge base"), IconKnowledge)}
          {link("/guide", tr("How it works"), IconGuide)}
          {(can("admin.sources") || can("sources.read") || can("audit.read") || can("admin.costs")) && link("/admin", tr("Administration"), IconAdmin)}
          {waiting > 0 && (can("approvals.read_own") || can("approvals.read_all")) && link("/admin/approvals", tr("Approvals ({} waiting)", waiting), IconAdmin)}
          <div className="who"><b>{me.email}</b>{lab("role", me.role)}<br /><button onClick={() => { clearToken(); setMe(null); setNeedLogin(true); }}>{tr("Sign out")}</button></div>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={can("today.read") ? "/today" : "/admin"} replace />} />
          <Route path="/today" element={<Today canReview={can("research.request")} canCampaign={can("draft.create")} />} />
          <Route path="/tenders" element={<div className="page"><Tenders canRefresh={can("admin.sources")} /></div>} />
          <Route path="/people" element={<div className="page"><People /></div>} />
          <Route path="/campaigns" element={<div className="page"><CampaignList /></div>} />
          <Route path="/campaigns/new" element={<div className="page"><NewCampaign /></div>} />
          <Route path="/campaigns/:id" element={<div className="page"><CampaignDetail key={loc.pathname} canDecide={can("approvals.decide")} /></div>} />
          <Route path="/guide" element={<Guide can={can} />} />
          <Route path="/feed" element={<div className="page"><Feed /></div>} />
          <Route path="/renewals" element={<Navigate to="/accounts" replace />} />
          <Route path="/search" element={<div className="page"><Search /></div>} />
          <Route path="/library" element={<div className="page"><LibraryHub /></div>} />
          <Route path="/knowledge/:doc?" element={<Knowledge />} />
          <Route path="/accounts" element={<div className="page"><Accounts me={me} /></div>} />
          <Route path="/accounts/:id" element={<div className="page"><Account key={loc.pathname} me={me} /></div>} />
          <Route path="/opportunities/:id" element={<div className="page"><Opportunity key={loc.pathname} me={me} /></div>} />
          <Route path="/explorer" element={<Navigate to="/accounts" replace />} />
          <Route path="/admin/*" element={<div className="page"><Admin me={me} waiting={waiting} refreshApprovals={refreshApprovals} /></div>} />
        </Routes>
      </main>
    </div>
  );
}
