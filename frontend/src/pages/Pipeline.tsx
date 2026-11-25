import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { api, patch, post } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";
import { useLoad, when } from "./PipelineStatus";

const VLABEL: Record<string, string> = { verified: "Checked", failed: "Could not check", blocked: "Page not allowed", waiting_budget: "Waiting for AI budget", no_event: "Nothing quotable", queued: "Waiting", fetching: "Reading", extracting: "Reading the page" };
const vk = (s: string) => (s === "verified" ? "ok" : s === "failed" || s === "blocked" ? "bad" : s === "waiting_budget" ? "warn" : s === "no_event" ? "" : "info");

const ORIGINS: Record<string, string> = {
  sheet_signal: "From your workbooks", exa_discovery: "From web search",
  seller_miss: "Reported by sellers", manual: "Submitted link",
};

export function Verification() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [status, setStatus] = useState("");
  const [origin, setOrigin] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const filters = new URLSearchParams({ status, origin, limit: "100" });
  const query = filters.toString();
  useEffect(() => {
    const request = new AbortController();
    const load = () => api(`/api/verifications?${query}`, { signal: request.signal })
      .then(data => { if (!request.signal.aborted) { setD({ query, data }); setErr(null); } })
      .catch(error => { if (!request.signal.aborted) setErr(error); });
    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => { request.abort(); clearInterval(timer); };
  }, [query, revision]);
  const result = d?.query === query ? d.data : null;
  const counts = d?.data.counts;
  const queue = d?.data.queue;
  const documents = d?.data.documents;
  const retry = async (id: string) => {
    setRetrying(id);
    try { await post(`/api/verifications/${id}/retry`); setRevision(n => n + 1); }
    catch (error) { setErr(error); }
    finally { setRetrying(null); }
  };
  return <>
    <p className="adm-lead">{tr("Pages that a web search found or a seller sent are opened and read here. Only what the page itself says becomes a finding. Open a row to see what was checked, or why a check stopped.")}</p>
    {queue && <div className="adm-panel" aria-label={tr("Verification queue")}>
      <dl className="adm-facts">
        <div><dt>{tr("Pages waiting to be checked")}</dt><dd>{number(queue.pending)}</dd></div>
        <div><dt>{tr("Waiting since")}</dt><dd>{queue.oldest_pending ? when(queue.oldest_pending) : tr("None")}</dd></div>
        <div><dt>{tr("Last page checked")}</dt><dd>{queue.last_verified ? when(queue.last_verified) : tr("None")}</dd></div>
        {documents && <div><dt>{tr("Pages read, not yet turned into findings")}</dt><dd>{number(documents.pending)}</dd></div>}
      </dl>
      {documents?.oldest_pending && <p className="small muted">{tr("The oldest has waited since {}.", when(documents.oldest_pending))}</p>}
      {queue.provider_billing > 0 && <p className="coverage-warning" role="status">{tr("{} page checks are blocked because the AI account is out of credit.", number(queue.provider_billing))}
        {queue.next_provider_retry && <> {tr("Next retry due {}", when(queue.next_provider_retry))}</>}</p>}
      {queue.held_for_recovery > 0 && <p className="small muted">{tr("{} older checks will start again in small batches once the AI reads pages again.", number(queue.held_for_recovery))}</p>}
      {queue.waiting_budget > 0 && <p className="small muted">{tr("{} pages are waiting for the monthly AI limit or for the worker to be set up.", number(queue.waiting_budget))}</p>}
    </div>}
    <div className="verification-filters">
      <label>{tr("Status")}<select value={status} onChange={e => setStatus(e.target.value)}>
        <option value="">{tr("All statuses")}</option>
        {Object.entries(VLABEL).map(([key, label]) => <option key={key} value={key}>{tr(label)}{counts?.[key] !== undefined ? ` (${number(counts[key])})` : ""}</option>)}
      </select></label>
      <label>{tr("Origin")}<select value={origin} onChange={e => setOrigin(e.target.value)}>
        <option value="">{tr("All origins")}</option>
        {Object.entries(ORIGINS).map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
      </select></label>
      {result && <p className="small muted m-0">{tr("Showing {} most recent checks", number(result.items.length))}</p>}
    </div>
    <Err e={err} onRetry={() => setRevision(n => n + 1)} />
    {!result && !err && <Skeleton />}
    {result?.items.length === 0 && <p className="quiet">{status || origin ? tr("No page check matches these filters. Choose All statuses and All origins.") : tr("No page has been checked yet. Pages arrive here from saved web searches, or when a seller sends a link on the Market feed.")}</p>}
    <ul className="adm-items">
      {result?.items.map((v: any) => <li className={`adm-item ${vk(v.status)}`} key={v.id}>
        <div className="row spread start">
          <div><b>{v.organization || tr("No company matched")}</b><div className="small muted">{tr(ORIGINS[v.origin] ?? "Submitted link")} · {when(v.updated_at ?? v.created_at)}</div></div>
          <Badge kind={vk(v.status)}>{tr(VLABEL[v.status] ?? humanize(v.status))}</Badge>
        </div>
        {v.status === "verified" && <p className="small">{tr("{} findings and {} buying signals came from this page.", number(v.events_created ?? 0), number(v.result?.signals ?? 0))}</p>}
        <div className="row spread">
          {/^https?:/i.test(v.url) && <a href={v.url} target="_blank" rel="noreferrer noopener">{tr("Original source")} ↗</a>}
          {(v.status === "failed" || v.status === "waiting_budget") && <button disabled={retrying !== null} onClick={() => retry(v.id)}>{tr("Retry")}</button>}
        </div>
        {(v.claimed_text || v.result?.reason || v.plan_id) && <details className="small">
          <summary>{tr("Check details")}</summary>
          {v.claimed_text && <p>{v.claimed_text}</p>}
          {v.result?.reason && <p>{tr(v.result.reason)}</p>}
          {v.plan_id && <p className="muted">{tr("Found by a saved web search.")}</p>}
        </details>}
      </li>)}
    </ul>
  </>;
}

