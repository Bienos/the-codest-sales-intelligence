import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { api, fmtDateTime, patch, post } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";

const VLABEL: Record<string, string> = { verified: "Checked", failed: "Could not check", blocked: "Page not allowed", waiting_budget: "Waiting for AI budget", no_event: "Nothing quotable", queued: "Waiting", fetching: "Reading", extracting: "Finding events" };
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
    <p className="adm-lead">{tr("Follow each source link from discovery to a verified finding. Open the details to see what was checked and why a check stopped.")}</p>
    {queue && <div className="adm-panel" aria-label={tr("Verification queue")}>
      <dl className="adm-facts">
        <div><dt>{tr("Pages pending verification")}</dt><dd>{number(queue.pending)}</dd></div>
        <div><dt>{tr("Oldest pending page")}</dt><dd>{queue.oldest_pending ? fmtDateTime(queue.oldest_pending) : tr("None")}</dd></div>
        <div><dt>{tr("Last successful page verification")}</dt><dd>{queue.last_verified ? fmtDateTime(queue.last_verified) : tr("None")}</dd></div>
        {documents && <div><dt>{tr("Collected documents awaiting extraction")}</dt><dd>{number(documents.pending)}</dd></div>}
      </dl>
      {documents?.oldest_pending && <p className="small muted">{tr("Oldest collected document awaiting extraction {}", fmtDateTime(documents.oldest_pending))}</p>}
      {queue.provider_billing > 0 && <p className="coverage-warning" role="status">{tr("{} page checks were blocked by Anthropic credit.", number(queue.provider_billing))}
        {queue.next_provider_retry && <> {tr("Next retry due {}", fmtDateTime(queue.next_provider_retry))}</>}</p>}
      {queue.held_for_recovery > 0 && <p className="small muted">{tr("{} older checks will resume in batches after extraction succeeds.", number(queue.held_for_recovery))}</p>}
      {queue.waiting_budget > 0 && <p className="small muted">{tr("{} pages are waiting for the configured extraction budget or worker setup.", number(queue.waiting_budget))}</p>}
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
    {result?.items.length === 0 && <p className="quiet">{tr("Nothing to show: no checks match these filters.")}</p>}
    <ul className="adm-items">
      {result?.items.map((v: any) => <li className={`adm-item ${vk(v.status)}`} key={v.id}>
        <div className="row spread start">
          <div><b>{v.organization || tr("No company matched")}</b><div className="small muted">{tr(ORIGINS[v.origin] ?? "Submitted link")} · {fmtDateTime(v.updated_at ?? v.created_at)}</div></div>
          <Badge kind={vk(v.status)}>{tr(VLABEL[v.status] ?? humanize(v.status))}</Badge>
        </div>
        {v.status === "verified" && <p className="small">{tr("Findings created: {} · Signals created: {}", number(v.events_created ?? 0), number(v.result?.signals ?? 0))}</p>}
        <div className="row spread">
          {/^https?:/i.test(v.url) && <a href={v.url} target="_blank" rel="noreferrer noopener">{tr("Original source")} ↗</a>}
          {(v.status === "failed" || v.status === "waiting_budget") && <button disabled={retrying !== null} onClick={() => retry(v.id)}>{tr("Retry")}</button>}
        </div>
        {(v.claimed_text || v.result?.reason || v.plan_id) && <details className="small">
          <summary>{tr("Check details")}</summary>
          {v.claimed_text && <p>{v.claimed_text}</p>}
          {v.result?.reason && <p>{tr(v.result.reason)}</p>}
          {v.plan_id && <p className="muted">{tr("Search plan")} · {v.plan_id}</p>}
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
  if (err && !d) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const b = d.budget;
  return (
    <>
      <div className="adm-panel">
        <div className="row spread">
          <div><h3 className="adm-h4 mt-0">{tr("Saved web searches")}</h3>
            <p className="m-0">{d.plans.length}{" " + tr("saved searches across") + " "}{new Set(d.plans.map((p: any) => p.theme)).size}{" " + tr("topics and") + " "}{new Set(d.plans.map((p: any) => p.geography)).size}{" " + tr("regions. Each runs at most once every") + " "}{d.interval_days}{" " + tr("days, with up to") + " "}{d.max_searches_per_run}{" " + tr("searches per run, so spend stays within the monthly limit.")}</p></div>
          <button className="primary" onClick={() => run()}>{tr("Run due searches now")}</button>
        </div>
        <div className="row small mt-3">
          <Badge kind={d.cap_usd > 0 ? "ok" : "bad"}>{tr("Monthly limit") + " "}{d.cap_usd > 0 ? `$${d.cap_usd}` : tr("not set")}</Badge>
          {b && <Badge>{tr("Spent $")}{Number(b.spent).toFixed(2)}{" " + tr("· set aside $")}{Number(b.reserved).toFixed(2)}</Badge>}
          <Badge kind={d.key_present_on_api ? "ok" : "warn"}>{d.key_present_on_api ? tr("Key connected") : tr("Key not visible here (set on the worker)")}</Badge>
        </div>
        <p className="small muted mb-0">{d.note}{" " + tr("Each search's cost comes from Exa's own response and is recorded under Usage and cost.")}</p>
        {msg && <div className="okmsg mt-3" role="status">{tr(msg)}</div>}<Err e={err} />
      </div>
      {d.plans.length === 0 && <p className="quiet">{tr("Nothing to show: no saved searches yet.")}</p>}
      {d.plans.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Saved web searches")}><table><thead><tr><th>{tr("Search")}</th><th>{tr("Signal")}</th><th>{tr("Last run")}</th><th>{tr("Runs")}</th><th>{tr("Results")}</th><th>{tr("Sent to check")}</th><th>{tr("Spent")}</th><th>{tr("On")}</th><th><span className="sr">{tr("Action")}</span></th></tr></thead><tbody>
        {d.plans.map((p: any) => <tr key={p.plan_id}><td><b>{humanize(p.theme)}</b> · {p.geography}<div className="small muted">{p.query}</div>{p.last_error && <div className="small"><Badge kind="bad">{p.last_error === "rate_limited" ? tr("The search provider asked us to slow down") : humanize(p.last_error)}</Badge></div>}</td>
          <td>{p.signal_rule_id}</td><td>{p.last_run_at ? fmtDateTime(p.last_run_at) : tr("never")}</td><td>{p.runs}</td><td>{p.results_total}</td><td>{p.requests_created}</td><td>${Number(p.cost_total_usd).toFixed(2)}</td>
          <td><input type="checkbox" aria-label={tr("Enable {} {}", p.theme, p.geography)} checked={p.enabled} onChange={(e) => patch(`/api/admin/discovery/plans/${p.plan_id}?enabled=${e.target.checked}`).then(load).catch(setErr)} /></td>
          <td><button className="ghost" onClick={() => run([p.plan_id])}>{tr("Run")}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}
