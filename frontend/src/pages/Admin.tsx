import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, fmtDateTime, post } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const hk = (s: string) => (s === "healthy" ? "ok" : s === "unconfigured" || s === "paused" ? "" : "bad");

function Load({ path, children }: { path: string; children: (d: any, reload: () => void) => any }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const reload = () => api(path).then(setD).catch(setErr);
  useEffect(() => { reload(); }, [path]);
  if (err) return <Err e={err} />;
  return d ? children(d, reload) : <p className="muted">Loading…</p>;
}

const Sources = () => <Load path="/api/admin/sources">{(d) => (
  <div className="card table-wrap"><table><thead><tr><th>Source</th><th>Tier</th><th>Family</th><th>Status</th><th>Health</th><th>Last success</th><th>Failures 7d</th><th>Certified</th></tr></thead><tbody>
    {d.items.map((s: any) => <tr key={s.source_id}><td>{s.display_name}<div className="small muted">{s.source_id}</div></td><td>{s.source_tier}</td><td>{s.source_family}</td><td><Badge kind={s.status === "active" ? "ok" : s.status === "pending_connector_validation" ? "warn" : "bad"}>{humanize(s.status)}</Badge></td>
      <td><Badge kind={hk(s.health ?? "unconfigured")}>{s.health ?? "unconfigured"}</Badge>{s.error_class && <div className="small">{s.error_class}</div>}</td><td>{fmtDateTime(s.last_success_at)}</td><td>{s.failures_7d}</td><td>{s.certified ? "yes" : "no"}</td></tr>)}</tbody></table>
    <p className="small muted">A source is only polled once its connector certification record exists. “Pending” means the integration is built but NOT verified against the live provider.</p></div>)}</Load>;

const Identity = () => <Load path="/api/admin/identity-reviews">{(d, reload) => <IdentityList d={d} reload={reload} />}</Load>;
function IdentityList({ d, reload }: { d: any; reload: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [note, setNote] = useState("");
  const act = async (id: string, decision: string, org?: string) => { try { await post(`/api/admin/identity-reviews/${id}/resolve`, { decision, organization_id: org, note }); reload(); } catch (e) { setErr(e); } };
  return <><Err e={err} /><p className="small muted">Parent and subsidiary are linked by edges, never merged. Every decision needs a reason and is audited.</p><input aria-label="Reviewer note" placeholder="Reviewer note (required)" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 360, marginBottom: 10 }} />
    {d.items.length === 0 && <div className="empty">No open identity reviews.</div>}
    {d.items.map((r: any) => <div key={r.id} className="card"><b>{r.raw_company_name}</b> <Badge kind="warn">{humanize(r.reason)}</Badge> <span className="muted small">{r.sheet_title ? `from ${r.sheet_title} row ${r.row_number}` : ""}</span>
      <div className="row" style={{ marginTop: 8 }}>{r.candidates.map((c: any) => <button key={c.id} disabled={note.length < 3} onClick={() => act(r.id, "link", c.id)}>Link to {c.display_name} ({c.country_code ?? "?"})</button>)}
        <button disabled={note.length < 3} onClick={() => act(r.id, "new")}>It is a new company</button><button disabled={note.length < 3} onClick={() => act(r.id, "reject")}>Reject row</button></div></div>)}</>;
}

const Approvals = () => <Load path="/api/approvals">{(d, reload) => <ApprovalList d={d} reload={reload} />}</Load>;
function ApprovalList({ d, reload }: { d: any; reload: () => void }) {
  const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const go = async (path: string, body?: unknown) => { setErr(null); try { const r = await post(path, body); setMsg(JSON.stringify(r)); reload(); } catch (e) { setErr(e); } };
  return <><Err e={err} />{msg && <div className="okmsg">{msg}</div>}{d.items.length === 0 && <div className="empty">No approvals.</div>}
    {d.items.map((a: any) => <div key={a.id} className="card"><div className="row spread"><b>{humanize(a.kind)}: {a.operation}</b><Badge kind={a.status === "approved" ? "ok" : a.status === "pending" ? "warn" : a.status === "executed" ? "info" : ""}>{a.status}</Badge></div>
      <div className="small">Target {a.target_system} {a.target_id} · requested by {a.requested_by} · expires {fmtDateTime(a.expires_at)}</div><pre className="small" style={{ margin: "6px 0" }}>{JSON.stringify(a.payload, null, 2)}</pre><div className="small muted">{a.justification}</div>
      <div className="row" style={{ marginTop: 8 }}>{a.status === "pending" && <><button className="primary" onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: true })}>Approve exactly this</button><button onClick={() => go(`/api/approvals/${a.id}/decision`, { approve: false })}>Reject</button></>}
        {a.status === "approved" && <button onClick={() => go(`/api/approvals/${a.id}/execute`)}>Execute approved action</button>}</div></div>)}</>;
}

