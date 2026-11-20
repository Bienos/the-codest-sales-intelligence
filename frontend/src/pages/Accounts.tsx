import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime } from "../api";
import { Badge, Err, Evidence, humanize, priorityKind } from "../components/ui";

export function Accounts() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { const t = setTimeout(() => api(`/api/accounts?q=${encodeURIComponent(q)}`).then((r) => setRows(r.items)).catch(setErr), 200); return () => clearTimeout(t); }, [q]);
  return (
    <>
      <div className="page-head"><div className="eyebrow">Your book</div><h2>Accounts</h2></div><p className="sub" style={{marginTop:-8}}>Only accounts you are authorised to see: your Pipedrive-owned accounts, unowned accounts and explicit shares.</p>
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
  useEffect(() => { api(`/api/accounts/${id}`).then(setD).catch(setErr); }, [id]);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading…</p>;
  const a = d.account;
  return (
    <>
      <h2>{a.display_name}</h2>
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
        <h3>Evidence</h3>{d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</div>}
    </>
  );
}
