import { counted, number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { AccountFindings } from "../components/Findings";
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
      <div className="row spread"><b>{tr("Add a company")}</b><button type="button" className="ghost" onClick={onClose}>{tr("Close")}</button></div>
      <p className="small muted mt-2 mb-3">{tr("Start with the company website. You can confirm its ownership, sector and headcount on the next screen.")}</p>
      <div className="addgrid">
        <label className="small">{tr("Website (required)")}<input id="add-website" required placeholder="example.com" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
        <label className="small">{tr("Company name (optional)")}<input id="add-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      </div>
      <label className="small block mt-3">{tr("Supporting links, one per line (optional)")}<textarea id="add-links" rows={3} value={f.links} onChange={(e) => setF({ ...f, links: e.target.value })} placeholder="https://…" /></label>
      <label className="small block mt-3">{tr("Why this company? (optional)")}<textarea id="add-notes" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <Err e={err} />
      <button className="primary" disabled={busy || f.website.trim().length < 3}>{tr("Add and research")}</button>
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
        <div><h1>{tr("Accounts & leads")}</h1><p className="sub">{view === "leads" ? tr("Find buying signals from the workbooks and open the companies they belong to.") : tr("Privately held banks and financial services companies. Over 500 employees in Europe and the UK, or over 2,000 elsewhere.")}</p></div>
        {research && view !== "leads" && <button className="primary" onClick={() => update({ add: adding ? "" : "1" })}>{adding ? tr("Cancel") : tr("Add a company")}</button>}
      </div>
      {adding && research && view !== "leads" && <AddCompany onClose={() => update({ add: "", name: "" })} />}
      <Tabs label={tr("Company workspace")} value={view} onChange={(key) => update({ view: key === "accounts" ? "" : key })} items={[
        { key: "accounts", label: "Accounts" },
        { key: "leads", label: "Leads" },
        ...(research ? [{ key: "review", label: "Ownership to check" }] : []),
      ]}>
        <div className="directory-filters">
          <label className="directory-search">{view === "leads" ? tr("Search leads") : tr("Search companies")}
            <input aria-label={view === "leads" ? tr("Search leads") : tr("Search companies")} type="search" maxLength={120} placeholder={view === "leads" ? tr("Company, website or buying signal") : tr("Company, website, sector or country code")} value={q} onChange={(e) => update({ q: e.target.value })} />
          </label>
          <label>{tr("Region")}
            <select aria-label={tr("Region")} value={region} onChange={(e) => update({ region: e.target.value })}>
              <option value="">{tr("All regions")}</option>
              {REGIONS.map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
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
      setMsg(tr("Checking {} {} for news. Findings will appear in the Market feed.", r.queued, r.queued === 1 ? "company" : "companies"));
      setSelection({ query, ids: [] });
    } catch (e) { setErr(e); }
  };

  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  const visiblePage = d?.page ?? page;
  const groups = REGIONS.map(([key, title]) => ({ key, title, items: d?.items.filter((item: any) => item.region === key) ?? [] })).filter(({ items }) => items.length > 0);
  return (
    <section aria-label={review ? tr("Companies to review") : tr("Matching accounts")}>
      {review ? <p className="small muted">{tr("These companies match the sector and size filters. Open a profile to record who owns the company and where you checked.")}</p> :
        <div className="directory-toolbar">
          <div className="directory-presets" aria-label={tr("Quick filters")}>
            {PRESETS.map(([label, filter]) => {
              const active = Object.entries(filter).every(([key, value]) => f[key as keyof Filters] === value);
              return <button key={label} className="chip" aria-pressed={active} onClick={() => filters({ ...EMPTY, sort: f.sort, ...(!active ? filter : {}) })}>{tr(label)}</button>;
            })}
          </div>
          <div className="row">
            <select aria-label={tr("Sort accounts within each region")} value={f.sort} onChange={(e) => change("sort", e.target.value)}>
              <option value="score">{tr("Strongest signals")}</option><option value="recent">{tr("Latest signal")}</option><option value="size">{tr("Largest first")}</option><option value="name">{tr("Company name")}</option>
            </select>
            <button disabled={!d?.total} onClick={exportCsv}>{tr("Export CSV")}</button>
            {selected.length > 0 && research && <button onClick={scanSelected}>{tr("Check news for {} companies", number(selected.length))}</button>}
          </div>
        </div>}
      {msg && <p className="okmsg" role="status">{tr(msg)}</p>}
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!d && !err && <Skeleton label={tr("Loading companies")} />}
      {d && <p className="small muted directory-count" role="status">{counted(d.total, ["company", "companies"], ["firma", "firmy", "firm"])}{regionName(region) && tr(" · {}", regionName(region))}</p>}
      {d && d.items.length === 0 && <div className="empty">
        <b>{tr("No matching companies")}</b>
        {q || region || f.watched || f.shortlist || f.signal_days || f.band ?
          <button onClick={() => { filters(EMPTY); clearSearch(); }}>{tr("Clear filters")}</button> :
          review ? tr("There are no matching profiles waiting for an ownership check.") : <>{tr("Matching companies appear here once their ownership is recorded with a source.")}{research && <> <Link to="/accounts?view=review">{tr("Review company ownership")}</Link>.</>}</>}
      </div>}
      {d && d.items.length > 0 && <>
        {groups.map(({ key, title, items }) => <section key={key} className="directory-region">
        {!region && <h2>{tr(title)}</h2>}
        <div className="card table-wrap flush" tabIndex={0} role="region" aria-label={tr("{} company directory", tr(title))}>
          <table className="directory-table"><thead><tr>
            <th>{tr("Company")}</th><th>{tr("Headquarters")}</th><th>{tr("Size")}</th><th>{review ? tr("Ownership") : tr("Buying signal and source")}</th><th>{review ? tr("Profile") : tr("Actions")}</th>
          </tr></thead><tbody>
            {items.map((r: any) => <tr key={r.id}>
              <td className="directory-company"><Link to={`/accounts/${r.id}${review ? "?profile=1" : ""}`}>{r.name}</Link>
                <span className="small muted">{[r.segment, r.canonical_domain].filter(Boolean).join(" · ")}</span>
                <span className="small muted">{r.shortlist ? tr("On your shortlist") : tr(ORIGIN[r.origin] ?? "")}</span>
              </td>
              <td data-label={tr("Headquarters")}>{countryName(r.country_code)}</td>
              <td data-label={tr("Size")}>{staffCount(r.employees, r.employees_uncertain)}</td>
              <td data-label={review ? tr("Ownership") : tr("Buying signal and source")}>
                {!review && r.buying_signal && <SignalSummary signal={r.buying_signal} />}
                {review ? tr("To check") : r.stopped ? <Badge kind="warn">{tr("On hold")}</Badge> : !r.buying_signal && r.signal_count > 0 ?
                  <><Badge kind={BAND[r.band]?.[0]}>{tr(BAND[r.band]?.[1] ?? "Activity")}</Badge> <span className="small">{counted(r.signal_count, ["checked signal", "checked signals"], ["sprawdzony sygnał", "sprawdzone sygnały", "sprawdzonych sygnałów"])}</span>
                    <span className="small muted">{[r.groups.map(groupName).join(", "), r.last_signal_at && fmtDate(r.last_signal_at)].filter(Boolean).join(" · ")}</span></> :
                  !r.buying_signal && <span className="small muted">{tr("No checked signals yet")}</span>}
              </td>
              <td data-label={review ? tr("Profile") : tr("Actions")}>
                {review ? <Link to={`/accounts/${r.id}?profile=1`}>{tr("Check ownership")}</Link> : <>
                  <div className="directory-actions">
                    {research && <label className="hit44"><input type="checkbox" aria-label={tr("Select {} for a news check", r.name)} checked={selected.includes(r.id)} disabled={!selected.includes(r.id) && selected.length === 25} onChange={() => toggle(r.id)} /></label>}
                    <button className="star hit44" aria-pressed={r.watched} aria-label={r.watched ? tr("Stop watching {}", r.name) : tr("Watch {}", r.name)} onClick={() => toggleWatch(r)}>{r.watched ? "★" : "☆"}</button>
                  </div>
                  {r.candidate_status === "pending_qualification" && research && <div className="row"><button onClick={() => decide(r, "qualify")}>{tr("Confirm account")}</button><button onClick={() => decide(r, "reject")}>{tr("Remove from list")}</button></div>}
                </>}
              </td>
            </tr>)}
          </tbody></table>
        </div></section>)}
        {pages > 1 && <div className="directory-pagination"><button disabled={visiblePage <= 1} onClick={() => setPage(visiblePage - 1)}>{tr("Previous")}</button><span>{tr("Page") + " "}{visiblePage}{" " + tr("of") + " "}{pages}</span><button disabled={visiblePage >= pages} onClick={() => setPage(visiblePage + 1)}>{tr("Next")}</button></div>}
      </>}
    </section>
  );
}