const Jobs = () => <Load path="/api/admin/jobs">{(d, reload) => <><div className="row">{d.by_status.map((s: any) => <Badge key={s.status} kind={s.status.includes("failed") ? "bad" : ""}>{s.status}: {s.n}</Badge>)}</div>
  <h3>Dead letters</h3>{d.dead_letters.length === 0 && <div className="empty">None.</div>}<div className="card table-wrap"><table><tbody>{d.dead_letters.map((x: any) => <tr key={x.id}><td>{x.kind}</td><td>{x.error_class}</td><td>{x.error}</td><td>{x.replayed_at ? "replayed" : <button onClick={() => post(`/api/admin/dead-letters/${x.id}/replay`).then(reload)}>Replay</button>}</td></tr>)}</tbody></table></div></>}</Load>;

const Costs = () => <Load path="/api/admin/costs">{(d) => <><p className="small muted">{d.note}</p><div className="card table-wrap"><table><thead><tr><th>Provider</th><th>Kind</th><th>Period</th><th>Cap</th><th>Reserved</th><th>Spent</th></tr></thead><tbody>{d.budgets.map((b: any, i: number) => <tr key={i}><td>{b.provider}</td><td>{b.kind}</td><td>{b.period}</td><td>{b.cap}</td><td>{b.reserved}</td><td>{b.spent}</td></tr>)}
  {d.budgets.length === 0 && <tr><td colSpan={6} className="muted">No budgets configured: no paid provider call can run.</td></tr>}</tbody></table></div></>}</Load>;

const Rules = () => <Load path="/api/admin/rules">{(d) => <><div className="row">{d.summary.map((s: any) => <Badge key={s.status} kind={s.status === "active" ? "ok" : ""}>{humanize(s.status)}: {s.n}</Badge>)}</div><div className="card table-wrap"><table><thead><tr><th>ID</th><th>Signal (original wording)</th><th>Str.</th><th>Fresh</th><th>Version</th><th>Status</th></tr></thead><tbody>
  {d.items.map((r: any) => <tr key={r.id}><td>{r.id}</td><td>{r.signal}</td><td>{r.strength}</td><td>{r.fresh_for_days}</td><td>{r.version}</td><td><Badge kind={r.status === "active" ? "ok" : ""}>{humanize(r.status)}</Badge></td></tr>)}</tbody></table></div></>}</Load>;

const Audit = () => <Load path="/api/admin/audit">{(d) => <div className="card table-wrap"><table><thead><tr><th>When</th><th>Actor</th><th>Operation</th><th>Record</th><th>Approval</th></tr></thead><tbody>{d.items.map((a: any) => <tr key={a.id}><td>{fmtDateTime(a.at)}</td><td>{a.actor}</td><td>{a.operation}</td><td>{a.record_type} {a.record_id?.slice(0, 8)}</td><td>{a.approval_id?.slice(0, 8)}</td></tr>)}</tbody></table></div>}</Load>;

const Quality = () => <Load path="/api/admin/metrics">{(d) => <><div className="gates">{Object.entries(d).filter(([, v]) => typeof v === "number").map(([k, v]: any) => <div key={k} className="gate"><b>{humanize(k)}</b><div style={{ fontSize: 22 }}>{v}</div></div>)}</div>
  <Load path="/api/admin/funnel">{(f) => <><h3>Outcome funnel</h3>{Object.keys(f.cohorts).length === 0 && <div className="empty">No outcomes recorded yet. Nothing here is inferred.</div>}{Object.entries(f.cohorts).map(([k, c]: any) => <div key={k} className="card"><b>{humanize(k)}</b> · presented {c.presented} {c.insufficient_sample && <Badge kind="warn">insufficient sample: no conclusions</Badge>}</div>)}<ul className="tight small muted">{f.caveats.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}</Load></>}</Load>;

export default function Admin({ me }: { me: { permissions: string[] } }) {
  const p = (x: string) => me.permissions.includes(x);
  return (
    <>
      <h2>Administration</h2>
      <div className="tabs">{p("sources.read") && <NavLink to="/admin">Sources</NavLink>}{p("identity.manage") && <NavLink to="/admin/identity"> Identity review</NavLink>}<NavLink to="/admin/approvals"> Approvals</NavLink>{p("admin.jobs") && <NavLink to="/admin/jobs"> Jobs</NavLink>}
        {p("admin.costs") && <NavLink to="/admin/costs"> Costs</NavLink>}{p("admin.rules") && <><NavLink to="/admin/rules"> Rules</NavLink><NavLink to="/admin/quality"> Quality</NavLink></>}{p("audit.read") && <NavLink to="/admin/audit"> Audit</NavLink>}</div>
      <Routes><Route index element={<Sources />} /><Route path="identity" element={<Identity />} /><Route path="approvals" element={<Approvals />} /><Route path="jobs" element={<Jobs />} /><Route path="costs" element={<Costs />} />
        <Route path="rules" element={<Rules />} /><Route path="quality" element={<Quality />} /><Route path="audit" element={<Audit />} /></Routes>
    </>
  );
}
