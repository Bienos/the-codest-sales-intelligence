import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, getToken, trackSourceOpened } from "../api";
import { getLanguage, number, tr } from "../locale";
import { countryName } from "../pages/targetMarket";
import { Badge, Err, Skeleton, lab, safeHref, useLogo } from "./ui";
import { SignalRef, SignalTag, signalName } from "./Signals";
import "../styles/findings.css";

type Source = { url: string; source: string | null; published_at?: string | null };
type Play = { play_id: string; offers: string[] };
type Finding = {
  id: string; event_ids: string[]; type: string; summary: string; language?: string;
  summary_en?: string; summary_pl?: string; happened: string; published?: string | null;
  organization: { id: string; name: string; country: string; qualified?: boolean; logo_url?: string | null };
  tender: { title: string; url: string; deadline: string; checked_at: string; notice_id: string; scope?: string | null } | null;
  evidence: Array<Source & { claim: string; claim_id: string; translation?: string | null }>;
  sources: Source[];
  fit: { plays: Play[] };
  why?: { level: string; service: string; reason: string } | null;
  signals?: SignalRef[];
  next_action?: { hypothesis_id: string; action_type: string; has_owner: boolean; owner_name?: string; due_at?: string } | null;
  first_seen?: string | null;
  /** On the signed-in person's watchlist. */
  watched?: boolean;
  /** Why the person marked it not relevant, or null. Only sent when hidden findings are asked for. */
  dismissed?: string | null;
};
type Lead = {
  id: string; url: string; origin: string; status: string; claimed_text: string | null;
  updated_at: string | null; event_id: string | null;
};
type Coverage = { monitored_sources: number; unhealthy: unknown[]; pending_validation_sources: number; extraction_waiting_for_credit?: boolean;
  no_active_monitoring?: boolean; all_monitored_healthy?: boolean };
/** A buying signal behind the findings in the window, with how many findings it has (All findings only). */
type Kind = { rule_id: string; name: string; findings: number; latest: string | null };
type Result = { items: Finding[]; leads: Lead[]; has_more: boolean; coverage: Coverage; kinds?: Kind[]; watchlist?: number; hidden_count?: number };
export type FindingItem = Finding;
export type FindingLead = Lead;
export type FindingsCoverage = Coverage;
export type FindingKind = Kind;
export type FindingsData = Result;

