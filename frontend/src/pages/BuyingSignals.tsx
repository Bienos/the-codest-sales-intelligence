import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, trackSourceOpened } from "../api";
import { counted, getLanguage, number, tr } from "../locale";
import { Empty, Err, Skeleton, Strength, safeHref } from "../components/ui";
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

type Company = { id: string; name: string; country: string | null; region: string; segment: string | null; domain: string | null;
  list_priority: string | null; list_score: number | null; people: number; people_with_email: number };
type Item = {
  id: string; rule_id: string; strength: number; demand_tier: string; intent: string; event_id: string; event_type: string;
  summary: string; summary_en?: string | null; summary_pl?: string | null; happened: string; age_days: number | null;
  fit?: { level?: string; service?: string; reason?: string } | null;
  tender: { deadline: string; url: string; title: string; scope?: string | null } | null;
  evidence: { quote: string | null; translation: string | null; url: string | null; source: string | null; published_at: string | null };
  organization: Company; more: number; questions: string[];
};
type Group = { rule_id: string; name: string; group: string | null; strength: number; meaning: string; who_to_contact: string | null;
  companies: number; reachable: number; latest: string | null; rank: number; items: Item[] };
type Data = { days: number; groups: Group[]; high_intent_rules: string[];
  totals: { signals: number; companies: number; reachable: number; fresh: number; held_back: number } };

/** Why one row made the list, shown only when it is stronger than the kind of signal already says. */
const INTENT: Record<string, string> = {
  outside_demand: "They asked for outside help",
  need_likely: "Outside help is likely",
  strong: "One of the strongest signals",
};
const WINDOWS: [string, string][] = [["30", "Last 30 days"], ["90", "Last 90 days"], ["180", "Last 6 months"], ["365", "Last 12 months"]];
const FRESH_DAYS = 14;

