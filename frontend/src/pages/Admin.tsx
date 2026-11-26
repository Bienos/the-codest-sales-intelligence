import { regionName } from "./targetMarket";
import { getLanguage, number, tr } from "../locale";
import { ReactNode, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useParams } from "react-router-dom";
import { api, post, put, upload, patch } from "../api";
import { Badge, Err, humanize, IconPeople, lab, Meter, Skeleton } from "../components/ui";
import Library from "./Library";
import { DataHealth, Fold, Health, HEALTH_PARTS, JobHistory, KnowledgeAdmin, Kind, MUST_BE_ZERO, Pager, Pick, Quiet, Say, SourceChecks, useLoad, useQueryState, when, windows } from "./PipelineStatus";
import Usage, { PaidCalls, SpendTotals } from "./Usage";
import { Discovery, Verification } from "./Pipeline";
import "../styles/admin.css";

type AdminUser = { email: string; permissions: string[] };

function Load({ path, children }: { path: string; children: (d: any, reload: () => void) => any }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const reload = () => { setErr(null); return api(path).then(setD).catch(setErr); };
  useEffect(() => { reload(); }, [path]);
  if (err) return <Err e={err} onRetry={reload} />;
  return d ? children(d, reload) : <Skeleton />;
}

/** Pipedrive is not connected and is hidden from the interface for now; the API still lists it. */
const HIDDEN_INTEGRATION = /pipedrive/i;

/* ------------------------------------------------------------------ section icons (same drawing style as the main navigation) */
const Svg = (d: ReactNode) => () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true">{d}</svg>;
const IcoOverview = Svg(<><rect x="3.5" y="3.5" width="7" height="7" /><rect x="13.5" y="3.5" width="7" height="7" /><rect x="3.5" y="13.5" width="7" height="7" /><path d="M13.5 17.5l2.3 2.3 4.7-5" /></>);
const IcoReview = Svg(<><path d="M3 13l2.6-8.5h12.8L21 13v7H3z" /><path d="M3 13h5.2l1.6 2.6h4.4l1.6-2.6H21" /></>);
const IcoData = Svg(<><path d="M4 4.5a15.5 15.5 0 0115.5 15.5" /><path d="M4 10.5A9.5 9.5 0 0113.5 20" /><rect x="4" y="16" width="4" height="4" /></>);
const IcoUsage = Svg(<><path d="M5 20v-8M11 20V5M17 20v-5M2.5 20h19" /></>);
const IcoHealth = Svg(<><path d="M2.5 12.5h4.5l2.5-6.5 4 13 2.6-6.5h5.4" /></>);
const IcoTeam = () => <IconPeople />;

/* ------------------------------------------------------------------ the six sections */
type Tab = { path: string; label: string; show: boolean; badge?: number };
type Section = { key: string; label: string; blurb: string; Icon: () => JSX.Element; tabs: Tab[]; badge?: number };

function sectionsFor(p: (x: string) => boolean, waiting: number): Section[] {
  const approvals = p("approvals.read_own") || p("approvals.read_all");
  return [
    { key: "overview", label: "Overview", Icon: IcoOverview, blurb: "Is everything working? Each line is one part of the platform, and opens the place where you deal with it.",
      tabs: [{ path: "/admin", label: "Overview", show: true }] },
    { key: "review", label: "To review", Icon: IcoReview, badge: approvals ? waiting : 0, blurb: "Decisions only a person can make: requests to approve, the few company names the matcher could not decide, and pages we could not check by ourselves.",
      tabs: [{ path: "/admin/approvals", label: "Approvals", show: approvals, badge: waiting }, { path: "/admin/identity", label: "Company matches", show: p("identity.manage") }, { path: "/admin/verification", label: "Pages being checked", show: p("sources.read") }] },
    { key: "data", label: "Sources & data", Icon: IcoData, blurb: "Where our information comes from: the sources we read, your workbooks, saved web searches and internal documents.",
      tabs: [{ path: "/admin/sources", label: "Sources", show: p("sources.read") }, { path: "/admin/data-health", label: "Data health", show: p("sources.read") }, { path: "/admin/source-checks", label: "Source checks", show: p("sources.read") }, { path: "/admin/imports", label: "Workbooks", show: p("admin.sources") }, { path: "/admin/discovery", label: "Web search", show: p("admin.costs") }, { path: "/admin/knowledge", label: "Knowledge base", show: p("admin.sources") }] },
    { key: "usage", label: "Usage & cost", Icon: IcoUsage, blurb: "What AI reading, web search and Apollo contact credits cost, measured against the monthly limits, down to every single paid call.",
      tabs: [{ path: "/admin/usage", label: "This month", show: p("admin.costs") }, { path: "/admin/usage/calls", label: "Every paid call", show: p("admin.costs") }, { path: "/admin/usage/limits", label: "Limits and totals", show: p("admin.costs") }] },
    { key: "health", label: "System health", Icon: IcoHealth, blurb: "Whether the background work is running and finding things, and a record of every job it ran.",
      tabs: [{ path: "/admin/health", label: "Health checks", show: p("admin.jobs") || p("admin.rules") }, { path: "/admin/job-history", label: "Job history", show: p("admin.jobs") }] },
    { key: "team", label: "Team & access", Icon: IcoTeam, blurb: "Who can sign in, which outside services we are connected to, and a record of every change.",
      tabs: [{ path: "/admin/users", label: "People", show: p("admin.users") }, { path: "/admin/integrations", label: "Connected services", show: p("admin.costs") }, { path: "/admin/audit", label: "Audit log", show: p("audit.read") }] },
  ].map((s) => ({ ...s, tabs: s.tabs.filter((t) => t.show) })).filter((s) => s.tabs.length > 0);
}
const sectionOf = (secs: Section[], path: string) => {
  const clean = path.replace(/\/+$/, "") || "/admin";
  return secs.find((s) => s.tabs.some((t) => (t.path === "/admin" ? clean === "/admin" : clean === t.path || clean.startsWith(t.path + "/"))))?.key ?? "overview";
};

/** The section bar: six places, each with an icon. On a phone it becomes a three-by-two grid, so nothing hides off screen. */
function SectionBar({ secs, active }: { secs: Section[]; active: string }) {
  return (
    <nav className="adm-bar" aria-label={tr("Administration sections")}>
      {secs.map(({ key, label, Icon, tabs, badge }) => (
        <Link key={key} to={tabs[0].path} className={key === active ? "on" : undefined} aria-current={key === active ? "page" : undefined}>
          <Icon /><span className="adm-bar-l">{tr(label)}</span>
          {!!badge && <span className="adm-n" aria-label={tr("{} waiting", badge)}>{badge}</span>}
        </Link>))}
    </nav>
  );
}

/** A section: its name, one plain line about what it is for, then its pages as tabs when there is more than one. */
function Sec({ s, bare = false, children }: { s?: Section; bare?: boolean; children: ReactNode }) {
  if (!s) return <Quiet>{tr("You do not have access to this part of Administration. Ask an administrator.")}</Quiet>;
  return (
    <section className="adm-sec" aria-labelledby={`adm-${s.key}`}>
      <header className={`adm-sec-head${bare ? " bare" : ""}`}><h2 id={`adm-${s.key}`} className={bare ? "sr" : undefined}>{tr(s.label)}</h2><p>{tr(s.blurb)}</p></header>
      {s.tabs.length > 1 && <nav className="adm-tabs" aria-label={tr("Pages in {}", tr(s.label))}>
        {s.tabs.map((t) => <NavLink key={t.path} to={t.path} end>{tr(t.label)}{!!t.badge && <span className="adm-tabs-n">{t.badge}</span>}</NavLink>)}
      </nav>}
      <div className="adm-body">{children}</div>
    </section>
  );
}

/** A page the person has no permission for: say so instead of showing an error from the server. */
const Gate = ({ ok, children }: { ok: boolean; children: ReactNode }) => ok ? <>{children}</> : <Quiet>{tr("You do not have access to this page. Ask an administrator.")}</Quiet>;

/* ------------------------------------------------------------------ Overview */

type Line = { key: string; title: string; to: string; kind: Kind; text: string; fig?: string; unit?: string; loading?: boolean };
const plural = (n: number, one: string, many: string) => tr(n === 1 ? one : many, number(n));
const timeNow = () => new Date().toLocaleTimeString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit" });

/** One line of the overview: what it is, how it is doing in a sentence, the number that matters, and a way in. */
const Row = ({ l }: { l: Line }) => (
  <li><Link to={l.to} className={`ov-row ${l.kind}`}>
    <span className="ov-row-main"><span className="ov-row-t">{tr(l.title)}</span><Say k={l.kind}>{l.text}</Say></span>
    <span className="ov-fig">{l.fig !== undefined && <><b>{l.fig}</b>{l.unit && <small>{l.unit}</small>}</>}</span>
  </Link></li>
);

const compactN = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : String(Math.round(v)));

type Tile = { key: string; title: string; to: string; fig: string; unit?: string; line: string; kind: Kind };

/** "Is everything working?" in five figures, each opening the list behind it. */
function glanceTiles(d: any): Tile[] {
  const tiles: Tile[] = [];
  if (!d) return tiles;
  if (d.sources) {
    const s = d.sources;
    tiles.push({ key: "src", title: "Sources read today", to: "/admin/source-checks?since=24h", fig: number(s.checked), unit: tr("of {} live", number(s.live)),
      kind: s.failing ? "warn" : s.checked ? "ok" : s.live ? "warn" : "",
      line: s.failing ? plural(s.failing, "One failed on its last read.", "{} failed on their last read.") : s.last_at ? tr("Last read: {}.", when(s.last_at)) + (s.company_pages ? " " + plural(s.company_pages, "One company page checked.", "{} company pages checked.") : "") : tr("No source has been read yet.") });
  }
  if (d.jobs) {
    const j = d.jobs; const failed = Number(j.failed_24h);
    tiles.push({ key: "jobs", title: "Jobs failed, last 24 hours", to: failed ? "/admin/job-history?since=24h&status=failed" : "/admin/job-history?since=24h", fig: number(failed), unit: plural(Number(j.done_24h), "one finished fine", "{} finished fine"),
      kind: failed ? "bad" : Number(j.retrying) ? "warn" : "ok",
      line: [Number(j.retrying) ? plural(Number(j.retrying), "One will retry by itself.", "{} will retry by themselves.") : "", Number(j.running) + Number(j.waiting) ? tr("{} running or waiting now.", number(Number(j.running) + Number(j.waiting))) : "",
        j.last_done ? tr("Last finished: {}.", when(j.last_done)) : tr("No job has finished yet.")].filter(Boolean).join(" ") });
  }
  if ("last_import" in d) {
    const i = d.last_import; const st = i?.status;
    tiles.push({ key: "imp", title: "Last import", to: "/admin/imports", fig: i ? when(i.at, false) : "—", kind: !i ? "warn" : st === "failed" ? "bad" : st === "completed" ? "ok" : "info",
      line: !i ? tr("No workbook loaded yet.") : st === "failed" ? tr("Workbook {} failed.", i.workbook) : st === "completed" ? tr("Workbook {}: {} new rows of {}.", i.workbook, number(i.new), number(i.rows)) : tr("Workbook {} is loading now.", i.workbook) });
  }
  if (d.matching) {
    const m = d.matching;
    tiles.push({ key: "match", title: "Automatic matching", to: "/admin/identity", fig: number(Number(m.auto_7d)), unit: tr("matched this week"), kind: Number(m.needs_person) ? "warn" : "ok",
      line: [m.last_run ? tr("Last run: {}.", when(m.last_run.at)) : tr("Not run yet."), Number(m.needs_person) ? plural(Number(m.needs_person), "One name needs you.", "{} names need you.") : "", Number(m.waiting) ? plural(Number(m.waiting), "One waits for the next run.", "{} wait for the next run.") : ""].filter(Boolean).join(" ") });
  }
  if (d.changes) {
    const c = d.changes; const reads = Number(c.all_24h) - Number(c.changes_24h);
    tiles.push({ key: "audit", title: "Changes recorded today", to: "/admin/audit?since=24h", fig: number(Number(c.changes_24h)), unit: tr("in the audit log"), kind: "info",
      line: reads > 0 ? plural(reads, "Also one view of contact details or import row, kept apart.", "Also {} views of contact details and import rows, kept apart.") : tr("Every change is kept with who made it.") });
  }
  return tiles;
}

