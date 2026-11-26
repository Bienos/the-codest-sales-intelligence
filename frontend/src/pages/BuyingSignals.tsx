import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, fmtDate, fmtDateTime, post, trackSourceOpened } from "../api";
import { counted, getLanguage, number, tr } from "../locale";
import { Empty, Err, Skeleton, safeHref } from "../components/ui";
import { CompanyAvatar, ListRank, ago } from "../components/Findings";
import { FAMILIES, FAMILY, familyName, familyOf, signalName } from "../components/Signals";
import { REGIONS, countryName } from "./targetMarket";
import "../styles/accounts.css";
import "../styles/signals.css";

/** Nav icon: a pulse line. */
export const IconSignals = (p: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>
    <path d="M2 12h4l3-7 5 14 3-7h5" />
  </svg>
);
const Ico = ({ d, className = "bs-ico" }: { d: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" aria-hidden="true">{d}</svg>
);
const IconSearch = () => <Ico d={<><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5l5 5" /></>} />;
const IconPlace = () => <Ico d={<><path d="M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0113 0c0 4.9-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>} />;
const IconClock = () => <Ico d={<><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>} />;
const IconSort = () => <Ico d={<path d="M7 4v16M3.5 16.5L7 20l3.5-3.5M17 20V4M13.5 7.5L17 4l3.5 3.5" />} />;
const IconKind = () => <Ico d={<path d="M2 12h4l3-7 5 14 3-7h5" />} />;
const IconRefresh = () => <Ico className="bs-ico bs-ico-refresh" d={<><path d="M20 11a8 8 0 10-2.3 5.7" /><path d="M20 4v7h-7" /></>} />;
const IconTable = () => <Ico d={<path d="M3 5h18M3 10h18M3 15h18M3 20h18M9 5v15" />} />;
const IconCards = () => <Ico d={<><rect x="3.5" y="3.5" width="7.5" height="7.5" /><rect x="13" y="3.5" width="7.5" height="7.5" /><rect x="3.5" y="13" width="7.5" height="7.5" /><rect x="13" y="13" width="7.5" height="7.5" /></>} />;
const IconList = () => <Ico d={<path d="M3 6h3M9 6h12M3 12h3M9 12h12M3 18h3M9 18h12" />} />;
const Chevron = ({ className = "bs-chev" }: { className?: string }) => <svg className={className} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M2.5 4.5L6 8l3.5-3.5" /></svg>;

type Fit = { key: string | null; name: string; reason: string | null; level: "strong" | "possible" | "weak" | null; score: number | null };
type Org = { id: string; name: string; country: string | null; region: string; segment: string | null; domain: string | null;
  list_priority: string | null; list_score: number | null; people: number; people_with_email: number; best_fit?: Fit | null };
type Tender = { deadline: string | null; url: string | null; title: string | null; scope?: string | null };
type Signal = {
  id: string; rule_id: string; strength: number; demand_tier: string; intent: string; event_id: string; event_type: string;
  summary: string; summary_en?: string | null; summary_pl?: string | null; happened: string | null; age_days: number | null; more: number;
  fit?: { level?: string; service?: string; reason?: string } | null;
  tender: Tender | null;
  evidence: { quote: string | null; translation: string | null; url: string | null; source: string | null; published_at: string | null };
};
type Company = { organization: Org; signals: Signal[]; strength: number; strength_reason: string; kinds: number; families?: string[]; latest: string | null; age_days: number | null };
type Kind = { rule_id: string; name: string; group: string | null; strength: number; meaning: string; who_to_contact: string | null; companies: number; fresh: number; latest: string | null };
type Top = { window: number; organization: { id: string; name: string; country: string | null; region: string }; company_strength: number; strength_reason: string;
  signal: { rule_id: string; event_id: string; summary: string; summary_en?: string | null; summary_pl?: string | null; happened: string | null; age_days: number | null; strength: number; tender: Tender | null } };
type Pulse = { days: number; windows: number[]; families: { family: string; d7: number; d30: number; d90: number }[];
  weeks: { start: string; end: string; news: number; companies: number }[]; week: { news: number; companies: number }; companies: number; top: Top | null };
type Data = { days: number; sort: string; rule: string; family?: string; companies: Company[]; kinds: Kind[]; all: { companies: number; signals: number }; fresh_days: number;
  totals: { companies: number; signals: number; reachable: number; fresh: number; strong: number; held_back: number }; best_fit?: boolean; pulse?: Pulse };
type Fresh = {
  now: string; tick_minutes: number; sources: { watched: number; checked_24h: number; failing: number; every_hours: [number, number] | null };
  last_checked_at: string | null; next_check_at: string | null; due_next: number; automatic: boolean; worker: boolean; ai_reading: "on" | "paused" | "off";
  running: boolean; in_progress: number; queued: number;
  manual: { at: string; by_me: boolean; jobs: number; done: number; failed: number; in_progress: number; running: boolean } | null;
  cooldown_minutes: number; cooldown_until: string | null; recent_minutes: number; can_refresh: boolean;
  blocked: null | "permission" | "no_sources" | "worker" | "running" | "cooldown"; poll_seconds: number;
};
type View = "table" | "cards" | "summary";

const WINDOWS: [string, string][] = [["30", "Last 30 days"], ["90", "Last 90 days"], ["180", "Last 6 months"], ["365", "Last 12 months"]];
const SORTS: [string, string][] = [["strength", "Strongest first"], ["newest", "Newest first"], ["priority", "Lead list priority"], ["company", "Company A to Z"]];
const SERVER_SORTS = ["strength", "newest", "priority"];
/** Each sortable column and the way it reads first: the strongest, newest, most reachable first; names A to Z. */
const DEFAULT_DIR: Record<string, "asc" | "desc"> = { strength: "desc", newest: "desc", priority: "asc", company: "asc", signal: "asc", region: "asc", email: "desc", fit: "asc" };
const LEVEL: Record<number, string> = { 3: "Strong", 2: "Good", 1: "Early" };
/** Why a company is rated strong, shown under the bars. A plain "Good" or "Early" needs no extra words. */
const REASON: Record<string, string> = {
  outside_demand: "They asked for outside help",
  need_likely: "Outside help is likely",
  several: "Several signals at once",
  strong_kind: "One of the strongest signals",
};
const REGION_SHORT: Record<string, string> = { uk: "UK", dach: "DACH", nordics: "Nordics", poland: "Poland", europe: "Europe", international: "Elsewhere" };
const PAGE = 40;
const FRESH_DAYS = 14;
const VIEW_KEY = "ri.signals.view";

function storedView(): View {
  try { const v = localStorage.getItem(VIEW_KEY); return v === "cards" || v === "summary" ? v : "table"; } catch { return "table"; }
}
function storeView(v: View) {
  try { localStorage.setItem(VIEW_KEY, v); } catch { /* storage unavailable: the choice lasts for this visit */ }
}

/** Signal strength as three rising bars, the way a phone shows reception. */
function Bars({ n }: { n: number }) {
  return <span className={`bs-bars is-s${n}`} aria-hidden="true">{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;
}
const Swatch = ({ ruleId, family }: { ruleId?: string; family?: string }) => <i className={`bs-fam bs-fam-${(family ?? familyOf(ruleId)).toLowerCase()}`} aria-hidden="true" />;

/** The summary often starts with the company's name, which the row already shows next to it. */
function plain(text: string, name: string) {
  const t = (text ?? "").trim();
  if (t.startsWith(name + " ")) {
    const rest = t.slice(name.length + 1);
    if (/^[a-ząćęłńóśźż]/.test(rest)) return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  return t;
}
const said = (s: { summary: string; summary_en?: string | null; summary_pl?: string | null }, name: string) =>
  plain((getLanguage() === "pl" ? s.summary_pl : s.summary_en) || s.summary, name);

const clock = (v: string | Date) => new Date(v).toLocaleTimeString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit" });
/** "just now", "14 min ago", "3 hours ago", then the usual days and weeks. */
function since(v: string | null, now: number): string {
  if (!v) return "";
  const minutes = Math.floor((now - new Date(v).getTime()) / 60_000);
  if (minutes < 1) return tr("just now");
  if (minutes < 60) return tr("{} min ago", minutes);
  if (minutes < 24 * 60) return Math.round(minutes / 60) === 1 ? tr("an hour ago") : tr("{} hours ago", Math.round(minutes / 60));
  return ago(v);
}
/** "~16:30" today, "~Thu 09:05" later in the week, a date after that. */
function when(v: string, now: number): string {
  const d = new Date(v);
  const hours = (d.getTime() - now) / 3_600_000;
  if (hours < 0.03) return tr("in a moment");
  const sameDay = new Date(now).toDateString() === d.toDateString();
  if (sameDay) return `~${clock(d)}`;
  if (hours < 6 * 24) return `~${d.toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { weekday: "short" })} ${clock(d)}`;
  return fmtDate(v);
}

function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t); }, [ms]);
  return now;
}

/** How fresh the signals are. Polls quickly while a check runs, slowly otherwise, and tells the page when a check has finished. */
function useFreshness(onFinished: () => void) {
  const [fresh, setFresh] = useState<Fresh | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [kick, setKick] = useState(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  const prev = useRef<{ last: string | null; running: boolean } | null>(null);
  const seen = (f: Fresh) => {
    const p = prev.current;
    if (p && ((p.running && !f.running) || (!f.running && f.last_checked_at && f.last_checked_at !== p.last))) finished.current();
    prev.current = { last: f.last_checked_at, running: f.running };
  };
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const load = () => {
      if (document.hidden) { timer = window.setTimeout(load, 15_000); return; }
      api<Fresh>("/api/signals/freshness")
        .then((f) => { if (!live) return; seen(f); setFresh(f); setError(null); timer = window.setTimeout(load, Math.max(5, f.poll_seconds) * 1000); })
        .catch((e) => { if (!live) return; setError(e); timer = window.setTimeout(load, 60_000); });
    };
    load();
    const wake = () => { if (!document.hidden) { window.clearTimeout(timer); load(); } };
    document.addEventListener("visibilitychange", wake);
    return () => { live = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", wake); };
  }, [kick]);      // eslint-disable-line react-hooks/exhaustive-deps
  return { fresh, error, set: (f: Fresh) => { seen(f); setFresh(f); }, restart: () => setKick((k) => k + 1) };
}

export default function BuyingSignals({ canCampaign = false, canPeople = false }: { canCampaign?: boolean; canPeople?: boolean }) {
  const [params, setParams] = useSearchParams();
  const days = WINDOWS.some(([k]) => k === params.get("days")) ? params.get("days")! : "90";
  const region = REGIONS.some(([k]) => k === params.get("region")) ? params.get("region")! : "";
  const rule = /^[A-Z]{3}-\d{2}$/.test(params.get("rule") ?? "") ? params.get("rule")! : "";
  const family = (FAMILIES as readonly string[]).includes(params.get("family") ?? "") ? params.get("family")! : "";
  const sort = Object.prototype.hasOwnProperty.call(DEFAULT_DIR, params.get("sort") ?? "") ? params.get("sort")! : "strength";
  const dir: "asc" | "desc" = params.get("dir") === "asc" || params.get("dir") === "desc" ? params.get("dir") as "asc" | "desc" : DEFAULT_DIR[sort];
  const q = params.get("q") ?? "";
  const urlView = params.get("view");
  const [view, setView] = useState<View>(() => urlView === "cards" || urlView === "summary" || urlView === "table" ? urlView : urlView === "type" ? "summary" : storedView());
  const [search, setSearch] = useState(q);
  const serverSort = SERVER_SORTS.includes(sort) ? sort : "strength";
  const query = new URLSearchParams(Object.entries({ days: days === "90" ? "" : days, region, rule, family, q, sort: serverSort === "strength" ? "" : serverSort })
    .filter(([, v]) => v)).toString();
  const [state, setState] = useState<{ query: string; data?: Data; error?: unknown } | null>(null);
  const [revision, setRevision] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [updated, setUpdated] = useState<number | null>(null);
  const freshness = useFreshness(() => { setRevision((n) => n + 1); setUpdated(Date.now()); });
  useEffect(() => {
    const request = new AbortController();
    api<Data>(`/api/buying-signals?${query}`, { signal: request.signal })
      .then((data) => { if (!request.signal.aborted) setState({ query, data }); })
      .catch((error) => { if (!request.signal.aborted) setState((s) => ({ query, data: s?.data, error })); });
    return () => request.abort();
  }, [query, revision]);
  useEffect(() => { setLimit(PAGE); }, [query]);
  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key); }
    setParams(next, { replace: true });
  };
  const chooseView = (v: View) => { setView(v); storeView(v); if (params.has("view")) update({ view: "" }); };
  const sortBy = (key: string) => {
    const nextDir = key === sort ? (dir === "asc" ? "desc" : "asc") : DEFAULT_DIR[key];
    update({ sort: key === "strength" ? "" : key, dir: nextDir === DEFAULT_DIR[key] ? "" : nextDir });
  };
  // Search as you type, a moment after the last key.
  const latest = useRef(q);
  latest.current = q;
  useEffect(() => {
    const value = search.trim();
    if (value === latest.current) return;
    const timer = window.setTimeout(() => update({ q: value }), 350);
    return () => window.clearTimeout(timer);
  }, [search]);     // eslint-disable-line react-hooks/exhaustive-deps

  const data = state?.data;
  const error = state?.query === query ? state.error : undefined;
  const loading = state?.query !== query;
  const filtered = Boolean(q || region || rule || family || days !== "90");
  const clear = () => { setSearch(""); update({ q: "", region: "", rule: "", family: "", days: "" }); };
  const windowLabel = tr(WINDOWS.find(([k]) => k === days)?.[1] ?? "Last 90 days").toLowerCase();
  const t = data?.totals;
  const kinds = useMemo(() => new Map((data?.kinds ?? []).map((k) => [k.rule_id, k])), [data]);
  const companies = useMemo(() => sorted(data?.companies ?? [], sort, dir, serverSort, kinds), [data, sort, dir, serverSort, kinds]);
  const shown = companies.slice(0, limit);
  const campaignRules = rule || (data?.kinds ?? []).filter((k) => !family || k.rule_id.startsWith(family)).map((k) => k.rule_id).join(",");
  const showFit = Boolean(data?.best_fit);

  return <div className="page bs-page">
    <header className="bs-head">
      <div className="bs-head-text">
        <h1>{tr("Buying signals")}</h1>
        <p className="bs-lede">{tr("Companies on your list where something happened recently that usually means they need engineering help. Start with the strongest and the newest.")}</p>
        <HowItWorks />
      </div>
      <Freshness state={freshness} updated={updated} />
    </header>

    {data?.pulse ? <SignalPulse pulse={data.pulse} family={family} onFamily={(f) => update({ family: f === family ? "" : f, rule: "" })} canPeople={canPeople} kinds={kinds} />
      : !data && !error ? <div className="bs-pulse is-empty" aria-hidden="true" /> : null}

    <section className="bs-tools" aria-label={tr("Search and filter")}>
      <form className="bs-bar" role="search" onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }}>
        <label className="bs-ctl bs-ctl-q"><span className="bs-vh">{tr("Search")}</span><IconSearch />
          <input type="search" maxLength={120} placeholder={tr("Search a company or what happened")} value={search} onChange={(e) => setSearch(e.target.value)} /></label>
        <label className="bs-ctl"><span className="bs-vh">{tr("Region")}</span><IconPlace />
          <select value={region} onChange={(e) => update({ region: e.target.value })}>
            <option value="">{tr("All regions")}</option>{REGIONS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select><Chevron /></label>
        <label className="bs-ctl"><span className="bs-vh">{tr("Time window")}</span><IconClock />
          <select value={days} onChange={(e) => update({ days: e.target.value === "90" ? "" : e.target.value })}>
            {WINDOWS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select><Chevron /></label>
        <label className="bs-ctl"><span className="bs-vh">{tr("Kind of signal")}</span><IconKind />
          <select value={rule || (family ? `fam:${family}` : "")} onChange={(e) => {
            const v = e.target.value;
            update(v.startsWith("fam:") ? { family: v.slice(4), rule: "" } : { rule: v, family: "" });
          }}>
            <option value="">{tr("All kinds of signal")}{data ? ` (${number(data.all.companies)})` : ""}</option>
            {FAMILIES.map((f) => {
              const list = (data?.kinds ?? []).filter((k) => k.rule_id.startsWith(f));
              if (!list.length && family !== f && !rule.startsWith(f)) return null;
              return <optgroup key={f} label={familyName(f)}>
                <option value={`fam:${f}`}>{tr("All: {}", familyName(f).toLowerCase())}</option>
                {list.map((k) => <option key={k.rule_id} value={k.rule_id}>{signalName(k.rule_id, k.name)} ({number(k.companies)})</option>)}
                {rule.startsWith(f) && !kinds.has(rule) && <option value={rule}>{signalName(rule)} (0)</option>}
              </optgroup>;
            })}
          </select><Chevron /></label>
        <label className="bs-ctl"><span className="bs-vh">{tr("Sort")}</span><IconSort />
          <select value={SORTS.some(([k]) => k === sort) ? sort : ""} onChange={(e) => update({ sort: e.target.value === "strength" ? "" : e.target.value, dir: "" })}>
            {!SORTS.some(([k]) => k === sort) && <option value="">{tr("Sorted by a column")}</option>}
            {SORTS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select><Chevron /></label>
      </form>
    </section>

    <div className="bs-resultbar">
      <p className="bs-count" role="status" aria-live="polite">
        {t ? <>
          <span><b>{counted(t.companies, ["company", "companies"], ["firma", "firmy", "firm"])}</b> {tr("with a signal in the {}", windowLabel)}</span>
          {t.fresh > 0 && <span className="bs-fresh"><i className="bs-new" aria-hidden="true" />{tr("{} with news in the last two weeks", number(t.fresh))}</span>}
        </> : <span>{tr("Loading buying signals")}</span>}
        {family && <button type="button" className="bs-chip" onClick={() => update({ family: "" })} aria-label={tr("Remove filter: {}", familyName(family))}>
          <Swatch family={family} />{familyName(family)}<span aria-hidden="true">×</span></button>}
        {filtered && Boolean(data) && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}
      </p>
      <div className="bs-seg" role="group" aria-label={tr("Show as")}>
        {([["table", "Table", IconTable], ["cards", "Cards", IconCards], ["summary", "Summary", IconList]] as const).map(([k, label, Icon]) =>
          <button key={k} type="button" aria-pressed={view === k} onClick={() => chooseView(k)}><Icon />{tr(label)}</button>)}
      </div>
    </div>

    {error ? <Err e={error} onRetry={() => setRevision((n) => n + 1)} /> : null}
    {!data && !error && <Skeleton rows={4} label={tr("Loading buying signals")} />}
    {data && data.companies.length === 0 && (data.all.companies === 0 && !q && !region
      ? <Empty title={tr("No strong signals in the {}", windowLabel)}>
          <p>{tr("Nothing at companies on your list points to a need right now. New signals appear here as sources are read and checked.")}</p>
          <p className="bs-empty-links">{days !== "365" && <button type="button" className="linkish" onClick={() => update({ days: "365" })}>{tr("Look at the last 12 months")}</button>}
            <Link to="/feed">{tr("See all findings")}</Link><Link to="/tenders">{tr("Open tenders")}</Link></p>
        </Empty>
      : <Empty title={tr("No company matches these filters")}>
          <p>{q ? tr("Nothing matches “{}”. Check the spelling, or search for a company name or a word from the news.", q) : tr("Try another region or kind of signal, or a longer time window.")}</p>
          <p className="bs-empty-links"><button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>
            {days !== "365" && <button type="button" className="linkish" onClick={() => update({ days: "365" })}>{tr("Look at the last 12 months")}</button>}</p>
        </Empty>)}

    {data && companies.length > 0 && <div className={`bs-results${loading ? " is-loading" : ""}`} aria-busy={loading}>
      {view === "table" && <SignalTable companies={shown} kinds={kinds} sort={sort} dir={dir} onSort={sortBy} showFit={showFit} canPeople={canPeople} canCampaign={canCampaign} focus={rule || family} />}
      {view === "cards" && <ul className="bs-cards" aria-label={tr("Companies with a buying signal")}>
        {shown.map((c) => <CompanyCard key={c.organization.id} c={c} kinds={kinds} canPeople={canPeople} canCampaign={canCampaign} />)}
      </ul>}
      {view === "summary" && <SummaryView companies={companies} kinds={kinds} family={family} />}
      {view !== "summary" && companies.length > limit && <div className="bs-more">
        <button type="button" onClick={() => setLimit((n) => n + PAGE)}>{tr("Show {} more", number(Math.min(PAGE, companies.length - limit)))}</button>
        <span className="small muted">{tr("{} of {}", number(limit), number(companies.length))}</span>
      </div>}
    </div>}

    {data && (t!.held_back > 0 || (canCampaign && data.companies.length > 0)) && <footer className="bs-end small">
      {canCampaign && data.companies.length > 0 && <p>{tr("Ready to reach out to these companies?")} <Link to={`/campaigns/new?rules=${campaignRules}`}>{tr("Turn these signals into a campaign")}</Link></p>}
      {t!.held_back > 0 && <p className="muted">{tr("{} with a signal here are held back because a reason to stop or wait was also found, such as a hiring freeze or an exclusive supplier.",
        counted(t!.held_back, ["company", "companies"], ["firma", "firmy", "firm"]))}</p>}
    </footer>}
  </div>;
}

/** Client-side order for the columns the server does not sort by, and the reverse direction for those it does. */
function sorted(list: Company[], sort: string, dir: "asc" | "desc", serverSort: string, kinds: Map<string, Kind>): Company[] {
  const out = [...list];
  const name = (c: Company) => c.organization.name.toLocaleLowerCase();
  const by: Record<string, (a: Company, b: Company) => number> = {
    company: (a, b) => name(a).localeCompare(name(b)),
    signal: (a, b) => signalName(a.signals[0].rule_id, kinds.get(a.signals[0].rule_id)?.name).localeCompare(signalName(b.signals[0].rule_id, kinds.get(b.signals[0].rule_id)?.name)) || name(a).localeCompare(name(b)),
    region: (a, b) => (REGION_SHORT[a.organization.region] ?? "~").localeCompare(REGION_SHORT[b.organization.region] ?? "~") || b.strength - a.strength || name(a).localeCompare(name(b)),
    email: (a, b) => a.organization.people_with_email - b.organization.people_with_email || a.strength - b.strength,
    fit: (a, b) => (a.organization.best_fit?.name ?? "~").localeCompare(b.organization.best_fit?.name ?? "~") || b.strength - a.strength,
  };
  if (by[sort]) {
    out.sort(by[sort]);
    return dir === "desc" ? out.reverse() : out;
  }
  // strength, newest and priority come in order from the server; a column click can turn them round
  return sort === serverSort && dir !== DEFAULT_DIR[sort] ? out.reverse() : out;
}

/* ------------------------------------------------------------------------------------------------ heading: rules and freshness */

function HowItWorks() {
  return <details className="bs-how">
    <summary>{tr("How companies get on this list")}</summary>
    <div className="bs-how-body">
      <p>{tr("A company is listed when a signal from the chosen time window points to a need: they asked for outside help or probably will, or the kind of signal usually starts a buying conversation, such as an open tender, a funded programme, a new technology leader, funding for a stated build or hiring beyond capacity. Every signal was checked against the original page.")}</p>
      <dl className="bs-legend">
        <div><dt><Bars n={3} /> {tr("Strong")}</dt><dd>{tr("They asked for outside help or probably will, or several signals at once.")}</dd></div>
        <div><dt><Bars n={2} /> {tr("Good")}</dt><dd>{tr("A clear sign of new engineering work.")}</dd></div>
        <div><dt><Bars n={1} /> {tr("Early")}</dt><dd>{tr("An early sign worth watching, such as funding without a named build.")}</dd></div>
      </dl>
      <p>{tr("Context-only news, such as a market launch, stays in")} <Link to="/feed">{tr("All findings")}</Link>. {tr("Companies with a reason to stop or wait, such as a hiring freeze, are held back.")}</p>
    </div>
  </details>;
}

/** When the sources were last checked, when the next automatic check is due, and a button to check now (with its limits explained). */
function Freshness({ state, updated }: { state: ReturnType<typeof useFreshness>; updated: number | null }) {
  const now = useNow();
  const { fresh: f, error } = state;
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const started = useRef(false);
  const noteId = useId();
  const running = Boolean(f?.running);
  useEffect(() => { if (!running && started.current) { started.current = false; setNote(null); } }, [running]);      // the "checking now" note goes when the check is done
  if (!f) return <aside className="bs-fresh-box" aria-label={tr("How fresh the signals are")}>
    <p className="bs-fresh-line">{error ? tr("Could not read when sources were last checked.") : tr("Reading when sources were last checked")}</p></aside>;

  const manual = f.manual?.running ? f.manual : null;
  const healthy = f.automatic && f.worker;
  const dot = !f.sources.watched ? "off" : f.running ? "run" : healthy ? "ok" : "warn";
  const press = async () => {
    setBusy(true); setNote(null); started.current = false;
    try {
      const r = await post<{ queued: number; skipped: { recent: number; queued: number; limit: number }; freshness: Fresh }>("/api/signals/refresh");
      state.set(r.freshness);
      state.restart();
      started.current = r.queued > 0 && r.freshness.running;
      setNote(r.queued ? tr("Checking {} sources now. The list updates by itself when they are done.", number(r.queued))
        : tr("Every source was checked in the last {} minutes or is already being checked. Nothing to add.", f.recent_minutes));
    } catch (e) {
      state.restart();
      setNote(e instanceof ApiError && (e.status === 409 || e.status === 429) ? null : tr("Could not start a check. Try again in a minute."));
    } finally { setBusy(false); }
  };

  let status: React.ReactNode;
  if (manual) status = <>{tr("Checking sources now")} · <b>{tr("{} of {} done", number(manual.done), number(manual.jobs))}</b></>;
  else if (f.running) status = <>{tr("Checking sources now")}</>;
  else if (f.last_checked_at) status = <>{tr("Last checked")} <b><time dateTime={f.last_checked_at} title={fmtDateTime(f.last_checked_at)}>{since(f.last_checked_at, now)}</time></b></>;
  else status = <>{f.sources.watched ? tr("Not checked yet") : tr("No sources switched on yet")}</>;
  const next = !f.running && f.next_check_at ? <> · {tr("next automatic check")} <b><time dateTime={f.next_check_at} title={fmtDateTime(f.next_check_at)}>{when(f.next_check_at, now)}</time></b></> : null;

  const reason = f.blocked === "no_sources" ? tr("New signals appear once an administrator switches sources on in Administration.")
    : f.blocked === "worker" ? tr("Checks are paused: the background worker is not running. Automatic checks start again by themselves when it is back.")
    : f.blocked === "cooldown" && f.cooldown_until ? tr("Checked by hand at {}. You can check again at {}: at most once every {} minutes, so the sources are not overloaded.",
      clock(f.manual?.at ?? f.cooldown_until), clock(f.cooldown_until), f.cooldown_minutes)
    : !f.automatic && f.sources.watched ? tr("Automatic checks are not running right now. You can still check by hand.")
    : null;
  const cadence = f.sources.every_hours ? (f.sources.every_hours[0] === f.sources.every_hours[1]
    ? tr("{} sources, each checked automatically every {} hours.", number(f.sources.watched), f.sources.every_hours[0])
    : tr("{} sources, each checked automatically every {} to {} hours.", number(f.sources.watched), f.sources.every_hours[0], f.sources.every_hours[1])) : null;
  const ai = f.sources.watched && f.ai_reading === "paused" ? tr("This month’s AI reading budget is used up: new articles are collected and read when it renews.") : null;

  return <aside className={`bs-fresh-box is-${dot}`} aria-label={tr("How fresh the signals are")}>
    <p className="bs-fresh-line" role="status" aria-live="polite"><i className="bs-dot" aria-hidden="true" /><span>{status}{next}</span></p>
    {manual && <span className="bs-progress" role="progressbar" aria-label={tr("Sources checked")} aria-valuemin={0} aria-valuemax={manual.jobs} aria-valuenow={manual.done}>
      <i style={{ inlineSize: `${manual.jobs ? Math.max(4, (manual.done / manual.jobs) * 100) : 0}%` }} /></span>}
    {cadence && <p className="bs-fresh-sub">{cadence}{updated && now - updated < 10 * 60_000 ? ` ${tr("List updated at {}.", clock(new Date(updated)))}` : ""}</p>}
    {f.blocked !== "permission" && f.blocked !== "no_sources" && <div className="bs-fresh-act">
      <button type="button" className="bs-refresh" onClick={press} disabled={busy || !f.can_refresh} aria-describedby={reason || note ? noteId : undefined}>
        <IconRefresh />{busy ? tr("Starting") : manual ? tr("Checking") : tr("Refresh now")}</button>
    </div>}
    {(reason || note || ai) && <p className="bs-fresh-note" id={noteId}>{note ?? reason}{ai && <span>{(note ?? reason) ? " " : ""}{ai}</span>}</p>}
  </aside>;
}

/* ------------------------------------------------------------------------------------------------ the pulse at the top */

/** The picture at the top: is anything new this week, which kinds of signal are busy, and the one signal to look at first. */
function SignalPulse({ pulse, family, onFamily, canPeople, kinds }: { pulse: Pulse; family: string; onFamily: (f: string) => void; canPeople: boolean; kinds: Map<string, Kind> }) {
  const max = Math.max(1, ...pulse.families.map((f) => f.d90));
  if (!pulse.companies) return <section className="bs-pulse is-quiet" aria-labelledby="bs-pulse-h">
    <h2 id="bs-pulse-h" className="bs-k">{tr("Signal pulse")}</h2>
    <p>{tr("No buying signal in the last {} days yet. Once sources are checked, this shows what is new each week, which kinds of signal are busy, and the strongest one to look at first.", pulse.days)}</p>
  </section>;
  return <section className="bs-pulse" aria-labelledby="bs-pulse-h">
    <h2 id="bs-pulse-h" className="bs-vh">{tr("Signal pulse")}</h2>
    <div className="bs-pulse-week">
      <p className="bs-k">{tr("New this week")}</p>
      <p className="bs-hero"><b>{number(pulse.week.news)}</b><span>{pulse.week.news === 1 ? tr("piece of news") : tr("pieces of news")}<br />
        {tr("at {}", counted(pulse.week.companies, ["company", "companies"], ["firmie", "firmach", "firmach"]))}</span></p>
      <WeekColumns weeks={pulse.weeks} />
    </div>

    <div className="bs-pulse-fam">
      <div className="bs-fam-head">
        <p className="bs-k">{tr("Companies by kind of signal")}</p>
        <p className="bs-fam-keys" aria-hidden="true"><span><i className="w7" />{tr("7 days")}</span><span><i className="w30" />{tr("30 days")}</span><span><i className="w90" />{tr("90 days")}</span></p>
      </div>
      <ul className="bs-fam-rows" aria-label={tr("Companies by kind of signal. Pick one to see only those companies.")}>
        {pulse.families.map((f) => {
          const on = family === f.family;
          const label = tr("{}: {} in the last 7 days, {} in 30 days, {} in 90 days", familyName(f.family), number(f.d7), number(f.d30), number(f.d90));
          return <li key={f.family}>
            <button type="button" className={`bs-famrow${f.d90 ? "" : " is-zero"}`} aria-pressed={on} disabled={!f.d90 && !on} onClick={() => onFamily(f.family)}
              aria-label={label} title={tr(FAMILY[f.family]?.meaning ?? "")}>
              <span className="bs-famrow-n"><Swatch family={f.family} />{familyName(f.family, true)}</span>
              <span className="bs-meter" aria-hidden="true">
                {f.d90 > 0 && <i className="w90" style={{ inlineSize: `${(f.d90 / max) * 100}%` }} />}
                {f.d30 > 0 && <i className="w30" style={{ inlineSize: `${(f.d30 / max) * 100}%` }} />}
                {f.d7 > 0 && <i className="w7" style={{ inlineSize: `${(f.d7 / max) * 100}%` }} />}
              </span>
              <span className="bs-famrow-v" aria-hidden="true"><b>{number(f.d7)}</b><span>{number(f.d30)}</span><span>{number(f.d90)}</span></span>
            </button>
          </li>;
        })}
      </ul>
    </div>

    <TopSignal top={pulse.top} canPeople={canPeople} meaning={pulse.top ? kinds.get(pulse.top.signal.rule_id)?.meaning : undefined} />
  </section>;
}

/** Pieces of news per week for the last 13 weeks; this week in navy, the rest lighter. Hover, focus and arrow keys read each week. */
function WeekColumns({ weeks }: { weeks: Pulse["weeks"] }) {
  const [hot, setHot] = useState<number | null>(null);
  const max = Math.max(1, ...weeks.map((w) => w.news));
  const W = 260, H = 84, slot = W / weeks.length, bar = Math.min(12, slot - 4);
  const peak = weeks.reduce((best, w, i) => (w.news > weeks[best].news ? i : best), 0);
  const label = (w: Pulse["weeks"][number], i: number) => tr("{}: {} at {}", i === weeks.length - 1 ? tr("This week") : tr("Week of {}", fmtDate(w.start)),
    w.news === 1 ? tr("1 piece of news") : tr("{} pieces of news", number(w.news)), counted(w.companies, ["company", "companies"], ["firmie", "firmach", "firmach"]));
  const total = weeks.reduce((n, w) => n + w.news, 0);
  return <div className="bs-weeks">
    <svg viewBox={`0 0 ${W} ${H + 14}`} role="img" tabIndex={0}
      aria-label={tr("News per week for the last 13 weeks: {} in all, {} this week. Use the arrow keys to read each week.", number(total), number(weeks[weeks.length - 1]?.news ?? 0))}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); setHot((h) => Math.max(0, Math.min(weeks.length - 1, (h ?? weeks.length) + (e.key === "ArrowRight" ? 1 : -1)))); }
        if (e.key === "Escape") setHot(null);
      }} onBlur={() => setHot(null)} onPointerLeave={() => setHot(null)}>
      <line x1="0" x2={W} y1={H + 0.5} y2={H + 0.5} className="bs-axis" />
      {weeks.map((w, i) => {
        const h = w.news ? Math.max(3, (w.news / max) * (H - 14)) : 0;
        const x = i * slot + (slot - bar) / 2;
        return <g key={i} className={`bs-wk${i === weeks.length - 1 ? " is-now" : ""}${hot === i ? " is-hot" : ""}`} onPointerEnter={() => setHot(i)}>
          <rect className="bs-wk-hit" x={i * slot} y="0" width={slot} height={H + 14} />
          {h > 0 && <rect className="bs-wk-bar" x={x} y={H - h} width={bar} height={h} />}
          {(i === peak && w.news > 0 && i !== weeks.length - 1) || (i === weeks.length - 1 && w.news > 0)
            ? <text x={x + bar / 2} y={H - h - 4} textAnchor="middle" className="bs-wk-v">{number(w.news)}</text> : null}
        </g>;
      })}
    </svg>
    <p className="bs-weeks-axis" aria-hidden="true"><span>{tr("13 weeks ago")}</span><span>{tr("This week")}</span></p>
    <p className="bs-weeks-tip" aria-live="polite">{hot != null ? label(weeks[hot], hot) : ""}</p>
    <table className="bs-vh"><caption>{tr("News per week")}</caption><thead><tr><th scope="col">{tr("Week")}</th><th scope="col">{tr("News")}</th><th scope="col">{tr("Companies")}</th></tr></thead>
      <tbody>{weeks.map((w, i) => <tr key={i}><td>{i === weeks.length - 1 ? tr("This week") : tr("Week of {}", fmtDate(w.start))}</td><td>{w.news}</td><td>{w.companies}</td></tr>)}</tbody></table>
  </div>;
}

