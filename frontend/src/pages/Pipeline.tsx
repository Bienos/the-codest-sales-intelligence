import { useEffect, useState } from "react";
import { api, fmtDateTime, patch, post } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";

const VLABEL: Record<string, string> = { verified: "Checked", failed: "Could not check", blocked: "Page not allowed", waiting_budget: "Waiting for AI budget", no_event: "Nothing quotable", queued: "Waiting", fetching: "Reading", extracting: "Finding events" };
const vk = (s: string) => (s === "verified" ? "ok" : s === "failed" || s === "blocked" ? "bad" : s === "waiting_budget" ? "warn" : s === "no_event" ? "" : "info");

export function Verification() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [status, setStatus] = useState(""); const [origin, setOrigin] = useState("");
  const load = () => api(`/api/verifications?status=${status}&origin=${origin}&limit=100`).then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [status, origin]);
  return (
    <>
      <p className="small muted mt-0">Each row is a page we were asked to check, from your workbooks or from a search. We read the original page, pull out events with word-for-word quotes, and keep only those whose quote is really on the page.</p>
      {d && <div className="chips mb-3">{Object.entries(d.counts).map(([k, v]: any) => <button key={k} className={`chip ${status === k ? "on" : ""}`} onClick={() => setStatus(status === k ? "" : k)}>{VLABEL[k] ?? humanize(k)} <b>{v}</b></button>)}
        <select aria-label="Origin" value={origin} onChange={(e) => setOrigin(e.target.value)}><option value="">All origins</option><option value="sheet_signal">From your workbooks</option><option value="exa_discovery">From web search</option><option value="seller_miss">Reported by sellers</option></select></div>}
      <Err e={err} />
      {d && d.items.length === 0 && <div className="empty"><b>Nothing here yet</b>Check a company from Today, or run a web search under Admin, Web search.</div>}
      {d && d.items.length > 0 && <div className="card table-wrap"><table><thead><tr><th>When</th><th>Account and page</th><th>Came from</th><th>Status</th><th>Result</th><th><span className="sr">Action</span></th></tr></thead><tbody>
        {d.items.map((v: any) => <tr key={v.id}><td>{fmtDateTime(v.created_at)}</td>
          <td><b>{v.organization ?? "No company matched"}</b><div className="small">{v.claimed_text}</div><a className="small" href={v.url} target="_blank" rel="noreferrer noopener">{v.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a></td>
          <td>{v.origin === "sheet_signal" ? "Your workbooks" : v.origin === "seller_miss" ? "Seller report" : "Web search"}</td><td><Badge kind={vk(v.status)}>{VLABEL[v.status] ?? humanize(v.status)}</Badge></td>
          <td className="small">{v.status === "verified" ? `${v.events_created} ${v.events_created === 1 ? "finding" : "findings"}, ${v.result?.signals ?? 0} ${v.result?.signals === 1 ? "signal" : "signals"}` : v.result?.reason ?? (v.status === "no_event" ? "Nothing on the page we could quote" : "")}</td>
          <td>{(v.status === "failed" || v.status === "waiting_budget") && <button className="ghost" onClick={() => post(`/api/verifications/${v.id}/retry`).then(load).catch(setErr)}>Retry</button>}</td></tr>)}</tbody></table></div>}
    </>
  );
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
      <div className="card">
        <div className="row spread">
          <div><div className="eyebrow">Web search (Exa)</div>
            <p className="m-0">{d.plans.length} saved searches across {new Set(d.plans.map((p: any) => p.theme)).size} topics and {new Set(d.plans.map((p: any) => p.geography)).size} regions. Each runs at most once every {d.interval_days} days, with up to {d.max_searches_per_run} searches per run, so spend stays within the monthly limit.</p></div>
          <button className="primary" onClick={() => run()}>Run due searches now</button>
        </div>
        <div className="row small mt-3">
          <Badge kind={d.cap_usd > 0 ? "ok" : "bad"}>Monthly limit {d.cap_usd > 0 ? `$${d.cap_usd}` : "not set"}</Badge>
          {b && <Badge>Spent ${Number(b.spent).toFixed(2)} · set aside ${Number(b.reserved).toFixed(2)}</Badge>}
          <Badge kind={d.key_present_on_api ? "ok" : "warn"}>{d.key_present_on_api ? "Key connected" : "Key not visible here (set on the worker)"}</Badge>
        </div>
        <p className="small muted mb-0">{d.note} Each search's cost comes from Exa's own response and is recorded under Usage and cost.</p>
        {msg && <div className="okmsg mt-3" role="status">{msg}</div>}<Err e={err} />
      </div>
      <div className="card table-wrap"><table><thead><tr><th>Search</th><th>Signal</th><th>Last run</th><th>Runs</th><th>Results</th><th>Sent to check</th><th>Spent</th><th>On</th><th><span className="sr">Action</span></th></tr></thead><tbody>
        {d.plans.map((p: any) => <tr key={p.plan_id}><td><b>{humanize(p.theme)}</b> · {p.geography}<div className="small muted">{p.query}</div>{p.last_error && <div className="small"><Badge kind="bad">{p.last_error === "rate_limited" ? "The search provider asked us to slow down" : humanize(p.last_error)}</Badge></div>}</td>
          <td>{p.signal_rule_id}</td><td>{p.last_run_at ? fmtDateTime(p.last_run_at) : "never"}</td><td>{p.runs}</td><td>{p.results_total}</td><td>{p.requests_created}</td><td>${Number(p.cost_total_usd).toFixed(2)}</td>
          <td><input type="checkbox" aria-label={`Enable ${p.theme} ${p.geography}`} checked={p.enabled} onChange={(e) => patch(`/api/admin/discovery/plans/${p.plan_id}?enabled=${e.target.checked}`).then(load).catch(setErr)} /></td>
          <td><button className="ghost" onClick={() => run([p.plan_id])}>Run</button></td></tr>)}</tbody></table></div>
    </>
  );
}