function Glance({ tiles, d, err }: { tiles: Tile[]; d: any; err: unknown }) {
  if (err) return <Err e={err} />;
  if (!d) return <div className="glance-wrap" aria-busy="true"><Skeleton rows={1} /></div>;
  if (!tiles.length) return null;
  return (
    <section className="glance-wrap" aria-labelledby="glance-h">
      <h3 id="glance-h" className="ov-h">{tr("At a glance")}</h3>
      <ul className="glance">{tiles.map((t) => <li key={t.key}><Link to={t.to} className={`glance-t ${t.kind}`}>
        <span className="glance-k">{tr(t.title)}</span>
        <span className="glance-f"><b>{t.fig}</b>{t.unit && <small>{t.unit}</small>}</span>
        <span className="glance-l">{t.line}</span>
      </Link></li>)}</ul>
    </section>
  );
}

function Overview({ can }: { can: (p: string) => boolean }) {
  const [tick, setTick] = useState(0); const [at, setAt] = useState(timeNow());
  const approvalsOn = can("approvals.read_own") || can("approvals.read_all");
  const appr = useLoad("/api/approvals", approvalsOn, tick);
  const ident = useLoad("/api/admin/identity/overview", can("identity.manage"), tick);
  const ver = useLoad("/api/verifications?limit=1", can("sources.read"), tick);
  const conn = useLoad("/api/admin/connections", can("sources.read"), tick);
  const pipe = useLoad("/api/admin/pipeline", can("admin.jobs"), tick);
  const jobs = useLoad("/api/admin/failures", can("admin.jobs"), tick);
  const metrics = useLoad("/api/admin/metrics", can("admin.rules"), tick);
  const integ = useLoad("/api/admin/integrations", can("admin.costs"), tick);
  const ov = useLoad("/api/admin/overview", true, tick);

  /** One line per check. A check that could not load says so and is not counted. */
  const line = (key: string, title: string, to: string, r: ReturnType<typeof useLoad>, make: (d: any) => Omit<Line, "key" | "title" | "to">): Line =>
    r.d ? { key, title, to, ...make(r.d) } : r.err ? { key, title, to, kind: "", text: tr("Could not load this check. Open it to see why.") } : { key, title, to, kind: "", text: tr("Checking"), loading: true };

  const waitingForYou: Line[] = [];
  if (approvalsOn) waitingForYou.push(line("approvals", "Approvals", "/admin/approvals", appr, (d) => {
    const pending = d.items.filter((a: any) => a.status === "pending").length, approved = d.items.filter((a: any) => a.status === "approved").length;
    if (pending) return { kind: "warn", fig: number(pending), unit: tr("waiting"), text: plural(pending, "One request is waiting for a decision.", "{} requests are waiting for a decision.") + (approved ? " " + plural(approved, "One more is approved but not run yet.", "{} more are approved but not run yet.") : "") };
    if (approved) return { kind: "warn", fig: number(approved), unit: tr("to run"), text: plural(approved, "One approved request has not been run yet.", "{} approved requests have not been run yet.") };
    return { kind: "ok", fig: "0", unit: tr("waiting"), text: tr("Nothing is waiting for a decision.") };
  }));
  if (can("identity.manage")) waitingForYou.push(line("identity", "Company matches", "/admin/identity", ident, (d) => {
    const c = d.counts ?? {}; const need = Number(c.needs_person ?? 0), wait = Number(c.waiting ?? 0), done = Number(c.auto_week ?? 0);
    const tail = done ? " " + plural(done, "One was matched automatically this week.", "{} were matched automatically this week.") : "";
    if (need) return { kind: "warn", fig: number(need), unit: tr("for you"), text: plural(need, "The matcher could not decide one company name. The evidence is ready.", "The matcher could not decide {} company names. The evidence is ready.") + tail };
    if (wait) return { kind: "info", fig: number(wait), unit: tr("in the queue"), text: plural(wait, "One company name waits for the automatic matcher.", "{} company names wait for the automatic matcher.") + tail };
    return { kind: "ok", fig: "0", unit: tr("for you"), text: tr("Every company name from your workbooks is matched.") + tail };
  }));
  if (can("sources.read")) waitingForYou.push(line("verification", "Pages being checked", "/admin/verification", ver, (d) => {
    const q = d.queue ?? {}; const failed = Number(d.counts?.failed ?? 0);
    const tail = failed ? " " + plural(failed, "One page could not be checked.", "{} pages could not be checked.") : "";
    if (q.provider_billing > 0) return { kind: "bad", fig: number(q.pending), unit: tr("waiting"), text: plural(q.provider_billing, "One page check is blocked because the AI account is out of credit.", "{} page checks are blocked because the AI account is out of credit.") };
    const old = q.oldest_pending && Date.now() - Date.parse(q.oldest_pending) > 86400000;
    if (q.pending > 0) return { kind: old ? "warn" : "info", fig: number(q.pending), unit: tr("waiting"), text: (old ? tr("Pages are waiting since {}. Checks may have stalled.", when(q.oldest_pending)) : plural(q.pending, "One page is being checked now.", "{} pages are being checked now.")) + tail };
    return { kind: "ok", fig: "0", unit: tr("waiting"), text: tr("No pages are waiting to be checked.") + tail };
  }));

  const dataIn: Line[] = [];
  if (can("sources.read")) dataIn.push(line("sources", "Sources", "/admin/sources", conn, (d) => {
    const items = d.items.filter((r: any) => !HIDDEN_INTEGRATION.test(`${r.source_id} ${r.name}`));
    const n = (...states: string[]) => items.filter((r: any) => states.includes(r.state)).length;
    const live = n("live"), test = n("testing", "needs_retest"), setup = n("needs_address", "needs_credential", "needs_terms"), blocked = n("blocked_by_site");
    const stale = Number(metrics.d?.stale_sources ?? 0);
    const parts = [test && plural(test, "one waits for its daily test", "{} wait for their daily test"), setup && plural(setup, "one needs a person (an address, a key or a terms check)", "{} need a person (an address, a key or a terms check)"), blocked && plural(blocked, "one is blocked by its site", "{} are blocked by their site")].filter(Boolean).join(", ");
    const head = tr("{} of {} sources are reporting.", number(live), number(items.length)) + (stale ? " " + plural(stale, "One has stopped reporting on schedule.", "{} have stopped reporting on schedule.") : "");
    return { kind: stale ? "bad" : setup ? "warn" : "ok", fig: number(live), unit: tr("of {} live", number(items.length)), text: head + (parts ? ` ${parts.charAt(0).toUpperCase()}${parts.slice(1)}.` : "") };
  }));
  if (can("admin.jobs")) dataIn.push(line("work", "Background work", "/admin/health/pipeline", pipe, (d) => {
    const st = (k: string) => d.stages.find((s: any) => s.key === k)?.state;
    const down = [["scheduler", tr("the scheduler")], ["worker", tr("the worker")]].filter(([k]) => ["stale", "never"].includes(st(k))).map(([, l]) => l);
    return down.length ? { kind: "bad", text: tr("Not reporting: {}. Nothing new is collected until it runs again.", down.join(tr(" and "))) } : { kind: "ok", text: tr("The scheduler and the worker are running.") };
  }));
  if (can("admin.jobs")) dataIn.push(line("jobs", "Failed jobs", "/admin/health/jobs", jobs, (d) => {
    const now = Number(d.still_happening ?? 0), open = Number(d.open ?? 0);
    if (now) return { kind: "bad", fig: number(now), unit: tr("failing now"), text: plural(now, "One job is failing now. Open it to see why.", "{} jobs are failing now, grouped by cause. Open them to see why.") };
    if (open) return { kind: "warn", fig: number(open), unit: tr("to tidy"), text: plural(open, "One old failure whose cause is gone: retry or dismiss it.", "{} old failures whose cause is gone or quiet: retry or dismiss them by group.") };
    return { kind: "ok", fig: "0", unit: tr("failed"), text: tr("Nothing has failed for good.") };
  }));
  if (can("admin.rules")) dataIn.push(line("safety", "Safety checks", "/admin/health/quality", metrics, (d) => {
    const bad = MUST_BE_ZERO.filter((k) => Number(d[k]) > 0);
    return bad.length ? { kind: "bad", text: tr("Something that should never happen was counted. Open the health checks.") } : { kind: "ok", text: tr("No unapproved changes, no duplicate alerts, and every urgent card cites a source.") };
  }));

  /** Spend this month for each paid service: AI reading, web search (Exa) and Apollo contact credits. */
  const sp = ov.d?.spend;
  const spends = can("admin.costs") && sp ? ([["ai", "AI reading"], ["exa", "Web search (Exa)"], ["apollo", "Apollo contact credits"]] as const).map(([k, label]) => {
    const g = sp[k]; const cap = Number(g.cap), used = Number(g.used);
    const pct = cap > 0 ? Math.round(Math.min(used / cap, 1) * 100) : 0;
    const money = k === "apollo" ? tr("{} credits", number(Math.round(used))) : `$${Number(g.usd).toFixed(2)}`;
    const capText = cap <= 0 ? "" : k === "exa" ? `$${cap.toFixed(0)}` : k === "apollo" ? tr("{} credits", number(cap)) : tr("{} tokens", compactN(cap));
    const kind: Kind = cap <= 0 ? (k === "apollo" ? "" : "warn") : used >= cap ? "bad" : pct >= 80 ? "warn" : "ok";
    const say = cap <= 0 ? (k === "apollo" ? tr("No credits allowed this month, so no email can be revealed. Set a limit in Connected services.") : tr("No monthly limit is set, so these calls are blocked."))
      : used >= cap ? tr("The limit is reached. These calls are blocked until next month.") : tr("{}% of the limit used.", pct) + (g.blocked_by_limit ? " " + plural(g.blocked_by_limit, "One call was stopped by the limit.", "{} calls were stopped by the limit.") : "");
    return { name: k, label, used, cap, money, capText, kind, say, unit: k === "exa" ? "usd" : k === "apollo" ? tr("credits") : tr("tokens") };
  }) : [];
  const services = (integ.d?.items ?? []).filter((r: any) => !HIDDEN_INTEGRATION.test(`${r.key} ${r.name}`));

  const lines = [...waitingForYou, ...dataIn];
  const tiles = glanceTiles(ov.d);
  const loading = lines.some((l) => l.loading) || ov.loading;
  const attention = [...lines.filter((l) => l.kind === "warn" || l.kind === "bad").map((l) => l.title), ...tiles.filter((t) => t.key === "imp" && (t.kind === "warn" || t.kind === "bad")).map((t) => t.title), ...spends.filter((s) => s.kind === "warn" || s.kind === "bad").map((s) => s.label)];
  const verdictKind: Kind = loading ? "" : attention.length ? (lines.some((l) => l.kind === "bad") ? "bad" : "warn") : "ok";
  const verdict = loading ? tr("Checking everything") : attention.length === 0 ? tr("Everything is running") : attention.length === 1 ? tr("One thing needs you") : tr("{} things need you", number(attention.length));
  const refresh = () => { setTick((t) => t + 1); setAt(timeNow()); };
  return (
    <div className="ov">
      <div className={`ov-verdict ${verdictKind}`} role="status">
        <span className="ov-mark" aria-hidden="true" />
        <div className="ov-verdict-main">
          <p className="ov-say">{verdict}</p>
          <p className="ov-sub">{loading ? tr("Asking each part of the platform how it is doing.") : attention.length ? tr("Start with: {}.", attention.map((a) => tr(a)).join(", ")) : tr("Nothing needs you right now.")}</p>
        </div>
        <div className="ov-when"><span>{tr("Checked at {}", at)}</span><button type="button" className="ghost" onClick={refresh}>{tr("Check again")}</button></div>
      </div>
      <Glance tiles={tiles} d={ov.d} err={ov.err} />
      <div className={`ov-grid${can("admin.costs") ? "" : " solo"}`}>
        <div className="ov-col">
          {waitingForYou.length > 0 && <section><h3 className="ov-h">{tr("Waiting for you")}</h3><ul className="ov-list">{waitingForYou.map((l) => <Row key={l.key} l={l} />)}</ul></section>}
          {dataIn.length > 0 && <section><h3 className="ov-h">{tr("Data coming in")}</h3><ul className="ov-list">{dataIn.map((l) => <Row key={l.key} l={l} />)}</ul></section>}
        </div>
        {can("admin.costs") && <aside className="ov-col">
          <section>
            <h3 className="ov-h">{tr("Spend this month")}</h3>
            <div className="ov-box">
              {ov.err ? <Err e={ov.err} onRetry={ov.reload} /> : !ov.d ? <Skeleton rows={2} /> : spends.map((s) => (
                <div key={s.name} className="ov-spend">
                  <div className="ov-spend-head"><b>{tr(s.label)}</b><span><b>{s.money}</b>{s.capText && <span className="muted">{" " + tr("of {}", s.capText)}</span>}</span></div>
                  <Meter used={s.used} cap={s.cap} unit={s.unit} />
                  <Say k={s.kind}>{s.say}</Say>
                </div>))}
              <Link className="ov-more" to="/admin/usage">{tr("See usage and cost")}</Link>
            </div>
          </section>
          <section>
            <h3 className="ov-h">{tr("Connected services")}</h3>
            <div className="ov-box">
              {integ.err ? <Err e={integ.err} onRetry={integ.reload} /> : !integ.d ? <Skeleton rows={2} /> : <>
                <p className="small muted m-0">{tr("{} of {} connected.", number(services.filter((r: any) => r.configured).length), number(services.length))}</p>
                <ul className="ov-svc">{services.map((r: any) => <li key={r.key}><span>{r.name}</span><Say k={r.configured ? (r.switch && !r.switch_on ? "info" : "ok") : "warn"}>{r.configured ? (r.switch && !r.switch_on ? tr("Connected, switched off") : tr("Connected")) : tr("Not connected")}</Say></li>)}</ul>
              </>}
              <Link className="ov-more" to="/admin/integrations">{tr("Manage connected services")}</Link>
            </div>
          </section>
        </aside>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ To review */

const TWO_PERSON = new Set(["signal_campaign_push", "contact_enrichment", "crm_write", "crm_activity", "outbound_message", "sequence_enrolment", "supplier_application", "quote_or_commitment", "proof_release"]);
const APPROVAL_FIELDS: Record<string, string> = { subject: "Subject", type: "Activity type", note: "Note", due_date: "Due date", org_id: "CRM organisation", person_id: "Contact ID", delay_days: "Days after the previous email" };

function PayloadDetails({ payload }: { payload: Record<string, unknown> }) {
  const readable = (v: unknown): React.ReactNode => {
    if (Array.isArray(v)) return <ol className="tight">{v.map((item, i) => <li key={i}>{readable(item)}</li>)}</ol>;
    if (v && typeof v === "object") return <PayloadDetails payload={v as Record<string, unknown>} />;
    if (typeof v === "boolean") return v ? tr("Yes") : tr("No");
    return String(v);
  };
  const values = Object.entries(payload).filter(([, v]) => v != null && v !== "");
  return <dl className="approval-payload">{values.map(([k, v]) => <div key={k}>
    <dt>{tr(APPROVAL_FIELDS[k] ?? humanize(k))}</dt><dd>{readable(v)}</dd>
  </div>)}</dl>;
}

/** A signal campaign approval: who gets it and the exact emails, without dumping every lead's variables. */
function CampaignPayload({ p }: { p: any }) {
  const members: any[] = p.members ?? [];
  const companies = new Set(members.map((m) => m.organization_id)).size;
  return <div className="approval-payload">
    <p className="m-0"><b>{p.campaign_name}</b>: {tr("{} people at {} companies", members.length, companies)} · <Link to={`/campaigns/${p.campaign_id}`}>{tr("Review the people and copy")}</Link></p>
    <details className="mt-2"><summary>{tr("The emails ({})", (p.steps ?? []).length)}</summary>
      {(p.steps ?? []).map((st: any, i: number) => <div key={i} className="mail"><div className="small muted">{i === 0 ? tr("First email") : tr("Follow-up after {} days", st.delay_days)}</div><b>{st.subject}</b><div className="mail-body">{st.body}</div></div>)}</details>
    <details className="mt-2"><summary>{tr("Who gets it ({})", members.length)}</summary>
      <ul className="tight small">{members.map((m) => <li key={m.member_id}>{m.variables?.firstName} {m.variables?.lastName}, {m.variables?.jobTitle} · {m.variables?.companyName} · {m.email}</li>)}</ul></details>
  </div>;
}

function Approvals({ me, refresh }: { me: AdminUser; refresh: () => void }) {
  return <Load path="/api/approvals">{(d, reload) => <ApprovalList d={d} me={me} reload={() => { void reload(); refresh(); }} />}</Load>;
}
const APPROVAL_STATUS: Record<string, [Kind, string]> = { pending: ["warn", "Waiting for approval"], approved: ["info", "Approved, not run yet"], executed: ["ok", "Done"], rejected: ["", "Rejected"], expired: ["", "Expired"] };
function ApprovalList({ d, reload, me }: { d: any; reload: () => void; me: AdminUser }) {
  const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (path: string, body?: unknown) => {
    setBusy(true); setErr(null); setMsg("");
    try {
      const result = await post(path, body);
      setMsg(result.status === "approved" ? tr("Approved. Press Run now to carry it out.")
        : result.status === "executed" ? result.note ?? tr("Action completed.")
        : result.status === "rejected" ? tr("Rejected.") : tr("Request status: {}.", humanize(result.status)));
      reload();
    } catch (e) { setErr(e); }
    finally { setBusy(false); }
  };
  const canDecide = me.permissions.includes("approvals.decide");
  return <><p className="adm-lead">{tr("Requests for signal campaigns, outreach and email reveals. Some need someone other than the person who asked.")}</p>
    <Err e={err} />{msg && <div className="okmsg" role="status">{msg}</div>}
    {d.items.length === 0 && <Quiet>{tr("Nothing waiting. New requests appear here and in the main menu.")}</Quiet>}
    <ul className="adm-items">
    {d.items.map((a: any) => { const [k, l] = APPROVAL_STATUS[a.status] ?? ["", humanize(a.status)]; return <li id={`approval-${a.id}`} key={a.id} className={`adm-item ${k}`}>
      <div className="adm-item-head"><b>{lab("approval", a.operation === "create_activity" ? "crm_activity" : a.kind)}{a.payload?.subject ? `: ${a.payload.subject}` : ""}</b><Say k={k}>{tr(l)}</Say></div>
      <div className="small muted">{tr("For {}, requested by {}, expires {}", a.target_system === "pipedrive" ? tr("the CRM") : humanize(a.target_system), a.requested_by, when(a.expires_at))}</div>
      {a.kind === "signal_campaign_push" ? <CampaignPayload p={a.payload ?? {}} /> : <PayloadDetails payload={a.payload ?? {}} />}
      {a.justification && <div className="small muted">{tr("Why:") + " "}{a.justification}</div>}
      {a.status === "pending" && TWO_PERSON.has(a.kind) && <p className="small muted m-0">{tr("Someone other than the requester must approve this.")}</p>}
      {canDecide && (a.status === "pending" || a.status === "approved") && <div className="row mt-2">
        {a.status === "pending" && <>
          <button className="primary" disabled={busy || (TWO_PERSON.has(a.kind) && a.requested_by === me.email)} onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: true })}>{tr("Approve this")}</button>
          <button disabled={busy || (TWO_PERSON.has(a.kind) && a.requested_by === me.email)} onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: false })}>{tr("Reject")}</button>
        </>}
        {a.status === "approved" && <button className="primary" disabled={busy} onClick={() => go(`/api/approvals/${a.id}/execute`)}>{tr("Run now")}</button>}
      </div>}
    </li>; })}</ul></>;
}

