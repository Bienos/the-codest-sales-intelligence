import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime } from "../api";
import { getLanguage, number, tr } from "../locale";
import { countryName } from "../pages/targetMarket";
import { Badge, Err, Skeleton, lab } from "./ui";

type Source = { url: string; source: string | null };
type Play = { play_id: string; offers: string[] };
type Finding = {
  id: string; event_ids: string[]; type: string; summary: string; language?: string;
  summary_en?: string; summary_pl?: string; happened: string;
  organization: { id: string; name: string; country: string; qualified?: boolean };
  tender: { title: string; url: string; deadline: string; checked_at: string; notice_id: string; scope?: string | null } | null;
  evidence: Array<Source & { claim: string; claim_id: string; translation?: string | null }>;
  sources: Source[];
  fit: { plays: Play[] };
  why?: { level: string; service: string; reason: string };
  next_action?: { hypothesis_id: string; action_type: string; has_owner: boolean; owner_name?: string; due_at?: string } | null;
};
type Lead = {
  id: string; url: string; origin: string; status: string; claimed_text: string | null;
  updated_at: string | null; event_id: string | null;
};
type Coverage = { monitored_sources: number; unhealthy: unknown[]; pending_validation_sources: number; extraction_waiting_for_credit?: boolean };
type Result = { items: Finding[]; leads: Lead[]; has_more: boolean; coverage: Coverage };

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

export function FindingCard({ item, onAccount = false }: { item: Finding; onAccount?: boolean }) {
  const language = getLanguage();
  const summary = (language === "pl" ? item.summary_pl : item.summary_en) || item.summary;
  const hiring = item.type === "engineering_job_opened" || item.type === "contractor_job_opened";
  const translated = summary !== item.summary;
  const play = item.fit.plays.find(p => WHY[p.play_id]);
  const service = item.why?.service ? SERVICES[item.why.service] : undefined;
  const reason = item.tender ? tr("The notice requests engineering services. Review scope and supplier eligibility before bidding.")
    : hiring ? tr("Engineering hiring can indicate a need for skills or capacity. Check the team and whether it uses external partners.")
    : play ? tr(WHY[play.play_id])
    : item.why?.level === "strong" && service ? tr("This finding may fit {}. Confirm the workstream and supplier need.", tr(service))
    : tr("Confirm the workstream and whether the company needs an external team.");
  const deadline = item.tender ? new Intl.DateTimeFormat(language, {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
  }).format(new Date(item.tender.deadline)) : null;
  const source = item.tender ?? item.evidence[0] ?? item.sources[0];
  return <article className={`fcard finding-card${item.tender ? " finding-tender" : ""}`} id={`finding-${item.id}`}>
    <div className="row spread start">
      <div className="row small">
        <Badge kind={item.tender ? "bad" : "info"}>{item.tender ? tr("Open RFP or tender") : lab("event", item.type)}</Badge>
        {!onAccount && <Link to={`/accounts/${item.organization.id}?finding=${item.id}`}><b>{item.organization.name}</b></Link>}
        <span className="muted">{countryName(item.organization.country)}</span>
        {item.organization.qualified === false && <Badge kind="warn">{tr("Ownership to check")}</Badge>}
      </div>
      <time className="small muted" dateTime={item.happened}>{fmtDate(item.happened)}</time>
    </div>
    <h3 className="fcard-t">{summary}</h3>
    {translated && <details className="small finding-details"><summary>{tr("Translated summary · view original")}</summary><p>{item.summary}</p></details>}
    {item.tender && <p className="small"><b>{tr("Tender deadline")}</b> {deadline} UTC
      <span className="muted"> · {tr("Status checked {}.", fmtDateTime(item.tender.checked_at))}</span></p>}
    {item.tender?.scope && <p className="small"><b>{tr("Scope")}</b> {item.tender.scope}</p>}
    <p className="finding-fit"><b>{tr("Why it fits The Codest")}</b><br />{reason}</p>
    <div className="row spread">
      {source && <a href={source.url} target="_blank" rel="noreferrer noopener">{tr("Original source")} ↗</a>}
      {onAccount && <Link to={`/feed?finding=${item.id}`}>{tr("Open in findings archive")}</Link>}
    </div>
    <p className="small muted">{item.tender ? tr("The source confirms the notice. Supplier eligibility still needs checking.")
      : tr("The source confirms the reported event. The need for outside help still needs checking.")}</p>
    <p className="small"><b>{tr("Next step")}</b>{" "}{item.next_action ? <Link to={`/opportunities/${item.next_action.hypothesis_id}`}>{tr("Open recommended action")}</Link> : item.tender ? tr("Check how to submit and whether we meet the supplier requirements.")
      : item.organization.qualified === false ? tr("Confirm company ownership and qualify demand for outside engineering.")
      : tr("Identify the delivery owner and qualify demand for outside engineering.")}</p>
    <dl className="account-profile-facts small">
      <div><dt>{tr("Action owner")}</dt><dd>{item.next_action?.owner_name || tr(item.next_action?.has_owner ? "Assigned in Pipedrive" : "Unassigned")}</dd></div>
      <div><dt>{tr("Follow-up due")}</dt><dd>{item.next_action?.due_at ? fmtDateTime(item.next_action.due_at) : tr("Not scheduled")}</dd></div>
    </dl>
    {item.evidence.length > 0 && <details className="finding-details">
      <summary>{tr("View evidence")}</summary>
      {item.evidence.map(e => <blockquote key={e.claim_id} className="fquote">
        <span>{tr("Original wording")}: “{e.claim}”</span>
        {language === "en" && e.translation && <span className="trans">{tr("English translation")}: “{e.translation}”</span>}
        <cite><a href={e.url} target="_blank" rel="noreferrer noopener">{e.source || tr("Original source")} ↗</a></cite>
      </blockquote>)}
    </details>}
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
  return <div className="row" aria-label={tr("Findings pages")}>
    <button type="button" disabled={page <= 1} onClick={() => change(page - 1)}>{tr("Previous")}</button>
    <span>{tr("Page {}", page)}</span>
    <button type="button" disabled={!hasMore} onClick={() => change(page + 1)}>{tr("Next")}</button>
  </div>;
}
