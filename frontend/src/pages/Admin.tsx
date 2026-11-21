import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, fmtDateTime, post, put, upload, patch } from "../api";
import { Badge, Err, humanize } from "../components/ui";
import Library from "./Library";
import { KnowledgeAdmin, Pipeline } from "./PipelineStatus";
import Usage from "./Usage";
import { Discovery, Verification } from "./Pipeline";


function Load({ path, children }: { path: string; children: (d: any, reload: () => void) => any }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const reload = () => api(path).then(setD).catch(setErr);
  useEffect(() => { reload(); }, [path]);
  if (err) return <Err e={err} />;
  return d ? children(d, reload) : <p className="muted">Loading…</p>;
}

function SourceControls({ s, can, reload }: { s: any; can: boolean; reload: () => void }) {
  const [url, setUrl] = useState<string>(s.url ?? "");
  const [licence, setLicence] = useState("Public newsroom page. robots.txt allows access. Reviewed by an administrator.");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const wrap = async (f: () => Promise<any>, done: (r: any) => string) => { setBusy(true); setErr(null); setMsg(""); try { setMsg(done(await f())); reload(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const v = s.validation;
  return (
    <div className="src-ctl">
      {s.url_basis && s.url && <p className="small muted" style={{ margin: "0 0 8px" }}>URL basis: {s.url_basis}</p>}
      <div className="row">
        <input aria-label={`Page URL for ${s.display_name}`} placeholder={`https://… on ${s.canonical_domain}`} value={url} onChange={(e) => setUrl(e.target.value)} style={{ flex: 1, minWidth: 260 }} disabled={!can} />
        <button disabled={!can || busy || url.length < 12} onClick={() => wrap(() => put(`/api/admin/sources/${s.source_id}/config`, { url }), () => "URL saved. Test it next.")}>Save URL</button>
        <button disabled={!can || busy || !s.url} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/validate`), (r) => (r.ok ? "Test passed." : `Test failed: ${r.detail}`))}>Test from server</button>
      </div>
      {v && <p className="small" style={{ margin: "8px 0 0" }}><Badge kind={v.ok ? "ok" : "bad"}>{v.ok ? "Test passed" : "Test failed"}</Badge> {v.detail} <span className="muted">· {fmtDateTime(v.at)}</span></p>}
      {can && v?.ok && s.status !== "active" && (
        <div style={{ marginTop: 12 }}>
          <label className="small">Licence and permission note<br /><textarea value={licence} onChange={(e) => setLicence(e.target.value)} /></label>
          <label className="small row" style={{ margin: "8px 0" }}><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I have checked that monitoring this page is permitted</label>
          <button className="primary" disabled={busy || !ok || licence.length < 10} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/activate`, { licence_note: licence, confirm_permitted: ok }), () => "Source activated. The scheduler will pick it up within five minutes.")}>Activate source</button>
        </div>)}
      {can && s.status === "active" && <button style={{ marginTop: 10 }} disabled={busy} onClick={() => wrap(() => post(`/api/admin/sources/${s.source_id}/pause`, { reason: "paused by administrator" }), () => "Source paused.")}>Pause</button>}
      {msg && <div className="okmsg" role="status" style={{ marginTop: 10 }}>{msg}</div>}<Err e={err} />
    </div>
  );
}


const STATE_KIND: Record<string, "ok" | "warn" | "bad" | "info"> = { live: "ok", built_in: "ok", per_account: "info", needs_address: "warn", blocked_by_site: "bad", needs_credential: "warn", not_built: "info", deferred: "info", needs_retest: "warn", testing: "info" };