function TopSignal({ top, canPeople, meaning }: { top: Top | null; canPeople: boolean; meaning?: string }) {
  if (!top) return <div className="bs-pulse-top is-none">
    <p className="bs-k">{tr("Strongest new signal")}</p>
    <p className="bs-top-none">{tr("Nothing new in the last 30 days. New signals appear here as sources are checked.")}</p>
  </div>;
  const s = top.signal;
  const level = tr(LEVEL[top.company_strength] ?? "Good");
  return <div className="bs-pulse-top">
    <p className="bs-k">{top.window === 7 ? tr("Strongest new signal this week") : tr("Quiet week. Strongest in the last 30 days")}</p>
    <p className="bs-top-co"><Link to={`/accounts/${top.organization.id}?event=${s.event_id}`}>{top.organization.name}</Link>
      {top.organization.country && <span>{countryName(top.organization.country)}</span>}</p>
    <p className="bs-top-k"><Swatch ruleId={s.rule_id} /><b>{signalName(s.rule_id)}</b><span className="bs-top-str"><Bars n={top.company_strength} />{level}</span></p>
    <p className="bs-top-t">{said(s, top.organization.name)}</p>
    {meaning && <p className="bs-top-why">{tr(meaning)}</p>}
    <p className="bs-top-f"><time dateTime={s.happened ?? undefined} title={s.happened ? fmtDate(s.happened) : undefined}>{ago(s.happened)}</time>
      {s.tender?.deadline && <span>{tr("Deadline {}", fmtDate(s.tender.deadline))}</span>}
      <Link to={`/accounts/${top.organization.id}?event=${s.event_id}`}>{tr("Open company")} →</Link>
      {canPeople && <Link to={`/people?q=${encodeURIComponent(top.organization.name)}`}>{tr("People")}</Link>}</p>
  </div>;
}