function Cited({ items }: { items: any[] }) {
  return <ul className="brief-list">{items.map((x, i) => <li key={i}><div>{x.statement}</div><blockquote className="fquote mt-2 mb-0">“{x.quote}”<cite>{x.source_url ? <a className="pill" href={x.source_url} target="_blank" rel="noreferrer noopener">{x.source_title || humanize(x.source_kind)} ↗</a> : <span>{x.source_title || humanize(x.source_kind)}</span>}</cite></blockquote></li>)}</ul>;
}

function BriefTab({ briefs }: { briefs: any[] }) {
  const b = briefs[0];
  if (!b) return <div className="card"><p className="muted m-0">{tr("No brief yet. Press “Write research brief”. It uses only what we have stored about this company, and every statement quotes its source.")}</p></div>;
  if (b.status === "queued" || b.status === "running") return <div className="card"><p className="m-0">{tr("Writing the brief. This takes about a minute.")}</p></div>;
  if (b.status === "failed") return <div className="card"><Err e={new Error(b.error || "The brief could not be written.")} /></div>;
  const c = b.content;
  return (
    <div className="card">
      <p className="small muted mt-0">{tr("Written") + " "}{fmtDateTime(b.finished_at)}{" " + tr("from") + " "}{c.source_count ?? 0}{" " + tr("sources we hold.") + " "}{b.dropped > 0 ? tr("We left out {} {} we could not match to its source.", b.dropped, b.dropped === 1 ? "statement" : "statements") : tr("Every statement below quotes its source.")}</p>
      {c.note && <p>{c.note}</p>}
      {c.summary?.length > 0 && <><h2 className="h-sub">{tr("What the company is and does")}</h2><Cited items={c.summary} /></>}
      {c.signals?.length > 0 && <><h2 className="h-sub">{tr("Signals")}</h2><Cited items={c.signals} /></>}
      {c.hiring?.length > 0 && <><h2 className="h-sub">{tr("Hiring")}</h2><Cited items={c.hiring} /></>}
      {c.icp_fit?.read && <><h2 className="h-sub">{tr("Does it fit our target customer?")}</h2><p>{c.icp_fit.read}</p>{c.icp_fit.reasons?.length > 0 && <Cited items={c.icp_fit.reasons} />}</>}
      {c.open_questions?.length > 0 && <><h2 className="h-sub">{tr("Still to find out")}</h2><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></>}
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
      <div className="row spread"><b>{tr("Find the people who decide")}</b>{can && <button className="primary" disabled={busy || !d.apollo_configured} onClick={() => run(() => post(`/api/accounts/${orgId}/find-contacts`), (r) => `Found ${r.found} ${r.found === 1 ? "person" : "people"}, ${r.added} new. Names and job titles only, no email yet.`)}>{tr("Search with Apollo")}</button>}</div>
      <p className="small muted mt-1 mb-2">{tr("Searching is free and gives names and job titles.") + " "}{d.reveal_price == null ? tr("The cost per email is not set yet. Ask an administrator.") : tr("Revealing an email costs {} {} per person and needs approval from another person.", d.reveal_price, d.reveal_price === 1 ? "credit" : "credits")} {d.apollo_configured ? "" : "Apollo is not connected yet. "}{d.paid_enrichment_enabled ? "" : tr("Revealing emails is switched off for now.")}</p>
      {d.items.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Name · Job title · Email")}><table><thead><tr><th>{tr("Name")}</th><th>{tr("Job title")}</th><th>{tr("Email")}</th><th><span className="sr">{tr("Actions")}</span></th></tr></thead><tbody>
        {d.items.map((c: any) => <tr key={c.id}><td>{c.linkedin_url ? <a href={c.linkedin_url} target="_blank" rel="noreferrer noopener">{c.name}</a> : c.name}</td><td>{c.title}</td>
          <td><Badge kind={c.status === "revealed" ? "ok" : c.status === "found" ? "info" : ""}>{c.status === "found" ? tr("Not revealed") : c.status === "revealed" ? "Revealed" : humanize(c.status)}</Badge></td>
          <td>{can && c.status === "found" && <div className="row"><button disabled={busy} onClick={() => run(() => post(`/api/contact-candidates/${c.id}/reveal`), (r) => `Approval requested (${r.credits} ${r.credits === 1 ? "credit" : "credits"}). Approve it under Administration, Approvals, then run it there.`)}>{tr("Ask to reveal email")}</button>
            <button disabled={busy} onClick={() => run(() => post(`/api/contact-candidates/${c.id}/dismiss`), () => "Dismissed.")}>{tr("Dismiss")}</button></div>}</td></tr>)}</tbody></table></div>}
      {msg && <div className="okmsg mt-2" role="status">{tr(msg)}</div>}<Err e={err} />
    </div>
  );
}

