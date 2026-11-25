import { useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, trackSourceOpened } from "../api";
import { counted, getLanguage, number, tr } from "../locale";
import { Empty, Err, Skeleton, safeHref } from "../components/ui";
import { CompanyAvatar, ListRank, ago } from "../components/Findings";
import { signalName } from "../components/Signals";
import { REGIONS, countryName } from "./targetMarket";
import "../styles/accounts.css";
import "../styles/signals.css";

/** Nav icon: a pulse line. */
export const IconSignals = (p: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>
    <path d="M2 12h4l3-7 5 14 3-7h5" />
  </svg>
);
const Ico = ({ d }: { d: React.ReactNode }) => (
  <svg className="bs-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" aria-hidden="true">{d}</svg>
);
const IconSearch = () => <Ico d={<><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5l5 5" /></>} />;
const IconPlace = () => <Ico d={<><path d="M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0113 0c0 4.9-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>} />;
const IconClock = () => <Ico d={<><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>} />;
const IconSort = () => <Ico d={<path d="M7 4v16M3.5 16.5L7 20l3.5-3.5M17 20V4M13.5 7.5L17 4l3.5 3.5" />} />;
const Chevron = () => <svg className="bs-chev" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M2.5 4.5L6 8l3.5-3.5" /></svg>;

type Org = { id: string; name: string; country: string | null; region: string; segment: string | null; domain: string | null;
  list_priority: string | null; list_score: number | null; people: number; people_with_email: number };
type Signal = {
  id: string; rule_id: string; strength: number; demand_tier: string; intent: string; event_id: string; event_type: string;
  summary: string; summary_en?: string | null; summary_pl?: string | null; happened: string | null; age_days: number | null; more: number;
  fit?: { level?: string; service?: string; reason?: string } | null;
  tender: { deadline: string | null; url: string | null; title: string | null; scope?: string | null } | null;
  evidence: { quote: string | null; translation: string | null; url: string | null; source: string | null; published_at: string | null };
};
type Company = { organization: Org; signals: Signal[]; strength: number; strength_reason: string; kinds: number; latest: string | null; age_days: number | null };
type Kind = { rule_id: string; name: string; group: string | null; strength: number; meaning: string; who_to_contact: string | null; companies: number; fresh: number; latest: string | null };
type Data = { days: number; sort: string; rule: string; companies: Company[]; kinds: Kind[]; all: { companies: number; signals: number }; fresh_days: number;
  totals: { companies: number; signals: number; reachable: number; fresh: number; strong: number; held_back: number } };

const WINDOWS: [string, string][] = [["30", "Last 30 days"], ["90", "Last 90 days"], ["180", "Last 6 months"], ["365", "Last 12 months"]];
const SORTS: [string, string][] = [["strength", "Strongest first"], ["newest", "Newest first"], ["priority", "Lead list priority"]];
const LEVEL: Record<number, string> = { 3: "Strong", 2: "Good", 1: "Early" };
/** Why a company is rated strong, shown under the bars. A plain "Good" or "Early" needs no extra words. */
const REASON: Record<string, string> = {
  outside_demand: "They asked for outside help",
  need_likely: "Outside help is likely",
  several: "Several signals at once",
  strong_kind: "One of the strongest signals",
};
const PAGE = 40;
const FRESH_DAYS = 14;

/** Signal strength as three rising bars, the way a phone shows reception. */
function Bars({ n }: { n: number }) {
  return <span className="bs-bars" aria-hidden="true">{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;
}

/** The summary often starts with the company's name, which the row already shows above it. */
function plain(text: string, name: string) {
  const t = text.trim();
  if (t.startsWith(name + " ")) {
    const rest = t.slice(name.length + 1);
    if (/^[a-ząćęłńóśźż]/.test(rest)) return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  return t;
}

export default function BuyingSignals({ canCampaign = false, canPeople = false }: { canCampaign?: boolean; canPeople?: boolean }) {
  const [params, setParams] = useSearchParams();
  const days = WINDOWS.some(([k]) => k === params.get("days")) ? params.get("days")! : "90";
  const region = REGIONS.some(([k]) => k === params.get("region")) ? params.get("region")! : "";
  const rule = /^[A-Z]{3}-\d{2}$/.test(params.get("rule") ?? "") ? params.get("rule")! : "";
  const sort = SORTS.some(([k]) => k === params.get("sort")) ? params.get("sort")! : "strength";
  const view = params.get("view") === "type" ? "type" : "company";
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  const query = new URLSearchParams(Object.entries({ days: days === "90" ? "" : days, region, rule, q, sort: sort === "strength" ? "" : sort }).filter(([, v]) => v)).toString();
  const [state, setState] = useState<{ query: string; data?: Data; error?: unknown } | null>(null);
  const [revision, setRevision] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => {
    const request = new AbortController();
    api<Data>(`/api/buying-signals?${query}`, { signal: request.signal })
      .then((data) => { if (!request.signal.aborted) setState({ query, data }); })
      .catch((error) => { if (!request.signal.aborted) setState((s) => ({ query, data: s?.data, error })); });
    setLimit(PAGE);
    return () => request.abort();
  }, [query, revision]);
  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key); }
    setParams(next, { replace: true });
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
  const filtered = Boolean(q || region || rule || days !== "90");
  const clear = () => { setSearch(""); update({ q: "", region: "", rule: "", days: "" }); };
  const windowLabel = tr(WINDOWS.find(([k]) => k === days)?.[1] ?? "Last 90 days").toLowerCase();
  const t = data?.totals;
  const kinds = new Map((data?.kinds ?? []).map((k) => [k.rule_id, k]));
  const ranked = Boolean(data?.companies.some((c) => c.organization.list_priority || c.organization.list_score != null));
  const shown = data?.companies.slice(0, limit) ?? [];
  const typeSections = (data?.kinds ?? []).filter((k) => !rule || k.rule_id === rule);
  const campaignRules = rule || (data?.kinds ?? []).map((k) => k.rule_id).join(",");

  return <div className="page bs-page">
    <header className="bs-head">
      <h1>{tr("Buying signals")}</h1>
      <p className="bs-lede">{tr("Companies on your list where something happened recently that usually means they need engineering help.")}</p>
      <details className="bs-how">
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
      </details>
    </header>

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
      </form>
      <div className="bs-kinds" role="group" aria-label={tr("Kind of signal")}>
        <button type="button" className="bs-kind" aria-pressed={!rule} onClick={() => update({ rule: "" })}>
          {tr("All signals")}{data && <b>{number(data.all.companies)}</b>}</button>
        {(data?.kinds ?? []).map((k) => <button key={k.rule_id} type="button" className="bs-kind" aria-pressed={rule === k.rule_id} title={tr(k.meaning)}
          onClick={() => update({ rule: rule === k.rule_id ? "" : k.rule_id })}>
          {signalName(k.rule_id, k.name)}<b>{number(k.companies)}</b></button>)}
        {rule && !kinds.has(rule) && <button type="button" className="bs-kind" aria-pressed onClick={() => update({ rule: "" })}>{signalName(rule)}<b>0</b></button>}
      </div>
    </section>

    <div className="bs-resultbar">
      <p className="bs-count" role="status" aria-live="polite">
        {t ? <>
          <span><b>{counted(t.companies, ["company", "companies"], ["firma", "firmy", "firm"])}</b> {tr("with a signal in the {}", windowLabel)}</span>
          {t.fresh > 0 && <span className="bs-fresh"><i className="bs-new" aria-hidden="true" />{tr("{} with news in the last two weeks", number(t.fresh))}</span>}
        </> : <span>{tr("Loading buying signals")}</span>}
        {filtered && Boolean(data?.companies.length) && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}
      </p>
      <div className="bs-view">
        <label className="bs-ctl bs-ctl-sm"><span className="bs-vh">{tr("Sort")}</span><IconSort />
          <select value={sort} onChange={(e) => update({ sort: e.target.value === "strength" ? "" : e.target.value })}>
            {SORTS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select><Chevron /></label>
        <div className="bs-seg" role="group" aria-label={tr("Show")}>
          <button type="button" aria-pressed={view === "company"} onClick={() => update({ view: "" })}>{tr("Companies")}</button>
          <button type="button" aria-pressed={view === "type"} onClick={() => update({ view: "type" })}>{tr("Signal types")}</button>
        </div>
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

    {data && data.companies.length > 0 && <div className={`bs-results${loading ? " is-loading" : ""}${ranked ? "" : " no-rank"}`} aria-busy={loading}>
      {view === "company" ? <>
        <div className="bs-colhead" aria-hidden="true">
          <span className="bs-h-co">{tr("Company")}</span><span className="bs-h-sig">{tr("What happened")}</span>
          <span className="bs-h-str">{tr("Strength")}</span>{ranked && <span className="bs-h-rank">{tr("Lead list")}</span>}
        </div>
        <ol className="bs-rows" aria-label={tr("Companies with a buying signal")}>
          {shown.map((c) => <CompanyRow key={c.organization.id} c={c} kinds={kinds} ranked={ranked} canPeople={canPeople} />)}
        </ol>
        {data.companies.length > limit && <div className="bs-more">
          <button type="button" onClick={() => setLimit((n) => n + PAGE)}>{tr("Show {} more", number(Math.min(PAGE, data.companies.length - limit)))}</button>
          <span className="small muted">{tr("{} of {}", number(limit), number(data.companies.length))}</span>
        </div>}
      </> : typeSections.map((k) => {
        const list = data.companies.filter((c) => c.signals.some((s) => s.rule_id === k.rule_id));
        return <section key={k.rule_id} className="bs-type" aria-labelledby={`bs-type-${k.rule_id}`}>
          <header className="bs-type-head">
            <div className="bs-type-title">
              <h2 id={`bs-type-${k.rule_id}`}>{signalName(k.rule_id, k.name)}</h2>
              <span className="bs-type-n">{counted(list.length, ["company", "companies"], ["firma", "firmy", "firm"])}</span>
            </div>
            <p className="bs-type-m">{tr(k.meaning)}</p>
            {k.who_to_contact && <p className="bs-type-who">{tr("Who to contact: {}", tr(k.who_to_contact))}</p>}
          </header>
          <ol className="bs-rows">
            {list.map((c) => <CompanyRow key={c.organization.id} c={c} kinds={kinds} ranked={ranked} canPeople={canPeople} only={k.rule_id} />)}
          </ol>
        </section>;
      })}
    </div>}

    {data && (t!.held_back > 0 || (canCampaign && data.companies.length > 0)) && <footer className="bs-end small">
      {t!.held_back > 0 && <p className="muted">{tr("{} with a signal here are held back because a reason to stop or wait was also found, such as a hiring freeze or an exclusive supplier.",
        counted(t!.held_back, ["company", "companies"], ["firma", "firmy", "firm"]))}</p>}
      {canCampaign && data.companies.length > 0 && <p>{tr("Ready to reach out to these companies?")} <Link to={`/campaigns/new?rules=${campaignRules}`}>{tr("Turn these signals into a campaign")}</Link></p>}
    </footer>}
  </div>;
}

function CompanyRow({ c, kinds, ranked, canPeople, only }: { c: Company; kinds: Map<string, Kind>; ranked: boolean; canPeople: boolean; only?: string }) {
  const org = c.organization;
  const signals = only ? c.signals.filter((s) => s.rule_id === only) : c.signals;
  const top = signals[0];
  const why = top.fit?.reason && top.fit.level !== "none" ? top.fit.reason : kinds.get(top.rule_id)?.meaning ? tr(kinds.get(top.rule_id)!.meaning) : null;
  const facts = [countryName(org.country), org.segment].filter(Boolean);
  const people = org.people ? tr("{}, {} with email", counted(org.people, ["person", "people"], ["osoba", "osoby", "osób"]), number(org.people_with_email)) : tr("No contacts yet");
  const level = tr(LEVEL[c.strength] ?? "Good");
  const reason = REASON[c.strength_reason] ? tr(REASON[c.strength_reason]) : "";
  return <li className={`bs-row is-s${c.strength}`}>
    <span className="bs-av"><CompanyAvatar name={org.name} /></span>
    <div className="bs-co">
      <Link className="bs-co-name" to={`/accounts/${org.id}?event=${top.event_id}`}>{org.name}</Link>
      {facts.length > 0 && <span className="bs-co-meta">{facts.map((f, n) => <span key={n}>{f}</span>)}</span>}
      {canPeople && org.people > 0
        ? <Link className={`bs-reach${org.people_with_email ? " on" : ""}`} to={`/people?q=${encodeURIComponent(org.name)}`} title={tr("See the people")}>{people}</Link>
        : <span className={`bs-reach${org.people_with_email ? " on" : ""}`}>{people}</span>}
    </div>
    <div className="bs-sig">
      <ul className="bs-sigs">{signals.map((s) => <SignalLine key={s.id} s={s} org={org} kind={kinds.get(s.rule_id)} named={!only} />)}</ul>
      {why && !only && <p className="bs-why"><b>{tr("Why it matters")}</b> {why}</p>}
    </div>
    <div className="bs-str" role="img" aria-label={tr("Signal strength: {}", level) + (reason ? `. ${reason}` : "")} title={reason || undefined}>
      <span className="bs-str-top"><Bars n={c.strength} /><b>{level}</b></span>
      {reason && <span className="bs-str-why">{reason}</span>}
    </div>
    {ranked && <div className="bs-rank"><span className="bs-rank-l">{tr("Lead list")}</span><ListRank priority={org.list_priority} score={org.list_score} /></div>}
  </li>;
}

/** One signal at a company: what kind, how fresh, what happened, and the evidence one click away. */
function SignalLine({ s, org, kind, named }: { s: Signal; org: Org; kind?: Kind; named: boolean }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const language = getLanguage();
  const fresh = s.age_days != null && s.age_days <= FRESH_DAYS;
  const href = safeHref(s.tender?.url ?? s.evidence.url);
  const when = s.evidence.published_at ?? s.happened;
  return <li>
    <p className="bs-sig-k">{named && <b>{signalName(s.rule_id, kind?.name)}</b>}
      <time className={fresh ? "is-fresh" : ""} dateTime={s.happened ?? undefined} title={s.happened ? fmtDate(s.happened) : undefined}>
        {fresh && <i className="bs-new" aria-hidden="true" />}{ago(s.happened) || tr("Date unknown")}</time>
      {s.more > 0 && <span className="bs-more-n">{tr("and {} earlier", number(s.more))}</span>}
      <button type="button" className="bs-evi-btn" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(!open)}>{tr("Evidence")}</button></p>
    <p className="bs-sig-t">{plain((language === "pl" ? s.summary_pl : s.summary_en) || s.summary, org.name)}</p>
    {s.tender?.deadline && <p className="bs-deadline">{tr("Tender deadline")} <b>{fmtDateTime(s.tender.deadline)}</b></p>}
    <div className="bs-evi" id={panel} hidden={!open}>
      {open && <>
        {s.evidence.quote ? <blockquote>“{s.evidence.quote}”
          {language === "en" && s.evidence.translation && <span>{tr("English translation")}: “{s.evidence.translation}”</span>}</blockquote>
          : <p className="muted">{tr("A structured record, with no quote to show.")}</p>}
        {s.tender?.scope && <p className="bs-evi-scope">{s.tender.scope}</p>}
        <p className="bs-evi-src">
          {href && <a href={href} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "evidence" })}>
            {tr("Open the source")} ↗</a>}
          <span className="bs-evi-meta">{s.evidence.source && <span>{s.evidence.source}</span>}{when && <span>{tr("published {}", fmtDate(when))}</span>}</span>
        </p>
      </>}
    </div>
  </li>;
}