/* ------------------------------------------------------------------------------------------------ the table */

function SortHead({ k, label, sort, dir, onSort, className }: { k: string; label: string; sort: string; dir: "asc" | "desc"; onSort: (k: string) => void; className?: string }) {
  const on = sort === k;
  return <th scope="col" className={className} aria-sort={on ? (dir === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className={`bs-th${on ? " on" : ""}`} onClick={() => onSort(k)}>{tr(label)}
      <svg className={`bs-sorti${on ? ` ${dir}` : ""}`} viewBox="0 0 10 12" aria-hidden="true"><path d="M5 1L8.5 5h-7zM5 11L1.5 7h7z" /></svg></button>
  </th>;
}

function SignalTable({ companies, kinds, sort, dir, onSort, showFit, canPeople, canCampaign, focus }: {
  companies: Company[]; kinds: Map<string, Kind>; sort: string; dir: "asc" | "desc"; onSort: (k: string) => void; showFit: boolean; canPeople: boolean; canCampaign: boolean; focus: string;
}) {
  const ref = useRef<HTMLTableElement>(null);
  const move = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(e.key) || !ref.current) return;
    const all = Array.from(ref.current.querySelectorAll<HTMLButtonElement>(".bs-toggle"));
    const i = all.indexOf(e.currentTarget);
    const next = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : i + (e.key === "ArrowDown" ? 1 : -1);
    if (all[next]) { e.preventDefault(); all[next].focus(); }
  };
  const cols = showFit ? 8 : 7;
  return <table ref={ref} className={`bs-table${showFit ? " has-fit" : ""}`}>
    <caption className="bs-vh">{tr("Companies with a buying signal. Open a row for the evidence, the people and what to do next.")}</caption>
    <thead><tr>
      <SortHead k="company" label="Company" sort={sort} dir={dir} onSort={onSort} className="c-co" />
      <SortHead k="signal" label="Signal and what happened" sort={sort} dir={dir} onSort={onSort} className="c-sig" />
      <SortHead k="strength" label="Strength" sort={sort} dir={dir} onSort={onSort} className="c-str" />
      <SortHead k="newest" label="When" sort={sort} dir={dir} onSort={onSort} className="c-when" />
      <SortHead k="region" label="Region" sort={sort} dir={dir} onSort={onSort} className="c-reg" />
      <SortHead k="email" label="Email" sort={sort} dir={dir} onSort={onSort} className="c-mail" />
      {showFit && <SortHead k="fit" label="Our offer" sort={sort} dir={dir} onSort={onSort} className="c-fit" />}
      <th scope="col" className="c-x"><span className="bs-vh">{tr("Details")}</span></th>
    </tr></thead>
    {companies.map((c) => <TableRow key={c.organization.id} c={c} kinds={kinds} cols={cols} showFit={showFit} canPeople={canPeople} canCampaign={canCampaign} focus={focus} onKey={move} />)}
  </table>;
}

