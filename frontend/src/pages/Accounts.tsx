import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { Badge, Err, Evidence, humanize, priorityKind } from "../components/ui";

function AddCompany({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [f, setF] = useState({ website: "", name: "", region: "", links: "", notes: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { const r = await post("/api/accounts/add", { website: f.website, name: f.name || null, region: f.region || null, notes: f.notes || null, links: f.links.split("\n").map((x) => x.trim()).filter(Boolean) }); nav(`/accounts/${r.id}`); }
    catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <form className="card" onSubmit={submit}>
      <div className="row spread"><b>Add a company</b><button type="button" className="ghost" onClick={onClose}>Close</button></div>
      <p className="small muted" style={{ margin: "6px 0 12px" }}>A website is enough. The platform reads the company's own site, news, careers page and job board, and runs one web search. Extra context below makes the research sharper.</p>
      <div className="addgrid">
        <label className="small">Website *<input id="add-website" required placeholder="example.com" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
        <label className="small">Company name (optional)<input id="add-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="small">Region (optional)<select id="add-region" value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })}><option value="">Not sure</option><option>UK</option><option>DACH</option><option>Nordics</option><option>Poland</option><option>Rest of Europe</option><option>US</option></select></label>
      </div>
      <label className="small" style={{ display: "block", marginTop: 10 }}>Other links, one per line (LinkedIn, press, a tender, anything useful)<textarea id="add-links" rows={3} value={f.links} onChange={(e) => setF({ ...f, links: e.target.value })} placeholder="https://www.linkedin.com/company/…" /></label>
      <label className="small" style={{ display: "block", marginTop: 10 }}>Why this company? (optional)<textarea id="add-notes" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <p className="small muted">LinkedIn and other social links are saved for context. They are not scraped, because their terms forbid it.</p>
      <Err e={err} />
      <button className="primary" disabled={busy || f.website.trim().length < 3}>Add and research</button>
    </form>
  );
}

export function Accounts() {
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { const t = setTimeout(() => api(`/api/accounts?q=${encodeURIComponent(q)}`).then((r) => setRows(r.items)).catch(setErr), 200); return () => clearTimeout(t); }, [q]);
  return (
    <>
      <div className="page-head"><div className="eyebrow">Your book</div><h2>Accounts</h2></div><p className="sub" style={{marginTop:-8}}>Only accounts you are authorised to see: your Pipedrive-owned accounts, unowned accounts and explicit shares.</p>
      <div className="row" style={{ marginBottom: 12 }}><button className="primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add a company"}</button></div>
      {adding && <AddCompany onClose={() => setAdding(false)} />}
      <input aria-label="Search accounts" placeholder="Search by name…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 280, marginBottom: 12 }} /><Err e={err} />
      <div className="card table-wrap"><table><thead><tr><th>Account</th><th>Country</th><th>List</th><th>Employees</th><th>CRM owner</th><th>Initiatives</th></tr></thead><tbody>
        {rows.map((r) => <tr key={r.id}><td><Link to={`/accounts/${r.id}`}>{r.display_name}</Link></td><td>{r.country_code ?? "n/a"}</td><td>{r.in_master_list ? <Badge kind="ok">master list</Badge> : <Badge kind="info">new</Badge>}</td>
          <td>{r.employees_estimate?.toLocaleString() ?? "n/a"} {r.employees_uncertain && <Badge kind="warn">estimate</Badge>}</td><td>{r.crm_owner_id ?? <span className="muted">unverified</span>}</td><td>{r.initiatives}</td></tr>)}
        {rows.length === 0 && <tr><td colSpan={6} className="muted">No accounts in your scope match.</td></tr>}</tbody></table></div>
    </>
  );
}

