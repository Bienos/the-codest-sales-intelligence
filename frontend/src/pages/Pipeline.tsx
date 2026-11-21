import { useEffect, useState } from "react";
import { api, fmtDateTime, patch, post } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const vk = (s: string) => (s === "verified" ? "ok" : s === "failed" || s === "blocked" ? "bad" : s === "waiting_budget" ? "warn" : s === "no_event" ? "" : "info");

export function Verification() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [status, setStatus] = useState(""); const [origin, setOrigin] = useState("");
  const load = () => api(`/api/verifications?status=${status}&origin=${origin}&limit=100`).then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [status, origin]);
  return (
    <>
      <p className="small muted" style={{ marginTop: 0 }}>Each row is a claim from your sheets or a search hit. The platform fetches the original page, asks the model to extract events with verbatim quotes, and keeps only events whose quote appears on the page.</p>
      {d && <div className="chips" style={{ marginBottom: 12 }}>{Object.entries(d.counts).map(([k, v]: any) => <button key={k} className={`chip ${status === k ? "on" : ""}`} onClick={() => setStatus(status === k ? "" : k)}>{humanize(k)} <b>{v}</b></button>)}
        <select aria-label="Origin" value={origin} onChange={(e) => setOrigin(e.target.value)}><option value="">All origins</option><option value="sheet_signal">From your sheets</option><option value="exa_discovery">From discovery</option></select></div>}
      <Err e={err} />
      {d && d.items.length === 0 && <div className="empty"><b>Nothing here yet</b>Verify a prospect from Today, or run discovery.</div>}
      {d && d.items.length > 0 && <div className="card table-wrap"><table><thead><tr><th>When</th><th>Account and claim</th><th>Origin</th><th>Status</th><th>Result</th><th></th></tr></thead><tbody>
        {d.items.map((v: any) => <tr key={v.id}><td>{fmtDateTime(v.created_at)}</td>
          <td><b>{v.organization ?? "Unmatched"}</b><div className="small">{v.claimed_text}</div><a className="small" href={v.url} target="_blank" rel="noreferrer noopener">{v.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a></td>
          <td>{v.origin === "sheet_signal" ? "Sheet" : "Discovery"}</td><td><Badge kind={vk(v.status)}>{humanize(v.status)}</Badge></td>
          <td className="small">{v.status === "verified" ? `${v.events_created} event${v.events_created === 1 ? "" : "s"}, ${v.result?.signals ?? 0} signal${v.result?.signals === 1 ? "" : "s"}` : v.result?.reason ?? (v.status === "no_event" ? "No quoted event on the page" : "")}</td>
          <td>{(v.status === "failed" || v.status === "waiting_budget") && <button className="ghost" onClick={() => post(`/api/verifications/${v.id}/retry`).then(load).catch(setErr)}>Retry</button>}</td></tr>)}</tbody></table></div>}
    </>
  );
}

export function Discovery() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const load = () => api("/api/admin/discovery").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = async (ids?: string[]) => { setErr(null); try { await post("/api/admin/discovery/run", { plan_ids: ids ?? null }); setMsg("Queued. The worker runs it within a few seconds and results appear under Verification."); load(); } catch (e) { setErr(e); } };
  if (err && !d) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  const b = d.budget;
  return (
    <>
      <div className="card">
        <div className="row spread">
          <div><div className="eyebrow">Exa discovery</div>
            <p style={{ margin: 0 }}>{d.plans.length} search plans across {new Set(d.plans.map((p: any) => p.theme)).size} themes and {new Set(d.plans.map((p: any) => p.geography)).size} regions. Each plan runs at most once every {d.interval_days} days, up to {d.max_searches_per_run} searches a run, so spend stays inside the monthly cap.</p></div>
          <button className="primary" onClick={() => run()}>Run due plans now</button>
        </div>
        <div className="row small" style={{ marginTop: 12 }}>
          <Badge kind={d.cap_usd > 0 ? "ok" : "bad"}>Monthly cap {d.cap_usd > 0 ? `$${d.cap_usd}` : "not set"}</Badge>
          {b && <Badge>Spent ${Number(b.spent).toFixed(3)} · reserved ${Number(b.reserved).toFixed(3)}</Badge>}
          <Badge kind={d.key_present_on_api ? "ok" : "warn"}>{d.key_present_on_api ? "API key present" : "Key is set on the worker only"}</Badge>
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>{d.note} Cost per search is read from Exa's own response and recorded in Usage.</p>
        {msg && <div className="okmsg" role="status" style={{ marginTop: 12 }}>{msg}</div>}<Err e={err} />
      </div>
      <div className="card table-wrap"><table><thead><tr><th>Plan</th><th>Rule</th><th>Last run</th><th>Runs</th><th>Hits</th><th>Queued</th><th>Spent</th><th>On</th><th></th></tr></thead><tbody>
        {d.plans.map((p: any) => <tr key={p.plan_id}><td><b>{humanize(p.theme)}</b> · {p.geography}<div className="small muted">{p.query}</div>{p.last_error && <div className="small"><Badge kind="bad">{p.last_error}</Badge></div>}</td>
          <td>{p.signal_rule_id}</td><td>{p.last_run_at ? fmtDateTime(p.last_run_at) : "never"}</td><td>{p.runs}</td><td>{p.results_total}</td><td>{p.requests_created}</td><td>${Number(p.cost_total_usd).toFixed(3)}</td>
          <td><input type="checkbox" aria-label={`Enable ${p.theme} ${p.geography}`} checked={p.enabled} onChange={(e) => patch(`/api/admin/discovery/plans/${p.plan_id}?enabled=${e.target.checked}`).then(load).catch(setErr)} /></td>
          <td><button className="ghost" onClick={() => run([p.plan_id])}>Run</button></td></tr>)}</tbody></table></div>
    </>
  );
}