function Reach({ org, canPeople }: { org: Org; canPeople: boolean }) {
  const text = org.people_with_email ? tr("Email ✓ {}", number(org.people_with_email)) : org.people ? tr("No email") : tr("No contacts");
  const title = org.people ? tr("{}, {} with email", counted(org.people, ["person", "people"], ["osoba", "osoby", "osób"]), number(org.people_with_email)) : tr("No contacts yet");
  return canPeople && org.people > 0
    ? <Link className={`bs-mail${org.people_with_email ? " on" : ""}`} to={`/people?q=${encodeURIComponent(org.name)}`} title={title} aria-label={title}>{text}</Link>
    : <span className={`bs-mail${org.people_with_email ? " on" : ""}`} title={title}>{text}</span>;
}

function TableRow({ c, kinds, cols, showFit, canPeople, canCampaign, focus, onKey }: {
  c: Company; kinds: Map<string, Kind>; cols: number; showFit: boolean; canPeople: boolean; canCampaign: boolean; focus: string; onKey: (e: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const org = c.organization;
  const top = c.signals[0];
  const fresh = c.age_days != null && c.age_days <= FRESH_DAYS;
  const level = tr(LEVEL[c.strength] ?? "Good");
  const reason = REASON[c.strength_reason] ? tr(REASON[c.strength_reason]) : "";
  const toggle = (e: React.MouseEvent) => { if (!(e.target as HTMLElement).closest("a,button")) setOpen((o) => !o); };
  return <tbody className={`bs-tb${open ? " is-open" : ""} is-s${c.strength}`}>
    <tr className="bs-tr" onClick={toggle}>
      <td className="c-co" data-label={tr("Company")}>
        <Link className="bs-co-name" to={`/accounts/${org.id}?event=${top.event_id}`}>{org.name}</Link>
        <span className="bs-co-sub">{countryName(org.country) ?? tr("Country unknown")}
          {org.list_priority && /^P[123]$/.test(org.list_priority) && <span className={`acc-p acc-${org.list_priority.toLowerCase()}`} title={tr("Lead list priority {}", org.list_priority)}>{org.list_priority}</span>}</span>
      </td>
      <td className="c-sig">
        <span className="bs-sig-type"><Swatch ruleId={top.rule_id} /><b>{signalName(top.rule_id, kinds.get(top.rule_id)?.name)}</b>
          {c.signals.length > 1 && <span className="bs-plus" title={c.signals.slice(1).map((s) => signalName(s.rule_id, kinds.get(s.rule_id)?.name)).join(", ")}>{tr("+{} more", c.signals.length - 1)}</span>}</span>
        <span className="bs-sig-line" title={said(top, org.name)}>{said(top, org.name)}</span>
      </td>
      <td className="c-str" data-label={tr("Strength")}><span className="bs-str" title={reason || undefined}><Bars n={c.strength} /><b>{level}</b><span className="bs-vh">{reason ? `. ${reason}` : ""}</span></span></td>
      <td className="c-when" data-label={tr("When")}><time className={fresh ? "is-fresh" : ""} dateTime={c.latest ?? undefined} title={c.latest ? fmtDate(c.latest) : undefined}>
        {fresh && <i className="bs-new" aria-hidden="true" />}{ago(c.latest) || tr("Date unknown")}</time></td>
      <td className="c-reg" data-label={tr("Region")} title={REGION_SHORT[org.region] ? tr(REGION_SHORT[org.region]) : undefined}>{REGION_SHORT[org.region] ? tr(REGION_SHORT[org.region]) : "—"}</td>
      <td className="c-mail" data-label={tr("Email")}><Reach org={org} canPeople={canPeople} /></td>
      {showFit && <td className="c-fit" data-label={tr("Best fit")}>{org.best_fit
        ? <span className={`bs-fit is-${org.best_fit.level ?? "possible"}`} title={org.best_fit.reason ?? undefined}>{tr(org.best_fit.name)}</span> : <span className="muted">—</span>}</td>}
      <td className="c-x"><button type="button" className="bs-toggle" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(!open)} onKeyDown={onKey}
        aria-label={open ? tr("Hide details for {}", org.name) : tr("Show details for {}", org.name)}><span className="bs-toggle-t">{open ? tr("Less") : tr("Details")}</span><Chevron className="bs-chev-t" /></button></td>
    </tr>
    <tr className="bs-detail" id={panel} hidden={!open}>
      <td colSpan={cols}>{open && <CompanyDetail c={c} kinds={kinds} canPeople={canPeople} canCampaign={canCampaign} focus={focus} />}</td>
    </tr>
  </tbody>;
}

/** Everything about one company's signals: what happened, why it matters, the evidence, who to contact and what to do next. */
function CompanyDetail({ c, kinds, canPeople, canCampaign, focus }: { c: Company; kinds: Map<string, Kind>; canPeople: boolean; canCampaign: boolean; focus: string }) {
  const org = c.organization;
  const top = c.signals[0];
  const kind = kinds.get(top.rule_id);
  const reason = REASON[c.strength_reason] ? tr(REASON[c.strength_reason]) : "";
  const rules = focus && /^[A-Z]{3}-\d{2}$/.test(focus) ? focus : top.rule_id;
  return <div className="bs-dt">
    <ol className="bs-dt-sigs">{c.signals.map((s) => <SignalDetail key={s.id} s={s} org={org} kind={kinds.get(s.rule_id)} />)}</ol>
    <aside className="bs-dt-side">
      <dl>
        <div><dt>{tr("Strength")}</dt><dd><Bars n={c.strength} /> {tr(LEVEL[c.strength] ?? "Good")}{reason && <span className="muted"> · {reason}</span>}</dd></div>
        {org.best_fit && <div><dt>{tr("Best fit")}</dt><dd><b>{tr(org.best_fit.name)}</b>{org.best_fit.reason && <span className="bs-dt-why">{org.best_fit.reason}</span>}</dd></div>}
        <div><dt>{tr("People")}</dt><dd>{org.people ? tr("{}, {} with email", counted(org.people, ["person", "people"], ["osoba", "osoby", "osób"]), number(org.people_with_email)) : tr("No contacts yet")}
          {canPeople && org.people > 0 && <> · <Link to={`/people?q=${encodeURIComponent(org.name)}`}>{tr("See the people")}</Link></>}</dd></div>
        {kind?.who_to_contact && <div><dt>{tr("Who to contact")}</dt><dd>{tr(kind.who_to_contact)}</dd></div>}
        {(org.list_priority || org.list_score != null) && <div><dt>{tr("Lead list")}</dt><dd><ListRank priority={org.list_priority} score={org.list_score} /></dd></div>}
        {org.segment && <div><dt>{tr("Sector")}</dt><dd>{org.segment}</dd></div>}
      </dl>
      <p className="bs-dt-act">
        <Link className="btn" to={`/accounts/${org.id}?event=${top.event_id}`}>{tr("Open company")}</Link>
        {canCampaign && org.people_with_email > 0 && <Link className="bs-dt-link" to={`/campaigns/new?rules=${rules}`}>{tr("Start a campaign with this signal")} →</Link>}
      </p>
    </aside>
  </div>;
}

/** One signal at a company: what kind, when, what happened, why it matters, and the evidence with its source. */
function SignalDetail({ s, org, kind }: { s: Signal; org: Org; kind?: Kind }) {
  const language = getLanguage();
  const href = safeHref(s.tender?.url ?? s.evidence.url);
  const published = s.evidence.published_at ?? s.happened;
  const fresh = s.age_days != null && s.age_days <= FRESH_DAYS;
  const why = s.fit?.reason && s.fit.level !== "none" ? s.fit.reason : kind?.meaning ? tr(kind.meaning) : null;
  return <li className="bs-sd">
    <p className="bs-sd-k"><Swatch ruleId={s.rule_id} /><b>{signalName(s.rule_id, kind?.name)}</b>
      <time className={fresh ? "is-fresh" : ""} dateTime={s.happened ?? undefined}>{fresh && <i className="bs-new" aria-hidden="true" />}{ago(s.happened) || tr("Date unknown")}{s.happened && <span className="bs-sd-date">· {fmtDate(s.happened)}</span>}</time>
      {s.more > 0 && <span className="bs-sd-more">{tr("and {} earlier", number(s.more))}</span>}</p>
    <p className="bs-sd-t">{said(s, org.name)}</p>
    {s.tender?.deadline && <p className="bs-sd-dl">{tr("Tender deadline")} <b>{fmtDateTime(s.tender.deadline)}</b></p>}
    {why && <p className="bs-sd-why"><b>{tr("Why it matters")}</b> {why}</p>}
    {s.tender?.scope && <p className="bs-sd-scope">{s.tender.scope}</p>}
    <div className="bs-evi">
      {s.evidence.quote ? <blockquote>“{s.evidence.quote}”
        {language === "en" && s.evidence.translation && <span>{tr("English translation")}: “{s.evidence.translation}”</span>}</blockquote>
        : <p className="muted">{tr("A structured record, with no quote to show.")}</p>}
      <p className="bs-evi-src">
        {href && <a href={href} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "evidence" })}>{tr("Open the source")} ↗</a>}
        <span className="bs-evi-meta">{s.evidence.source && <span>{s.evidence.source}</span>}{published && <span>{tr("published {}", fmtDate(published))}</span>}</span>
      </p>
    </div>
  </li>;
}