function SendPanel({ draft, orgId, can }: { draft: any; orgId: string; can: boolean }) {
  const [plan, setPlan] = useState<any>(null); const [sel, setSel] = useState<string[]>([]); const [en, setEn] = useState<any>({ items: [] });
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const load = () => { api(`/api/accounts/${orgId}/contacts-plan`).then(setPlan).catch(() => setPlan(null)); api(`/api/campaigns/${draft.id}/enrolments`).then(setEn).catch(() => undefined); };
  useEffect(load, [draft.id]);
  if (!plan) return null;
  const ready = en.outbound_enabled === true && en.lemlist_configured === true;
  const unavailable = [
    [plan.needs_verification.length, tr("found but unverified")], [plan.manual_review, tr("need manual review")],
    [plan.no_email, tr("without an email")], [plan.suppressed, tr("opted out")],
  ].filter(([count]) => Number(count) > 0).map(([count, reason]) => `${count} ${reason}`);
  const toggle = (id: string) => setSel((x) => x.includes(id) ? x.filter((i) => i !== id) : [...x, id]);
  const request = async () => { setErr(null); setMsg(""); try { const r = await post(`/api/campaigns/${draft.id}/enrol`, { person_ids: sel }); setMsg(tr("Approval requested for {} {}. A colleague approves it under Administration, Approvals.{}", r.contacts, r.contacts === 1 ? "contact" : "contacts", r.ownership_checked ? "" : " We could not check who owns this account because it is not linked to Pipedrive yet.")); setSel([]); load(); } catch (e) { setErr(e); } };
  return (
    <div className="card mt-3">
      <div className="row spread"><b>{tr("Prepare a Lemlist campaign")}</b>{typeof en.lemlist_configured === "boolean" && <Badge kind={ready ? "ok" : "warn"}>{ready ? tr("Ready for approval") : tr("Lemlist setup required")}</Badge>}</div>
      <p className="small muted mt-1 mb-2">{tr("Only contacts with a checked email address can be used. We build the campaign in Lemlist but do not launch it: a person presses Launch there.") + " "}{en.outbound_enabled === false && tr("Sending is switched off for now.")}{en.lemlist_configured === false && tr("Lemlist is not connected yet.")}</p>
      {plan.eligible.length === 0 && <p className="small">{tr("No contact here has a checked email address yet.")}</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can || !ready} /> {c.name}{c.title ? `, ${c.title}` : ""}</label>)}
      {unavailable.length > 0 && <p className="small muted">{tr("Unavailable for outreach:") + " "}{unavailable.join(" · ")}.</p>}
      {can && <button className="primary" disabled={!ready || sel.length === 0} onClick={request}>{tr("Ask for approval to add") + " "}{sel.length || ""}</button>}
      {msg && <div className="okmsg mt-2" role="status">{tr(msg)}</div>}<Err e={err} />
      {en.items.length > 0 && <div className="table-wrap mt-3" tabIndex={0} role="region" aria-label={tr("Contact · Status · Last activity")}><table><thead><tr><th>{tr("Contact")}</th><th>{tr("Status")}</th><th>{tr("Last activity")}</th></tr></thead><tbody>{en.items.map((x: any) => <tr key={x.id}><td>{x.full_name ?? tr("Contact")}</td><td><Badge kind={x.status === "interested" ? "ok" : x.status === "unsubscribed" || x.status === "bounced" ? "bad" : "info"}>{humanize(x.status)}</Badge></td><td>{x.last_event_at ? fmtDateTime(x.last_event_at) : tr("No reply yet")}</td></tr>)}</tbody></table></div>}
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
      <p className="small muted mt-0">{tr("Three emails written from facts we have checked about this company, and tested against The Codest's brand rules. These are drafts: nothing is sent from here.") + " "}{!camps.ownership_checked && camps.note}</p>
      {canDraft && <div className="row mb-3">
        <label className="small">{tr("Who is it for?")}<select id="camp-role" value={role} onChange={(e) => setRole(e.target.value)} className="ml-2"><option value="">{tr("Technology or delivery leader")}</option><option value="CTO">CTO</option><option value="Head of Engineering">{tr("Head of Engineering")}</option><option value="Head of Payments">{tr("Head of Payments")}</option><option value="Head of Delivery or PMO">{tr("Head of Delivery or PMO")}</option><option value="Procurement lead">{tr("Procurement lead")}</option></select></label>
        <button className="primary" disabled={busy} onClick={() => make(role)}>{tr("Write outreach sequence")}</button></div>}
      {!b && <p className="muted">{tr("No draft yet. Write a research brief first for the best result, because the emails are built from what it finds.")}</p>}
      {busy && <p>{tr("Writing and checking the draft. This takes about a minute.")}</p>}
      {b?.status === "failed" && <Err e={new Error(b.error || "The draft could not be written.")} />}
      {b && (b.status === "draft" || b.status === "needs_work" || b.status === "approved" || b.status === "rejected") && <>
        <div className="row mt-2 mb-3">
          <Badge kind={b.status === "approved" ? "ok" : b.status === "draft" ? "info" : b.status === "rejected" ? "" : "warn"}>{b.status === "draft" ? tr("Passed the brand review") : b.status === "needs_work" ? "Needs work" : humanize(b.status)}</Badge>
          <span className="small muted">{b.rounds > 1 ? `Rewritten ${b.rounds === 2 ? "once" : `${b.rounds - 1} times`} to fix rule breaks. ` : ""}{tr("Written") + " "}{fmtDateTime(b.finished_at)}{b.decided_by ? ` · ${b.status} by ${b.decided_by}` : ""}</span></div>
        {b.review?.breaks?.length > 0 && <div className="err" role="alert"><b>{tr("Fix these before approving:")}</b><ul className="tight">{b.review.breaks.map((x: any, i: number) => <li key={i}>{x.step ? `Email ${x.step}: ` : ""}{x.detail}</li>)}</ul></div>}
        {(b.steps ?? []).map((st: any, i: number) => (
          <article key={i} className="fcard">
            <div className="row spread"><b>{tr("Email") + " "}{i + 1}</b><span className="row small muted">{st.day === 0 ? "Day 0" : `Day ${st.day}`}<button className="chip" onClick={() => copy(`${b.id}-${i}`, `Subject: ${st.subject}\n\n${st.body}`)}>{copied === `${b.id}-${i}` ? tr("Copied") : tr("Copy")}</button></span></div>
            <p className="mt-2 mb-1"><b>{tr("Subject:")}</b> {st.subject}</p>
            <div className="pre-wrap">{st.body}</div>
            
          </article>))}
        {canDraft && b.status === "draft" && <div className="row"><button className="primary" onClick={() => decide(b.id, "approve")}>{tr("Approve draft")}</button><button onClick={() => decide(b.id, "reject")}>{tr("Reject")}</button></div>}
        {b.status === "approved" && <SendPanel draft={b} orgId={orgId} can={canDraft} />}
        {b.facts && Object.keys(b.facts).length > 0 && <details className="mt-3"><summary>{tr("Facts these emails are built on")}</summary><ul className="tight small">{Object.entries(b.facts).map(([k, v]: any) => <li key={k}><b>{k}</b> {v}</li>)}</ul></details>}
      </>}
    </div>
  );
}