function Connections() {
  const [filter, setFilter] = useState("");
  return (
    <Load path="/api/admin/connections">{(d) => {
      const rows = d.items.filter((r: any) => !filter || r.state === filter);
      return (<>
        <p className="sub">Every place the platform reads from, what state it is in, what is needed to turn it on and where to get it. “Runs per account” means it is found for each account when you press Scan on that account.</p>
        <div className="row" style={{ marginBottom: 12, flexWrap: "wrap" }}>
          <button className={!filter ? "primary" : ""} onClick={() => setFilter("")}>All ({d.items.length})</button>
          {Object.entries(d.counts).map(([k, n]) => <button key={k} className={filter === k ? "primary" : ""} onClick={() => setFilter(k)}>{d.labels[k]} ({n as number})</button>)}
        </div>
        <div className="card table-wrap"><table><thead><tr><th>Connection</th><th>State</th><th>Reads</th><th>What to do</th><th>Where to get it</th></tr></thead><tbody>
          {rows.map((r: any) => <tr key={r.source_id}>
            <td><b>{r.name}</b><div className="small muted">{humanize(r.family)} · tier {r.tier}{r.countries?.length ? ` · ${r.countries.join(", ")}` : ""}</div></td>
            <td><Badge kind={STATE_KIND[r.state] ?? "info"}>{r.state_label}</Badge>{r.last_success_at && <div className="small muted">last read {fmtDateTime(r.last_success_at)}</div>}</td>
            <td className="small">{r.documents ? <>{r.documents.toLocaleString()} documents<div className="muted">latest {fmtDateTime(r.last_document_at)}</div></> : <span className="muted">nothing yet</span>}{r.address && <div className="muted" style={{ wordBreak: "break-all" }}>{r.address}</div>}</td>
            <td className="small">{r.what_to_do}{r.last_error && <div className="muted">Last error: {r.last_error}</div>}</td>
            <td className="small">{r.where_to_get ?? <span className="muted">-</span>}</td></tr>)}
        </tbody></table></div></>);
    }}</Load>
  );
}

function ScanTop() {
  const [n, setN] = useState(25); const [search, setSearch] = useState(true); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(null); setMsg(""); try { const r = await post("/api/admin/scan-accounts", { limit: n, with_search: search }); setMsg(`Queued ${r.queued} accounts. ${r.note} Results appear in the Market feed as they are verified.`); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <div className="card">
      <b>Find signals for your priority accounts</b>
      <div className="small muted" style={{ margin: "4px 0 10px" }}>Reads each account's own news, press, careers and job-board pages{search ? " and runs one search per account" : ""}. Nothing runs on a timer: it happens when you press the button. Cost is capped by the Usage budgets.</div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <label className="small">How many accounts <input type="number" min={1} max={300} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: 80 }} /></label>
        <label className="small row"><input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} /> include one web search per account (uses Exa budget)</label>
        <button className="primary" disabled={busy || n < 1} onClick={go}>Scan priority accounts</button>
      </div>
      {msg && <div className="okmsg" role="status" style={{ marginTop: 10 }}>{msg}</div>}<Err e={err} />
    </div>
  );
}

function Sources({ canEdit }: { canEdit: boolean }) {
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const bulk = async (activate: boolean) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post("/api/admin/sources/validate-all", { activate }); setMsg(activate ? "Testing every source from the server now. Each one that passes is activated. Refresh in a minute to see the result on each card." : "Testing every source from the server now. Nothing is activated."); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (<>
    {canEdit && <ScanTop />}
    {canEdit && <div className="card row spread"><div><b>Test all sources</b><div className="small muted">Runs each source's test from the server. Pages are read only where robots.txt allows. Sources without an address or credential are skipped and say why.</div></div>
      <div className="row"><button disabled={busy} onClick={() => bulk(false)}>Test only</button><button className="primary" disabled={busy} onClick={() => bulk(true)}>Test and activate passing</button></div></div>}
    {msg && <div className="okmsg" role="status" style={{ marginBottom: 12 }}>{msg}</div>}<Err e={err} />
    <Library render={(s, reload) => <SourceControls s={s} can={canEdit} reload={reload} />} />
  </>);
}

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