/** True while the viewport matches the media query (the phone layout starts at 760px). */
export function useMedia(query: string) {
  const get = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [match, setMatch] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Why each kind of buying signal matters to The Codest, in one plain sentence about the company (the same lines Buying signals shows). */
const WHY_RULE: Record<string, string> = {
  "SRC-01": "They are buying software work now and a deadline is set, so we can bid or talk to the buyer before it closes.",
  "SRC-02": "A supplier panel is open. Getting on it gives us access to the work that follows.",
  "SRC-04": "An integrator won the programme. The integration and custom work around it may still be open to partners.",
  "SRC-05": "A platform was chosen, and the integration work around it still needs a team.",
  "SRC-06": "They asked for contract engineers, so they are short of people on a named system.",
  "CAP-01": "They are hiring several engineers for one platform. A team extension can start sooner than hiring.",
  "CAP-02": "Contract roles on a named system mean the work has a date and the team is short.",
  "CAP-05": "They are taking AI from a pilot into production, which is work for our AI and data engineers.",
  "PRG-01": "A core banking replacement needs extra engineers for integrations, data migration and testing.",
  "PRG-02": "A cloud or data platform move has workstreams that a delivery team can own.",
  "PRG-06": "A payments scheme change comes with fixed test dates, which suits a dedicated delivery team.",
  "MAN-01": "A new technology leader sets the roadmap and the team model in the first months, a good time to introduce us.",
  "REL-03": "Someone who knows us has started a new role, so there is a warm way in.",
  "COM-01": "New funding usually brings a product plan that needs engineers quickly.",
  "COM-02": "Integration work after an acquisition has a hard date, and teams are often short.",
  "COM-03": "A launch in a new market brings payments, compliance and integration work with a fixed date.",
  "COM-04": "A new licence means a fixed date for the platform behind the launch.",
  "COM-05": "A customer go-live puts the integration work on a fixed date.",
  "COM-06": "A new product line needs new integrations while the current platform keeps running.",
  "COM-07": "A partnership with integration scope means connector and API work on a timeline.",
  "COM-08": "A cost programme with a technology scope often means retiring old systems while the business keeps running.",
};
const WHY_FAMILY: Record<string, string> = {
  SRC: "They have a way to buy engineering help open right now.",
  CAP: "They are short of engineers, which is where a team extension helps.",
  PRG: "A funded programme is under way and usually needs extra engineering.",
  MAN: "A new technology leader is setting the roadmap and the team model.",
  COM: "A business event like this usually brings build or integration work with a date.",
  REL: "Someone who knows us is involved, so there is a warm way in.",
};
/** The "meaning" the findings answer carries for a signal is an outreach opener ("If you are choosing suppliers... I would like to"). It is not shown as a reason. */
const OPENER = /\bI (would|am|can|will|hope)\b|^If you\b/i;

/** What each sales play means for us in one sentence. Used when the finding has no reason of its own. */
const WHY: Record<string, string> = {
  "SP-01": "A specialist engineering gap may suit staff augmentation.",
  "SP-02": "The programme may need extra engineers or a team to own a workstream.",
  "SP-03": "A defined software workstream may suit a dedicated delivery team.",
  "SP-04": "Payments integration and testing may suit a dedicated delivery team.",
  "SP-05": "A platform rollout may have integration and migration work The Codest can deliver.",
  "SP-06": "The cloud or data migration may have workstreams an engineering team can own.",
  "SP-07": "A named AI workflow may suit a prototype or production integration.",
  "SP-08": "The lead supplier may need help delivering part of the programme.",
  "SP-09": "The procurement route could give The Codest access to a defined engineering scope.",
};
const SERVICES: Record<string, string> = {
  staff_augmentation: "Staff Augmentation", team_extension: "Team Extension", ai_services: "AI Services",
};
const LEAD_STATUS: Record<string, string> = {
  queued: "Waiting to be checked", fetching: "Reading the page", extracting: "Finding events",
  waiting_budget: "Waiting for AI budget", no_event: "No verified finding for this company", not_checked: "Not checked yet",
  blocked: "Page not allowed", failed: "Could not check", verified: "Source checked",
};

/* ------------------------------------------------------------------ small shared pieces (also used by the account list and buying signals) */

/** "4 days ago", "3 weeks ago": how fresh a piece of news is. */
export function ago(v?: string | null): string {
  if (!v) return "";
  const t = new Date(v).getTime();
  if (isNaN(t)) return "";
  const days = Math.floor((Date.now() - t) / 86_400_000);
  if (days <= 0) return tr("today");
  if (days === 1) return tr("yesterday");
  if (days < 14) return tr("{} days ago", days);
  if (days < 60) return tr("{} weeks ago", Math.round(days / 7));
  return tr("{} months ago", Math.round(days / 30));
}

const LEGAL = /^(the|plc|ltd|limited|ag|sa|s\.a\.|ab|as|asa|gmbh|se|nv|bv|spa|oyj|inc|llc|a\/s|sp\.?|z|o\.o\.)$/i;
/** A square with the company's logo when the API sends one (`logo`), otherwise its initials. The shade is stable per name so the same company always
 *  looks the same. A logo goes on a white tile and drops back to the initials if it does not load; the tile keeps its size either way. */
export function CompanyAvatar({ name, small = false, tiny = false, logo }: { name: string; small?: boolean; tiny?: boolean; logo?: string | null }) {
  const pic = useLogo(logo);
  const words = name.split(/[\s\-–/&,()]+/).filter((w) => w && !LEGAL.test(w));
  const letters = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? name).slice(0, 2)).toUpperCase();
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return <span className={`co-av co-av-${h % 4}${small ? " sm" : ""}${tiny ? " xs" : ""}${pic.ok ? " has-logo" : ""}`} aria-hidden="true">{!pic.ok && letters}{pic.img}</span>;
}

