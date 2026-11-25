import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, trackSourceOpened } from "../api";
import { getLanguage, number, tr } from "../locale";
import { countryName } from "../pages/targetMarket";
import { Badge, Err, Skeleton, lab, safeHref } from "./ui";
import { SignalRef, SignalTag } from "./Signals";
import "../styles/findings.css";

type Source = { url: string; source: string | null; published_at?: string | null };
type Play = { play_id: string; offers: string[] };
type Finding = {
  id: string; event_ids: string[]; type: string; summary: string; language?: string;
  summary_en?: string; summary_pl?: string; happened: string; published?: string | null;
  organization: { id: string; name: string; country: string; qualified?: boolean };
  tender: { title: string; url: string; deadline: string; checked_at: string; notice_id: string; scope?: string | null } | null;
  evidence: Array<Source & { claim: string; claim_id: string; translation?: string | null }>;
  sources: Source[];
  fit: { plays: Play[] };
  why?: { level: string; service: string; reason: string } | null;
  signals?: SignalRef[];
  next_action?: { hypothesis_id: string; action_type: string; has_owner: boolean; owner_name?: string; due_at?: string } | null;
};
type Lead = {
  id: string; url: string; origin: string; status: string; claimed_text: string | null;
  updated_at: string | null; event_id: string | null;
};
type Coverage = { monitored_sources: number; unhealthy: unknown[]; pending_validation_sources: number; extraction_waiting_for_credit?: boolean };
type Result = { items: Finding[]; leads: Lead[]; has_more: boolean; coverage: Coverage };

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
/** A square with the company's initials. The shade is stable per name so the same company always looks the same. */
export function CompanyAvatar({ name, small = false }: { name: string; small?: boolean }) {
  const words = name.split(/[\s\-–/&,()]+/).filter((w) => w && !LEGAL.test(w));
  const letters = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? name).slice(0, 2)).toUpperCase();
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return <span className={`co-av co-av-${h % 4}${small ? " sm" : ""}`} aria-hidden="true">{letters}</span>;
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
    let token: string | null = null;
    try { token = sessionStorage.getItem("ri_token"); } catch { /* storage unavailable */ }
    if (!permissionCache || permissionCache.token !== token) {
      permissionCache = { token, perms: api<{ permissions: string[] }>("/api/me").then((m) => m.permissions).catch(() => { permissionCache = null; return []; }) };
    }
    let live = true;
    permissionCache.perms.then((p) => { if (live) setPerms(p); });
    return () => { live = false; };
  }, []);
  return (permission) => (perms ? perms.includes(permission) : undefined);
}

const same = (a?: string | null, b?: string | null) => (a ?? "").replace(/\W+/g, " ").trim().toLowerCase() === (b ?? "").replace(/\W+/g, " ").trim().toLowerCase();

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
  if (item.type === "engineering_job_opened" || item.type === "contractor_job_opened")
    return { text: tr("Engineering hiring can indicate a need for skills or capacity. Check the team and whether it uses external partners."), specific: false };
  const play = item.fit.plays.find(p => WHY[p.play_id]);
  if (play) return { text: tr(WHY[play.play_id]), specific: false };
  const service = item.why?.service ? SERVICES[item.why.service] : undefined;
  if (item.why?.level === "strong" && service) return { text: tr("This finding may fit {}. Confirm the workstream and supplier need.", tr(service)), specific: false };
  const meaning = item.signals?.find(s => s.meaning)?.meaning;
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
  const meaning = signals.find(s => s.meaning)?.meaning;
  const why = reasonFor(item);
  const org = item.organization;
  const company = `/accounts/${org.id}?finding=${item.id}`;
  const deadline = item.tender ? new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(item.tender.deadline)) : null;
  const first = item.evidence[0];
  const source = item.tender ? { url: item.tender.url, source: tr("Official notice"), published_at: null } : first ?? item.sources[0];
  const published = first?.published_at ?? item.sources[0]?.published_at ?? item.published ?? null;
  const showQuote = first && !same(first.claim, item.summary) && !same(first.claim, summary);
  const extra = Math.max(item.evidence.length - 1, 0) + item.sources.filter(s => !item.evidence.some(e => e.url === s.url)).length;
  const href = safeHref(source?.url);
  return <article className={`fd-card${item.tender ? " is-tender" : ""}`} id={`finding-${item.id}`} aria-labelledby={`finding-${item.id}-h`}>
    <header className="fd-top">
      {!onAccount && <Link to={company} className="fd-co" aria-label={tr("Open company {}", org.name)}>
        <CompanyAvatar name={org.name} />
        <span className="fd-co-text"><span className="fd-co-name">{org.name}</span><span className="fd-co-meta">{countryName(org.country)}</span></span>
      </Link>}
      {onAccount && (item.tender || signals.length === 0 ? <span className={`fd-kind${item.tender ? " is-tender" : ""}`}>{item.tender ? tr("Open RFP or tender") : lab("event", item.type)}</span> : <span />)}
      <span className="fd-when"><time dateTime={item.happened}>{fmtDate(item.happened)}</time><span>{ago(item.happened)}</span></span>
    </header>
    <h3 className="fd-head" id={`finding-${item.id}-h`}>{summary}</h3>
    <div className="fd-tags">
      {!onAccount && (item.tender || signals.length === 0) && <span className={`fd-kind${item.tender ? " is-tender" : ""}`}>{item.tender ? tr("Open RFP or tender") : lab("event", item.type)}</span>}
      {signals.map(sig => <SignalTag key={sig.rule_id} s={sig} />)}
      {org.qualified === false && <Badge kind="warn">{tr("Ownership to check")}</Badge>}
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
        {source?.source && <span>{source.source}</span>}
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
