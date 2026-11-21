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

const ICP_NAMES: Record<string, string> = { strategic_5000_plus: "5,000+ staff", enterprise_1000_plus: "1,000+ staff", scaleup_500_999: "500 to 999 staff", below_threshold: "Below threshold", unknown: "Size unknown" };
const BAND: Record<string, ["ok" | "warn" | "info" | "bad" | "", string]> = { hot: ["bad", "Hot"], warm: ["warn", "Warm"], watch: ["info", "Watch"], none: ["", "No signal"] };
const GROUP_NAME: Record<string, string> = { SRC: "route", CAP: "capacity", MAN: "leader", PRG: "programme", COM: "business event", REG: "deadline", REL: "relationship" };
const PRESETS: [string, Record<string, string>][] = [
  ["Hot or warm", { band: "hot" }], ["UK banks and fintechs", { region: "uk" }], ["DACH", { region: "dach" }], ["Nordics", { region: "nordics" }], ["Poland", { region: "poland" }],
  ["Signal in last 30 days", { signal_days: "30" }], ["Your shortlist", { shortlist: "true" }], ["Added by people", { origin: "added" }], ["Found by the platform", { origin: "discovered" }],
];
type Q = { q: string; region: string; icp: string; segment: string; band: string; origin: string; signal_days: string; shortlist: string; sort: string; page: number };
const EMPTY: Q = { q: "", region: "", icp: "", segment: "", band: "", origin: "", signal_days: "", shortlist: "", sort: "score", page: 1 };
const qs = (f: Q, extra = "") => { const u = new URLSearchParams(); (Object.entries(f) as [string, any][]).forEach(([k, v]) => { if (v !== "" && v !== 1 && !(k === "sort" && v === "score")) u.set(k, String(v)); }); if (f.page > 1) u.set("page", String(f.page)); return u.toString() + extra; };