const TABS = ["Overview", "Initiatives", "Signals & evidence", "Suppliers", "Buyers", "Access & CRM", "Research & coverage"];

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState(TABS[0]);
  const [err, setErr] = useState<unknown>(null);
  const [eps, setEps] = useState<any[]>([]);
  const [scan, setScan] = useState<{ busy: boolean; msg: string; err: unknown }>({ busy: false, msg: "", err: null });
  useEffect(() => { api(`/api/accounts/${id}`).then(setD).catch(setErr); api(`/api/accounts/${id}/endpoints`).then((r) => setEps(r.items)).catch(() => setEps([])); }, [id]);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading…</p>;
  const a = d.account;
  const canScan = me.permissions.includes("research.request");
  const runScan = async () => {
    setScan({ busy: true, msg: "", err: null });
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? `Scanning ${r.website} and searching the web. New findings appear in the Market feed once verified; reload this tab in a minute.` : "Queued. This account has no website recorded, so only the web search will run.", err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  return (
    <>
      <div className="row spread"><h2 style={{ margin: 0 }}>{a.display_name}</h2>{canScan && <button className="primary" disabled={scan.busy} onClick={runScan}>Find signals now</button>}</div>
      {scan.msg && <div className="okmsg" role="status" style={{ margin: "8px 0" }}>{scan.msg}</div>}<Err e={scan.err} />
      <div className="row"><Badge kind={a.in_master_list ? "ok" : "info"}>{a.in_master_list ? "in master list" : "new candidate"}</Badge><Badge>{a.country_code ?? "country unknown"}</Badge><Badge kind={d.ownership_verified ? "ok" : "warn"}>{d.ownership_verified ? `Pipedrive owner ${d.crm.owner_id}` : "ownership unverified"}</Badge>
        <Badge>{a.employees_estimate?.toLocaleString() ?? "?"} employees{a.employees_uncertain ? " (estimate)" : ""}</Badge></div>
      <div className="tabs" role="tablist">{TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "Overview" && (<>
        <h3>Opportunities</h3>
        {d.opportunities.length === 0 && <div className="empty">No hypotheses yet for this account.</div>}
        {d.opportunities.map((o: any) => (
          <div key={o.hypothesis_id} className="card"><div className="row spread"><div><Badge kind={priorityKind(o.priority)}>{o.priority}</Badge> <b>{o.initiative.title}</b> → {humanize(o.route.type)} via {o.route.counterparty}</div><Link to={`/opportunities/${o.hypothesis_id}`}>Open</Link></div>
            <p className="small">{o.why_it_matters}</p>{o.next_action && <p className="small"><b>Next:</b> {o.next_action.objective}</p>}</div>))}
      </>)}
      {tab === "Initiatives" && d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">analyst working title</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">budget not verified</Badge>}
        <p className="small">Technology: {i.technology_tags.join(", ") || "n/a"} · last material event {fmtDate(i.last_material_event_at)} · next milestone {fmtDate(i.next_milestone_at)}</p>
        <ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></div>)}
      {tab === "Signals & evidence" && <div className="card table-wrap"><table><thead><tr><th>Rule</th><th>Demand tier</th><th>Status</th><th>Event</th><th>Event date</th><th>Published</th><th>First seen</th><th>Expires</th></tr></thead><tbody>
        {d.signals.map((s: any, i: number) => <tr key={i}><td>{s.rule_id}</td><td>{s.demand_tier}</td><td><Badge kind={s.status === "active" ? "ok" : ""}>{s.status}</Badge></td><td>{s.summary}</td><td>{fmtDate(s.event_at)}</td><td>{fmtDate(s.pub_at)}</td><td>{fmtDate(s.first_observed_at)}</td><td>{fmtDate(s.expires_at)}</td></tr>)}</tbody></table></div>}
      {tab === "Suppliers" && <div className="card table-wrap"><table><thead><tr><th>Supplier</th><th>Role</th><th>Scope</th><th>Relation</th><th>Exclusivity</th></tr></thead><tbody>{d.suppliers.map((s: any, i: number) => <tr key={i}><td>{s.supplier_name}</td><td>{s.supplier_role}</td><td>{s.scope_text}</td><td>{s.relation}</td><td>{s.exclusivity}</td></tr>)}</tbody></table>
        <p className="small muted">A named supplier does not prove exclusivity or scope. Edges are scoped to this legal entity only.</p></div>}
      {tab === "Buyers" && (me.permissions.includes("contacts.read") ? <div className="card table-wrap"><table><thead><tr><th>Name</th><th>Title (reported)</th><th>Lane</th><th>Role verified</th><th>Email status</th><th>Suppressed</th></tr></thead><tbody>
        {d.buyers.map((b: any) => <tr key={b.id}><td>{b.name}</td><td>{b.title}</td><td>{b.lane}</td><td>{b.role_verified}</td><td>{b.email_status}</td><td>{b.suppressed ? <Badge kind="bad">yes</Badge> : "no"}</td></tr>)}</tbody></table>
        <p className="small muted">Titles are reported, not proven authority. Email addresses are never shown here; access to personal data is audited.</p></div> : <div className="empty">Contact data is not available to your role.</div>)}
      {tab === "Access & CRM" && <div className="card"><p>{d.crm ? <>Linked to Pipedrive organisation {d.crm.crm_id}; open deals {d.crm.open_deals_count ?? "?"}; mirror synced {fmtDateTime(d.crm.synced_at)}. Pipedrive remains the source of truth for owner and deal stage.</> : "Not linked to Pipedrive · ownership cannot be checked, so outreach and CRM proposals are disabled for this account."}</p>
        <h3>Master-list source rows</h3><ul className="tight small">{d.source_rows.map((r: any, i: number) => <li key={i}>{r.sheet_title} (row {r.row_number}, workbook …{r.source_file_id.slice(-6)})</li>)}</ul></div>}
      {tab === "Research & coverage" && <div className="card"><p className="small">{d.source_coverage_note}</p><table><thead><tr><th>Source family</th><th>State</th><th>Last success</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{c.source_family}</td><td>{c.coverage_state}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
        {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">No account-level coverage recorded yet: visibility is unknown, not “nothing happening”.</td></tr>}</tbody></table>
        <h3>Where this account publishes</h3>
        {eps.length === 0 ? <p className="small muted">Nothing found yet. Press “Find signals now” to look for this account's news feed, press page, careers page and job board.</p>
          : <table><thead><tr><th>Kind</th><th>Address</th><th>Last read</th><th>Items</th></tr></thead><tbody>{eps.map((e, i) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small" style={{ wordBreak: "break-all" }}>{e.url}</td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status}</Badge>{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : "not yet"}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}
        <h3>Evidence</h3>{d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</div>}
    </>
  );
}