/* ------------------------------------------------------------------------------------------------ cards and summary */

function CompanyCard({ c, kinds, canPeople, canCampaign }: { c: Company; kinds: Map<string, Kind>; canPeople: boolean; canCampaign: boolean }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const org = c.organization;
  const top = c.signals[0];
  const kind = kinds.get(top.rule_id);
  const fresh = c.age_days != null && c.age_days <= FRESH_DAYS;
  const level = tr(LEVEL[c.strength] ?? "Good");
  const why = top.fit?.reason && top.fit.level !== "none" ? top.fit.reason : kind?.meaning ? tr(kind.meaning) : null;
  const facts = [countryName(org.country), REGION_SHORT[org.region] && org.region !== "poland" && org.region !== "uk" ? tr(REGION_SHORT[org.region]) : null, org.segment].filter(Boolean);
  return <li className={`bs-card is-s${c.strength}`}>
    <header className="bs-card-h">
      <CompanyAvatar name={org.name} />
      <div className="bs-card-co">
        <Link className="bs-co-name" to={`/accounts/${org.id}?event=${top.event_id}`}>{org.name}</Link>
        <span className="bs-co-sub">{facts.join(" · ")}
          {org.list_priority && /^P[123]$/.test(org.list_priority) && <span className={`acc-p acc-${org.list_priority.toLowerCase()}`} title={tr("Lead list priority {}", org.list_priority)}>{org.list_priority}</span>}</span>
      </div>
      <span className="bs-str bs-card-str" title={REASON[c.strength_reason] ? tr(REASON[c.strength_reason]) : undefined}><Bars n={c.strength} /><b>{level}</b></span>
    </header>
    <div className="bs-card-b">
      <p className="bs-card-k"><Swatch ruleId={top.rule_id} /><b>{signalName(top.rule_id, kind?.name)}</b>
        <time className={fresh ? "is-fresh" : ""} dateTime={top.happened ?? undefined} title={top.happened ? fmtDate(top.happened) : undefined}>{fresh && <i className="bs-new" aria-hidden="true" />}{ago(top.happened) || tr("Date unknown")}</time></p>
      <p className="bs-card-t">{said(top, org.name)}</p>
      {top.tender?.deadline && <p className="bs-sd-dl">{tr("Tender deadline")} <b>{fmtDateTime(top.tender.deadline)}</b></p>}
      {why && <p className="bs-card-why">{why}</p>}
      {c.signals.length > 1 && <p className="bs-card-also">{tr("Also")}: {c.signals.slice(1).map((s) => signalName(s.rule_id, kinds.get(s.rule_id)?.name)).join(", ")}</p>}
    </div>
    <dl className="bs-card-facts">
      <div><dt className="bs-vh">{tr("Email")}</dt><dd><Reach org={org} canPeople={canPeople} /></dd></div>
      {org.best_fit && <div><dt>{tr("Best fit")}</dt><dd title={org.best_fit.reason ?? undefined}>{tr(org.best_fit.name)}</dd></div>}
    </dl>
    <footer className="bs-card-f">
      <Link to={`/accounts/${org.id}?event=${top.event_id}`}>{tr("Open company")} →</Link>
      {canCampaign && org.people_with_email > 0 && <Link to={`/campaigns/new?rules=${top.rule_id}`}>{tr("Campaign")}</Link>}
      <button type="button" className="bs-card-ev" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(!open)}>{tr("Evidence")}<Chevron className="bs-chev-t" /></button>
    </footer>
    <div className="bs-card-x" id={panel} hidden={!open}>{open && <ol className="bs-dt-sigs">{c.signals.map((s) => <SignalDetail key={s.id} s={s} org={org} kind={kinds.get(s.rule_id)} />)}</ol>}</div>
  </li>;
}

