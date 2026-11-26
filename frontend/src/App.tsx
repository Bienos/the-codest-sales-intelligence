import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AUTH_EXPIRED, ApiError, api, clearToken, getToken, keepSessionAlive } from "./api";
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
import Person from "./pages/Person";
import Tenders from "./pages/Tenders";
import BuyingSignals, { IconSignals } from "./pages/BuyingSignals";
import { CampaignDetail, CampaignList, NewCampaign } from "./pages/Campaigns";
import { Err, Skeleton, lab, useScrollHints, IconAccounts, IconAdmin, IconFeed, IconGuide, IconKnowledge, IconLibrary, IconToday, IconPeople, IconCampaign, IconTender, Brand } from "./components/ui";

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
  useScrollHints();
  useEffect(() => { window.scrollTo(0, 0); }, [loc.pathname]);
  useEffect(() => { setMenu(false); }, [loc.key]);
  // The open menu covers the whole phone screen: the page behind it stays still, and Escape closes it.
  useEffect(() => {
    document.documentElement.classList.toggle("nav-open", menu);
    if (!menu) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); document.documentElement.classList.remove("nav-open"); };
  }, [menu]);
  const [signInError, setSignInError] = useState<unknown>(null);
  useEffect(() => {
    setSignInError(null);
    api<Me>("/api/me").then((m) => { setMe(m); setNeedLogin(false); setNotice(null); }).catch((e) => {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        const t = getToken();
        setNotice(t ? (e.status === 403 ? { kind: "denied", email: emailFromToken(t) } : { kind: "expired" }) : null);
        clearToken(); setNeedLogin(true);
      } else { setNeedLogin(false); setSignInError(e); }
    });
  }, [n]);
  // One sign-in for every tab: signing in or out in another tab is followed here, and a sign-in the server no longer accepts
  // shows the sign-in screen (the page stays where it is, so signing in again brings you straight back to it).
  const needLoginRef = useRef(false);
  needLoginRef.current = needLogin;
  useEffect(() => {
    const ended = () => { setNotice({ kind: "expired" }); setMe(null); setNeedLogin(true); };
    const changed = (e: StorageEvent) => {
      if (e.key !== null && e.key !== "ri_token") return;
      if (e.newValue) { if (needLoginRef.current) setN((v) => v + 1); }
      else { setNotice(null); setMe(null); setNeedLogin(true); }
    };
    window.addEventListener(AUTH_EXPIRED, ended);
    window.addEventListener("storage", changed);
    return () => { window.removeEventListener(AUTH_EXPIRED, ended); window.removeEventListener("storage", changed); };
  }, []);
  const signedIn = me !== null;
  useEffect(() => (signedIn ? keepSessionAlive() : undefined), [signedIn]);
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
    <div className="nav nav-signing"><div className="nav-top"><div className="brand"><Brand /></div><LanguageSwitcher /></div></div>
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
          <button className="nav-toggle" aria-expanded={menu} aria-controls="nav-panel" onClick={() => setMenu(!menu)}>
            <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">{menu ? <path d="M4 4l12 12M16 4L4 16" /> : <path d="M3 5h14M3 10h14M3 15h14" />}</svg>
            <span>{menu ? tr("Close") : "Menu"}</span>
          </button>
        </div>
        <div className="nav-panel" id="nav-panel" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) setMenu(false); }}>
          <form className="navsearch" role="search" aria-label={tr("Navigation search")} onSubmit={(e) => { e.preventDefault(); const v = (new FormData(e.currentTarget).get("q") as string || "").trim(); if (v) nav(`/search?q=${encodeURIComponent(v)}`); }}><input name="q" aria-label={tr("Search the workspace")} placeholder={tr("Search the workspace")} /></form>
          <span className="nav-lang"><LanguageSwitcher /></span>
          <span className="nav-group">{tr("Work")}</span>
          {can("today.read") && link("/today", tr("Today"), IconToday)}
          {can("accounts.read") && link("/signals", tr("Buying signals"), IconSignals)}
          {can("accounts.read") && link("/accounts", tr("Accounts"), IconAccounts)}
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
          <div className="who">
            <span className="who-lang"><LanguageSwitcher /></span>
            <div className="who-id"><b>{me.email}</b><span>{lab("role", me.role)}</span></div>
            <button type="button" className="who-out" onClick={() => { clearToken(); setMe(null); setNeedLogin(true); }}>{tr("Sign out")}</button>
          </div>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to={can("today.read") ? "/today" : "/admin"} replace />} />
          <Route path="/today" element={<Today canReview={can("research.request")} canCampaign={can("draft.create")} />} />
          <Route path="/signals" element={<BuyingSignals canCampaign={can("draft.create")} canPeople={can("contacts.read")} />} />
          <Route path="/tenders" element={<div className="page"><Tenders canRefresh={can("admin.sources")} /></div>} />
          <Route path="/people" element={<div className="page"><People /></div>} />
          <Route path="/people/:id" element={<div className="page"><Person key={loc.pathname} /></div>} />
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
