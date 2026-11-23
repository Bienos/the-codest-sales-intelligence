import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
const del = (p: string) => api(p, { method: "DELETE" });
import { Badge, Err, Evidence, LABELS, groupName, humanize, lab, priorityKind, Skeleton, reloadPage, whyShort, Tabs } from "../components/ui";

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
      <p className="small muted mt-2 mb-3">A website is enough. We read the company's own site, news, careers page and job board, and run one web search. Anything you add below makes the research sharper.</p>
      <div className="addgrid">
        <label className="small">Website (required)<input id="add-website" required placeholder="example.com" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
        <label className="small">Company name (optional)<input id="add-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="small">Region (optional)<select id="add-region" value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })}><option value="">Not sure</option><option>UK</option><option>DACH</option><option>Nordics</option><option>Poland</option><option>Rest of Europe</option><option>US</option></select></label>
      </div>
      <label className="small block mt-3">Other useful links, one per line (LinkedIn, press, a tender)<textarea id="add-links" rows={3} value={f.links} onChange={(e) => setF({ ...f, links: e.target.value })} placeholder="https://www.linkedin.com/company/…" /></label>
      <label className="small block mt-3">Why this company? (optional)<textarea id="add-notes" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <p className="small muted">LinkedIn and other social links are saved for context only. We do not read them, because their terms forbid it.</p>
      <Err e={err} />
      <button className="primary" disabled={busy || f.website.trim().length < 3}>Add and research</button>
    </form>
  );
}