/** One line per company, grouped by kind of signal: the quickest read of the whole list. */
function SummaryView({ companies, kinds, family }: { companies: Company[]; kinds: Map<string, Kind>; family: string }) {
  const [more, setMore] = useState<Record<string, boolean>>({});
  const groups = FAMILIES.filter((f) => !family || f === family).map((f) => ({
    f, list: companies.filter((c) => c.signals.some((s) => familyOf(s.rule_id) === f)),
  })).filter((g) => g.list.length);
  return <div className="bs-sum">{groups.map(({ f, list }) => {
    const shown = more[f] ? list : list.slice(0, 8);
    return <section key={f} className="bs-sum-g" aria-labelledby={`bs-sum-${f}`}>
      <header className="bs-sum-h">
        <h2 id={`bs-sum-${f}`}><Swatch family={f} />{familyName(f)}</h2>
        <span className="bs-sum-n">{counted(list.length, ["company", "companies"], ["firma", "firmy", "firm"])}</span>
        <p>{tr(FAMILY[f].meaning)}</p>
      </header>
      <ul className="bs-sum-l">{shown.map((c) => {
        const s = c.signals.find((x) => familyOf(x.rule_id) === f)!;
        const fresh = s.age_days != null && s.age_days <= FRESH_DAYS;
        return <li key={c.organization.id}>
          <Link className="bs-sum-co" to={`/accounts/${c.organization.id}?event=${s.event_id}`}>{c.organization.name}</Link>
          <span className="bs-sum-t" title={said(s, c.organization.name)}><b>{signalName(s.rule_id, kinds.get(s.rule_id)?.name)}</b> · {said(s, c.organization.name)}</span>
          <time className={fresh ? "is-fresh" : ""} dateTime={s.happened ?? undefined}>{fresh && <i className="bs-new" aria-hidden="true" />}{ago(s.happened)}</time>
          <span className="bs-sum-s" role="img" aria-label={tr("Signal strength: {}", tr(LEVEL[c.strength] ?? "Good"))}><Bars n={c.strength} /></span>
        </li>;
      })}</ul>
      {list.length > 8 && <button type="button" className="linkish bs-sum-more" aria-expanded={Boolean(more[f])} onClick={() => setMore((m) => ({ ...m, [f]: !m[f] }))}>
        {more[f] ? tr("Show fewer") : tr("Show all {}", number(list.length))}</button>}
    </section>;
  })}</div>;
}