const TABS = ["Overview", "Findings timeline", "Assigned leads", "Signals & sources", "Research brief", "People", "Outreach"];
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "Checked programme, outside demand unknown", D4: "Attention, outside demand unknown" };
const H = ({ children }: { children: React.ReactNode }) => <h2 className="h-sub">{children}</h2>;

const OWNERSHIP: Record<string, string> = {
  private: "Privately held",
  listed: "Publicly listed",
  state_owned: "State-owned",
  unknown: "Ownership unconfirmed",
};

function CompanyProfile({ account, onSave }: { account: any; onSave: (profile: any) => void }) {
  const [form, setForm] = useState({
    ownership: account.company_ownership ?? tr("unknown"), source_url: account.company_profile_source_url ?? "",
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
    <h2>{tr("Company details")}</h2>
    <p className="small muted">{tr("Use the company register, annual report or company website to confirm ownership. For a subsidiary, check who owns the parent company.")}</p>
    <div className="directory-filters">
      <label>{tr("Ownership")}<select value={form.ownership} onChange={(e) => setForm({ ...form, ownership: e.target.value })}>
        {Object.entries(OWNERSHIP).map(([key, title]) => <option key={key} value={key}>{tr(title)}</option>)}
      </select></label>
      <label>{tr("Ownership source")}<input type="url" maxLength={1000} value={form.source_url} required={form.ownership !== "unknown"} placeholder="https://…" onChange={(e) => setForm({ ...form, source_url: e.target.value })} /></label>
      <label>{tr("Headquarters country code")}<input maxLength={2} pattern="[A-Z]{2}" value={form.country_code} placeholder={tr("e.g. GB")} onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })} /></label>
      <label>{tr("Sector")}<input maxLength={80} value={form.segment} placeholder={tr("e.g. Banking or Payments")} onChange={(e) => setForm({ ...form, segment: e.target.value })} /></label>
      <label>{tr("Employees")}<input type="number" min={1} max={2147483647} value={form.employees} onChange={(e) => setForm({ ...form, employees: e.target.value })} /></label>
      <label className="profile-estimate"><input type="checkbox" checked={form.employees_uncertain} onChange={(e) => setForm({ ...form, employees_uncertain: e.target.checked })} />{" " + tr("Headcount is an estimate")}</label>
    </div>
    <button className="primary" disabled={busy}>{busy ? tr("Saving…") : tr("Save company details")}</button>
    {saved && <span className="small ml-2" role="status">{tr("Saved.")}</span>}
    <Err e={err} />
  </form>;
}

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(params.get("profile") === "1");
  const [d, setD] = useState<any>(null);
  const linkedTab = params.has("finding") || params.has("timeline") ? "Findings timeline"
    : params.has("event") ? "Signals & sources" : null;
  const [tab, setTab] = useState(linkedTab ?? TABS[0]);
  useEffect(() => { if (linkedTab) setTab(linkedTab); }, [linkedTab, params]);
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
    <p className="small mt-0"><Link to="/accounts">{tr("Accounts")}</Link></p><h1 className="title-sm">{tr("Account details")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the account")} />}
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
  const activeSignals = new Set(d.signals.filter((signal: any) => signal.status === "active").map((signal: any) => signal.story_key ?? signal.event_id)).size;
  const makeCampaign = async (role: string) => { try { await post(`/api/accounts/${id}/campaign`, { role: role || null }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const decideCampaign = async (did: string, decision: string) => { try { await post(`/api/campaigns/${did}/decision`, { decision }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const makeBrief = async () => { try { await post(`/api/accounts/${id}/brief`); await loadBriefs(); setTab("Research brief"); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const runScan = async () => {
    setScan({ busy: true, msg: "", err: null });
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? tr("Reading {} and searching the web. Verified findings appear in the archive.", r.website) : tr("Started. This account has no website on record, so we will only run the web search."), err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  const blank = (v: any) => (v === null || v === undefined || v === "" ? tr("Not known") : v);
  return (
    <>
      <div className="account-profile">
        <p className="account-breadcrumb small"><Link to="/accounts">{tr("Accounts & leads")}</Link></p>
        <div className="row spread start">
          <div>
            <h1 className="title-sm">{a.display_name}</h1>
            <p className="small">{[a.segment, countryName(a.country_code)].filter(Boolean).join(" · ")}</p>
            <div className="row">
              {a.in_master_list && <Badge>{tr("From your workbooks")}</Badge>}
              {a.canonical_domain && <a href={`https://${a.canonical_domain}`} target="_blank" rel="noreferrer noopener">{a.canonical_domain} ↗</a>}
            </div>
          </div>
          <div className="row account-actions">
            {canScan && <button className="primary" disabled={scan.busy} onClick={runScan}>{tr("Check for news")}</button>}
            {(briefs.length > 0 || canScan) && <button onClick={briefs.length ? () => setTab("Research brief") : makeBrief}>{briefs.length ? tr("Open brief") : tr("Write research brief")}</button>}
          </div>
        </div>
        <dl className="account-profile-facts">
          <div><dt>{tr("Size")}</dt><dd>{staffCount(a.employees_estimate, a.employees_uncertain)}</dd></div>
          <div><dt>{tr("Ownership")}</dt><dd>{tr(OWNERSHIP[a.company_ownership] ?? OWNERSHIP.unknown)}{a.company_profile_source_url && <> · <a href={a.company_profile_source_url} target="_blank" rel="noreferrer noopener">{tr("View source ↗")}</a></>}</dd></div>
          <div><dt>{tr("Current signals")}</dt><dd>{number(activeSignals)}</dd></div>
          <div><dt>{tr("Monitored company pages")}</dt><dd>{number(eps.length)}</dd></div>
        </dl>
      </div>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      {canScan && a.entity_type !== "public_body" && <p className="small"><button className="ghost" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? tr("Close company details") : tr("Edit company details")}</button></p>}
      {editing && canScan && a.entity_type !== "public_body" && <CompanyProfile account={a} onSave={(profile) => setD({ ...d, account: { ...a, ...profile } })} />}
      {d.score && <div className="card"><div className="row spread"><div><span className="eyebrow" title={tr("Adds up this account's recent checked signals. Different kinds count for more than repeats, and each signal fades as it ages. Hot is 9 or more, warm 4 or more. It is not a probability.")}>{tr("Signal score")}</span><div className="kpi-n mt-1 mb-1">{d.score.stopped ? tr("On hold") : d.score.score}<span className="kpi-u"> {tr(BAND[d.score.band][1])}</span></div></div>
        <div className="small measure-sm">{d.score.stopped ? "Something we found says not to pursue this account now, so the score is held at zero until it expires." : `${d.score.signal_count} ${d.score.signal_count === 1 ? "signal" : "signals"} (${d.score.groups.map(groupName).join(", ")}). Recent signals carry more weight. A mix of signals raises the score more than repeated signals of the same kind.`}
          {d.score.top?.length > 0 && <div className="chips mt-2">{d.score.top.map((t: any) => <Badge key={t.rule_id}>{sigNames[t.rule_id] ?? t.name ?? t.rule_id}</Badge>)}</div>}</div></div></div>}
      <Tabs items={TABS.map((t) => ({ key: t, label: tr(t) }))} value={tab} onChange={next => {
        setTab(next);
        if (linkedTab && linkedTab !== next) {
          const q = new URLSearchParams(params);
          q.delete("finding");
          q.delete("timeline");
          q.delete("event");
          setParams(q, { replace: true });
        }
      }} label={tr("Account sections")}>
      {tab === "Overview" && (<>
        <div className="account-shortcuts">
          <button onClick={() => setTab("Assigned leads")}>{tr("Assigned leads")} →</button>
          <button onClick={() => setTab("Findings timeline")}>{tr("Findings timeline")} →</button>
        </div>
        <H>{tr("Strongest current buying signal")}</H>
        <div className="card"><SignalSummary signal={d.buying_signal} detailed /></div>
        <H>{tr("Opportunities")}</H>
        {d.opportunities.length === 0 && <div className="empty"><b>{tr("No opportunities yet")}</b>{tr("Opportunities appear when a checked source points to work this company might hire for.")}</div>}
        {d.opportunities.map((o: any) => (
          <div key={o.hypothesis_id} className="card"><div className="row spread"><div><Badge kind={priorityKind(o.priority)}>{lab("priority", o.priority)}</Badge> <b>{o.initiative.title}</b> · {lab("route", o.route.type)}{o.route.counterparty ? ` (${o.route.counterparty})` : ""} · {lab("access", o.route.access_status).toLowerCase()}</div><Link to={`/opportunities/${o.hypothesis_id}`}>{tr("Open")}</Link></div>
            <p className="small">{o.plain_reason ?? whyShort(o.why_it_matters)}</p>{o.next_action && <p className="small"><b>{tr("Next:")}</b> {o.next_action.objective}</p>}</div>))}
      </>)}
      {(tab === "Findings timeline" || tab === "Assigned leads") && id &&
        <AccountFindings key={tab + (params.get("finding") ?? "")} accountId={id} selected={params.get("finding")} leadsOnly={tab === "Assigned leads"} />}
      {tab === "Research brief" && <><BriefTab briefs={briefs} />{canScan && briefs[0]?.status === "failed" && <button onClick={makeBrief}>{tr("Try writing the brief again")}</button>}</>}
      {tab === "Outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={String(id)} make={makeCampaign} decide={decideCampaign} />}
      {tab === "People" && <>
        <FindContacts orgId={String(id)} can={me.permissions.includes("draft.create")} />
        {me.permissions.includes("contacts.read") ? <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Name · Job title (as reported) · Area")}><table><thead><tr><th>{tr("Name")}</th><th>{tr("Job title (as reported)")}</th><th>{tr("Area")}</th><th>{tr("Role confirmed")}</th><th>{tr("Email")}</th><th>{tr("Opted out")}</th></tr></thead><tbody>
          {d.buyers.map((b: any) => <tr key={b.id}><td>{b.name}</td><td>{blank(b.title)}</td><td>{blank(b.lane)}</td><td>{b.role_verified === "verified" ? tr("Confirmed") : b.role_verified === "unverified" ? tr("Not confirmed") : humanize(b.role_verified)}</td><td>{humanize(b.email_status)}</td><td>{b.suppressed ? <Badge kind="bad">{tr("Yes")}</Badge> : tr("No")}</td></tr>)}
          {d.buyers.length === 0 && <tr><td colSpan={6} className="muted">{tr("No people recorded yet. Use “Find the people who decide” above.")}</td></tr>}</tbody></table>
          <p className="small muted">{tr("A job title does not prove someone holds the budget. Email addresses are not shown here, and every look at personal data is logged.")}</p></div> : <div className="empty">{tr("Your role cannot see contact details.")}</div>}</>}
      {tab === "Signals & sources" && <>
        <H>{tr("Programmes")}</H>
        {d.initiatives.length === 0 && <p className="small muted">{tr("No programmes found yet.")}</p>}
        {d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">{tr("Our working title")}</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">{tr("Budget not confirmed")}</Badge>}
          <p className="small">{tr("Technology:") + " "}{i.technology_tags.join(", ") || tr("Not known")}{" " + tr("· last big news") + " "}{fmtDate(i.last_material_event_at)}{" " + tr("· next milestone") + " "}{fmtDate(i.next_milestone_at)}</p>
          {i.research_gaps.length > 0 && <><span className="eyebrow">{tr("Still to find out")}</span><ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></>}</div>)}
        <H>{tr("Signals")}</H>
        <div className="row gap-2 mb-2">
          <label>{tr("Status") + " "}<select value={signalStatus} onChange={e => setSignalStatus(e.target.value)}><option value="all">{tr("All signals")}</option><option value="active">{tr("Current")}</option><option value="historical">{tr("Historical")}</option></select></label>
          <label>{tr("Order") + " "}<select value={signalOrder} onChange={e => setSignalOrder(e.target.value)}><option value="newest">{tr("Newest first")}</option><option value="oldest">{tr("Oldest first")}</option></select></label>
          <label>{tr("Evidence of demand") + " "}<select value={signalTier} onChange={e => setSignalTier(e.target.value)}><option value="all">{tr("All levels")}</option><option value="D1">{tr("Explicit")}</option><option value="D2">{tr("Inferred")}</option><option value="D3">{tr("Programme")}</option><option value="D4">{tr("Context")}</option></select></label>
        </div>
        {signalStories.map(group => {
          const first = group[0];
          const eventIds = [...new Set(group.map(x => x.event_id))];
          const rules = new Map(group.map(x => [`${x.rule_id}:${x.status}:${x.demand_tier}`, x]));
          const evidence = new Map(group.flatMap(x => x.evidence).map(e => [e.claim_id, e]));
          return <div className="card" key={first.story_key ?? first.event_id}>
            {eventIds.map(eventId => <span id={`event-${eventId}`} key={eventId} />)}
            <b>{first.summary}</b><p className="small muted">{first.event_at || first.pub_at ? fmtDate(first.event_at ?? first.pub_at) : tr("Date unknown")}</p>
            {[...rules.entries()].map(([key, x]) => <p className="small" key={key}><Badge kind={x.status === "active" ? "ok" : ""}>{x.status === "active" ? tr("Current") : tr("Historical")}</Badge> {sigNames[x.rule_id] ?? x.rule_id} · {tr(DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier))}</p>)}
            <Evidence items={[...evidence.values()]} />
          </div>;
        })}
        {signalStories.length === 0 && <p className="muted">{d.signals.length ? tr("No signals match these filters.") : tr("No signals yet.")}</p>}
        <H>{tr("Suppliers")}</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Suppliers table")}><table><thead><tr><th>{tr("Supplier")}</th><th>{tr("Role")}</th><th>{tr("What they do here")}</th><th>{tr("Relationship")}</th><th>{tr("Exclusive?")}</th></tr></thead><tbody>{d.suppliers.map((x: any, i: number) => <tr key={i}><td>{x.supplier_name}</td><td>{humanize(x.supplier_role)}</td><td>{blank(x.scope_text)}</td><td>{humanize(x.relation)}</td><td>{blank(x.exclusivity)}</td></tr>)}
          {d.suppliers.length === 0 && <tr><td colSpan={5} className="muted">{tr("No suppliers recorded yet.")}</td></tr>}</tbody></table>
          <p className="small muted">{tr("A named supplier does not prove they have exclusive rights or cover everything. This is for this legal entity only.")}</p></div>
        <H>Pipedrive</H>
        <div className="card"><p className="mt-0">{d.crm ? <>{tr("Linked to Pipedrive organisation") + " "}{d.crm.crm_id}{tr(", with") + " "}{d.crm.open_deals_count ?? tr("an unknown number of")}{" " + tr("open") + " "}{d.crm.open_deals_count === 1 ? "deal" : "deals"}{tr(". Pipedrive data last copied") + " "}{fmtDateTime(d.crm.synced_at)}{tr(". Pipedrive is the source of truth for owner and deal stage.")}</> : tr("Not linked to Pipedrive. We cannot check who owns this account, so outreach and Pipedrive requests are switched off for it.")}</p>
          {d.source_rows.length > 0 && <><span className="eyebrow">{tr("Where it appears in your workbooks")}</span><ul className="tight small">{d.source_rows.map((r: any, i: number) => <li key={i}>{r.sheet_title}{tr(", row") + " "}{r.row_number}</li>)}</ul></>}</div>
        <H>{tr("Where we look for news")}</H>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Coverage table")}><p className="small mt-0">{tr(d.source_coverage_note)}</p><table><thead><tr><th>{tr("Kind of source")}</th><th>{tr("State")}</th><th>{tr("Last successful check")}</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{humanize(c.source_family)}</td><td>{humanize(c.coverage_state)}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
          {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">{tr("We have not checked this account's sources yet. That means we cannot see, not that nothing is happening.")}</td></tr>}</tbody></table>
          <h3>{tr("Where this company publishes")}</h3>
          {eps.length === 0 ? <p className="small muted">{tr("Nothing found yet. Press “Check for news” to look for its news feed, press page, careers page and job board.")}</p>
            : <table><thead><tr><th>{tr("Kind")}</th><th>{tr("Address")}</th><th>{tr("Last check")}</th><th>{tr("Items read")}</th></tr></thead><tbody>{eps.map((e, i) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small break-all"><a href={e.url} target="_blank" rel="noreferrer noopener">{e.url} ↗</a></td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? tr("Worked") : tr("Problem")}</Badge>{e.last_success_at && <div className="muted">{tr("Last successful check")} {fmtDateTime(e.last_success_at)}</div>}{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : tr("Not yet")}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}</div>
        <H>{tr("Sources for its opportunities")}</H>{d.opportunities.length === 0 ? <p className="small muted">{tr("None yet.")}</p> : d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</>}
      </Tabs>
    </>
  );
}