const ICP_NAMES = LABELS.icp as Record<string, string>;
const BAND: Record<string, ["ok" | "warn" | "info" | "bad" | "", string]> = { hot: ["bad", "Hot"], warm: ["warn", "Warm"], watch: ["info", "Watch"], none: ["", "No signal"] };
const PRESETS: [string, Record<string, string>][] = [
  ["Hot", { band: "hot" }], ["Warm", { band: "warm" }], ["UK", { region: "uk" }], ["DACH", { region: "dach" }], ["Nordics", { region: "nordics" }], ["Poland", { region: "poland" }],
  ["Signal in last 30 days", { signal_days: "30" }], ["Watching", { watched: "true" }], ["Your shortlist", { shortlist: "true" }], ["Added by the team", { origin: "added" }], ["Found by us", { origin: "discovered" }], ["Public sector tenders", { lane: "public" }],
];
type Q = { q: string; region: string; icp: string; segment: string; band: string; origin: string; signal_days: string; shortlist: string; watched: string; lane: string; sort: string; page: number };
const EMPTY: Q = { q: "", region: "", icp: "", segment: "", band: "", origin: "", signal_days: "", shortlist: "", watched: "", lane: "", sort: "score", page: 1 };
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
  const toggleWatch = async (r: any) => {
    setErr(null);
    try { await (r.watched ? del(`/api/accounts/${r.id}/watch`) : post(`/api/accounts/${r.id}/watch`)); setD({ ...d, items: d.items.map((x: any) => (x.id === r.id ? { ...x, watched: !r.watched } : x)) }); } catch (e) { setErr(e); }
  };
  const decide = async (r: any, decision: "qualify" | "reject") => {
    setErr(null);
    try { const o = await post(`/api/leads/${r.id}/qualify`, { decision }); setD({ ...d, items: decision === "reject" ? d.items.filter((x: any) => x.id !== r.id) : d.items.map((x: any) => (x.id === r.id ? { ...x, candidate_status: o.status } : x)) }); } catch (e) { setErr(e); }
  };
  const exportCsv = async () => {
    try {
      const t = sessionStorage.getItem("ri_token"); const r = await fetch(`/api/leads.csv?${qs(f)}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!r.ok) throw new Error(await r.text());
      const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = "leads.csv"; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setErr(e); }
  };
  const scanSelected = async () => {
    setErr(null); setMsg("");
    try { const r = await post("/api/leads/scan", { ids: Array.from(sel).slice(0, 25), with_search: false }); setMsg(`Checking ${r.queued} ${r.queued === 1 ? "account" : "accounts"} for news. New findings appear in the Market feed once we have checked them.`); setSel(new Set()); } catch (e) { setErr(e); }
  };
  const fac = d?.facets ?? {};
  const opt = (m: Record<string, number> | undefined, label?: (k: string) => string) => Object.entries(m ?? {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => <option key={k} value={k}>{label ? label(k) : humanize(k)} ({n})</option>);
  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  return (
    <>
      <div className="page-head"><div className="eyebrow">Companies we know about</div><h1>Accounts</h1></div>
      <p className="sub sub-tight">{f.lane === "public" ? "Public bodies that publish tenders. Kept apart from private companies because they buy through formal procurement. Scores here come from tender notices only." : "Every company we know about, with a signal score built from its checked signals. Filter by region, size or signal strength. Press “Add a company” if one is missing."}</p>
      <div className="row mb-3">{can("research.request") && <button className="primary" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add a company"}</button>}
        <button onClick={exportCsv}>Export CSV</button>{sel.size > 0 && can("research.request") && <button onClick={scanSelected}>Check {sel.size} for news</button>}</div>
      {adding && <AddCompany onClose={() => setAdding(false)} />}
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <div className="chips chips-scroll" aria-label="Saved views">{PRESETS.map(([l, p]) => <button key={l} className="chip" aria-pressed={Object.entries(p).every(([k, v]) => (f as any)[k] === v)} onClick={() => preset(p)}>{l}</button>)}<button className="chip" onClick={() => preset({})}>Clear</button></div>
      <div className="leadbar">
        <input id="lead-q" aria-label="Search accounts" placeholder="Search name or website" value={f.q} onChange={(e) => set("q", e.target.value)} />
        <select id="lead-region" aria-label="Region" value={f.region} onChange={(e) => set("region", e.target.value)}><option value="">All regions</option>{d?.regions.map((r: any) => <option key={r.key} value={r.key}>{r.label} ({fac.region?.[r.key] ?? 0})</option>)}</select>
        <select id="lead-icp" aria-label="Company size" value={f.icp} onChange={(e) => set("icp", e.target.value)}><option value="">Any size</option>{opt(fac.icp, (k) => ICP_NAMES[k] ?? k)}</select>
        <select id="lead-segment" aria-label="Segment" value={f.segment} onChange={(e) => set("segment", e.target.value)}><option value="">Any segment</option>{opt(fac.segment)}</select>
        <select id="lead-band" aria-label="Signal strength" value={f.band} onChange={(e) => set("band", e.target.value)}><option value="">Any signal</option>{["hot", "warm", "watch", "none"].map((k) => <option key={k} value={k}>{BAND[k][1]} ({fac.band?.[k] ?? 0})</option>)}</select>
        <select id="lead-origin" aria-label="Where it came from" value={f.origin} onChange={(e) => set("origin", e.target.value)}><option value="">Any source</option>{opt(fac.origin, (k) => d.origins[k] ?? k)}</select>
        <select id="lead-sort" aria-label="Sort" value={f.sort} onChange={(e) => set("sort", e.target.value)}><option value="score">Highest score</option><option value="recent">Latest signal</option><option value="size">Largest</option><option value="name">Name</option></select>
      </div>
      <Err e={err} />
      <div className="card table-wrap flush"><table className="leads-table cards"><thead><tr><th><span className="sr">Select</span></th><th><span className="sr">Watch</span></th><th>Company</th><th>Region</th><th>Size</th><th title="Adds up recent checked signals. Different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability.">Signal score</th><th>Signals</th><th>Latest signal</th><th>Came from</th></tr></thead><tbody>
        {d?.items.map((r: any) => <tr key={r.id}>
          <td className="sel"><label className="hit44"><input type="checkbox" aria-label={`Select ${r.name}`} checked={sel.has(r.id)} onChange={() => toggle(r.id)} /></label></td>
          <td className="sel"><button className="star hit44" aria-pressed={r.watched} aria-label={r.watched ? `Stop watching ${r.name}` : `Watch ${r.name}`} title={r.watched ? "Watching: we re-read its site, news and jobs every week" : "Watch: re-read its site, news and jobs every week"} onClick={() => toggleWatch(r)}>{r.watched ? "★" : "☆"}</button></td>
          <td className="name"><Link to={`/accounts/${r.id}`}>{r.name}</Link>{r.shortlist && <> <Badge kind="ok">Shortlist</Badge></>}<div className="small muted">{r.segment ?? "Segment not known"}{r.canonical_domain ? ` · ${r.canonical_domain}` : ""}</div></td>
          <td data-label="Region">{r.country_code ?? "Not known"}<div className="small muted">{d.regions.find((x: any) => x.key === r.region)?.label ?? humanize(r.region)}</div></td>
          <td data-label="Size">{r.employees?.toLocaleString() ?? "Not known"}{r.employees_uncertain && <> <span title="Estimated headcount" className="small muted">(estimate)</span></>}<div className="small muted">{ICP_NAMES[r.icp] ?? r.icp}</div></td>
          <td data-label="Signal score">{r.score !== null ? <><b>{r.score}</b> <Badge kind={BAND[r.band][0]}>{BAND[r.band][1]}</Badge></> : <span className="muted">None</span>}{r.stopped && <div className="small muted" title="Something we found says not to pursue this now. The score stays at zero until it expires.">On hold: a stop signal was found</div>}</td>
          <td data-label="Signals" className="small">{r.signal_count ? <>{r.signal_count} · {r.groups.map(groupName).join(", ")}</> : <span className="muted">None yet</span>}</td>
          <td data-label="Latest signal" className="small">{r.last_signal_at ? fmtDate(r.last_signal_at) : <span className="muted">None</span>}</td>
          <td data-label="Came from" className="small">{d.origins[r.origin]}{r.candidate_status === "pending_qualification" && <div className="row gap-2 mt-1"><span className="muted">Found by us. Add it?</span>{can("research.request") && <><button className="chip" onClick={() => decide(r, "qualify")}>Add to accounts</button><button className="chip" onClick={() => decide(r, "reject")}>Not a fit</button></>}</div>}{r.candidate_status === "qualified" && <div><Badge kind="ok">Added</Badge></div>}</td></tr>)}
        {d && d.items.length === 0 && <tr><td colSpan={9} className="muted">No accounts match these filters. Try clearing some.{d.facets && Object.values(d.facets.origin ?? {}).length === 0 ? " There are no accounts yet: load the workbooks in Administration, Imports, or add a company." : ""}</td></tr>}</tbody></table></div>
      {d && <div className="row spread small mt-3"><span>{d.total.toLocaleString()} {d.total === 1 ? "account" : "accounts"}</span>
        <span className="row"><button disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>Previous</button><span>Page {f.page} of {pages}</span><button disabled={f.page >= pages} onClick={() => setF({ ...f, page: f.page + 1 })}>Next</button></span></div>}
    </>
  );
}

function Cited({ items }: { items: any[] }) {
  return <ul className="brief-list">{items.map((x, i) => <li key={i}><div>{x.statement}</div><blockquote className="fquote mt-2 mb-0">“{x.quote}”<cite>{x.source_url ? <a className="pill" href={x.source_url} target="_blank" rel="noreferrer noopener">{x.source_title || humanize(x.source_kind)} ↗</a> : <span>{x.source_title || humanize(x.source_kind)}</span>}</cite></blockquote></li>)}</ul>;
}

function BriefTab({ briefs }: { briefs: any[] }) {
  const b = briefs[0];
  if (!b) return <div className="card"><p className="muted m-0">No brief yet. Press “Write research brief”. It uses only what we have stored about this company, and every statement quotes its source.</p></div>;
  if (b.status === "queued" || b.status === "running") return <div className="card"><p className="m-0">Writing the brief. This takes about a minute.</p></div>;
  if (b.status === "failed") return <div className="card"><Err e={new Error(b.error || "The brief could not be written.")} /></div>;
  const c = b.content;
  return (
    <div className="card">
      <p className="small muted mt-0">Written {fmtDateTime(b.finished_at)} from {c.source_count ?? 0} sources we hold. {b.dropped > 0 ? `We left out ${b.dropped} ${b.dropped === 1 ? "statement" : "statements"} we could not match to its source.` : "Every statement below quotes its source."}</p>
      {c.note && <p>{c.note}</p>}
      {c.summary?.length > 0 && <><h3>What the company is and does</h3><Cited items={c.summary} /></>}
      {c.signals?.length > 0 && <><h3>Signals</h3><Cited items={c.signals} /></>}
      {c.hiring?.length > 0 && <><h3>Hiring</h3><Cited items={c.hiring} /></>}
      {c.icp_fit?.read && <><h3>Does it fit our target customer?</h3><p>{c.icp_fit.read}</p>{c.icp_fit.reasons?.length > 0 && <Cited items={c.icp_fit.reasons} />}</>}
      {c.open_questions?.length > 0 && <><h3>Still to find out</h3><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></>}
    </div>
  );
}

function FindContacts({ orgId, can }: { orgId: string; can: boolean }) {
  const [d, setD] = useState<any>({ items: [] }); const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const load = () => api(`/api/accounts/${orgId}/contact-candidates`).then(setD).catch(() => undefined);
  useEffect(() => { load(); }, [orgId]);
  const run = async (fn: () => Promise<any>, ok: (r: any) => string) => { setErr(null); setMsg(""); setBusy(true); try { setMsg(ok(await fn())); await load(); } catch (e) { setErr(e); } finally { setBusy(false); } };
  return (
    <div className="card">
      <div className="row spread"><b>Find the people who decide</b>{can && <button className="primary" disabled={busy || !d.apollo_configured} onClick={() => run(() => post(`/api/accounts/${orgId}/find-contacts`), (r) => `Found ${r.found} ${r.found === 1 ? "person" : "people"}, ${r.added} new. Names and job titles only, no email yet.`)}>Search with Apollo</button>}</div>
      <p className="small muted mt-1 mb-2">Searching is free and gives names and job titles. Revealing an email costs {d.reveal_price ?? "an amount not set yet (ask an administrator)"} {d.reveal_price === 1 ? "credit" : "credits"} per person and needs approval. {d.apollo_configured ? "" : "Apollo is not connected yet. "}{d.paid_enrichment_enabled ? "" : "Revealing emails is switched off for now."}</p>
      {d.items.length > 0 && <div className="table-wrap"><table><thead><tr><th>Name</th><th>Job title</th><th>Email</th><th><span className="sr">Actions</span></th></tr></thead><tbody>
        {d.items.map((c: any) => <tr key={c.id}><td>{c.linkedin_url ? <a href={c.linkedin_url} target="_blank" rel="noreferrer noopener">{c.name}</a> : c.name}</td><td>{c.title}</td>
          <td><Badge kind={c.status === "revealed" ? "ok" : c.status === "found" ? "info" : ""}>{c.status === "found" ? "Not revealed" : c.status === "revealed" ? "Revealed" : humanize(c.status)}</Badge></td>
          <td>{can && c.status === "found" && <div className="row"><button disabled={busy} onClick={() => run(() => post(`/api/contact-candidates/${c.id}/reveal`), (r) => `Approval requested (${r.credits} ${r.credits === 1 ? "credit" : "credits"}). Approve it under Administration, Approvals, then run it there.`)}>Ask to reveal email</button>
            <button disabled={busy} onClick={() => run(() => post(`/api/contact-candidates/${c.id}/dismiss`), () => "Dismissed.")}>Dismiss</button></div>}</td></tr>)}</tbody></table></div>}
      {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} />
    </div>
  );
}

function SendPanel({ draft, orgId, can }: { draft: any; orgId: string; can: boolean }) {
  const [plan, setPlan] = useState<any>(null); const [sel, setSel] = useState<string[]>([]); const [en, setEn] = useState<any>({ items: [] });
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const load = () => { api(`/api/accounts/${orgId}/contacts-plan`).then(setPlan).catch(() => setPlan(null)); api(`/api/campaigns/${draft.id}/enrolments`).then(setEn).catch(() => undefined); };
  useEffect(load, [draft.id]);
  if (!plan) return null;
  const toggle = (id: string) => setSel((x) => x.includes(id) ? x.filter((i) => i !== id) : [...x, id]);
  const request = async () => { setErr(null); setMsg(""); try { const r = await post(`/api/campaigns/${draft.id}/enrol`, { person_ids: sel }); setMsg(`Approval requested for ${r.contacts} ${r.contacts === 1 ? "contact" : "contacts"}. A colleague approves it under Administration, Approvals.${r.ownership_checked ? "" : " We could not check who owns this account because it is not linked to Pipedrive yet."}`); setSel([]); load(); } catch (e) { setErr(e); } };
  return (
    <div className="card mt-3">
      <b>Send through Lemlist</b>
      <p className="small muted mt-1 mb-2">Only contacts with a checked email address can be used. We build the campaign in Lemlist but do not launch it: a person presses Launch there. {en.outbound_enabled ? "" : "Sending is switched off for now."}{en.lemlist_configured ? "" : " Lemlist is not connected yet."}</p>
      {plan.eligible.length === 0 && <p className="small">No contact here has a checked email address yet.</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can} /> {c.name}{c.title ? `, ${c.title}` : ""}</label>)}
      <p className="small muted">Not usable yet: {plan.needs_verification.length} found but unverified · {plan.manual_review} need manual review · {plan.no_email} without an email · {plan.suppressed} suppressed.</p>
      {can && <button className="primary" disabled={sel.length === 0} onClick={request}>Ask for approval to add {sel.length || ""}</button>}
      {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} />
      {en.items.length > 0 && <div className="table-wrap mt-3"><table><thead><tr><th>Contact</th><th>Status</th><th>Last activity</th></tr></thead><tbody>{en.items.map((x: any) => <tr key={x.id}><td>{x.full_name ?? "Contact"}</td><td><Badge kind={x.status === "interested" ? "ok" : x.status === "unsubscribed" || x.status === "bounced" ? "bad" : "info"}>{humanize(x.status)}</Badge></td><td>{x.last_event_at ? fmtDateTime(x.last_event_at) : "No reply yet"}</td></tr>)}</tbody></table></div>}
    </div>
  );
}

function CampaignTab({ camps, canDraft, orgId, make, decide }: { camps: any; canDraft: boolean; orgId: string; make: (role: string) => void; decide: (id: string, d: string) => void }) {
  const [role, setRole] = useState("");
  const [copied, setCopied] = useState("");
  const b = camps.items[0];
  const busy = b && (b.status === "queued" || b.status === "running");
  const copy = async (k: string, t: string) => { try { await navigator.clipboard.writeText(t); setCopied(k); setTimeout(() => setCopied(""), 1500); } catch { /* clipboard unavailable */ } };
  return (
    <div className="card">
      <p className="small muted mt-0">Three emails written from facts we have checked about this company, and tested against The Codest's brand rules. These are drafts: nothing is sent from here. {!camps.ownership_checked && camps.note}</p>
      {canDraft && <div className="row mb-3">
        <label className="small">Who is it for?<select id="camp-role" value={role} onChange={(e) => setRole(e.target.value)} className="ml-2"><option value="">Technology or delivery leader</option><option>CTO</option><option>Head of Engineering</option><option>Head of Payments</option><option>Head of Delivery or PMO</option><option>Procurement lead</option></select></label>
        <button className="primary" disabled={busy} onClick={() => make(role)}>Write outreach sequence</button></div>}
      {!b && <p className="muted">No draft yet. Write a research brief first for the best result, because the emails are built from what it finds.</p>}
      {busy && <p>Writing and checking the draft. This takes about a minute.</p>}
      {b?.status === "failed" && <Err e={new Error(b.error || "The draft could not be written.")} />}
      {b && (b.status === "draft" || b.status === "needs_work" || b.status === "approved" || b.status === "rejected") && <>
        <div className="row mt-2 mb-3">
          <Badge kind={b.status === "approved" ? "ok" : b.status === "draft" ? "info" : b.status === "rejected" ? "" : "warn"}>{b.status === "draft" ? "Passed the brand review" : b.status === "needs_work" ? "Needs work" : humanize(b.status)}</Badge>
          <span className="small muted">{b.rounds > 1 ? `Rewritten ${b.rounds === 2 ? "once" : `${b.rounds - 1} times`} to fix rule breaks. ` : ""}Written {fmtDateTime(b.finished_at)}{b.decided_by ? ` · ${b.status} by ${b.decided_by}` : ""}</span></div>
        {b.review?.breaks?.length > 0 && <div className="err" role="alert"><b>Fix these before approving:</b><ul className="tight">{b.review.breaks.map((x: any, i: number) => <li key={i}>{x.step ? `Email ${x.step}: ` : ""}{x.detail}</li>)}</ul></div>}
        {(b.steps ?? []).map((st: any, i: number) => (
          <article key={i} className="fcard">
            <div className="row spread"><b>Email {i + 1}</b><span className="row small muted">{st.day === 0 ? "Day 0" : `Day ${st.day}`}<button className="chip" onClick={() => copy(`${b.id}-${i}`, `Subject: ${st.subject}\n\n${st.body}`)}>{copied === `${b.id}-${i}` ? "Copied" : "Copy"}</button></span></div>
            <p className="mt-2 mb-1"><b>Subject:</b> {st.subject}</p>
            <div className="pre-wrap">{st.body}</div>
            
          </article>))}
        {canDraft && b.status === "draft" && <div className="row"><button className="primary" onClick={() => decide(b.id, "approve")}>Approve draft</button><button onClick={() => decide(b.id, "reject")}>Reject</button></div>}
        {b.status === "approved" && <SendPanel draft={b} orgId={orgId} can={canDraft} />}
        {b.facts && Object.keys(b.facts).length > 0 && <details className="mt-3"><summary>Facts these emails are built on</summary><ul className="tight small">{Object.entries(b.facts).map(([k, v]: any) => <li key={k}><b>{k}</b> {v}</li>)}</ul></details>}
      </>}
    </div>
  );
}

const TABS = ["Overview", "Research brief", "Outreach", "People", "Background"];
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "Funded project, need not known", D4: "Shows interest" };
const H = ({ children }: { children: React.ReactNode }) => <h2 className="h-sub">{children}</h2>;

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState(TABS[0]);
  const [sigNames, setSigNames] = useState<Record<string, string>>({});
  useEffect(() => { api("/api/library/signals").then((r) => setSigNames(Object.fromEntries(r.items.map((x: any) => [x.rule_id, x.signal])))).catch(() => undefined); }, []);
  const [err, setErr] = useState<unknown>(null);
  const [eps, setEps] = useState<any[]>([]);
  const [scan, setScan] = useState<{ busy: boolean; msg: string; err: unknown }>({ busy: false, msg: "", err: null });
  useEffect(() => { api(`/api/accounts/${id}`).then(setD).catch(setErr); api(`/api/accounts/${id}/endpoints`).then((r) => setEps(r.items)).catch(() => setEps([])); }, [id]);
  // every hook runs on every render, before any early return (React rule of hooks)
  const [briefs, setBriefs] = useState<any[]>([]);
  const loadBriefs = () => api(`/api/accounts/${id}/briefs`).then((r) => setBriefs(r.items)).catch(() => setBriefs([]));
  useEffect(() => { loadBriefs(); }, [id]);
  useEffect(() => { if (!briefs.some((b) => b.status === "queued" || b.status === "running")) return; const t = setInterval(() => { if (!document.hidden) loadBriefs(); }, 6000); return () => clearInterval(t); }, [briefs]);
  const [camps, setCamps] = useState<any>({ items: [] });
  const loadCamps = () => api(`/api/accounts/${id}/campaigns`).then(setCamps).catch(() => setCamps({ items: [] }));
  useEffect(() => { loadCamps(); }, [id]);
  useEffect(() => { if (!camps.items.some((b: any) => b.status === "queued" || b.status === "running")) return; const t = setInterval(() => { if (!document.hidden) loadCamps(); }, 6000); return () => clearInterval(t); }, [camps]);
  if (err) return <><p className="small mt-0"><Link to="/accounts">Accounts</Link></p><Err e={err} onRetry={reloadPage} /></>;
  if (!d) return <><p className="small mt-0"><Link to="/accounts">Accounts</Link></p><Skeleton rows={4} label="Loading the account" /></>;
  const a = d.account;
  const canScan = me.permissions.includes("research.request");
  const makeCampaign = async (role: string) => { try { await post(`/api/accounts/${id}/campaign`, { role: role || null }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const decideCampaign = async (did: string, decision: string) => { try { await post(`/api/campaigns/${did}/decision`, { decision }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const makeBrief = async () => { try { await post(`/api/accounts/${id}/brief`); await loadBriefs(); setTab("Research brief"); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const runScan = async () => {
    setScan({ busy: true, msg: "", err: null });
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? `Reading ${r.website} and searching the web. New findings appear in the Market feed once we have checked them. Reload this page in a minute.` : "Started. This account has no website on record, so we will only run the web search.", err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  const blank = (v: any) => (v === null || v === undefined || v === "" ? "Not known" : v);
  const briefBusy = briefs.some((b) => b.status === "queued" || b.status === "running");
  return (
    <>
      <p className="small mt-0 mb-2"><Link to="/accounts">Accounts</Link> / {a.display_name}</p>
      <div className="row spread"><h1 className="m-0 title-sm">{a.display_name}</h1>{canScan && <div className="row"><button className="primary" disabled={briefBusy} onClick={makeBrief}>Write research brief</button><button disabled={scan.busy} onClick={runScan}>Check for news</button></div>}</div>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      <div className="row mt-3 mb-3"><Badge kind={a.in_master_list ? "ok" : "info"}>{a.in_master_list ? "On your lists" : "New company"}</Badge>{a.country_code && <Badge>{a.country_code}</Badge>}<Badge kind={d.ownership_verified ? "ok" : "warn"}>{d.ownership_verified ? `Pipedrive owner ${d.crm.owner_id}` : "No owner in Pipedrive"}</Badge>
        {a.employees_estimate && <Badge>{a.employees_estimate.toLocaleString()} staff{a.employees_uncertain ? " (estimate)" : ""}</Badge>}</div>
      {d.score && <div className="card"><div className="row spread"><div><span className="eyebrow" title="Adds up this account's recent checked signals. Different kinds count for more than repeats, and each signal fades as it ages. Hot is 9 or more, warm 4 or more. It is not a probability.">Signal score</span><div className="kpi-n mt-1 mb-1">{d.score.stopped ? "On hold" : d.score.score}<span className="kpi-u"> {BAND[d.score.band][1]}</span></div></div>
        <div className="small measure-sm">{d.score.stopped ? "Something we found says not to pursue this account now, so the score is held at zero until it expires." : `${d.score.signal_count} ${d.score.signal_count === 1 ? "signal" : "signals"} (${d.score.groups.map(groupName).join(", ")}). Different kinds of signal agreeing count for more than the same kind repeated, and each signal fades as it ages. This is a ranking, not a probability.`}
          {d.score.top?.length > 0 && <div className="chips mt-2">{d.score.top.map((t: any) => <Badge key={t.rule_id}>{sigNames[t.rule_id] ?? t.name ?? t.rule_id}</Badge>)}</div>}</div></div></div>}
      <Tabs items={TABS.map((t) => ({ key: t, label: t }))} value={tab} onChange={setTab} label="Account sections" />
      {tab === "Overview" && (<>
        <H>Opportunities</H>
        {d.opportunities.length === 0 && <div className="empty"><b>No opportunities yet</b>One appears when checked signals point to a concrete piece of work. Press “Check for news” to look for some.</div>}
        {d.opportunities.map((o: any) => (
          <div key={o.hypothesis_id} className="card"><div className="row spread"><div><Badge kind={priorityKind(o.priority)}>{lab("priority", o.priority)}</Badge> <b>{o.initiative.title}</b> · {lab("route", o.route.type)} ({o.route.counterparty})</div><Link to={`/opportunities/${o.hypothesis_id}`}>Open</Link></div>
            <p className="small">{o.plain_reason ?? whyShort(o.why_it_matters)}</p>{o.next_action && <p className="small"><b>Next:</b> {o.next_action.objective}</p>}</div>))}
      </>)}
      {tab === "Research brief" && <BriefTab briefs={briefs} />}
      {tab === "Outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={String(id)} make={makeCampaign} decide={decideCampaign} />}
      {tab === "People" && <>
        <FindContacts orgId={String(id)} can={me.permissions.includes("draft.create")} />
        {me.permissions.includes("contacts.read") ? <div className="card table-wrap"><table><thead><tr><th>Name</th><th>Job title (as reported)</th><th>Area</th><th>Role confirmed</th><th>Email</th><th>Opted out</th></tr></thead><tbody>
          {d.buyers.map((b: any) => <tr key={b.id}><td>{b.name}</td><td>{blank(b.title)}</td><td>{blank(b.lane)}</td><td>{blank(b.role_verified)}</td><td>{humanize(b.email_status)}</td><td>{b.suppressed ? <Badge kind="bad">Yes</Badge> : "No"}</td></tr>)}
          {d.buyers.length === 0 && <tr><td colSpan={6} className="muted">No people recorded yet. Use “Find the people who decide” above.</td></tr>}</tbody></table>
          <p className="small muted">A job title does not prove someone holds the budget. Email addresses are not shown here, and every look at personal data is logged.</p></div> : <div className="empty">Your role cannot see contact details.</div>}</>}
      {tab === "Background" && <>
        <H>Programmes</H>
        {d.initiatives.length === 0 && <p className="small muted">No programmes found yet.</p>}
        {d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">Our working title</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">Budget not confirmed</Badge>}
          <p className="small">Technology: {i.technology_tags.join(", ") || "Not known"} · last big news {fmtDate(i.last_material_event_at)} · next milestone {fmtDate(i.next_milestone_at)}</p>
          {i.research_gaps.length > 0 && <><span className="eyebrow">Still to find out</span><ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></>}</div>)}
        <H>Signals</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Signals table"><table><thead><tr><th>Signal</th><th>What it says about demand</th><th>Status</th><th>What happened</th><th>Date</th><th>Expires</th></tr></thead><tbody>
          {d.signals.map((x: any, i: number) => <tr key={i}><td>{sigNames[x.rule_id] ?? x.rule_id}</td><td className="small">{DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier)}</td><td><Badge kind={x.status === "active" ? "ok" : ""}>{x.status === "active" ? "Current" : humanize(x.status)}</Badge></td><td>{x.summary}</td><td>{fmtDate(x.event_at ?? x.pub_at)}</td><td>{fmtDate(x.expires_at)}</td></tr>)}
          {d.signals.length === 0 && <tr><td colSpan={6} className="muted">No signals yet.</td></tr>}</tbody></table></div>
        <H>Suppliers</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Suppliers table"><table><thead><tr><th>Supplier</th><th>Role</th><th>What they do here</th><th>Relationship</th><th>Exclusive?</th></tr></thead><tbody>{d.suppliers.map((x: any, i: number) => <tr key={i}><td>{x.supplier_name}</td><td>{humanize(x.supplier_role)}</td><td>{blank(x.scope_text)}</td><td>{humanize(x.relation)}</td><td>{blank(x.exclusivity)}</td></tr>)}
          {d.suppliers.length === 0 && <tr><td colSpan={5} className="muted">No suppliers recorded yet.</td></tr>}</tbody></table>
          <p className="small muted">A named supplier does not prove they have exclusive rights or cover everything. This is for this legal entity only.</p></div>
        <H>Pipedrive</H>
        <div className="card"><p className="mt-0">{d.crm ? <>Linked to Pipedrive organisation {d.crm.crm_id}, with {d.crm.open_deals_count ?? "an unknown number of"} open {d.crm.open_deals_count === 1 ? "deal" : "deals"}. Pipedrive data last copied {fmtDateTime(d.crm.synced_at)}. Pipedrive is the source of truth for owner and deal stage.</> : "Not linked to Pipedrive. We cannot check who owns this account, so outreach and Pipedrive requests are switched off for it."}</p>
          {d.source_rows.length > 0 && <><span className="eyebrow">Where it appears in your workbooks</span><ul className="tight small">{d.source_rows.map((r: any, i: number) => <li key={i}>{r.sheet_title}, row {r.row_number}</li>)}</ul></>}</div>
        <H>Where we look for news</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Coverage table"><p className="small mt-0">{d.source_coverage_note}</p><table><thead><tr><th>Kind of source</th><th>State</th><th>Last worked</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{humanize(c.source_family)}</td><td>{humanize(c.coverage_state)}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
          {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">We have not checked this account's sources yet. That means we cannot see, not that nothing is happening.</td></tr>}</tbody></table>
          <h3>Where this company publishes</h3>
          {eps.length === 0 ? <p className="small muted">Nothing found yet. Press “Check for news” to look for its news feed, press page, careers page and job board.</p>
            : <table><thead><tr><th>Kind</th><th>Address</th><th>Last read</th><th>Items</th></tr></thead><tbody>{eps.map((e, i) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small break-all">{e.url}</td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? "Worked" : "Problem"}</Badge>{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : "Not yet"}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}</div>
        <H>Sources for its opportunities</H>{d.opportunities.length === 0 ? <p className="small muted">None yet.</p> : d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</>}
    </>
  );
}