/* Company matches: the platform matches company names by itself (rules, then one web search, then a short AI check). A person only sees
   what stays unsure, with the evidence, and can undo an automatic decision. */

const hostOf = (u?: string | null) => { try { return u ? new URL(u).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };

/** Why the matcher decided, in words (the reason is rebuilt from the rule so it reads in the person's language). */
function matchReason(m: any): string {
  const org = m.org_name ?? tr("the company");
  const site = hostOf(m.evidence_url) || m.org_domain || "";
  switch (m.rule) {
    case "exact_now": return tr("The name now matches {} exactly.", org);
    case "same_website": return tr("Same website ({}) and the same name as {}.", site, org);
    case "same_core_name": return tr("The same name as {} once legal forms such as AG, plc or Group are removed, and the country agrees.", org);
    case "web_same_site": return tr("A web search shows its website is {}, the same as {}.", site, org);
    case "web_known_site": return tr("A web search shows its website is {}, which {} already has.", site, org);
    case "web_new_company": return tr("Not an account yet. A web search finds its own website, {}.", site);
    case "web_other_site": return tr("A web search shows its own website is {}; no candidate has it.", site);
    case "other_country_and_site": return tr("Every candidate is in another country and has another website.");
    case "regional_bank": return tr("A regional savings or cooperative bank: the place in its name differs from every candidate.");
    case "ai": return tr("AI check: {}", m.reason ?? "");
    default: return m.reason ? tr(m.reason) : "";
  }
}

/** Why a review is still with a person. */
function unsureReason(r: any): string {
  if (r.undone) return tr("{} undid the automatic decision, so it stays with a person.", r.undone.by);
  const a = r.auto ?? {};
  if (a.ai) return tr("The AI check was {}% sure, below the 85% needed to decide alone.", Math.round(Number(a.ai.confidence ?? 0) * 100));
  if (a.rule === "none" || !a.rule) return a.reason?.startsWith("Waiting") ? tr("Waiting for the web search or the AI check (not available right now).") : tr("The rules, the web search and the AI check could not tell with confidence.");
  return tr("Not sure enough to decide alone.");
}

function Evidence({ r }: { r: any }) {
  const off = r.web?.official; const ai = r.auto?.ai;
  if (!r.web && !ai) return null;
  return (
    <dl className="idm-ev">
      {r.web && <div><dt>{tr("Web search")}</dt><dd>{off ? <><a href={off.url} target="_blank" rel="noreferrer noopener">{off.domain} ↗</a>{off.title && <span className="muted"> · {off.title}</span>}{off.snippet && <q>{off.snippet}</q>}</>
        : r.web.results?.length ? <>{tr("No company website among the results.")} {r.web.results.slice(0, 2).map((x: any) => <a key={x.url} className="small" href={x.url} target="_blank" rel="noreferrer noopener"> {x.domain ?? hostOf(x.url)} ↗</a>)}</>
        : tr("The search found nothing.")}</dd></div>}
      {ai && <div><dt>{tr("AI check")}</dt><dd><b>{ai.decision === "link" ? tr("Probably the same company") : ai.decision === "separate" ? tr("Probably a different company") : tr("Cannot tell")}</b> <span className="muted">({tr("{}% sure", Math.round(Number(ai.confidence ?? 0) * 100))})</span><q>{ai.reason}</q></dd></div>}
    </dl>
  );
}

function NeedsPerson({ r, note, done }: { r: any; note: string; done: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const act = async (decision: string, org?: string) => { setBusy(true); setErr(null); try { await post(`/api/admin/identity-reviews/${r.id}/resolve`, { decision, organization_id: org, note }); done(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const suggested = r.auto?.ai?.candidate_id;
  return (
    <li className="adm-item warn idm">
      <div className="idm-head">
        <div><b className="idm-name">{r.raw_company_name}</b><span className="small muted">{[r.sheet_title && tr("{}, row {}", r.sheet_title, r.row_number), r.country, r.website].filter(Boolean).join(" · ")}</span></div>
        <Say k="warn">{unsureReason(r)}</Say>
      </div>
      <Evidence r={r} />
      <div className="idm-choices" role="group" aria-label={tr("Which company is {}?", r.raw_company_name)}>
        {r.candidates.map((c: any) => (
          <div key={c.id} className={`idm-cand${c.id === suggested ? " sug" : ""}`}>
            <div><Link to={`/accounts/${c.id}`}>{c.display_name}</Link><span className="small muted">{[c.country_code, c.canonical_domain].filter(Boolean).join(" · ") || tr("No country or website on record")}</span>
              {c.id === suggested && <span className="small idm-sug">{tr("The AI check leans to this one")}</span>}</div>
            <button disabled={busy || note.length < 3} onClick={() => act("link", c.id)}>{tr("Same company")}</button>
          </div>))}
        <div className="row idm-other">
          <button disabled={busy || note.length < 3} onClick={() => act("new")}>{tr("A different company")}</button>
          <button className="ghost" disabled={busy || note.length < 3} onClick={() => act("reject")}>{tr("Ignore this row")}</button>
        </div>
      </div>
      <Err e={err} />
    </li>
  );
}

function MatchedRow({ m, done, canUndo }: { m: any; done: () => void; canUndo: boolean }) {
  const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const undo = async () => { setBusy(true); setErr(null); try { await post(`/api/admin/identity-reviews/${m.id}/undo`, {}); done(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <li className={`idm-row ${m.decision}`}>
      <div className="idm-what">
        <b>{m.raw_company_name}</b>
        <span>{m.decision === "link" ? tr("linked to") : tr("added as a new company")}{m.organization_id && <> <Link to={`/accounts/${m.organization_id}`}>{m.decision === "link" ? m.org_name : tr("open it")}</Link></>}</span>
      </div>
      <p className="idm-why">{matchReason(m)}{m.evidence_url && <> <a href={m.evidence_url} target="_blank" rel="noreferrer noopener" className="small">{tr("evidence")} ↗</a></>}</p>
      <div className="idm-meta">
        {m.confidence !== null && <span className="idm-conf" title={tr("How sure the matcher was")}><span className="idm-bar" aria-hidden="true"><i style={{ width: `${Math.round(m.confidence * 100)}%` }} /></span>{tr("{}% sure", Math.round(m.confidence * 100))}</span>}
        <span className="small muted">{when(m.resolved_at)}</span>
        {canUndo && <button className="ghost" disabled={busy} onClick={undo}>{tr("Undo")}</button>}
      </div>
      <Err e={err} />
    </li>
  );
}

function Identity({ isAdmin }: { isAdmin: boolean }) {
  const [tick, setTick] = useState(0);
  const ov = useLoad("/api/admin/identity/overview", true, tick);
  const [note, setNote] = useState(tr("Checked the evidence shown"));
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [all, setAll] = useState(false);
  const reload = () => setTick((t) => t + 1);
  const runNow = async () => { setBusy(true); setErr(null); setMsg(""); try { const r = await post("/api/admin/identity/auto/run"); setMsg(r.queued ? tr("Matching started. It looks at up to 40 names; refresh in a few minutes.") : tr("Matching is already queued.")); reload(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  if (ov.err) return <Err e={ov.err} onRetry={ov.reload} />;
  if (!ov.d) return <Skeleton />;
  const d = ov.d; const c = d.counts ?? {};
  const last = d.last_run;
  const recent = all ? d.recent : d.recent.slice(0, 12);
  return (
    <div className="idm-page">
      <p className="adm-lead">{tr("Company names from your workbooks that could be more than one company are matched automatically: first by simple rules, then with one web search, then a short AI check. Only what stays unsure comes to you, with the evidence. A parent and its subsidiary are kept apart.")}</p>
      <div className="idm-sum">
        <div className="ok"><b>{number(c.auto_total ?? 0)}</b><span>{tr("matched automatically")}</span><small>{tr("{} linked to an account, {} added as companies", number(c.auto_linked ?? 0), number(c.auto_new ?? 0))}</small></div>
        <div className={c.needs_person ? "warn" : ""}><b>{number(c.needs_person ?? 0)}</b><span>{tr("need a person")}</span><small>{c.needs_person ? tr("Listed below with the evidence") : tr("Nothing for you to do")}</small></div>
        <div className={c.waiting ? "info" : ""}><b>{number(c.waiting ?? 0)}</b><span>{tr("waiting for the matcher")}</span><small>{tr("It runs every hour and after each import")}</small></div>
      </div>
      <div className="adm-action idm-run">
        <div>
          <b>{last ? tr("Last run {}", when(last.at)) : tr("Not run yet")}</b>
          <p className="small muted m-0">{last?.result ? tr("{} names checked: {} linked, {} added as companies, {} left for a person. {} web searches, {} AI checks.", last.result.checked ?? 0, last.result.linked ?? 0, last.result.separate ?? 0, last.result.unsure ?? 0, last.result.searches ?? 0, last.result.ai_checks ?? 0) : tr("The matcher looks at up to {} names per run and never uses the last 30% of the monthly web search or AI budget.", d.per_run)}
            {last?.result?.stopped && <> {tr(last.result.stopped)}</>}</p>
          {(!d.can.web_search || !d.can.ai) && <p className="small m-0 mt-2"><Say k="info">{!d.can.web_search && !d.can.ai ? tr("Web search and AI are not available on the worker right now, so only the free rules run.") : !d.can.web_search ? tr("Web search is not available on the worker right now.") : tr("The AI check is not available on the worker right now.")}</Say></p>}
        </div>
        {isAdmin && <button className="primary" disabled={busy || d.queued} onClick={runNow}>{d.queued ? tr("Matching is queued") : tr("Match automatically now")}</button>}
      </div>
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />

      <section className="idm-sec" aria-labelledby="idm-need">
        <h3 id="idm-need" className="adm-h4">{tr("Need a person ({})", number(d.needs_person.length))}</h3>
        {d.needs_person.length === 0 ? <Quiet>{tr("Nothing needs you. Names the matcher could not decide will appear here with the evidence it found.")}</Quiet> : <>
          <label className="adm-field idm-note"><span>{tr("Your reason, saved with each decision below")}</span><input value={note} onChange={(e) => setNote(e.target.value)} className="field-lg" /></label>
          <ul className="adm-items">{d.needs_person.map((r: any) => <NeedsPerson key={r.id} r={r} note={note} done={reload} />)}</ul>
        </>}
      </section>

      <section className="idm-sec" aria-labelledby="idm-auto">
        <h3 id="idm-auto" className="adm-h4">{tr("Matched automatically")}</h3>
        {d.recent.length === 0 ? <Quiet>{tr("Nothing matched automatically yet.")}</Quiet> : <>
          <ul className="idm-list">{recent.map((m: any) => <MatchedRow key={m.id} m={m} done={reload} canUndo />)}</ul>
          {d.recent.length > recent.length && <button className="ghost mt-2" onClick={() => setAll(true)}>{tr("Show all {}", d.recent.length)}</button>}
        </>}
      </section>

      {d.waiting.length > 0 && <details className="idm-wait"><summary>{tr("Waiting for the matcher ({})", number(c.waiting ?? d.waiting.length))}</summary>
        <ul className="tight small">{d.waiting.map((r: any) => <li key={r.id}><b>{r.raw_company_name}</b>{r.sheet_title && <span className="muted"> · {tr("{}, row {}", r.sheet_title, r.row_number)}</span>}{r.auto?.reason && <span className="muted"> · {unsureReason(r)}</span>}</li>)}</ul>
      </details>}
    </div>
  );
}

/* ------------------------------------------------------------------ Sources & data */

function SourceControls({ s, can, reload }: { s: any; can: boolean; reload: () => void }) {
  const [url, setUrl] = useState<string>(s.url ?? "");
  const [licence, setLicence] = useState(tr("Public newsroom page. robots.txt allows access. Reviewed by an administrator."));
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const wrap = async (f: () => Promise<any>, done: (r: any) => string) => { setBusy(true); setErr(null); setMsg(""); try { setMsg(done(await f())); reload(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const v = s.validation;
  return (
    <div className="src-ctl">
      {s.url_basis && s.url && <p className="small muted mt-0 mb-2">{tr("Why this address:") + " "}{s.url_basis}</p>}
      <div className="row">
        <input aria-label={tr("Page URL for {}", s.display_name)} placeholder={tr("https://… on {}", s.canonical_domain)} value={url} onChange={(e) => setUrl(e.target.value)} className="grow field-md" disabled={!can} />
        <button disabled={!can || busy || url.length < 12} onClick={() => wrap(() => put(`/api/admin/sources/${s.source_id}/config`, { url }), () => tr("Address saved. Test it next."))}>{tr("Save URL")}</button>
        <button disabled={!can || busy || !s.url} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/validate`), (r) => (r.ok ? tr("Test passed.") : tr("Test failed: {}", r.detail)))}>{tr("Test from server")}</button>
      </div>
      {v && <p className="small mt-2 mb-0"><Badge kind={v.ok ? "ok" : "bad"}>{v.ok ? tr("Test passed") : tr("Test failed")}</Badge> {v.detail} <span className="muted">· {when(v.at)}</span></p>}
      {can && v?.ok && s.status !== "active" && (
        <div className="mt-3">
          <label className="small">{tr("Permission note: why we may read this page")}<br /><textarea value={licence} onChange={(e) => setLicence(e.target.value)} /></label>
          <label className="small row mt-2 mb-2"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />{" " + tr("I have checked that monitoring this page is permitted")}</label>
          <button className="primary" disabled={busy || !ok || licence.length < 10} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/activate`, { licence_note: licence, confirm_permitted: ok }), () => tr("Source switched on. It will start within five minutes."))}>{tr("Switch source on")}</button>
        </div>)}
      {can && s.status === "active" && <button className="mt-3" disabled={busy} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/pause`, { reason: "paused by administrator" }), () => tr("Source paused."))}>{tr("Pause")}</button>}
      {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
    </div>
  );
}

const SCAN_OUTCOME: Record<string, string> = { no_website: "Company website missing", no_sources: "No sources found", all_sources_failed: "All source checks failed", partially_checked: "Some source checks failed" };
const DEMAND: Record<string, string> = { D1: "Explicit outside demand", D2: "Inferred outside demand", D3: "Verified programme", D4: "Early context" };

/** Check priority accounts for news, and the finance pilot results. */
function ScanTop() {
  const [n, setN] = useState(10); const [search, setSearch] = useState(false); const [financeOnly, setFinanceOnly] = useState(true);
  const [showPilot, setShowPilot] = useState(false); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(null); setMsg(""); try { const r = await post("/api/admin/scan-accounts", { limit: n, with_search: search, finance_only: financeOnly }); setMsg(tr("Checking {} {}. {} Results appear in the Market feed as we check them.", r.queued, r.queued === 1 ? tr("account") : tr("accounts"), r.note)); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <>
      <p className="small muted mt-0">{tr("Reads company news, career pages, job boards and public supplier pages")}{search ? tr(" and runs one web search per account") : ""}{tr(". The finance pilot checks up to 25 companies daily within the existing usage limits.")}</p>
      <div className="row">
        <label className="small">{tr("How many accounts") + " "}<input type="number" min={1} max={300} value={n} onChange={(e) => setN(Number(e.target.value))} className="field-xs" /></label>
        <label className="small row"><input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} />{" " + tr("Include one web search per account (uses the Exa budget)")}</label>
        <label className="small row"><input type="checkbox" checked={financeOnly} onChange={(e) => setFinanceOnly(e.target.checked)} />{" " + tr("Banking and financial services only")}</label>
        <button className="primary" disabled={busy || n < 1} onClick={go}>{tr("Check these accounts")}</button>
      </div>
      {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
      <button className="ghost mt-2" aria-expanded={showPilot} onClick={() => setShowPilot(!showPilot)}>{showPilot ? tr("Hide finance pilot") : tr("View finance pilot")}</button>
      {showPilot && <Load path="/api/admin/source-pilot">{(d, refresh) => (
        <div className="mt-2">
          <div className="row spread"><h4 className="adm-h4">{tr("Finance monitoring pilot")}</h4><button onClick={refresh}>{tr("Refresh results")}</button></div>
          <p className="small muted">{tr("Shows checks of company pages and up to five verified findings per account from the last 30 days, with buying signals listed first.")}</p>
          {d.items.length === 0 && <Quiet>{tr("No companies match the finance pilot criteria.")}</Quiet>}
          {d.items.length > 0 && <div className="adm-panel">
            <b>{tr("Pilot coverage by region")}</b>
            <ul className="tight small">{Array.from(new Set<string>(d.items.map((item: any) => item.region))).map(region => {
              const companies = d.items.filter((item: any) => item.region === region);
              const withoutWorkingSources = companies.filter((item: any) => !item.endpoints.some((source: any) => source.last_status === "ok")).length;
              return <li key={region}>{regionName(region)} · {tr("{} companies", companies.length)} · {tr("{} without a successful source check", withoutWorkingSources)}</li>;
            })}</ul>
          </div>}
          {d.items.map((account: any) => <details key={account.id} className="adm-pilot">
            <summary>{account.name}{account.confirmed ? ` · ${tr("Target account")}` : ""} · {tr("{} company sources", account.endpoints.length)} · {tr("{} recent findings", account.recent.length === 5 ? "5+" : account.recent.length)}</summary>
            <p className="small"><Link to={`/accounts/${account.id}`}>{tr("Open company")}</Link></p>
            <p className="small muted">{account.scan ? <>{tr("Scheduled check ·") + " "}{account.scan.status === "completed" ? tr(SCAN_OUTCOME[account.scan.outcome] ?? (account.scan.result?.endpoints === 0 ? "No sources found" : account.scan.result?.scanned === 0 ? "All source checks failed" : "Scan completed")) : humanize(account.scan.status)} · {when(account.scan.updated_at)}</> : tr("Waiting for the first scheduled check.")}</p>
            {account.scan?.status === "completed" && account.scan?.result && <p className="small">
              {tr("New or changed materials: {}", account.scan.result.documents ?? 0)} · {tr("New findings in this scan: {}", account.scan.result.processed?.events_created ?? 0)}
              {account.scan.result.error && <> · {humanize(account.scan.result.error)}</>}
              {account.scan.result.processed?.deferred_provider === "provider_billing" && <span className="coverage-warning">{tr("New pages are waiting for verification because the provider is out of credit.")}</span>}
            </p>}
            {account.scan?.last_error && <p className="small">{tr("Last scan error")} · {account.scan.last_error}</p>}
            {account.endpoints.length > 0 && !account.endpoints.some((source: any) => source.kind === "procurement") && <p className="small muted">{tr("No supplier page found")}</p>}
            {account.endpoints.length === 0 ? <p className="small muted">{tr("No company sources configured.")}</p> :
              <ul className="tight small">{account.endpoints.map((source: any) => <li key={source.url}>
                <a href={source.url} target="_blank" rel="noreferrer noopener">{source.kind === "procurement" ? tr("Supplier page") : humanize(source.kind)}</a>
                {source.last_scanned_at ? <> · {source.last_status === "ok" ? tr("Checked") : tr("Check failed")} {when(source.last_scanned_at)} · {tr("{} items read", source.docs_seen)}</> : <>{" " + tr("· waiting for first check")}</>}
                {source.last_success_at && <> · {tr("Last successful check")} {when(source.last_success_at)}</>}{source.last_status !== "ok" && source.last_detail && <div className="muted">{source.last_detail}</div>}
              </li>)}</ul>}
            {account.recent.map((finding: any) => <div className="mt-2" key={finding.event_id}>
              <b>{finding.summary}</b> <span className="small muted">{when(finding.date)}</span>
              <div className="small">{finding.demand_tier ? tr(DEMAND[finding.demand_tier] ?? finding.demand_tier) : tr("Verified finding · no current buying signal")}</div>
              <ul className="tight small">{finding.sources.map((source: any) => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer noopener">{source.source}</a></li>)}</ul>
            </div>)}
          </details>)}
        </div>
      )}</Load>}
    </>
  );
}

/* Sources: tested automatically every day; public feeds and open APIs that pass switch on by themselves. The rest say exactly why not and what a
   person needs to do. */

const SRC_PARTS: { key: string; label: string; states: string[]; kind: Kind }[] = [
  { key: "live", label: "Live", states: ["live"], kind: "ok" },
  { key: "testing", label: "Waiting for the daily test", states: ["testing"], kind: "info" },
  { key: "person", label: "Need a person", states: ["needs_credential", "needs_terms", "blocked_by_site", "needs_address", "needs_retest", "paused"], kind: "warn" },
  { key: "other", label: "Run another way or not built", states: ["built_in", "per_account", "not_built", "deferred"], kind: "" },
];
const SRC_GROUP: Record<string, string> = { needs_credential: "Needs a key or a contract", needs_terms: "Needs a terms check", blocked_by_site: "Blocked by the site", needs_address: "Needs an address",
  needs_retest: "Failed today's test", paused: "Paused", testing: "Waiting for the daily test" };

/** The reason a source is not live, in the person's language (a few reasons carry a name: the key to add, the registry note, the status). */
function srcReason(i: any): string {
  const t = i.test || {};
  if (i.code === "needs_key") return tr("Needs an API key: add {} on the worker.", i.param);
  if (i.code === "needs_terms") return tr("Needs a person to check the reuse terms. The registry note says: “{}”.", i.param);
  if (i.code === "marked") return tr("Marked {} by an earlier check, so it is not switched on automatically.", humanize(i.param).toLowerCase());
  return tr(i.reason ?? "") + (t.ok === true && i.state !== "live" ? " " + tr("Today's server test passed.") : "");
}

function SourceSummary({ canEdit }: { canEdit: boolean }) {
  const [tick, setTick] = useState(0);
  const r = useLoad("/api/admin/sources/auto", true, tick);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); setErr(null); setMsg(""); try { const x = await post("/api/admin/sources/auto/run"); setMsg(x.queued ? tr("Testing every source now. Those that pass and need no key or terms check switch on by themselves. Refresh in a few minutes.") : tr("A test of every source is already queued.")); setTick((t) => t + 1); } catch (e) { setErr(e); } finally { setBusy(false); } };
  if (r.err) return <Err e={r.err} onRetry={r.reload} />;
  if (!r.d) return <Skeleton rows={2} />;
  const d = r.d;
  const parts = SRC_PARTS.map((p) => ({ ...p, n: p.states.reduce((a, k) => a + Number(d.counts[k] ?? 0), 0) })).filter((p) => p.n > 0);
  const last = d.last_run; const sw = last?.result?.switched_on ?? [];
  return (
    <section className="src-sum" aria-labelledby="src-sum-h">
      <div className="src-sum-head">
        <h3 id="src-sum-h" className="adm-h4 mt-0">{tr("{} sources", number(d.total))}</h3>
        <p className="small muted m-0">{tr("Every source is tested from the server each day. A public feed or open API that passes, needs no key and has no terms to check switches on by itself. A source a person paused stays paused.")}</p>
      </div>
      <div className="seg" role="img" aria-label={parts.map((p) => `${tr(p.label)}: ${p.n}`).join(", ")}>
        {parts.map((p) => <span key={p.key} className={`seg-p ${p.kind}`} style={{ flexGrow: p.n }} title={`${tr(p.label)}: ${p.n}`}>{p.n / d.total > 0.06 ? number(p.n) : ""}</span>)}
      </div>
      <ul className="seg-key">{parts.map((p) => <li key={p.key}><i className={`seg-sw ${p.kind}`} /><b>{number(p.n)}</b> {tr(p.label)}</li>)}</ul>
      <div className="adm-action src-run">
        <div><b>{last ? tr("Last daily test {}", when(last.at)) : tr("The daily test has not run yet")}</b>
          <p className="small muted m-0">{last?.status === "completed" ? (sw.length ? tr("{} switched on by themselves in that run.", sw.length) : tr("Nothing new switched on in that run.")) : last?.error ? tr("It failed: {}", last.error) : last ? tr("Running now.") : tr("It runs once a day.")}
            {d.auto_on.length > 0 && <> {tr("Switched on automatically so far: {}.", d.auto_on.slice(0, 6).map((x: any) => x.name).join(", ") + (d.auto_on.length > 6 ? ` ${tr("and {} more", d.auto_on.length - 6)}` : ""))}</>}</p></div>
        {canEdit && <button className="primary" disabled={busy} onClick={run}>{tr("Test every source now")}</button>}
      </div>
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />
      {d.groups.filter((g: any) => g.state !== "testing").length > 0 && <div className="src-need">
        <h4 className="adm-h4">{tr("What needs a person")}</h4>
        {d.groups.filter((g: any) => g.state !== "testing").map((g: any) => (
          <details key={g.state} className="src-g" open={["needs_credential", "needs_terms", "needs_address"].includes(g.state)}>
            <summary><Say k={g.state === "blocked_by_site" ? "bad" : g.state === "paused" ? "" : "warn"}>{tr(SRC_GROUP[g.state] ?? g.label)}</Say><span className="src-g-n">{number(g.items.length)}</span></summary>
            <ul>{g.items.map((i: any) => <li key={i.source_id}>
              <b>{i.name}</b>
              <span>{srcReason(i)}</span>
              {(i.todo || i.where) && <span className="small muted">{[i.todo && tr(i.todo), i.where && i.where !== "-" && tr("Where: {}", tr(i.where))].filter(Boolean).join(" ")}</span>}
            </li>)}</ul>
          </details>))}
      </div>}
    </section>
  );
}

function Sources({ canEdit }: { canEdit: boolean }) {
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const bulk = async (activate: boolean) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post("/api/admin/sources/validate-all", { activate }); setMsg(activate ? tr("Testing every source now. Each one that passes is switched on. Refresh in a minute to see the result on each source.") : tr("Testing every source now. Nothing is switched on.")); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (<>
    <SourceSummary canEdit={canEdit} />
    {canEdit && <div className="adm-actions">
      <Fold title="Manual controls" desc="Test without switching anything on, or switch on everything that passes, including sources the daily test leaves for a person.">
        <div className="adm-action">
          <div><b>{tr("Test all sources")}</b><p className="small muted m-0">{tr("Tests each source from the server. We only read pages the site allows (robots.txt). Sources missing an address or a key are skipped and say why.")}</p></div>
          <div className="row"><button disabled={busy} onClick={() => bulk(false)}>{tr("Test only")}</button><button disabled={busy} onClick={() => bulk(true)}>{tr("Test, then switch on those that pass")}</button></div>
        </div>
        {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
      </Fold>
      <Fold title="Check priority accounts for news" desc="Read company news, careers and supplier pages for your top accounts now, instead of waiting for the daily run."><ScanTop /></Fold>
    </div>}
    <Library admin render={(s, reload) => <SourceControls s={s} can={canEdit} reload={reload} />} />
  </>);
}

const TAB_STATUS: Record<string, string> = { ok: "imported", drift: "columns changed, paused", missing: "tab not found", failed: "failed" };

function Imports() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const [tick, setTick] = useState(0);
  const load = () => { setTick((n) => n + 1); return api("/api/admin/imports").then(setD).catch(setErr); };
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 8000); return () => clearInterval(t); }, []);
  const send = async (f: File | undefined) => {
    if (!f) return; setBusy(true); setErr(null); setMsg("");
    try { const r = await upload("/api/admin/import/upload", f); setMsg(tr("Workbook {} received. It is being loaded now, which can take several minutes.", r.workbook)); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const sync = async (wb: string) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post(`/api/admin/import/sync?workbook=${wb}`); setMsg(tr("Sync of workbook {} started. This can take several minutes.", wb)); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const gs = d?.google_sheets;
  return (
    <>
      <p className="adm-lead">{tr("Your two lead workbooks are the list of accounts and contacts. Load them in one of two ways.")}</p>
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />
      <div className="adm-two">
        <div className="adm-panel">
          <h3 className="adm-h4 mt-0">{tr("Sync from Google Sheets")}</h3>
          {gs?.configured ? <p className="mt-0"><Say k="ok">{tr("Connected")}</Say>{" " + tr("Share both workbooks with") + " "}<b>{gs.service_account_email ?? tr("the service account")}</b>{" " + tr("as Viewer, then press Sync. Each sync re-reads the sheets and keeps the data up to date.")}</p>
            : <ol className="small mt-0">
                <li>{tr("In Google Cloud, create a service account in any project and enable the Google Sheets API. Create a JSON key for it.")}</li>
                <li>{tr("In Render, add the whole JSON as the secret") + " "}<code>GOOGLE_SERVICE_ACCOUNT_JSON</code>{" " + tr("on the API and the worker.")}</li>
                <li>{tr("Open each workbook in Google Sheets, press Share, and add the service account's email as Viewer.")}</li>
                <li>{tr("Come back here and press Sync. If your Google admin blocks sharing outside the company, upload the exports instead.")}</li>
              </ol>}
          <div className="row">
            <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("A")}>{tr("Sync workbook A")}</button>
            <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("B")}>{tr("Sync workbook B")}</button>
            {gs?.workbooks && <><a href={gs.workbooks.A} target="_blank" rel="noreferrer">{tr("Open A")}</a><a href={gs.workbooks.B} target="_blank" rel="noreferrer">{tr("Open B")}</a></>}
          </div>
        </div>
        <div className="adm-panel">
          <h3 className="adm-h4 mt-0">{tr("Or upload a workbook export")}</h3>
          <p className="mt-0 small">{tr("In Google Sheets choose") + " "}<b>{tr("File, Download, Microsoft Excel (.xlsx)")}</b>{" " + tr("for each workbook, then upload it here. We work out which workbook it is, check every tab's column headings against the agreed layout, and load it in the background. A renamed or reordered column pauses that tab instead of guessing.")}</p>
          <label className="adm-file"><span>{tr("Workbook file (.xlsx)")}</span><input type="file" accept=".xlsx" disabled={busy} onChange={(e) => { void send(e.target.files?.[0]); e.target.value = ""; }} /></label>
          <p className="small muted mb-0">{tr("Uploading the same file again changes nothing. We delete the file after loading it and keep only its fingerprint and the result.")}</p>
        </div>
      </div>
      <ImportHistory tick={tick} />
    </>
  );
}

const IMPORT_STATUS: Record<string, [Kind, string]> = { completed: ["ok", "Imported"], failed: ["bad", "Failed"], running: ["info", "Loading"], queued: ["info", "Waiting"],
  paused_schema_drift: ["warn", "Paused: columns changed"] };

/** Every workbook load, uploaded or synced, newest first: what came in, and why a tab was paused or failed. */
function ImportHistory({ tick }: { tick: number }) {
  const r = useLoad("/api/admin/imports/history", true, tick);
  return <section aria-labelledby="imp-h">
    <h3 id="imp-h" className="adm-h4">{tr("Import history")}</h3>
    {r.err ? <Err e={r.err} onRetry={r.reload} /> : !r.d ? <Skeleton /> : r.d.items.length === 0 ? <Quiet>{tr("No workbook has been loaded yet. Upload the two exports above, or sync them from Google Sheets.")}</Quiet> :
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Import history")}><table className="cards adm-t">
        <thead><tr><th>{tr("When")}</th><th>{tr("How")}</th><th>{tr("Workbook")}</th><th>{tr("Result")}</th><th className="num">{tr("New rows")}</th><th className="num">{tr("Rows read")}</th><th>{tr("Tabs")}</th></tr></thead>
        <tbody>{r.d.items.map((u: any) => {
          const tabs = u.tabs ? Object.entries(u.tabs) as [string, any][] : [];
          const known = tabs.filter(([, v]) => v.status !== "unknown_tab"), other = tabs.length - known.length;
          const [k, l] = IMPORT_STATUS[u.status] ?? ["", humanize(u.status)];
          return <tr key={u.id}>
            <td data-label={tr("When")} className="nowrap">{when(u.at)}</td>
            <td data-label={tr("How")} className="wide">{u.how === "upload" ? <><b>{u.name}</b><div className="small muted">{tr("Uploaded by {}", u.by ?? tr("someone"))}</div></> : <b>{tr("Synced (Google Sheets or the server)")}</b>}</td>
            <td data-label={tr("Workbook")}>{u.workbook ? tr("Workbook {}", u.workbook) : "—"}</td>
            <td data-label={tr("Result")}><Say k={k}>{tr(l)}</Say>{u.error && <div className="small">{u.error}</div>}</td>
            <td data-label={tr("New rows")} className="num">{number(u.new)}</td>
            <td data-label={tr("Rows read")} className="num">{number(u.rows)}</td>
            <td data-label={tr("Tabs")} className="small full">{known.length === 0 ? <span className="muted">—</span> : <details><summary>{plural(known.length, "One tab read", "{} tabs read")}</summary>
              <ul className="tight">{known.map(([name, v]) => <li key={name}><b>{name}</b>: {TAB_STATUS[v.status] ? tr(TAB_STATUS[v.status]) : humanize(v.status)}{v.rows !== undefined ? ` · ${tr("{} new of {} rows", number(v.new ?? 0), number(v.rows - (v.blank ?? 0)))}` : ""}</li>)}</ul>
              {other > 0 && <p className="muted m-0">{tr("{} other tabs not imported (archives and filtered views)", number(other))}</p>}</details>}</td>
          </tr>; })}</tbody></table></div>}
  </section>;
}

/* ------------------------------------------------------------------ Usage & cost */

const PROVIDER: Record<string, string> = { anthropic: "AI reading (Anthropic)", exa: "Web search (Exa)", apollo: "Apollo contact credits" };
const UNIT: Record<string, string> = { tokens: "tokens", usd: "dollars", credits: "credits" };
const amount = (v: any, unit: string) => unit === "usd" ? `$${Number(v).toFixed(2)}` : number(Math.round(Number(v)));
const monthOf = (period: string) => /^\d{4}-\d{2}$/.test(period) ? new Date(`${period}-15T12:00:00Z`).toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { month: "long", year: "numeric" }) : period;

/** Every monthly limit: what it allows, what is set aside for calls in progress, what is used and what is left. */
function BudgetDetails() {
  return <Load path="/api/admin/costs">{(d) => {
    const rows = [...d.budgets].sort((a: any, b: any) => String(b.period).localeCompare(String(a.period)) || String(a.provider).localeCompare(String(b.provider)));
    return <section aria-labelledby="lim-h">
      <h3 id="lim-h" className="adm-h4 mt-0">{tr("Monthly limits")}</h3>
      <p className="adm-lead">{tr("A paid call only runs if it fits in what is left of its limit. At zero, nothing runs. Apollo can never spend more credits than the limit set in Connected services.")}</p>
      {rows.length === 0 ? <Quiet>{tr("No limits are set yet, so no paid call can run. Limits are set on the server; the Apollo limit is set in Connected services.")}</Quiet> :
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Monthly limits")}>
          <table className="cards adm-t"><thead><tr><th>{tr("Service")}</th><th>{tr("Month")}</th><th className="num">{tr("Limit")}</th><th className="num">{tr("Used")}</th><th className="num">{tr("Set aside now")}</th><th className="num">{tr("Left")}</th><th>{tr("Counted in")}</th></tr></thead>
            <tbody>{rows.map((b: any) => {
              const left = Math.max(Number(b.cap) - Number(b.spent) - Number(b.reserved), 0);
              const full = Number(b.cap) > 0 && left <= 0;
              return <tr key={`${b.provider}-${b.kind}-${b.period}`}>
                <td data-label={tr("Service")} className="wide"><b>{tr(PROVIDER[b.provider] ?? humanize(b.provider))}</b>{b.kind !== "monthly" && <div className="small muted">{tr(humanize(b.kind))}</div>}</td>
                <td data-label={tr("Month")}>{monthOf(b.period)}</td>
                <td data-label={tr("Limit")} className="num">{amount(b.cap, b.unit)}</td>
                <td data-label={tr("Used")} className="num">{amount(b.spent, b.unit)}</td>
                <td data-label={tr("Set aside now")} className="num">{amount(b.reserved, b.unit)}</td>
                <td data-label={tr("Left")} className="num">{full ? <Say k="bad">{tr("None")}</Say> : amount(left, b.unit)}</td>
                <td data-label={tr("Counted in")}>{tr(UNIT[b.unit] ?? humanize(b.unit))}</td>
              </tr>; })}</tbody></table>
        </div>}
      {!rows.some((b: any) => b.provider === "apollo" && b.period === new Date().toISOString().slice(0, 7)) && <p className="small"><Say k="">{tr("Apollo contact credits: no limit is set for this month, so no email address can be revealed.")}</Say> <Link to="/admin/integrations">{tr("Set the limit in Connected services")}</Link></p>}
    </section>;
  }}</Load>;
}

/* ------------------------------------------------------------------ Team & access */

function ApolloCap({ r }: { r: any }) {
  const [v, setV] = useState(String(r.cap ?? 0)); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const save = async () => { setErr(null); try { await post("/api/admin/integrations/apollo/cap", { credits: Number(v) }); setMsg(tr("Saved for this month.")); } catch (e) { setErr(e); } };
  return <div><span className="k">{tr("Credits this month")}</span>
    <div className="row"><input aria-label={tr("Apollo credits allowed this month")} type="number" min={0} value={v} onChange={(e) => setV(e.target.value)} className="field-sm" /><button onClick={save}>{tr("Set the limit")}</button><span className="small muted">{tr("{} credits used this month.", number(Number(r.spent ?? 0)))} {r.credits_per_reveal ? tr("One reveal costs {} credits.", r.credits_per_reveal) : tr("The price of one reveal is not set on the server yet, so no reveal can be approved.")} {tr("At zero, no reveal can run. Apollo never spends beyond this limit.")}</span></div>
    {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} /></div>;
}

function Integrations() {
  const [res, setRes] = useState<Record<string, { ok: boolean; detail: string }>>({}); const [busy, setBusy] = useState("");
  const test = async (k: string) => { setBusy(k); try { const r = await post(`/api/admin/integrations/${k}/test`); setRes((x) => ({ ...x, [k]: r })); } catch (e) { setRes((x) => ({ ...x, [k]: { ok: false, detail: e instanceof Error ? e.message : tr("Test failed") } })); } finally { setBusy(""); } };
  return (
    <Load path="/api/admin/integrations">{(d) => (<>
      <p className="adm-lead">{tr("The accounts we connect to for outreach and contact data. Keys are stored on the server and never shown here. A passing test only proves the key works. Sending also needs its switch turned on and a colleague's approval.")}</p>
      <ul className="adm-items">
        {d.items.filter((r: any) => !HIDDEN_INTEGRATION.test(`${r.key} ${r.name}`)).map((r: any) => <li key={r.key} className={`adm-item ${r.configured ? "ok" : "warn"}`}>
          <div className="adm-item-head"><div><b>{r.name}</b><div className="small muted">{r.what}</div></div>
            <div className="row"><Say k={r.configured ? "ok" : "warn"}>{r.configured ? tr("Connected") : tr("Not connected")}</Say>{r.switch && <Say k={r.switch_on ? "ok" : ""}>{r.switch_on ? tr("Switched on") : tr("Switched off")}</Say>}</div></div>
          {r.blocked_reason && <div className="err m-0" role="alert">{r.blocked_reason}</div>}
          <div className="conn-cols"><div><span className="k">{tr("How to connect it")}</span>{r.how}</div>{r.switch && <div><span className="k">{tr("Safety switch")}</span>{r.switch}: {r.switch_on ? tr("on") : tr("off. Nothing is sent or changed until it is turned on.")}</div>}</div>
          {r.key === "apollo" && <ApolloCap r={r} />}
          {r.testable && <div className="row"><button disabled={!r.configured || busy === r.key} onClick={() => test(r.key)}>{tr("Test the connection")}</button>
            {res[r.key] && <span className={res[r.key].ok ? "okmsg m-0" : "small"} role="status">{res[r.key].detail}</span>}</div>}
        </li>)}
      </ul></>)}</Load>
  );
}

const ROLES = ["bdm", "sales_manager", "research_analyst", "marketing", "delivery_expert", "admin", "compliance"];
function Users() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const [f, setF] = useState({ email: "", display_name: "", role: "bdm", team: "default" });
  const load = () => api("/api/admin/users").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = async (fn: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await fn(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  return (
    <>
      <p className="adm-lead">{tr("Everyone who can sign in, and what they can do. People sign in with Google using their work address")}{d?.domain ? ` ${tr("(only @{} is accepted)", d.domain)}` : ""}.</p>
      <div className="adm-panel">
        <h3 className="adm-h4 mt-0">{tr("Add a person")}</h3>
        <div className="adm-form">
          <label><span>{tr("Work email")}</span><input placeholder="name@thecodest.co" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label><span>{tr("Display name")}</span><input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></label>
          <label><span>{tr("Role")}</span><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select></label>
          <label><span>{tr("Team")}</span><input value={f.team} onChange={(e) => setF({ ...f, team: e.target.value })} /></label>
          <button className="primary" disabled={!f.email.includes("@") || !f.display_name} onClick={() => run(async () => { await post("/api/admin/users", f); setF({ ...f, email: "", display_name: "" }); }, tr("Person added. They can sign in now."))}>{tr("Add")}</button>
        </div>
        {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
      </div>
      {d && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("People who can sign in")}><table><thead><tr><th>{tr("Person")}</th><th>{tr("Role")}</th><th>{tr("Team")}</th><th>{tr("Status")}</th><th><span className="sr">{tr("Actions")}</span></th></tr></thead><tbody>{d.items.map((u: any) => <tr key={u.id}>
        <td>{u.display_name}<div className="small muted">{u.email}</div></td>
        <td><select aria-label={tr("Role for {}", u.email)} value={u.role} onChange={(e) => run(() => patch(`/api/admin/users/${u.id}`, { role: e.target.value }), tr("Role updated."))}>{ROLES.map((r) => <option key={r} value={r}>{lab("role", r)}</option>)}</select></td>
        <td>{u.team}</td><td><Say k={u.active ? "ok" : ""}>{u.active ? tr("Active") : tr("Deactivated")}</Say></td>
        <td><button className="ghost" onClick={() => run(() => patch(`/api/admin/users/${u.id}`, { active: !u.active }), u.active ? tr("Deactivated.") : tr("Reactivated."))}>{u.active ? tr("Deactivate") : tr("Reactivate")}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}

/** What each recorded action means, in words. Anything not listed is spelled out from its name. */
const OPS: Record<string, string> = {
  pii_read: "Looked at contact details", reconcile_row: "Loaded a workbook row", sheet_import: "Loaded a workbook", sheet_upload: "Uploaded a workbook", sheet_sync_requested: "Asked for a workbook sync",
  activate_source: "Switched a source on", pause_source: "Paused a source", source_set_url: "Set a source's page address", source_validate: "Tested a source", sources_switched_on_automatically: "Sources switched on by the daily test",
  sources_auto_check_requested: "Asked for a test of every source", sources_validate_all_requested: "Asked for a test of every source", source_run_failed: "A source failed",
  opportunity_transition: "Moved an opportunity", evaluate_hypothesis: "Scored an opportunity", create_initiative: "Created a programme", outcome_recorded: "Recorded an outcome",
  identity_review_resolved: "Decided a company match", identity_review_undone: "Undid a company match", identity_auto_requested: "Started automatic matching", identity_match: "Checked a company match",
  approval_requested: "Asked for an approval", approval_approved: "Approved a request", approval_rejected: "Rejected a request", approval_executed: "Carried out an approved request",
  dead_letters_retried: "Retried failed jobs", dead_letters_dismissed: "Dismissed failed jobs", dlq_replay: "Retried a failed job",
  user_add: "Added a person", user_edit: "Changed a person's access", apollo_cap_set: "Set the Apollo credit limit", integration_tested: "Tested a connected service", kb_upload: "Uploaded a knowledge base document",
  discovery_plan_toggle: "Switched a saved web search on or off", discovery_run_requested: "Ran saved web searches", account_scan_requested: "Asked to check a company", account_scan_batch_requested: "Asked to check priority companies",
  tenders_refresh_requested: "Asked for new tenders", tender_summaries_requested: "Asked for tender summaries", tender_summary: "Summarised a tender",
  account_owner_set: "Set a company's owner", account_relationship_set: "Set our relationship with a company", company_added: "Added a company", company_profile_updated: "Updated a company profile",
  person_added_from_exa: "Added a person from a web search", buyer_role_added: "Added a buyer role", enrich_person: "Looked up a person", people_search: "Searched for people", exa_people_search: "Searched the web for people",
  contact_search: "Searched for contacts", contact_revealed: "Revealed an email address", leads_exported: "Exported leads", watch_added: "Added a company to the watch list",
  signal_campaign_created: "Created a campaign", signal_campaign_copy_changed: "Changed a campaign's emails", signal_campaign_push_requested: "Asked to send a campaign to Lemlist",
  signal_campaign_pushed: "Sent a campaign to Lemlist", signal_campaign_withdrawn: "Withdrew a campaign", lemlist_enrolment_created: "Added someone to a Lemlist sequence",
  campaign_draft: "Drafted campaign emails", account_brief: "Wrote a company brief", verification_requested: "Asked to check a page", verification_batch_requested: "Asked to check pages",
  miss_reported: "Reported a page we missed", deadline_proposed: "Proposed a deadline", proof_asset_created: "Added a proof asset", schema_drift: "A workbook's columns changed",
  scheduler_lane_changed: "Changed an automatic task", scheduler_lane_run_requested: "Asked an automatic task to run", source_cadence_changed: "Changed how often a source is read",
  source_read_now: "Asked for a source to be read now",
  record_hard_block: "Blocked a record", create_activity: "Logged a CRM activity", create_deal: "Created a CRM deal", canary_failed: "A safety check failed", delivery_validation: "Checked a delivery",
};
const opName = (op: string) => tr(OPS[op] ?? humanize(op));
const RECORD: Record<string, string> = { organization: "Company", organization_people: "People at a company", people_candidates: "People found for a company", crm_organization: "Company",
  person: "Person", people_list: "People list", source: "Source", source_family: "Group of sources", user: "Person with access", signal_campaign: "Campaign", identity_review: "Company match",
  initiative: "Programme", opportunity_hypothesis: "Opportunity", discovery_plan: "Saved web search", import_run: "Workbook load", import_upload: "Workbook upload", account_source_link: "Workbook row",
  approval: "Approval", dead_letter: "Failed job", dead_letter_group: "Failed jobs", job: "Background job", integration: "Connected service", kb_document: "Knowledge base document",
  verification_request: "Page check", regulatory_deadline: "Deadline", proof_asset: "Proof asset", campaign_draft: "Email draft", platform: "Platform", sheet: "Workbook",
  scheduler_lane: "Automatic task" };
const recordLink = (t: string, id: string) => (["organization", "organization_people", "people_candidates", "crm_organization"].includes(t) ? `/accounts/${id}` : t === "person" ? `/people/${id}`
  : t === "opportunity_hypothesis" ? `/opportunities/${id}` : t === "signal_campaign" ? `/campaigns/${id}` : null);
/** Who did it: a person's address, or the automatic job that did it. */
const actorName = (a: string) => (a === "auto" ? tr("Automatic matcher") : a.startsWith("system:") ? tr("Automatic ({})", tr(humanize(a.slice(7).replace(/-/g, "_"))).toLowerCase()) : a);

/** A short line from the recorded detail: counts, names and notes, never raw JSON. */
function detailLine(d: any): string {
  if (!d || typeof d !== "object") return "";
  const out: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) out.push(`${tr(humanize(k))}: ${number(v.length)}`);
    else if (typeof v === "boolean") out.push(`${tr(humanize(k))}: ${v ? tr("yes") : tr("no")}`);
    else if (typeof v === "number") out.push(`${tr(humanize(k))}: ${number(v)}`);
    else if (typeof v === "string" && v.length <= 120) out.push(`${tr(humanize(k))}: ${v}`);
    if (out.length >= 3) break;
  }
  return out.join(" · ");
}

const AUDIT_VIEWS: [string, string][] = [["changes", "Changes only"], ["all", "Everything"], ["reads", "Views of contact details"], ["bulk", "Rows from workbook loads"]];

function Audit() {
  const [f, setF] = useQueryState({ view: "changes", since: "30d", actor: "", operation: "", page: "0" });
  const LIMIT = 50; const off = Number(f.page) || 0;
  const q = new URLSearchParams({ view: f.view, since: f.since === "all" ? "" : f.since, limit: String(LIMIT), offset: String(off) });
  if (f.actor) q.set("actor", f.actor);
  if (f.operation) q.set("operation", f.operation);
  const r = useLoad(`/api/admin/audit?${q}`);
  const d = r.d;
  const set = (p: Partial<typeof f>) => setF({ ...p, page: "0" });
  return <>
    <p className="adm-lead">{tr("Every change anyone made, and every automatic change, newest first, with the approval behind it when one was needed. Looking at contact details and the rows of a workbook load are recorded too, and kept apart.")}</p>
    <div className="fbar">
      <Pick label="Show" value={f.view} onChange={(v) => set({ view: v, operation: "" })} options={AUDIT_VIEWS.map(([k, l]) => [k, tr(l)])} />
      <Pick label="When" value={f.since} onChange={(v) => set({ since: v })} options={windows()} />
      <Pick label="Who" value={f.actor} onChange={(v) => set({ actor: v })} options={[["", tr("Anyone")], ...(d?.actors ?? []).map((a: any) => [a.actor, `${actorName(a.actor)} (${number(a.n)})`] as [string, string]),
        ...(f.actor && !(d?.actors ?? []).some((a: any) => a.actor === f.actor) ? [[f.actor, actorName(f.actor)] as [string, string]] : [])]} />
      <Pick label="What" value={f.operation} onChange={(v) => set({ operation: v })} options={[["", tr("Any action")], ...(d?.operations ?? []).map((o: any) => [o.operation, `${opName(o.operation)} (${number(o.n)})`] as [string, string]),
        ...(f.operation && !(d?.operations ?? []).some((o: any) => o.operation === f.operation) ? [[f.operation, opName(f.operation)] as [string, string]] : [])]} />
    </div>
    {d && d.hidden > 0 && <p className="small muted">{plural(d.hidden, "One view of contact details or workbook row is hidden here. Choose Everything to see it.", "{} views of contact details and workbook rows are hidden here. Choose Everything to see them.")}</p>}
    {r.err ? <Err e={r.err} onRetry={r.reload} /> : !d ? <Skeleton /> : d.items.length === 0 ? <Quiet>{f.actor || f.operation ? tr("Nothing matches these filters. Choose Anyone and Any action, or a longer time.") : tr("Nothing recorded in this time. Choose a longer time.")}</Quiet> : <>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Audit log")}><table className="cards adm-t">
        <thead><tr><th>{tr("When")}</th><th>{tr("Who")}</th><th>{tr("What they did")}</th><th>{tr("About")}</th><th>{tr("Detail")}</th><th>{tr("Approval")}</th></tr></thead>
        <tbody>{d.items.map((a: any) => {
          const link = a.record_id ? recordLink(a.record_type, a.record_id) : null;
          const what = tr(RECORD[a.record_type] ?? humanize(a.record_type));
          const name = a.record_name ?? (a.record_id && !/^[0-9a-f-]{20,}$/i.test(a.record_id) ? a.record_id : null);
          return <tr key={a.id}>
            <td data-label={tr("When")} className="nowrap">{when(a.at)}</td>
            <td data-label={tr("Who")}>{actorName(a.actor)}</td>
            <td data-label={tr("What they did")}><b>{opName(a.operation)}</b></td>
            <td data-label={tr("About")} className="wide">{name ? (link ? <Link to={link}>{name}</Link> : <span>{name}</span>) : link ? <Link to={link}>{tr("Open the {}", what.toLowerCase())}</Link> : null}<div className="small muted">{what}</div></td>
            <td data-label={tr("Detail")} className="small full">{detailLine(a.detail) || <span className="muted">—</span>}</td>
            <td data-label={tr("Approval")} className="nowrap">{a.approval_id ? <Link to={`/admin/approvals#approval-${a.approval_id}`}>{tr("Approved")}</Link> : <span className="muted" aria-label={tr("Not needed")}>—</span>}</td>
          </tr>; })}</tbody></table></div>
      <Pager total={d.total} offset={off} limit={LIMIT} onPage={(o) => setF({ page: String(o) })} label="Audit log" />
    </>}
  </>;
}

/* ------------------------------------------------------------------ the page */

function HealthRoute({ s, can }: { s?: Section; can: (p: string) => boolean }) {
  const { part } = useParams();
  return <Sec s={s}><Health can={can} part={part} /></Sec>;
}

export default function Admin({ me, waiting, refreshApprovals }: { me: AdminUser; waiting: number; refreshApprovals: () => void }) {
  const p = (x: string) => me.permissions.includes(x);
  const loc = useLocation();
  const secs = sectionsFor(p, waiting);
  const active = sectionOf(secs, loc.pathname);
  const sec = (k: string) => secs.find((s) => s.key === k);
  const first = (k: string) => sec(k)?.tabs[0]?.path ?? "/admin";
  const review = sec("review"), data = sec("data"), usage = sec("usage"), health = sec("health"), team = sec("team");
  return (
    <div className="adm">
      <div className="page-head adm-head"><h1>{tr("Administration")}</h1><p className="sub">{tr("Keep the data flowing, and decide what needs a person.")}</p></div>
      <SectionBar secs={secs} active={active} />
      <Routes>
        <Route index element={<Sec s={sec("overview")} bare><Overview can={p} /></Sec>} />
        <Route path="review" element={<Navigate to={first("review")} replace />} />
        <Route path="approvals" element={<Sec s={review}><Gate ok={p("approvals.read_own") || p("approvals.read_all")}><Approvals me={me} refresh={refreshApprovals} /></Gate></Sec>} />
        <Route path="identity" element={<Sec s={review}><Gate ok={p("identity.manage")}><Identity isAdmin={p("admin.identity")} /></Gate></Sec>} />
        <Route path="verification" element={<Sec s={review}><Gate ok={p("sources.read")}><Verification /></Gate></Sec>} />
        <Route path="data" element={<Navigate to={first("data")} replace />} />
        <Route path="sources" element={<Sec s={data}><Gate ok={p("sources.read")}><Sources canEdit={p("admin.sources")} /></Gate></Sec>} />
        <Route path="data-health" element={<Sec s={data}><Gate ok={p("sources.read")}><DataHealth /></Gate></Sec>} />
        <Route path="source-checks" element={<Sec s={data}><Gate ok={p("sources.read")}><SourceChecks /></Gate></Sec>} />
        <Route path="connections" element={<Navigate to="/admin/sources" replace />} />
        <Route path="imports" element={<Sec s={data}><Gate ok={p("admin.sources")}><Imports /></Gate></Sec>} />
        <Route path="discovery" element={<Sec s={data}><Gate ok={p("admin.costs")}><Discovery /></Gate></Sec>} />
        <Route path="knowledge" element={<Sec s={data}><Gate ok={p("admin.sources")}><KnowledgeAdmin /></Gate></Sec>} />
        <Route path="usage" element={<Sec s={usage}><Usage /></Sec>} />
        <Route path="usage/calls" element={<Sec s={usage}><PaidCalls /></Sec>} />
        <Route path="usage/limits" element={<Sec s={usage}><BudgetDetails /><SpendTotals /></Sec>} />
        <Route path="budgets" element={<Navigate to="/admin/usage/limits" replace />} />
        <Route path="costs" element={<Navigate to="/admin/usage" replace />} />
        <Route path="health/:part?" element={<HealthRoute s={health} can={p} />} />
        <Route path="job-history" element={<Sec s={health}><Gate ok={p("admin.jobs")}><JobHistory /></Gate></Sec>} />
        {HEALTH_PARTS.map((k) => <Route key={k} path={k} element={<Navigate to={`/admin/health/${k}`} replace />} />)}
        <Route path="team" element={<Navigate to={first("team")} replace />} />
        <Route path="users" element={<Sec s={team}><Gate ok={p("admin.users")}><Users /></Gate></Sec>} />
        <Route path="integrations" element={<Sec s={team}><Gate ok={p("admin.costs")}><Integrations /></Gate></Sec>} />
        <Route path="audit" element={<Sec s={team}><Gate ok={p("audit.read")}><Audit /></Gate></Sec>} />
        <Route path="rules" element={<Navigate to="/library?tab=signals" replace />} />
        <Route path="deadlines" element={<Navigate to="/admin" replace />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </div>
  );
}