export function Discovery() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const load = () => api("/api/admin/discovery").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = async (ids?: string[]) => { setErr(null); try { await post("/api/admin/discovery/run", { plan_ids: ids ?? null }); setMsg("Started. It should begin within a few seconds, and results appear under Pages being checked."); load(); } catch (e) { setErr(e); } };
  const sig = useLoad("/api/library/signals");
  const signal = (id: string) => (sig.d?.items ?? []).find((x: any) => x.rule_id === id)?.signal ?? tr("A buying signal");
  if (err && !d) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const b = d.budget;
  return (
    <>
      <p className="adm-lead">{tr("Saved web searches look for new pages about your markets and accounts. Every page they find is checked before it can become a finding, and they stay within the monthly web search limit.")}</p>
      <div className="adm-panel">
        <div className="row spread">
          <div><h3 className="adm-h4 mt-0">{tr("Saved web searches")}</h3>
            <p className="m-0">{d.plans.length}{" " + tr("saved searches across") + " "}{new Set(d.plans.map((p: any) => p.theme)).size}{" " + tr("topics and") + " "}{new Set(d.plans.map((p: any) => p.geography)).size}{" " + tr("regions. Each runs at most once every") + " "}{d.interval_days}{" " + tr("days, with up to") + " "}{d.max_searches_per_run}{" " + tr("searches per run, so spend stays within the monthly limit.")}</p></div>
          <button className="primary" onClick={() => run()}>{tr("Run due searches now")}</button>
        </div>
        <div className="row small mt-3">
          <Badge kind={d.cap_usd > 0 ? "ok" : "bad"}>{tr("Monthly limit") + " "}{d.cap_usd > 0 ? `$${d.cap_usd}` : tr("not set")}</Badge>
          {b && <Badge>{tr("Spent $")}{Number(b.spent).toFixed(2)}{" " + tr("· set aside $")}{Number(b.reserved).toFixed(2)}</Badge>}
          <Badge kind={d.key_present_on_api ? "ok" : "info"}>{d.key_present_on_api ? tr("Web search key connected") : tr("Web search key: set on the worker")}</Badge>
        </div>
        <p className="small muted mb-0">{tr("A search result is only a lead: its page is opened and checked first. Each search's cost is what Exa reports, and is listed under Usage & cost, Every paid call.")}</p>
        {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
      </div>
      {d.plans.length === 0 && <p className="quiet">{tr("No saved web searches yet. They are created on the server for your markets and priority accounts; once there, they run by themselves.")}</p>}
      {d.plans.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Saved web searches")}><table className="cards adm-t"><thead><tr><th>{tr("Search")}</th><th>{tr("Looks for")}</th><th>{tr("Last run")}</th><th className="num">{tr("Runs")}</th><th className="num">{tr("Results")}</th><th className="num">{tr("Sent to check")}</th><th className="num">{tr("Spent")}</th><th>{tr("On")}</th><th><span className="sr">{tr("Action")}</span></th></tr></thead><tbody>
        {d.plans.map((p: any) => <tr key={p.plan_id}><td data-label={tr("Search")} className="wide"><b>{tr(humanize(p.theme))}</b> · {p.geography}<div className="small muted">{p.query}</div>{p.last_error && <div className="small"><Badge kind="bad">{p.last_error === "rate_limited" ? tr("The search provider asked us to slow down") : humanize(p.last_error)}</Badge></div>}</td>
          <td data-label={tr("Looks for")} className="small">{signal(p.signal_rule_id)}</td><td data-label={tr("Last run")} className="nowrap">{p.last_run_at ? when(p.last_run_at) : tr("Not run yet")}</td><td data-label={tr("Runs")} className="num">{number(p.runs)}</td><td data-label={tr("Results")} className="num">{number(p.results_total)}</td><td data-label={tr("Sent to check")} className="num">{number(p.requests_created)}</td><td data-label={tr("Spent")} className="num">${Number(p.cost_total_usd).toFixed(2)}</td>
          <td data-label={tr("On")}><input type="checkbox" aria-label={tr("Switch on {} {}", humanize(p.theme), p.geography)} checked={p.enabled} onChange={(e) => patch(`/api/admin/discovery/plans/${p.plan_id}?enabled=${e.target.checked}`).then(load).catch(setErr)} /></td>
          <td><button className="ghost" onClick={() => run([p.plan_id])}>{tr("Run now")}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}