/** Lead-list priority and score: P1 to P3 as a code, the 0 to 100 score as ten squares. */
export function ListRank({ priority, score }: { priority?: string | null; score?: number | null }) {
  const p = priority && /^P[123]$/.test(priority) ? priority : null;
  if (!p && score == null) return <span className="acc-none">{tr("Not ranked")}</span>;
  const filled = score == null ? 0 : Math.round(Math.max(0, Math.min(100, score)) / 10);
  return <span className="acc-rankbox">
    <span className="acc-rank-top">
      {p ? <span className={`acc-p acc-${p.toLowerCase()}`} title={tr("Lead list priority {}", p)}>{p}</span> : <span />}
      {score != null && <span className="acc-score">{number(Math.round(score))}</span>}
    </span>
    {score != null && <span className="acc-cells" role="img" aria-label={tr("List score {} of 100", number(Math.round(score)))}>
      {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < filled ? "on" : ""} />)}</span>}
  </span>;
}

let permissionCache: { token: string | null; perms: Promise<string[]> } | null = null;
/** The signed-in person's permissions, fetched once per session, for pages rendered without them (Today, the findings archive). */
export function useCan(): (permission: string) => boolean | undefined {
  const [perms, setPerms] = useState<string[] | null>(null);
  useEffect(() => {
    const token = getToken();
    if (!permissionCache || permissionCache.token !== token) {
      permissionCache = { token, perms: api<{ permissions: string[] }>("/api/me").then((m) => m.permissions).catch(() => { permissionCache = null; return []; }) };
    }
    let live = true;
    permissionCache.perms.then((p) => { if (live) setPerms(p); });
    return () => { live = false; };
  }, []);
  return (permission) => (perms ? perms.includes(permission) : undefined);
}

const norm = (t?: string | null) => (t ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
/** True when `a` adds nothing to `b`: the same words, or one is inside the other (a notice number in front of a title, a buyer name before it). */
const same = (a?: string | null, b?: string | null) => { const x = norm(a), y = norm(b); return Boolean(x && y && (x === y || x.includes(y) || y.includes(x))); };

export function SourceCoverage({ coverage }: { coverage: Coverage }) {
  const { monitored_sources: monitored, unhealthy, pending_validation_sources: pending } = coverage;
  return <div className="finding-coverage">
    <span><b>{tr("Source monitoring")}</b><br />
      {monitored ? tr("Sources reporting successfully: {} of {} monitored.", number(monitored - unhealthy.length), number(monitored))
        : tr("Source monitoring has not started yet.")}
      {pending > 0 && <> {tr("{} still need validation.", number(pending))}</>}
      {coverage.extraction_waiting_for_credit && <span className="coverage-warning">{tr("New pages are waiting for verification because the provider is out of credit.")}</span>}
    </span>
    <Link to="/library?tab=sources">{tr("View source health")}</Link>
  </div>;
}

export function useFindings(query: string) {
  const [state, setState] = useState<{ query: string; data?: Result; error?: unknown }>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setState(undefined);
    api<Result>(`/api/findings?${query}`, { signal: request.signal })
      .then(data => { if (!request.signal.aborted) setState({ query, data }); })
      .catch(error => { if (!request.signal.aborted) setState({ query, error }); });
    return () => request.abort();
  }, [query, revision]);
  return {
    data: state?.query === query ? state.data : undefined,
    error: state?.query === query ? state.error : undefined,
    retry: () => setRevision(n => n + 1),
  };
}

/** Why the finding matters to The Codest: the finding's own reason first, then what the kind of news usually means. */
function reasonFor(item: Finding): { text: string; specific: boolean } {
  if (item.why?.reason && item.why.level !== "none") return { text: item.why.reason, specific: true };
  if (item.tender) return { text: tr("The notice requests engineering services. Review scope and supplier eligibility before bidding."), specific: false };
  const rule = item.signals?.find(s => !s.rule_id.startsWith("NEG-"))?.rule_id;
  const line = rule ? WHY_RULE[rule] ?? WHY_FAMILY[rule.slice(0, 3)] : undefined;
  if (line) return { text: tr(line), specific: false };
  if (item.type === "engineering_job_opened" || item.type === "contractor_job_opened")
    return { text: tr("Engineering hiring can indicate a need for skills or capacity. Check the team and whether it uses external partners."), specific: false };
  const play = item.fit.plays.find(p => WHY[p.play_id]);
  if (play) return { text: tr(WHY[play.play_id]), specific: false };
  const service = item.why?.service ? SERVICES[item.why.service] : undefined;
  if (item.why?.level === "strong" && service) return { text: tr("This finding may fit {}. Confirm the workstream and supplier need.", tr(service)), specific: false };
  const meaning = item.signals?.find(s => s.meaning && !OPENER.test(s.meaning))?.meaning;
  if (meaning) return { text: tr(meaning), specific: false };
  return { text: tr("Confirm the workstream and whether the company needs an external team."), specific: false };
}