export function Accounts({ me }: { me?: { permissions: string[] } }) {
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState<Q>(EMPTY);
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const [sel, setSel] = useState<Set<string>>(new Set()); const [msg, setMsg] = useState("");
  const can = (p: string) => !me || me.permissions.includes(p);
  useEffect(() => { const t = setTimeout(() => api(`/api/leads?${qs(f)}`).then(setD).catch(setErr), f.q ? 250 : 0); return () => clearTimeout(t); }, [f]);
  const set = (k: keyof Q, v: string) => setF({ ...f, [k]: v, page: 1 });
  const preset = (p: Record<string, string>) => setF({ ...EMPTY, ...p, page: 1 });
  const toggle = (id: string) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n); };
  const exportCsv = async () => {
    try {
      const t = sessionStorage.getItem("ri_token"); const r = await fetch(`/api/leads.csv?${qs(f)}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!r.ok) throw new Error(await r.text());
      const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = "leads.csv"; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setErr(e); }
  };
  const scanSelected = async () => {
    setErr(null); setMsg("");
    try { const r = await post("/api/leads/scan", { ids: Array.from(sel).slice(0, 25), with_search: false }); setMsg(`Queued ${r.queued} scans. New findings appear in the Market feed once verified.`); setSel(new Set()); } catch (e) { setErr(e); }
  };
  const fac = d?.facets ?? {};
  const opt = (m: Record<string, number> | undefined, label?: (k: string) => string) => Object.entries(m ?? {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => <option key={k} value={k}>{label ? label(k) : humanize(k)} ({n})</option>);
  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  return (
    <>
      <div className="page-head"><div className="eyebrow">Your book</div><h2>Leads</h2></div>
      <p className="sub" style={{ marginTop: -8 }}>Every company the platform knows, with a score built from its verified signals. Filter by region, size and signal strength; press Add a company for one that is missing.</p>
      <div className="row" style={{ marginBottom: 12 }}>{can("research.request") && <button className="primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add a company"}</button>}
        <button onClick={exportCsv}>Export CSV</button>{sel.size > 0 && can("research.request") && <button onClick={scanSelected}>Scan {sel.size} selected</button>}</div>
      {adding && <AddCompany onClose={() => setAdding(false)} />}
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <div className="chips" aria-label="Saved views">{PRESETS.map(([l, p]) => <button key={l} className="chip" aria-pressed={Object.entries(p).every(([k, v]) => (f as any)[k] === v)} onClick={() => preset(p)}>{l}</button>)}<button className="chip" onClick={() => preset({})}>Clear</button></div>
      <div className="leadbar">
        <input id="lead-q" aria-label="Search leads" placeholder="Search name or website" value={f.q} onChange={(e) => set("q", e.target.value)} />
        <select id="lead-region" aria-label="Region" value={f.region} onChange={(e) => set("region", e.target.value)}><option value="">All regions</option>{d?.regions.map((r: any) => <option key={r.key} value={r.key}>{r.label} ({fac.region?.[r.key] ?? 0})</option>)}</select>
        <select id="lead-icp" aria-label="Company size" value={f.icp} onChange={(e) => set("icp", e.target.value)}><option value="">Any size</option>{opt(fac.icp, (k) => ICP_NAMES[k] ?? k)}</select>
        <select id="lead-segment" aria-label="Segment" value={f.segment} onChange={(e) => set("segment", e.target.value)}><option value="">Any segment</option>{opt(fac.segment)}</select>
        <select id="lead-band" aria-label="Signal band" value={f.band} onChange={(e) => set("band", e.target.value)}><option value="">Any signal</option>{["hot", "warm", "watch", "none"].map((k) => <option key={k} value={k}>{BAND[k][1]} ({fac.band?.[k] ?? 0})</option>)}</select>
        <select id="lead-origin" aria-label="Origin" value={f.origin} onChange={(e) => set("origin", e.target.value)}><option value="">Any origin</option>{opt(fac.origin, (k) => d.origins[k] ?? k)}</select>
        <select id="lead-sort" aria-label="Sort" value={f.sort} onChange={(e) => set("sort", e.target.value)}><option value="score">Highest score</option><option value="recent">Latest signal</option><option value="size">Largest</option><option value="name">Name</option></select>
      </div>
      <Err e={err} />
      <div className="card table-wrap flush"><table><thead><tr><th><span className="sr">Select</span></th><th>Company</th><th>Region</th><th>Size</th><th>Score</th><th>Signals</th><th>Latest</th><th>Origin</th></tr></thead><tbody>
        {d?.items.map((r: any) => <tr key={r.id}>
          <td><input type="checkbox" aria-label={`Select ${r.name}`} checked={sel.has(r.id)} onChange={() => toggle(r.id)} /></td>
          <td><Link to={`/accounts/${r.id}`}>{r.name}</Link>{r.shortlist && <> <Badge kind="ok">Shortlist</Badge></>}<div className="small muted">{r.segment ?? "Segment unknown"}{r.canonical_domain ? ` · ${r.canonical_domain}` : ""}</div></td>
          <td>{r.country_code ?? "n/a"}<div className="small muted">{d.regions.find((x: any) => x.key === r.region)?.label ?? humanize(r.region)}</div></td>
          <td>{r.employees?.toLocaleString() ?? "n/a"}{r.employees_uncertain && <> <Badge kind="warn">estimate</Badge></>}<div className="small muted">{ICP_NAMES[r.icp] ?? r.icp}</div></td>
          <td>{r.score !== null ? <><b>{r.score}</b> <Badge kind={BAND[r.band][0]}>{BAND[r.band][1]}</Badge></> : <span className="muted">none</span>}{r.stopped && <div className="small muted">Stopped by a rule</div>}</td>
          <td className="small">{r.signal_count ? <>{r.signal_count} · {r.groups.map((g: string) => GROUP_NAME[g] ?? g).join(", ")}</> : <span className="muted">none yet</span>}</td>
          <td className="small">{r.last_signal_at ? fmtDate(r.last_signal_at) : <span className="muted">n/a</span>}</td>
          <td className="small">{d.origins[r.origin]}{r.candidate_status === "pending_qualification" && <div className="muted">Not yet qualified</div>}</td></tr>)}
        {d && d.items.length === 0 && <tr><td colSpan={8} className="muted">Nothing matches these filters.{d.facets && Object.values(d.facets.origin ?? {}).length === 0 ? " No companies are loaded yet: upload the workbooks or add a company." : ""}</td></tr>}</tbody></table></div>
      {d && <div className="row spread small" style={{ marginTop: 10 }}><span>{d.total.toLocaleString()} leads</span>
        <span className="row"><button disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>Previous</button><span>Page {f.page} of {pages}</span><button disabled={f.page >= pages} onClick={() => setF({ ...f, page: f.page + 1 })}>Next</button></span></div>}
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
      {d.score && <div className="card"><div className="row spread"><div><span className="eyebrow">Account score</span><div className="kpi-n" style={{ margin: "4px 0" }}>{d.score.stopped ? "Stopped" : d.score.score}<span className="kpi-u"> {BAND[d.score.band][1]}</span></div></div>
        <div className="small" style={{ maxWidth: 520 }}>{d.score.stopped ? "A stop rule is active for this account, so the score is held at zero." : `${d.score.signal_count} signals across ${d.score.groups.map((g: string) => GROUP_NAME[g] ?? g).join(", ")}. Different kinds of signal agreeing count for more than the same kind repeated.`}
          {d.score.top?.length > 0 && <div className="chips" style={{ marginTop: 6 }}>{d.score.top.map((t: any) => <Badge key={t.rule_id}>{t.rule_id} · {t.weight}</Badge>)}</div>}</div></div></div>}
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
