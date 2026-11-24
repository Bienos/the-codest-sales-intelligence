import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { SignalSummary } from "../components/BuyingSignal";
import Prospects from "./Prospects";
import { REGIONS, countryName, regionName, staffCount } from "./targetMarket";
import { Badge, Err, Evidence, groupName, humanize, lab, priorityKind, Skeleton, reloadPage, whyShort, Tabs } from "../components/ui";

const del = (p: string) => api(p, { method: "DELETE" });

function AddCompany({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [f, setF] = useState({ website: "", name: params.get("name") ?? "", links: "", notes: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { const r = await post("/api/accounts/add", { website: f.website, name: f.name || null, notes: f.notes || null, links: f.links.split("\n").map((x) => x.trim()).filter(Boolean) }); nav(`/accounts/${r.id}?profile=1`); }
    catch (x) { setErr(x); } finally { setBusy(false); }
  };
  return (
    <form className="card" onSubmit={submit}>
      <div className="row spread"><b>Add a company</b><button type="button" className="ghost" onClick={onClose}>Close</button></div>
      <p className="small muted mt-2 mb-3">Start with the company website. You can confirm its ownership, sector and headcount on the next screen.</p>
      <div className="addgrid">
        <label className="small">Website (required)<input id="add-website" required placeholder="example.com" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
        <label className="small">Company name (optional)<input id="add-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      </div>
      <label className="small block mt-3">Supporting links, one per line (optional)<textarea id="add-links" rows={3} value={f.links} onChange={(e) => setF({ ...f, links: e.target.value })} placeholder="https://…" /></label>
      <label className="small block mt-3">Why this company? (optional)<textarea id="add-notes" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <Err e={err} />
      <button className="primary" disabled={busy || f.website.trim().length < 3}>Add and research</button>
    </form>
  );
}

const BAND: Record<string, ["ok" | "warn" | "info" | "bad" | "", string]> = { hot: ["bad", "Hot"], warm: ["warn", "Warm"], watch: ["info", "Watch"], none: ["", "No signal"] };
type Filters = { band: string; signal_days: string; shortlist: string; watched: string; sort: string };
const EMPTY: Filters = { band: "", signal_days: "", shortlist: "", watched: "", sort: "score" };
const PRESETS: [string, Partial<Filters>][] = [
  ["Hot accounts", { band: "hot" }],
  ["Activity in the last 30 days", { signal_days: "30" }],
  ["Watchlist", { watched: "true" }],
  ["Shortlist", { shortlist: "true" }],
];
const ORIGIN: Record<string, string> = { list: "From your workbooks", added: "Added by the team", discovered: "Found through research" };

export function Accounts({ me }: { me: { permissions: string[] } }) {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "leads" ? "leads" : params.get("view") === "review" && me.permissions.includes("research.request") ? "review" : "accounts";
  const q = params.get("q") ?? "";
  const requestedRegion = params.get("region") ?? "";
  const region = REGIONS.some(([key]) => key === requestedRegion) ? requestedRegion : "";
  const adding = params.get("add") === "1";
  const update = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };
  const research = me.permissions.includes("research.request");

  return (
    <>
      <div className="page-head directory-heading">
        <div><h1>Accounts &amp; leads</h1><p className="sub">Privately held banks and financial services companies. Over 500 employees in Europe and the UK, or over 2,000 elsewhere.</p></div>
        {research && view !== "leads" && <button className="primary" onClick={() => update({ add: adding ? "" : "1" })}>{adding ? "Cancel" : "Add a company"}</button>}
      </div>
      {adding && research && view !== "leads" && <AddCompany onClose={() => update({ add: "", name: "" })} />}
      <Tabs label="Company workspace" value={view} onChange={(key) => update({ view: key === "accounts" ? "" : key })} items={[
        { key: "accounts", label: "Accounts" },
        { key: "leads", label: "Leads" },
        ...(research ? [{ key: "review", label: "Ownership to check" }] : []),
      ]}>
        <div className="directory-filters">
          <label className="directory-search">Search {view === "leads" ? "leads" : "companies"}
            <input aria-label={view === "leads" ? "Search leads" : "Search companies"} type="search" maxLength={120} placeholder={view === "leads" ? "Company, website or buying signal" : "Company, website, sector or country code"} value={q} onChange={(e) => update({ q: e.target.value })} />
          </label>
          <label>Region
            <select aria-label="Region" value={region} onChange={(e) => update({ region: e.target.value })}>
              <option value="">All regions</option>
              {REGIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
        </div>
        {view === "leads" ? <Prospects q={q} region={region} canVerify={research} /> :
          <CompanyDirectory key={view} q={q} region={region} research={research} review={view === "review"} clearSearch={() => update({ q: "", region: "" })} />}
      </Tabs>
    </>
  );
}

function CompanyDirectory({ q, region, research, review, clearSearch }: { q: string; region: string; research: boolean; review: boolean; clearSearch: () => void }) {
  const [f, setF] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState({ page: 1, q, region });
  const page = offset.q === q && offset.region === region ? offset.page : 1;
  const scope = review ? "review" : "target";
  const query = new URLSearchParams(Object.entries({ ...f, q, region, page: String(page), scope }).filter(([, value]) => value !== "")).toString();
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [selection, setSelection] = useState<{ query: string; ids: string[] }>({ query, ids: [] });
  const selected = selection.query === query ? selection.ids : [];
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    const timer = setTimeout(() => {
      api(`/api/leads?${query}`, { signal: request.signal })
        .then((data) => { if (!request.signal.aborted) setResult({ query, data }); })
        .catch((error) => { if (!request.signal.aborted) setErr(error); });
    }, q ? 250 : 0);
    return () => { clearTimeout(timer); request.abort(); };
  }, [query, q, retry, scope]);

  const setPage = (next: number) => setOffset({ page: next, q, region });
  const filters = (next: Filters) => { setF(next); setPage(1); };
  const change = (key: keyof Filters, value: string) => filters({ ...f, [key]: value });
  const toggle = (id: string) => setSelection({ query, ids: selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id] });
  const toggleWatch = async (r: any) => {
    try {
      await (r.watched ? del(`/api/accounts/${r.id}/watch`) : post(`/api/accounts/${r.id}/watch`));
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const decide = async (r: any, decision: "qualify" | "reject") => {
    try {
      await post(`/api/leads/${r.id}/qualify`, { decision });
      setSelection({ query, ids: selected.filter((id) => id !== r.id) });
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const exportCsv = async () => {
    try {
      const t = sessionStorage.getItem("ri_token");
      const response = await fetch(`/api/leads.csv?${query}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
      if (!response.ok) throw new Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "accounts.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) { setErr(e); }
  };
  const scanSelected = async () => {
    setErr(null);
    setMsg("");
    try {
      const r = await post("/api/leads/scan", { ids: selected, with_search: false });
      setMsg(`Checking ${r.queued} ${r.queued === 1 ? "company" : "companies"} for news. Findings will appear in the Market feed.`);
      setSelection({ query, ids: [] });
    } catch (e) { setErr(e); }
  };

  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  const visiblePage = d?.page ?? page;
  const groups = REGIONS.map(([key, title]) => ({ key, title, items: d?.items.filter((item: any) => item.region === key) ?? [] })).filter(({ items }) => items.length > 0);
  return (
    <section aria-label={review ? "Companies to review" : "Matching accounts"}>
      {review ? <p className="small muted">These companies match the sector and size filters. Open a profile to record who owns the company and where you checked.</p> :
        <div className="directory-toolbar">
          <div className="directory-presets" aria-label="Quick filters">
            {PRESETS.map(([label, filter]) => {
              const active = Object.entries(filter).every(([key, value]) => f[key as keyof Filters] === value);
              return <button key={label} className="chip" aria-pressed={active} onClick={() => filters({ ...EMPTY, sort: f.sort, ...(!active ? filter : {}) })}>{label}</button>;
            })}
          </div>
          <div className="row">
            <select aria-label="Sort accounts within each region" value={f.sort} onChange={(e) => change("sort", e.target.value)}>
              <option value="score">Strongest signals</option><option value="recent">Latest signal</option><option value="size">Largest first</option><option value="name">Company name</option>
            </select>
            <button disabled={!d?.total} onClick={exportCsv}>Export CSV</button>
            {selected.length > 0 && research && <button onClick={scanSelected}>Check {selected.length} for news</button>}
          </div>
        </div>}
      {msg && <p className="okmsg" role="status">{msg}</p>}
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!d && !err && <Skeleton label="Loading companies" />}
      {d && <p className="small muted directory-count" role="status">{d.total.toLocaleString()} {d.total === 1 ? "company" : "companies"}{regionName(region) && ` in ${regionName(region)}`}</p>}
      {d && d.items.length === 0 && <div className="empty">
        <b>No matching companies</b>
        {q || region || f.watched || f.shortlist || f.signal_days || f.band ?
          <button onClick={() => { filters(EMPTY); clearSearch(); }}>Clear filters</button> :
          review ? "There are no matching profiles waiting for an ownership check." : <>Matching companies appear here once their ownership is recorded with a source.{research && <> <Link to="/accounts?view=review">Review company ownership</Link>.</>}</>}
      </div>}
      {d && d.items.length > 0 && <>
        {groups.map(({ key, title, items }) => <section key={key} className="directory-region">
        {!region && <h2>{title}</h2>}
        <div className="card table-wrap flush" tabIndex={0} role="region" aria-label={`${title} company directory`}>
          <table className="directory-table"><thead><tr>
            <th>Company</th><th>Headquarters</th><th>Size</th><th>{review ? "Ownership" : "Buying signal and source"}</th><th>{review ? "Profile" : "Actions"}</th>
          </tr></thead><tbody>
            {items.map((r: any) => <tr key={r.id}>
              <td className="directory-company"><Link to={`/accounts/${r.id}${review ? "?profile=1" : ""}`}>{r.name}</Link>
                <span className="small muted">{[r.segment, r.canonical_domain].filter(Boolean).join(" · ")}</span>
                <span className="small muted">{r.shortlist ? "On your shortlist" : ORIGIN[r.origin]}</span>
              </td>
              <td data-label="Headquarters">{countryName(r.country_code)}</td>
              <td data-label="Size">{staffCount(r.employees, r.employees_uncertain)}</td>
              <td data-label={review ? "Ownership" : "Buying signal and source"}>
                {!review && r.buying_signal && <SignalSummary signal={r.buying_signal} />}
                {review ? "To check" : r.stopped ? <Badge kind="warn">On hold</Badge> : !r.buying_signal && r.signal_count > 0 ?
                  <><Badge kind={BAND[r.band]?.[0]}>{BAND[r.band]?.[1] ?? "Activity"}</Badge> <span className="small">{r.signal_count} checked {r.signal_count === 1 ? "signal" : "signals"}</span>
                    <span className="small muted">{[r.groups.map(groupName).join(", "), r.last_signal_at && fmtDate(r.last_signal_at)].filter(Boolean).join(" · ")}</span></> :
                  !r.buying_signal && <span className="small muted">No checked signals yet</span>}
              </td>
              <td data-label={review ? "Profile" : "Actions"}>
                {review ? <Link to={`/accounts/${r.id}?profile=1`}>Check ownership</Link> : <>
                  <div className="directory-actions">
                    {research && <label className="hit44"><input type="checkbox" aria-label={`Select ${r.name} for a news check`} checked={selected.includes(r.id)} disabled={!selected.includes(r.id) && selected.length === 25} onChange={() => toggle(r.id)} /></label>}
                    <button className="star hit44" aria-pressed={r.watched} aria-label={r.watched ? `Stop watching ${r.name}` : `Watch ${r.name}`} onClick={() => toggleWatch(r)}>{r.watched ? "★" : "☆"}</button>
                  </div>
                  {r.candidate_status === "pending_qualification" && research && <div className="row"><button onClick={() => decide(r, "qualify")}>Confirm account</button><button onClick={() => decide(r, "reject")}>Remove from list</button></div>}
                </>}
              </td>
            </tr>)}
          </tbody></table>
        </div></section>)}
        {pages > 1 && <div className="directory-pagination"><button disabled={visiblePage <= 1} onClick={() => setPage(visiblePage - 1)}>Previous</button><span>Page {visiblePage} of {pages}</span><button disabled={visiblePage >= pages} onClick={() => setPage(visiblePage + 1)}>Next</button></div>}
      </>}
    </section>
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
      {c.summary?.length > 0 && <><h2 className="h-sub">What the company is and does</h2><Cited items={c.summary} /></>}
      {c.signals?.length > 0 && <><h2 className="h-sub">Signals</h2><Cited items={c.signals} /></>}
      {c.hiring?.length > 0 && <><h2 className="h-sub">Hiring</h2><Cited items={c.hiring} /></>}
      {c.icp_fit?.read && <><h2 className="h-sub">Does it fit our target customer?</h2><p>{c.icp_fit.read}</p>{c.icp_fit.reasons?.length > 0 && <Cited items={c.icp_fit.reasons} />}</>}
      {c.open_questions?.length > 0 && <><h2 className="h-sub">Still to find out</h2><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></>}
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
      <p className="small muted mt-1 mb-2">Searching is free and gives names and job titles. {d.reveal_price == null ? "The cost per email is not set yet. Ask an administrator." : `Revealing an email costs ${d.reveal_price} ${d.reveal_price === 1 ? "credit" : "credits"} per person and needs approval from another person.`} {d.apollo_configured ? "" : "Apollo is not connected yet. "}{d.paid_enrichment_enabled ? "" : "Revealing emails is switched off for now."}</p>
      {d.items.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label="Name · Job title · Email"><table><thead><tr><th>Name</th><th>Job title</th><th>Email</th><th><span className="sr">Actions</span></th></tr></thead><tbody>
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
  const unavailable = [
    [plan.needs_verification.length, "found but unverified"], [plan.manual_review, "need manual review"],
    [plan.no_email, "without an email"], [plan.suppressed, "opted out"],
  ].filter(([count]) => Number(count) > 0).map(([count, reason]) => `${count} ${reason}`);
  const toggle = (id: string) => setSel((x) => x.includes(id) ? x.filter((i) => i !== id) : [...x, id]);
  const request = async () => { setErr(null); setMsg(""); try { const r = await post(`/api/campaigns/${draft.id}/enrol`, { person_ids: sel }); setMsg(`Approval requested for ${r.contacts} ${r.contacts === 1 ? "contact" : "contacts"}. A colleague approves it under Administration, Approvals.${r.ownership_checked ? "" : " We could not check who owns this account because it is not linked to Pipedrive yet."}`); setSel([]); load(); } catch (e) { setErr(e); } };
  return (
    <div className="card mt-3">
      <b>Send through Lemlist</b>
      <p className="small muted mt-1 mb-2">Only contacts with a checked email address can be used. We build the campaign in Lemlist but do not launch it: a person presses Launch there. {en.outbound_enabled ? "" : "Sending is switched off for now."}{en.lemlist_configured ? "" : " Lemlist is not connected yet."}</p>
      {plan.eligible.length === 0 && <p className="small">No contact here has a checked email address yet.</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can} /> {c.name}{c.title ? `, ${c.title}` : ""}</label>)}
      {unavailable.length > 0 && <p className="small muted">Unavailable for outreach: {unavailable.join(" · ")}.</p>}
      {can && <button className="primary" disabled={sel.length === 0} onClick={request}>Ask for approval to add {sel.length || ""}</button>}
      {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} />
      {en.items.length > 0 && <div className="table-wrap mt-3" tabIndex={0} role="region" aria-label="Contact · Status · Last activity"><table><thead><tr><th>Contact</th><th>Status</th><th>Last activity</th></tr></thead><tbody>{en.items.map((x: any) => <tr key={x.id}><td>{x.full_name ?? "Contact"}</td><td><Badge kind={x.status === "interested" ? "ok" : x.status === "unsubscribed" || x.status === "bounced" ? "bad" : "info"}>{humanize(x.status)}</Badge></td><td>{x.last_event_at ? fmtDateTime(x.last_event_at) : "No reply yet"}</td></tr>)}</tbody></table></div>}
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

const TABS = ["Overview", "Research brief", "Outreach", "People", "Signals & sources"];
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "Checked programme, outside demand unknown", D4: "Attention, outside demand unknown" };
const H = ({ children }: { children: React.ReactNode }) => <h2 className="h-sub">{children}</h2>;

const OWNERSHIP: Record<string, string> = {
  private: "Privately held",
  listed: "Publicly listed",
  state_owned: "State-owned",
  unknown: "Ownership to check",
};

function CompanyProfile({ account, onSave }: { account: any; onSave: (profile: any) => void }) {
  const [form, setForm] = useState({
    ownership: account.company_ownership ?? "unknown", source_url: account.company_profile_source_url ?? "",
    country_code: account.country_code ?? "", segment: account.segment ?? "", employees: account.employees_estimate ?? "",
    employees_uncertain: account.employees_uncertain ?? false,
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setSaved(false);
    setErr(null);
    try {
      onSave(await put(`/api/accounts/${account.id}/company-profile`, {
        ...form, country_code: form.country_code || null, employees: form.employees ? Number(form.employees) : null,
      }));
      setSaved(true);
    } catch (error) { setErr(error); }
    finally { setBusy(false); }
  };
  return <form className="card company-profile" onSubmit={save}>
    <h2>Company details</h2>
    <p className="small muted">Use the company register, annual report or company website to confirm ownership. For a subsidiary, check who owns the parent company.</p>
    <div className="directory-filters">
      <label>Ownership<select value={form.ownership} onChange={(e) => setForm({ ...form, ownership: e.target.value })}>
        {Object.entries(OWNERSHIP).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
      </select></label>
      <label>Ownership source<input type="url" maxLength={1000} value={form.source_url} required={form.ownership !== "unknown"} placeholder="https://…" onChange={(e) => setForm({ ...form, source_url: e.target.value })} /></label>
      <label>Headquarters country code<input maxLength={2} pattern="[A-Z]{2}" value={form.country_code} placeholder="e.g. GB" onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })} /></label>
      <label>Sector<input maxLength={80} value={form.segment} placeholder="e.g. Banking or Payments" onChange={(e) => setForm({ ...form, segment: e.target.value })} /></label>
      <label>Employees<input type="number" min={1} max={2147483647} value={form.employees} onChange={(e) => setForm({ ...form, employees: e.target.value })} /></label>
      <label className="profile-estimate"><input type="checkbox" checked={form.employees_uncertain} onChange={(e) => setForm({ ...form, employees_uncertain: e.target.checked })} /> Headcount is an estimate</label>
    </div>
    <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save company details"}</button>
    {saved && <span className="small ml-2" role="status">Saved.</span>}
    <Err e={err} />
  </form>;
}

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [params] = useSearchParams();
  const [editing, setEditing] = useState(params.get("profile") === "1");
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState(params.get("event") ? "Signals & sources" : TABS[0]);
  const [signalStatus, setSignalStatus] = useState("all");
  const [signalTier, setSignalTier] = useState("all");
  const [signalOrder, setSignalOrder] = useState("newest");
  const [sigNames, setSigNames] = useState<Record<string, string>>({});
  useEffect(() => { api("/api/library/signals").then((r) => setSigNames(Object.fromEntries(r.items.map((x: any) => [x.rule_id, x.signal])))).catch(() => undefined); }, []);
  const [err, setErr] = useState<unknown>(null);
  const [eps, setEps] = useState<any[]>([]);
  const [scan, setScan] = useState<{ busy: boolean; msg: string; err: unknown }>({ busy: false, msg: "", err: null });
  useEffect(() => { api(`/api/accounts/${id}?target=true`).then(setD).catch(setErr); api(`/api/accounts/${id}/endpoints`).then((r) => setEps(r.items)).catch(() => setEps([])); }, [id]);
  // every hook runs on every render, before any early return (React rule of hooks)
  const [briefs, setBriefs] = useState<any[]>([]);
  const loadBriefs = () => api(`/api/accounts/${id}/briefs`).then((r) => setBriefs(r.items)).catch(() => setBriefs([]));
  useEffect(() => { loadBriefs(); }, [id]);
  useEffect(() => { if (!briefs.some((b) => b.status === "queued" || b.status === "running")) return; const t = setInterval(() => { if (!document.hidden) loadBriefs(); }, 6000); return () => clearInterval(t); }, [briefs]);
  const [camps, setCamps] = useState<any>({ items: [] });
  const loadCamps = () => api(`/api/accounts/${id}/campaigns`).then(setCamps).catch(() => setCamps({ items: [] }));
  useEffect(() => { loadCamps(); }, [id]);
  useEffect(() => { if (!camps.items.some((b: any) => b.status === "queued" || b.status === "running")) return; const t = setInterval(() => { if (!document.hidden) loadCamps(); }, 6000); return () => clearInterval(t); }, [camps]);
  useEffect(() => {
    const event = params.get("event");
    if (d && tab === "Signals & sources" && event) document.getElementById(`event-${event}`)?.scrollIntoView({ block: "center" });
  }, [d, tab, params]);
  if (!d) return <>
    <p className="small mt-0"><Link to="/accounts">Accounts</Link></p><h1 className="title-sm">Account details</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label="Loading the account" />}
  </>;
  const signalGroups = new Map<string, any[]>();
  for (const signal of d.signals) {
    if ((signalStatus !== "all" && signal.status !== signalStatus) || (signalTier !== "all" && signal.demand_tier !== signalTier)) continue;
    const key = signal.story_key ?? signal.event_id;
    const group = signalGroups.get(key) ?? [];
    group.push(signal);
    signalGroups.set(key, group);
  }
  const signalStories = [...signalGroups.values()];
  signalStories.sort(([a], [b]) => {
    const dateA = a.event_at ?? a.pub_at;
    const dateB = b.event_at ?? b.pub_at;
    if (!dateA) return dateB ? 1 : 0;
    if (!dateB) return -1;
    const order = Date.parse(dateA) - Date.parse(dateB);
    return signalOrder === "oldest" ? order : -order;
  });
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
  return (
    <>
      <p className="small mt-0 mb-2"><Link to="/accounts">Accounts</Link> / {a.display_name}</p>
      <div className="row spread"><h1 className="m-0 title-sm">{a.display_name}</h1><div className="row">
        {(briefs.length > 0 || canScan) && <button className="primary" onClick={briefs.length ? () => setTab("Research brief") : makeBrief}>{briefs.length ? "Open brief" : "Write research brief"}</button>}
        {canScan && <button disabled={scan.busy} onClick={runScan}>Check for news</button>}
      </div></div>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      <div className="card account-facts">
        <dl>
          <div><dt>Sector</dt><dd>{a.segment || "Sector unknown"}</dd></div>
          <div><dt>Headquarters</dt><dd>{countryName(a.country_code) || "Country unknown"}</dd></div>
          <div><dt>Size</dt><dd>{staffCount(a.employees_estimate, a.employees_uncertain)}</dd></div>
          <div><dt>Ownership</dt><dd>{OWNERSHIP[a.company_ownership] ?? OWNERSHIP.unknown}{a.company_profile_source_url && <> · <a href={a.company_profile_source_url} target="_blank" rel="noreferrer noopener">View source ↗</a></>}</dd></div>
        </dl>
        <div className="row spread">
          <div className="row small">
            {a.canonical_domain && <a href={`https://${a.canonical_domain}`} target="_blank" rel="noreferrer noopener">{a.canonical_domain} ↗</a>}
            <span className="muted">{d.ownership_verified ? "Pipedrive owner confirmed" : "Pipedrive owner unknown"}</span>
          </div>
          {canScan && a.entity_type !== "public_body" && <button aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? "Close company details" : "Edit company details"}</button>}
        </div>
      </div>
      {editing && canScan && a.entity_type !== "public_body" && <CompanyProfile account={a} onSave={(profile) => setD({ ...d, account: { ...a, ...profile } })} />}
      {d.score && <div className="card"><div className="row spread"><div><span className="eyebrow" title="Adds up this account's recent checked signals. Different kinds count for more than repeats, and each signal fades as it ages. Hot is 9 or more, warm 4 or more. It is not a probability.">Signal score</span><div className="kpi-n mt-1 mb-1">{d.score.stopped ? "On hold" : d.score.score}<span className="kpi-u"> {BAND[d.score.band][1]}</span></div></div>
        <div className="small measure-sm">{d.score.stopped ? "Something we found says not to pursue this account now, so the score is held at zero until it expires." : `${d.score.signal_count} ${d.score.signal_count === 1 ? "signal" : "signals"} (${d.score.groups.map(groupName).join(", ")}). Recent signals carry more weight. A mix of signals raises the score more than repeated signals of the same kind.`}
          {d.score.top?.length > 0 && <div className="chips mt-2">{d.score.top.map((t: any) => <Badge key={t.rule_id}>{sigNames[t.rule_id] ?? t.name ?? t.rule_id}</Badge>)}</div>}</div></div></div>}
      <Tabs items={TABS.map((t) => ({ key: t, label: t }))} value={tab} onChange={setTab} label="Account sections">
      {tab === "Overview" && (<>
        <H>Strongest current buying signal</H>
        <div className="card"><SignalSummary signal={d.buying_signal} detailed /></div>
        <H>Opportunities</H>
        {d.opportunities.length === 0 && <div className="empty"><b>No opportunities yet</b>Opportunities appear when a checked source points to work this company might hire for.</div>}
        {d.opportunities.map((o: any) => (
          <div key={o.hypothesis_id} className="card"><div className="row spread"><div><Badge kind={priorityKind(o.priority)}>{lab("priority", o.priority)}</Badge> <b>{o.initiative.title}</b> · {lab("route", o.route.type)}{o.route.counterparty ? ` (${o.route.counterparty})` : ""} · {lab("access", o.route.access_status).toLowerCase()}</div><Link to={`/opportunities/${o.hypothesis_id}`}>Open</Link></div>
            <p className="small">{o.plain_reason ?? whyShort(o.why_it_matters)}</p>{o.next_action && <p className="small"><b>Next:</b> {o.next_action.objective}</p>}</div>))}
      </>)}
      {tab === "Research brief" && <><BriefTab briefs={briefs} />{canScan && briefs[0]?.status === "failed" && <button onClick={makeBrief}>Try writing the brief again</button>}</>}
      {tab === "Outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={String(id)} make={makeCampaign} decide={decideCampaign} />}
      {tab === "People" && <>
        <FindContacts orgId={String(id)} can={me.permissions.includes("draft.create")} />
        {me.permissions.includes("contacts.read") ? <div className="card table-wrap" tabIndex={0} role="region" aria-label="Name · Job title (as reported) · Area"><table><thead><tr><th>Name</th><th>Job title (as reported)</th><th>Area</th><th>Role confirmed</th><th>Email</th><th>Opted out</th></tr></thead><tbody>
          {d.buyers.map((b: any) => <tr key={b.id}><td>{b.name}</td><td>{blank(b.title)}</td><td>{blank(b.lane)}</td><td>{b.role_verified === "verified" ? "Confirmed" : b.role_verified === "unverified" ? "Not confirmed" : humanize(b.role_verified)}</td><td>{humanize(b.email_status)}</td><td>{b.suppressed ? <Badge kind="bad">Yes</Badge> : "No"}</td></tr>)}
          {d.buyers.length === 0 && <tr><td colSpan={6} className="muted">No people recorded yet. Use “Find the people who decide” above.</td></tr>}</tbody></table>
          <p className="small muted">A job title does not prove someone holds the budget. Email addresses are not shown here, and every look at personal data is logged.</p></div> : <div className="empty">Your role cannot see contact details.</div>}</>}
      {tab === "Signals & sources" && <>
        <H>Programmes</H>
        {d.initiatives.length === 0 && <p className="small muted">No programmes found yet.</p>}
        {d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">Our working title</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">Budget not confirmed</Badge>}
          <p className="small">Technology: {i.technology_tags.join(", ") || "Not known"} · last big news {fmtDate(i.last_material_event_at)} · next milestone {fmtDate(i.next_milestone_at)}</p>
          {i.research_gaps.length > 0 && <><span className="eyebrow">Still to find out</span><ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></>}</div>)}
        <H>Signals</H>
        <div className="row gap-2 mb-2">
          <label>Status <select value={signalStatus} onChange={e => setSignalStatus(e.target.value)}><option value="all">All signals</option><option value="active">Current</option><option value="historical">Historical</option></select></label>
          <label>Order <select value={signalOrder} onChange={e => setSignalOrder(e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label>
          <label>Evidence of demand <select value={signalTier} onChange={e => setSignalTier(e.target.value)}><option value="all">All levels</option><option value="D1">Explicit</option><option value="D2">Inferred</option><option value="D3">Programme</option><option value="D4">Context</option></select></label>
        </div>
        {signalStories.map(group => {
          const first = group[0];
          const eventIds = [...new Set(group.map(x => x.event_id))];
          const rules = new Map(group.map(x => [`${x.rule_id}:${x.status}:${x.demand_tier}`, x]));
          const evidence = new Map(group.flatMap(x => x.evidence).map(e => [e.claim_id, e]));
          return <div className="card" key={first.story_key ?? first.event_id}>
            {eventIds.map(eventId => <span id={`event-${eventId}`} key={eventId} />)}
            <b>{first.summary}</b><p className="small muted">{first.event_at || first.pub_at ? fmtDate(first.event_at ?? first.pub_at) : "Date unknown"}</p>
            {[...rules.entries()].map(([key, x]) => <p className="small" key={key}><Badge kind={x.status === "active" ? "ok" : ""}>{x.status === "active" ? "Current" : "Historical"}</Badge> {sigNames[x.rule_id] ?? x.rule_id} · {DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier)}</p>)}
            <Evidence items={[...evidence.values()]} />
          </div>;
        })}
        {signalStories.length === 0 && <p className="muted">{d.signals.length ? "No signals match these filters." : "No signals yet."}</p>}
        <H>Suppliers</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Suppliers table"><table><thead><tr><th>Supplier</th><th>Role</th><th>What they do here</th><th>Relationship</th><th>Exclusive?</th></tr></thead><tbody>{d.suppliers.map((x: any, i: number) => <tr key={i}><td>{x.supplier_name}</td><td>{humanize(x.supplier_role)}</td><td>{blank(x.scope_text)}</td><td>{humanize(x.relation)}</td><td>{blank(x.exclusivity)}</td></tr>)}
          {d.suppliers.length === 0 && <tr><td colSpan={5} className="muted">No suppliers recorded yet.</td></tr>}</tbody></table>
          <p className="small muted">A named supplier does not prove they have exclusive rights or cover everything. This is for this legal entity only.</p></div>
        <H>Pipedrive</H>
        <div className="card"><p className="mt-0">{d.crm ? <>Linked to Pipedrive organisation {d.crm.crm_id}, with {d.crm.open_deals_count ?? "an unknown number of"} open {d.crm.open_deals_count === 1 ? "deal" : "deals"}. Pipedrive data last copied {fmtDateTime(d.crm.synced_at)}. Pipedrive is the source of truth for owner and deal stage.</> : "Not linked to Pipedrive. We cannot check who owns this account, so outreach and Pipedrive requests are switched off for it."}</p>
          {d.source_rows.length > 0 && <><span className="eyebrow">Where it appears in your workbooks</span><ul className="tight small">{d.source_rows.map((r: any, i: number) => <li key={i}>{r.sheet_title}, row {r.row_number}</li>)}</ul></>}</div>
        <H>Where we look for news</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Coverage table"><p className="small mt-0">{d.source_coverage_note}</p><table><thead><tr><th>Kind of source</th><th>State</th><th>Last successful check</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{humanize(c.source_family)}</td><td>{humanize(c.coverage_state)}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
          {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">We have not checked this account's sources yet. That means we cannot see, not that nothing is happening.</td></tr>}</tbody></table>
          <h3>Where this company publishes</h3>
          {eps.length === 0 ? <p className="small muted">Nothing found yet. Press “Check for news” to look for its news feed, press page, careers page and job board.</p>
            : <table><thead><tr><th>Kind</th><th>Address</th><th>Last check</th><th>Items read</th></tr></thead><tbody>{eps.map((e, i) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small break-all"><a href={e.url} target="_blank" rel="noreferrer noopener">{e.url} ↗</a></td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? "Worked" : "Problem"}</Badge>{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : "Not yet"}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}</div>
        <H>Sources for its opportunities</H>{d.opportunities.length === 0 ? <p className="small muted">None yet.</p> : d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</>}
      </Tabs>
    </>
  );
}