/** One verified finding: the company, what happened and when, the buying signal, why it matters for us, the evidence and what to do next. */
export function FindingCard({ item, onAccount = false, canCampaign, canPeople }: { item: Finding; onAccount?: boolean; canCampaign?: boolean; canPeople?: boolean }) {
  const can = useCan();
  const campaign = canCampaign ?? can("draft.create");
  const people = canPeople ?? can("contacts.read");
  const [hint, setHint] = useState(false);
  const language = getLanguage();
  const summary = (language === "pl" ? item.summary_pl : item.summary_en) || item.summary;
  const translated = summary !== item.summary;
  const signals = (item.signals ?? []).filter(s => !s.rule_id.startsWith("NEG-"));
  const rule = signals[0]?.rule_id;
  const meaning = signals.find(s => s.meaning && !OPENER.test(s.meaning))?.meaning;
  const why = reasonFor(item);
  const org = item.organization;
  const company = `/accounts/${org.id}?finding=${item.id}`;
  const deadline = item.tender ? new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(item.tender.deadline)) : null;
  const first = item.evidence[0];
  const source = item.tender ? { url: item.tender.url, source: tr("Official notice"), published_at: null } : first ?? item.sources[0];
  const published = first?.published_at ?? item.sources[0]?.published_at ?? item.published ?? null;
  const showQuote = first && !same(first.claim, item.summary) && !same(first.claim, summary) && !same(first.claim, item.tender?.title) && !same(first.claim, item.tender?.scope);
  const extra = Math.max(item.evidence.length - 1, 0) + item.sources.filter(s => !item.evidence.some(e => e.url === s.url)).length;
  const href = safeHref(source?.url);
  return <article className={`fd-card${item.tender ? " is-tender" : ""}`} id={`finding-${item.id}`} aria-labelledby={`finding-${item.id}-h`}>
    <header className="fd-top">
      {!onAccount && <Link to={company} className="fd-co" aria-label={tr("Open company {}", org.name)}>
        <CompanyAvatar name={org.name} logo={org.logo_url} />
        <span className="fd-co-text"><span className="fd-co-name">{org.name}</span><span className="fd-co-meta">{countryName(org.country)}</span></span>
      </Link>}
      {onAccount && (item.tender || signals.length === 0 ? <span className={`fd-kind${item.tender ? " is-tender" : ""}`}>{item.tender ? tr("Open RFP or tender") : lab("event", item.type)}</span> : <span />)}
      <span className="fd-when"><time dateTime={item.happened}>{fmtDate(item.happened)}</time><span>{ago(item.happened)}</span></span>
    </header>
    <h3 className="fd-head" id={`finding-${item.id}-h`}>{item.tender ? tenderLine(item) || summary : summary}</h3>
    <div className="fd-tags">
      {!onAccount && (item.tender || signals.length === 0) && <span className={`fd-kind${item.tender ? " is-tender" : ""}`}>{item.tender ? tr("Open RFP or tender") : lab("event", item.type)}</span>}
      {signals.map(sig => <SignalTag key={sig.rule_id} s={sig} />)}
    </div>
    {translated && <details className="fd-orig"><summary>{tr("Translated summary · view original")}</summary><p>{item.summary}</p></details>}
    {item.tender && <p className="fd-deadline"><b>{tr("Tender deadline")}</b> {deadline} UTC <span className="muted">{tr("Status checked {}.", fmtDateTime(item.tender.checked_at))}</span>
      {item.tender.scope && <span className="fd-scope"><b>{tr("Scope")}</b> {item.tender.scope}</span>}</p>}
    <div className="fd-why">
      <p><b>{tr("Why this matters for us")}</b> {why.text}</p>
      {meaning && why.text !== tr(meaning) && <>
        <button type="button" className="fd-hint-btn" aria-expanded={hint} onClick={() => setHint(!hint)}>{hint ? tr("Hide what this signal usually means") : tr("What this signal usually means")}</button>
        {hint && <p className="fd-hint">{tr(meaning)}</p>}
      </>}
    </div>
    <div className="fd-ev">
      {showQuote && <blockquote className="fd-quote">“{first.claim}”
        {language === "en" && first.translation && <span className="fd-trans">{tr("English translation")}: “{first.translation}”</span>}</blockquote>}
      <p className="fd-src">
        <span className="fd-checked">{item.tender ? tr("Confirmed by the official notice") : tr("Checked against the original page")}</span>
        {source?.source && !item.tender && <span>{source.source}</span>}
        {published && fmtDate(published) !== fmtDate(item.happened) && <span>{tr("published {}", fmtDate(published))}</span>}
        {href && <a href={href} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "evidence" })}>{tr("Open source")} ↗</a>}
      </p>
      {extra > 0 && <details className="fd-more">
        <summary>{tr("More evidence ({})", extra)}</summary>
        {item.evidence.slice(1).map(e => <blockquote key={e.claim_id} className="fd-quote">“{e.claim}”
          {language === "en" && e.translation && <span className="fd-trans">{tr("English translation")}: “{e.translation}”</span>}
          <cite>{safeHref(e.url) ? <a href={safeHref(e.url)} target="_blank" rel="noreferrer noopener">{e.source || tr("Original source")} ↗</a> : e.source}</cite>
        </blockquote>)}
        <ul className="fd-links">{item.sources.filter(s => !item.evidence.some(e => e.url === s.url)).map(s => <li key={s.url}>
          {safeHref(s.url) ? <a href={safeHref(s.url)} target="_blank" rel="noreferrer noopener">{s.source || new URL(safeHref(s.url)!).hostname} ↗</a> : s.source}
          {s.published_at && <span className="muted"> {fmtDate(s.published_at)}</span>}</li>)}</ul>
      </details>}
    </div>
    <footer className="fd-actions">
      {!onAccount && <Link className="fd-act primary" to={company}>{tr("Open company")}</Link>}
      {campaign && rule && <Link className="fd-act" to={`/campaigns/new?rules=${rule}`}>{tr("Start a campaign with this signal")}</Link>}
      {people && !onAccount && <Link className="fd-act" to={`/people?q=${encodeURIComponent(org.name)}`}>{tr("People at this company")}</Link>}
      {onAccount && <Link className="fd-act" to={`/feed?finding=${item.id}`}>{tr("Open in findings archive")}</Link>}
      {item.next_action && <span className="fd-next"><Link to={`/opportunities/${item.next_action.hypothesis_id}`}>{tr("Open recommended action")}</Link>
        <span className="muted"> {tr("Owner")}: {item.next_action.owner_name || tr(item.next_action.has_owner ? "Assigned" : "Unassigned")}
          {item.next_action.due_at && <>, {tr("due {}", fmtDateTime(item.next_action.due_at))}</>}</span></span>}
    </footer>
  </article>;
}