export default function BuyingSignals({ canCampaign = false, canPeople = false }: { canCampaign?: boolean; canPeople?: boolean }) {
  const [params, setParams] = useSearchParams();
  const days = WINDOWS.some(([k]) => k === params.get("days")) ? params.get("days")! : "90";
  const region = REGIONS.some(([k]) => k === params.get("region")) ? params.get("region")! : "";
  const rule = /^[A-Z]{3}-\d{2}$/.test(params.get("rule") ?? "") ? params.get("rule")! : "";
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  const query = new URLSearchParams(Object.entries({ days, region, rule, q }).filter(([, v]) => v)).toString();
  const [state, setState] = useState<{ query: string; data?: Data; error?: unknown } | null>(null);
  const [revision, setRevision] = useState(0);
  const [rules, setRules] = useState<string[]>([]);
  useEffect(() => {
    const request = new AbortController();
    api<Data>(`/api/buying-signals?${query}`, { signal: request.signal })
      .then((data) => { if (!request.signal.aborted) { setState({ query, data }); setRules(data.high_intent_rules); } })
      .catch((error) => { if (!request.signal.aborted) setState({ query, error }); });
    return () => request.abort();
  }, [query, revision]);
  const data = state?.query === query ? state.data : undefined;
  const error = state?.query === query ? state.error : undefined;
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const jump = (e: React.MouseEvent, id: string) => {
    const target = document.getElementById(id);
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ block: "start" });
    target.querySelector<HTMLElement>("h2")?.focus();
  };
  const filtered = Boolean(q || region || rule || days !== "90");
  const windowLabel = tr(WINDOWS.find(([k]) => k === days)?.[1] ?? "Last 90 days").toLowerCase();
  const t = data?.totals;

  return <>
    <header className="bs-band">
      <div className="page bs-band-in">
        <div className="bs-band-copy">
          <h1>{tr("Buying signals")}</h1>
          <p>{tr("What is happening right now at companies on your list that suggests they need engineering help: open tenders, calls for contractors, new technology leaders, funded programmes. Each signal was checked against the original page.")}</p>
          {t && t.signals > 0 && <p className="bs-band-sum" role="status">
            {tr("{} at {} in the {}.", counted(t.signals, ["signal", "signals"], ["sygnał", "sygnały", "sygnałów"]), counted(t.companies, ["company", "companies"], ["firmie", "firmach", "firmach"]), windowLabel)}{" "}
            {tr("{} with someone to email.", counted(t.reachable, ["company", "companies"], ["firma", "firmy", "firm"]))}{" "}
            {t.fresh > 0 && <span className="bs-fresh"><i className="bs-new" aria-hidden="true" />{tr("{} new in the last two weeks.", number(t.fresh))}</span>}</p>}
        </div>
        {data && data.groups.length > 0 && <nav className="bs-index" aria-label={tr("Signals by kind")}>
          {data.groups.map((g) => {
            const fresh = g.items.some((i) => i.age_days != null && i.age_days <= FRESH_DAYS);
            return <a key={g.rule_id} href={`#sig-${g.rule_id}`} className="bs-index-row" onClick={(e) => jump(e, `sig-${g.rule_id}`)}>
              <span className="bs-index-name"><i className={`bs-new${fresh ? "" : " off"}`} title={fresh ? tr("New in the last two weeks") : undefined} />{signalName(g.rule_id, g.name)}</span>
              <span className="bs-index-fill" aria-hidden="true" />
              <span className="bs-index-n" aria-label={counted(g.companies, ["company", "companies"], ["firma", "firmy", "firm"])}>{number(g.companies)}</span>
            </a>;
          })}
        </nav>}
      </div>
    </header>
    <div className="page bs-page">
      <form className="bs-filters" role="search" aria-label={tr("Filter buying signals")} onSubmit={(e) => { e.preventDefault(); update("q", search.trim()); }}>
        <div className="bs-field"><label htmlFor="bs-q">{tr("Search")}</label>
          <span className="bs-search"><input id="bs-q" type="search" maxLength={120} placeholder={tr("Company or keyword")} value={search} onChange={(e) => setSearch(e.target.value)} />
            <button type="submit">{tr("Search")}</button></span></div>
        <label>{tr("Region")}
          <select value={region} onChange={(e) => update("region", e.target.value)}>
            <option value="">{tr("All regions")}</option>{REGIONS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select></label>
        <label>{tr("Kind of signal")}
          <select value={rule} onChange={(e) => update("rule", e.target.value)}>
            <option value="">{tr("All high-intent signals")}</option>
            {(rules.length ? rules : rule ? [rule] : []).map((id) => <option key={id} value={id}>{signalName(id)}</option>)}
          </select></label>
        <label>{tr("When")}
          <select value={days} onChange={(e) => update("days", e.target.value === "90" ? "" : e.target.value)}>
            {WINDOWS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select></label>
      </form>
      <p className="bs-rule small">
        {tr("Only signals that point to a need: outside demand evidenced or likely, the strongest kinds of signal, and events that usually start a buying conversation. Context-only news, such as a market launch, stays in")}{" "}
        <Link to="/feed">{tr("All findings")}</Link>.
        {filtered && <> <button type="button" className="linkish" onClick={() => { setSearch(""); setParams(new URLSearchParams(), { replace: true }); }}>{tr("Clear filters")}</button></>}
      </p>
      {error ? <Err e={error} onRetry={() => setRevision((n) => n + 1)} /> : null}
      {!data && !error && <Skeleton rows={4} label={tr("Loading buying signals")} />}
      {data && data.groups.length === 0 && <Empty title={tr("No high-intent signals in this view")}>
        <p>{tr("Nothing at companies on your list matches right now.")} {days !== "365" && <button type="button" className="linkish" onClick={() => update("days", "365")}>{tr("Look at the last 12 months")}</button>}</p>
        <p><Link to="/feed">{tr("See all findings")}</Link> · <Link to="/tenders">{tr("Open tenders")}</Link></p>
      </Empty>}
      {data?.groups.map((g) => <SignalGroup key={g.rule_id} g={g} canCampaign={canCampaign} canPeople={canPeople} />)}
      {t && t.held_back > 0 && <p className="bs-held small muted">{tr("{} with a signal here are held back because a reason to stop or wait was also found, such as a hiring freeze or an exclusive supplier.",
        counted(t.held_back, ["company", "companies"], ["firma", "firmy", "firm"]))}</p>}
    </div>
  </>;
}

function SignalGroup({ g, canCampaign, canPeople }: { g: Group; canCampaign: boolean; canPeople: boolean }) {
  const id = `sig-${g.rule_id}`;
  const campaign = `/campaigns/new?rules=${g.rule_id}`;
  return <section className="bs-group" id={id} aria-labelledby={`${id}-h`}>
    <header className="bs-group-head">
      <div className="bs-group-text">
        <p className="bs-group-kind">{g.group && <span>{tr(g.group)}</span>}<Strength n={g.strength} /></p>
        <h2 id={`${id}-h`} tabIndex={-1}>{signalName(g.rule_id, g.name)}</h2>
        <p className="bs-group-why"><b>{tr("Why it points to a need")}</b> {tr(g.meaning)}</p>
        <p className="bs-group-meta">
          <span>{counted(g.companies, ["company", "companies"], ["firma", "firmy", "firm"])}</span>
          <span>{tr("{} with someone to email", number(g.reachable))}</span>
          {g.who_to_contact && <span>{tr("Who to contact: {}", tr(g.who_to_contact))}</span>}
        </p>
      </div>
      {canCampaign && <div className="bs-group-cta">
        <Link className="btn primary" to={campaign}>{tr("Start a campaign")}</Link>
        {g.reachable === 0 && <span className="small muted">{tr("Nobody to email yet: find contacts first")}</span>}
      </div>}
    </header>
    <ol className="bs-items">{g.items.map((i) => <SignalItem key={i.id} i={i} canPeople={canPeople} />)}</ol>
  </section>;
}

function SignalItem({ i, canPeople }: { i: Item; canPeople: boolean }) {
  const language = getLanguage();
  const org = i.organization;
  const summary = (language === "pl" ? i.summary_pl : i.summary_en) || i.summary;
  const fresh = i.age_days != null && i.age_days <= FRESH_DAYS;
  const href = safeHref(i.tender?.url ?? i.evidence.url);
  const reason = i.fit?.reason && i.fit.level !== "none" ? i.fit.reason : null;
  const facts = [countryName(org.country), org.segment].filter(Boolean);
  return <li className={`bs-item${fresh ? " is-fresh" : ""}${i.tender ? " is-tender" : ""}`}>
    <div className="bs-when">
      <b>{fresh && <i className="bs-new" aria-hidden="true" />}{ago(i.happened) || tr("Date unknown")}</b>
      <time dateTime={i.happened}>{fmtDate(i.happened)}</time>
    </div>
    <div className="bs-body">
      <div className="bs-co">
        <CompanyAvatar name={org.name} small />
        <span className="bs-co-text">
          <Link className="bs-co-name" to={`/accounts/${org.id}?event=${i.event_id}`}>{org.name}</Link>
          {facts.length > 0 && <span className="bs-co-meta">{facts.map((f, n) => <span key={n}>{f}</span>)}</span>}
        </span>
        {(org.list_priority || org.list_score != null) && <span className="bs-co-rank"><ListRank priority={org.list_priority} score={org.list_score} /></span>}
      </div>
      <p className="bs-what">{summary}</p>
      {INTENT[i.intent] && <p className="bs-intent">{tr(INTENT[i.intent])}</p>}
      {i.tender && <p className="bs-deadline"><b>{tr("Tender deadline")}</b> {fmtDateTime(i.tender.deadline)}{i.tender.scope && <span>{i.tender.scope}</span>}</p>}
      {reason && <p className="bs-why"><b>{tr("Why it matters for us")}</b> {reason}</p>}
      {i.evidence.quote && i.evidence.quote.trim() !== i.summary.trim() && <blockquote className="bs-quote">“{i.evidence.quote}”
        {language === "en" && i.evidence.translation && <span>{tr("English translation")}: “{i.evidence.translation}”</span>}</blockquote>}
      <div className="bs-foot">
        <span className={`bs-reach${org.people_with_email ? " on" : ""}`}>
          {org.people ? tr("{} at this company, {} with email", counted(org.people, ["person", "people"], ["osoba", "osoby", "osób"]), number(org.people_with_email)) : tr("No contacts yet")}</span>
        <Link to={`/accounts/${org.id}?event=${i.event_id}`}>{tr("Open company")}</Link>
        {canPeople && org.people > 0 && <Link to={`/people?q=${encodeURIComponent(org.name)}`}>{tr("See the people")}</Link>}
        {href && <a href={href} target="_blank" rel="noreferrer noopener" title={i.evidence.source ?? undefined} onClick={() => trackSourceOpened({ organization_id: org.id, where: "evidence" })}>
          {tr("Open source")} ↗</a>}
        {i.more > 0 && <span className="muted">{tr("{} more like this at the same company", number(i.more))}</span>}
      </div>
    </div>
  </li>;
}