function Imports() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const load = () => api("/api/admin/imports").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);
  const send = async (f: File | undefined) => {
    if (!f) return; setBusy(true); setErr(null); setMsg("");
    try { const r = await upload("/api/admin/import/upload", f); setMsg(`Workbook ${r.workbook} received. The worker is importing it now. This can take several minutes.`); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const sync = async (wb: string) => {
    setBusy(true); setErr(null); setMsg("");
    try { await post(`/api/admin/import/sync?workbook=${wb}`); setMsg(`Sync of workbook ${wb} queued. The worker reads it from Google Sheets now. This can take several minutes.`); load(); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const gs = d?.google_sheets;
  return (
    <>
      <div className="card">
        <div className="eyebrow">Connect Google Sheets (no uploads needed)</div>
        {gs?.configured ? <p style={{ marginTop: 0 }}><Badge kind="ok">Connected</Badge> Share both workbooks with <b>{gs.service_account_email ?? "the service account"}</b> as Viewer, then sync. Each sync re-reads the sheets and keeps the data current.</p>
          : <ol className="small" style={{ marginTop: 0 }}>
              <li>In Google Cloud, create a service account in any project and enable the Google Sheets API. Create a JSON key for it.</li>
              <li>In Render, add the whole JSON as the secret <code>GOOGLE_SERVICE_ACCOUNT_JSON</code> on the API and the worker.</li>
              <li>Open each workbook in Google Sheets, press Share, and add the service account's email as Viewer.</li>
              <li>Come back here and press Sync. If your Google admin blocks sharing outside the company, upload the exports below instead.</li>
            </ol>}
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("A")}>Sync workbook A</button>
          <button className="primary" disabled={busy || !gs?.configured} onClick={() => sync("B")}>Sync workbook B</button>
          {gs?.workbooks && <><a href={gs.workbooks.A} target="_blank" rel="noreferrer">Open A</a><a href={gs.workbooks.B} target="_blank" rel="noreferrer">Open B</a></>}
        </div>
      </div>
      <div className="card">
        <div className="eyebrow">Or upload a workbook export</div>
        <p style={{ marginTop: 0 }}>In Google Sheets choose <b>File, Download, Microsoft Excel (.xlsx)</b> for each workbook, then upload it here. The platform recognises which workbook it is, checks every tab header against the agreed contract, and imports in the background. A renamed or reordered column pauses that tab instead of guessing.</p>
        <input type="file" accept=".xlsx" aria-label="Workbook file (.xlsx)" disabled={busy} onChange={(e) => { void send(e.target.files?.[0]); e.target.value = ""; }} />
        {msg && <div className="okmsg" role="status" style={{ marginTop: 12 }}>{msg}</div>}<Err e={err} />
        <p className="small muted" style={{ marginBottom: 0 }}>Re-uploading the same file changes nothing. The file itself is deleted after import; only its fingerprint and the result are kept.</p>
      </div>
      <h3>Recent uploads</h3>
      {!d ? <p className="muted">Loading</p> : d.uploads.length === 0 ? <div className="empty">No uploads yet.</div> :
        <div className="card table-wrap"><table><thead><tr><th>When</th><th>File</th><th>Workbook</th><th>Status</th><th>Detail</th></tr></thead><tbody>{d.uploads.map((u: any) => {
          const tabs = u.stats?.tabs ? Object.entries(u.stats.tabs) as [string, any][] : [];
          return <tr key={u.id}><td>{fmtDateTime(u.created_at)}</td><td>{u.filename}<div className="small muted">{u.uploaded_by}</div></td><td>{u.workbook_key}</td>
            <td><Badge kind={u.status === "completed" ? "ok" : u.status === "failed" ? "bad" : "info"}>{u.status}</Badge></td>
            <td className="small">{u.error ?? tabs.map(([k, v]) => `${k}: ${v.status}${v.rows !== undefined ? ` (${v.rows} rows)` : ""}`).join(" · ")}</td></tr>; })}</tbody></table></div>}
    </>
  );
}

const ROLES = ["bdm", "sales_manager", "research_analyst", "marketing", "delivery_expert", "admin", "compliance"];
function Users() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const [f, setF] = useState({ email: "", display_name: "", role: "bdm", team: "default" });
  const load = () => api("/api/admin/users").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = async (fn: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await fn(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  return (
    <>
      <div className="card">
        <div className="eyebrow">Add a person</div>
        <p className="small muted" style={{ marginTop: 0 }}>They sign in with Google using this address{d?.domain ? ` (only @${d.domain} is accepted)` : ""}. Sellers see accounts that have no CRM owner, plus their own and any shared with them.</p>
        <div className="row">
          <input aria-label="Work email" placeholder="name@thecodest.co" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} style={{ minWidth: 240 }} />
          <input aria-label="Display name" placeholder="Display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <select aria-label="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select>
          <input aria-label="Team" placeholder="Team" value={f.team} onChange={(e) => setF({ ...f, team: e.target.value })} style={{ width: 120 }} />
          <button className="primary" disabled={!f.email.includes("@") || !f.display_name} onClick={() => run(async () => { await post("/api/admin/users", f); setF({ ...f, email: "", display_name: "" }); }, "Person added. They can sign in now.")}>Add</button>
        </div>
        {msg && <div className="okmsg" role="status" style={{ marginTop: 12 }}>{msg}</div>}<Err e={err} />
      </div>
      {d && <div className="card table-wrap"><table><thead><tr><th>Person</th><th>Role</th><th>Team</th><th>Status</th><th></th></tr></thead><tbody>{d.items.map((u: any) => <tr key={u.id}>
        <td>{u.display_name}<div className="small muted">{u.email}</div></td>
        <td><select aria-label={`Role for ${u.email}`} value={u.role} onChange={(e) => run(() => patch(`/api/admin/users/${u.id}`, { role: e.target.value }), "Role updated.")}>{ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select></td>
        <td>{u.team}</td><td><Badge kind={u.active ? "ok" : ""}>{u.active ? "Active" : "Deactivated"}</Badge></td>
        <td><button className="ghost" onClick={() => run(() => patch(`/api/admin/users/${u.id}`, { active: !u.active }), u.active ? "Deactivated." : "Reactivated.")}>{u.active ? "Deactivate" : "Reactivate"}</button></td></tr>)}</tbody></table></div>}
    </>
  );
}

export default function Admin({ me }: { me: { permissions: string[] } }) {
  const p = (x: string) => me.permissions.includes(x);
  return (
    <>
      <div className="page-head"><div className="eyebrow">Control plane</div><h2>Administration</h2></div>
      <div className="tabs">{p("admin.jobs") && <NavLink to="/admin/pipeline">Pipeline</NavLink>}{p("sources.read") && <NavLink to="/admin" end> Sources</NavLink>}{p("sources.read") && <NavLink to="/admin/connections"> Connections</NavLink>}{p("admin.sources") && <NavLink to="/admin/imports"> Imports</NavLink>}{p("admin.users") && <NavLink to="/admin/users"> People</NavLink>}{p("admin.sources") && <NavLink to="/admin/knowledge"> Knowledge</NavLink>}{p("identity.manage") && <NavLink to="/admin/identity"> Identity review</NavLink>}<NavLink to="/admin/approvals"> Approvals</NavLink>{p("admin.jobs") && <NavLink to="/admin/jobs"> Jobs</NavLink>}
        {p("sources.read") && <NavLink to="/admin/verification"> Verification</NavLink>}{p("admin.costs") && <><NavLink to="/admin/discovery"> Discovery</NavLink><NavLink to="/admin/usage"> Usage</NavLink></>}{p("admin.rules") && <><NavLink to="/admin/rules"> Rules</NavLink><NavLink to="/admin/quality"> Quality</NavLink></>}{p("audit.read") && <NavLink to="/admin/audit"> Audit</NavLink>}</div>
      <Routes><Route index element={<Sources canEdit={p("admin.sources")} />} /><Route path="pipeline" element={<Pipeline />} /><Route path="connections" element={<Connections />} /><Route path="knowledge" element={<KnowledgeAdmin />} /><Route path="imports" element={<Imports />} /><Route path="users" element={<Users />} /><Route path="identity" element={<Identity />} /><Route path="approvals" element={<Approvals />} /><Route path="jobs" element={<Jobs />} /><Route path="costs" element={<Costs />} /><Route path="usage" element={<Usage />} /><Route path="discovery" element={<Discovery />} /><Route path="verification" element={<Verification />} />
        <Route path="rules" element={<Rules />} /><Route path="quality" element={<Quality />} /><Route path="audit" element={<Audit />} /></Routes>
    </>
  );
}