export function FindingsResult({ data, error, retry, children, empty }: {
  data?: Result; error?: unknown; retry: () => void; children: React.ReactNode; empty: string;
}) {
  if (error) return <Err e={error} onRetry={retry} />;
  if (!data) return <Skeleton rows={3} label={tr("Loading findings")} />;
  if (!data.items.length) return <div className="empty"><b>{tr(empty)}</b></div>;
  return <>{children}</>;
}

export function ResearchLeads({ data }: { data: Result }) {
  return data.leads.length > 0 && <details className="card">
    <summary>{tr("RFP links to check")} · {data.leads.length}</summary>
    <ul className="assigned-leads">{data.leads.map(lead => <li key={lead.id} className="mt-3">
      <a href={lead.url} target="_blank" rel="noreferrer noopener">{lead.claimed_text || lead.url} ↗</a>
      <div><Badge kind="info">{tr(LEAD_STATUS[lead.status] ?? "Could not check")}</Badge></div>
    </li>)}</ul>
  </details>;
}

export function AccountFindings({ accountId, selected, leadsOnly }: { accountId: string; selected: string | null; leadsOnly: boolean }) {
  const [page, setPage] = useState(1);
  const query = new URLSearchParams({ scope: "account", organization: accountId, days: "365", page: String(page) });
  if (selected && !leadsOnly) query.set("event", selected);
  const { data, error, retry } = useFindings(query.toString());
  if (error) return <Err e={error} onRetry={retry} />;
  if (!data) return <Skeleton rows={3} label={tr("Loading findings")} />;
  if (leadsOnly) return <>
    <p>{tr("Links assigned to this company, with their source and current verification status.")}</p>
    {data.leads.length === 0 && <div className="empty"><b>{tr("No assigned leads yet")}</b></div>}
    <ul className="assigned-leads">{data.leads.map(lead => <li key={lead.id} className="card">
      <div className="row spread">
        <a href={lead.url} target="_blank" rel="noreferrer noopener">{lead.claimed_text || lead.url} ↗</a>
        <Badge kind={lead.event_id ? "ok" : "info"}>{tr(LEAD_STATUS[lead.event_id || lead.status !== "verified" ? lead.status : "no_event"] ?? "Could not check")}</Badge>
      </div>
      <div className="row spread small muted">
        <span>{lead.updated_at && <>{fmtDate(lead.updated_at)} · </>}{tr(({ sheet_signal: "Workbook lead", exa_discovery: "Web search", manual: "Submitted link", seller_miss: "Reported link" } as Record<string, string>)[lead.origin] ?? "Submitted link")}</span>
        {lead.event_id && <Link to={`?finding=${lead.event_id}`}>{tr("Open linked finding")}</Link>}
      </div>
    </li>)}</ul>
  </>;
  return <>
    {selected && <p><Link to="?timeline=1">{tr("Show all company findings")}</Link></p>}
    <FindingsResult data={data} retry={retry} empty="No verified findings for this company yet">
      {data.items.map(item => <FindingCard key={item.id} item={item} onAccount />)}
    </FindingsResult>
    <FindingsPagination page={page} hasMore={data.has_more} change={setPage} />
  </>;
}

export function FindingsPagination({ page, hasMore, change }: { page: number; hasMore?: boolean; change: (page: number) => void }) {
  if (page === 1 && !hasMore) return null;
  return <nav className="fd-pages" aria-label={tr("Findings pages")}>
    <button type="button" disabled={page <= 1} onClick={() => change(page - 1)}>{tr("Previous")}</button>
    <span>{tr("Page {}", page)}</span>
    <button type="button" disabled={!hasMore} onClick={() => change(page + 1)}>{tr("Next")}</button>
  </nav>;
}

/* ------------------------------------------------------------------ the compact finding used by the All findings timeline */

/** The summary often starts with the company's name, which the card already shows above it. */
export function plainSummary(text: string, name: string): string {
  const t = (text ?? "").trim();
  if (name && t.startsWith(name + " ")) {
    const rest = t.slice(name.length + 1);
    if (/^[a-ząćęłńóśźż]/.test(rest)) return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  return t;
}

/** The colour family of a buying signal: the letters before the dash (hiring, new leader, funded project...). */
export const signalFamily = (ruleId?: string | null) => (ruleId ? ruleId.slice(0, 3).toLowerCase() : "none");

const DISMISS_REASONS = ["wrong_entity", "stale", "duplicate"] as const;

export type DayGroup<T> = { key: string; label: string; span: string; items: T[] };
/** Splits a newest-first list into Today, Yesterday, Earlier this week, Last week, Earlier this month, then one group per month. */
export function groupByDay<T extends { happened: string }>(items: T[], now: Date = new Date()): DayGroup<T>[] {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const today = day(now);
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const lastMonday = new Date(monday);
  lastMonday.setDate(monday.getDate() - 7);
  const locale = getLanguage() === "pl" ? "pl-PL" : "en-GB";
  const short = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
  const long = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long" });
  const month = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" });
  const groups: (DayGroup<T> & { first: Date; last: Date })[] = [];
  for (const item of items) {
    const d = day(new Date(item.happened));
    if (isNaN(d.getTime())) continue;
    const diff = Math.round((today.getTime() - d.getTime()) / 86_400_000);
    const [key, label] = diff <= 0 ? ["today", tr("Today")] : diff === 1 ? ["yesterday", tr("Yesterday")]
      : d >= monday ? ["week", tr("Earlier this week")] : d >= lastMonday ? ["last-week", tr("Last week")]
      : d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear() ? ["month", tr("Earlier this month")]
      : [`${d.getFullYear()}-${d.getMonth() + 1}`, month.format(d).replace(/^./, (c) => c.toUpperCase())];
    const open = groups[groups.length - 1];
    if (open && open.key === key) {
      open.items.push(item);
      if (d < open.first) open.first = d;
      if (d > open.last) open.last = d;
    } else groups.push({ key, label, span: "", items: [item], first: d, last: d });
  }
  return groups.map(({ first, last, ...g }) => ({
    ...g,
    span: g.key === "today" || g.key === "yesterday" ? long.format(first)
      : first.getTime() === last.getTime() ? short.format(first) : `${short.format(first)} – ${short.format(last)}`,
  }));
}

/** For an open tender: the buyer's own title (TED titles read "Country – Category – Own title"). */
function tenderLine(item: Finding): string {
  const parts = (item.tender?.title ?? "").split(" – ").map(p => p.trim());
  return parts.length >= 3 ? parts.slice(2).join(" – ") : (item.tender?.title ?? "").trim();
}

/** One finding as a compact card: the company, what happened in one sentence, the buying signal, the source, and what to do.
 * Details (the quote, why it matters, more sources) open in place. With onDismiss the person can mark it not relevant;
 * a dismissed finding folds into one line with an undo. */
export function FindingBrief({ item, onWatch, onDismiss, onRestore, canPeople = false, canCampaign = false }: {
  item: Finding; onWatch?: () => void; onDismiss?: (reason: string) => void; onRestore?: () => void; canPeople?: boolean; canCampaign?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const language = getLanguage();
  const org = item.organization;
  const summary = (language === "pl" ? item.summary_pl : item.summary_en) || item.summary;
  const sentence = plainSummary(summary, org.name);
  const signals = (item.signals ?? []).filter(s => !s.rule_id.startsWith("NEG-"));
  const family = item.tender ? "tender" : signalFamily(signals[0]?.rule_id);
  const company = `/accounts/${org.id}?finding=${item.id}`;
  const first = item.evidence[0];
  const source = item.tender ? { url: item.tender.url, source: tr("Official notice"), published_at: null } : first ?? item.sources[0];
  const href = safeHref(source?.url);
  const host = href ? new URL(href).hostname.replace(/^www\./, "") : null;
  const published = first?.published_at ?? item.sources[0]?.published_at ?? item.published ?? null;
  // why it matters: the finding's own reason, else what its kind of buying signal means for us
  const reason = reasonFor(item);
  const why = reason.text;
  const showWhy = Boolean(item.tender || signals.length || reason.specific);
  const showQuote = first && !same(first.claim, item.summary) && !same(first.claim, summary) && !same(first.claim, item.tender?.title) && !same(first.claim, item.tender?.scope);
  const others = item.sources.filter(s => !item.evidence.some(e => e.url === s.url) && s.url !== source?.url);
  const rule = signals[0]?.rule_id;
  const heading = `finding-${item.id}-h`;

  if (item.dismissed) return <article className="fb is-dismissed" id={`finding-${item.id}`} aria-label={tr("{}: marked not relevant", org.name)}>
    <span className="fb-node" aria-hidden="true" />
    <p className="fb-gone"><span><b>{org.name}</b> · {sentence}</span>
      {onRestore && <button type="button" className="fb-link" onClick={onRestore}>{tr("Undo")}</button>}</p>
    {onDismiss && <p className="fb-reasons"><span>{tr("Why? It helps us tune the sources.")}</span>
      {DISMISS_REASONS.map(r => <button key={r} type="button" className="fb-reason" aria-pressed={item.dismissed === r} onClick={() => onDismiss(r)}>{lab("dismiss", r)}</button>)}
    </p>}
  </article>;

  return <article className={`fb fam-${family}`} id={`finding-${item.id}`} aria-labelledby={heading}>
    <span className="fb-node" aria-hidden="true" />
    <header className="fb-top">
      <Link to={company} className="fb-co">
        <CompanyAvatar name={org.name} small logo={org.logo_url} />
        <span className="fb-co-text"><span className="fb-co-name">{org.name}</span>{org.country && <span className="fb-co-meta">{countryName(org.country)}</span>}</span>
      </Link>
      {onWatch && <button type="button" className="fb-star" aria-pressed={Boolean(item.watched)} onClick={onWatch}
        aria-label={item.watched ? tr("Stop watching {}", org.name) : tr("Watch {}", org.name)} title={item.watched ? tr("On your watchlist") : tr("Add to your watchlist")}>
        <span aria-hidden="true">{item.watched ? "★" : "☆"}</span></button>}
      <span className="fb-when"><time dateTime={item.happened}>{fmtDate(item.happened)}</time><span>{ago(item.happened)}</span></span>
    </header>
    <h3 className="fb-what" id={heading}>{item.tender ? tenderLine(item) || sentence : sentence}</h3>
    <p className="fb-tags">
      {item.tender && <span className="fb-tag fam-tender"><i aria-hidden="true" />{tr("Open tender")}<span className="fb-tag-x">{tr("closes {}", fmtDate(item.tender.deadline))}</span></span>}
      {signals.map(s => <span key={s.rule_id} className={`fb-tag fam-${signalFamily(s.rule_id)}`} title={WHY_RULE[s.rule_id] ? tr(WHY_RULE[s.rule_id]) : undefined}>
        <i aria-hidden="true" /><span className="fb-vh">{tr("Buying signal")}: </span>{signalName(s.rule_id, s.name)}</span>)}
      {!item.tender && signals.length === 0 && <span className="fb-tag fam-none"><i aria-hidden="true" />{tr("Background news, no buying signal")}</span>}
    </p>
    {showWhy && <p className="fb-why"><b>{tr("Why it matters")}</b> {why}</p>}
    <div className="fb-more" id={panel} hidden={!open}>
      {open && <>
        {showQuote && <blockquote className="fb-quote">“{first.claim}”
          {language === "en" && first.translation && <span>{tr("English translation")}: “{first.translation}”</span>}</blockquote>}
        {summary !== item.summary && <p className="fb-note">{tr("Translated summary. The original reads:")} {item.summary}</p>}
        {item.tender && <p className="fb-note"><b>{tr("Tender deadline")}</b> {fmtDateTime(item.tender.deadline)}{item.tender.scope && <> · {item.tender.scope}</>}</p>}
        <p className="fb-note">{item.tender ? tr("Confirmed by the official notice.") : tr("Checked against the original page.")}
          {item.first_seen && fmtDate(item.first_seen) !== fmtDate(item.happened) && <> {tr("We found it {}.", ago(item.first_seen))}</>}</p>
        {(item.evidence.length > 1 || others.length > 0) && <ul className="fb-also" aria-label={tr("More sources")}>
          {item.evidence.slice(1).map(e => <li key={e.claim_id}>{safeHref(e.url) ? <a href={safeHref(e.url)} target="_blank" rel="noreferrer noopener">{e.source || tr("Original source")} ↗</a> : e.source}
            {e.claim && !same(e.claim, item.summary) && <span> “{e.claim}”</span>}</li>)}
          {others.map(s => <li key={s.url}>{safeHref(s.url) ? <a href={safeHref(s.url)} target="_blank" rel="noreferrer noopener">{s.source || new URL(safeHref(s.url)!).hostname} ↗</a> : s.source}
            {s.published_at && <span className="muted"> {fmtDate(s.published_at)}</span>}</li>)}
        </ul>}
        {(canPeople || (canCampaign && rule) || item.next_action) && <p className="fb-next">
          {canPeople && <Link to={`/people?q=${encodeURIComponent(org.name)}`}>{tr("People at this company")}</Link>}
          {canCampaign && rule && <Link to={`/campaigns/new?rules=${rule}`}>{tr("Start a campaign with this signal")}</Link>}
          {item.next_action && <Link to={`/opportunities/${item.next_action.hypothesis_id}`}>{tr("Open recommended action")}</Link>}
        </p>}
      </>}
    </div>
    <footer className="fb-foot">
      <span className="fb-src" title={source?.source ?? undefined}>
        {href ? <a href={href} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "evidence" })}>{host ?? source?.source ?? tr("Original source")} <span aria-hidden="true">↗</span><span className="fb-vh"> {tr("(opens in a new tab)")}</span></a>
          : <b>{host ?? source?.source ?? tr("Original source")}</b>}
        {published && fmtDate(published) !== fmtDate(item.happened) && <span>{tr("published {}", fmtDate(published))}</span>}</span>
      <span className="fb-acts">
        <Link className="fb-btn is-main" to={company}>{tr("Open company")}</Link>
        <button type="button" className="fb-btn fb-toggle" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(!open)}>{open ? tr("Less") : tr("Details")}</button>
        {onDismiss && <button type="button" className="fb-btn is-quiet" onClick={() => onDismiss("not_relevant")}><span aria-hidden="true">✕</span> {tr("Not relevant")}</button>}
      </span>
    </footer>
  </article>;
}
