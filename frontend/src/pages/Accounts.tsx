import { counted, number, tr } from "../locale";
import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, post } from "../api";
import Prospects from "./Prospects";
import { REGIONS, countryName, staffCount } from "./targetMarket";
import { Badge, Err, Skeleton, Tabs, groupName, safeHref } from "../components/ui";
import { CompanyAvatar, ListRank, ago } from "../components/Findings";
import { signalName } from "../components/Signals";
import "../styles/accounts.css";

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

const SIZE = 40;
const SORTS: [string, string][] = [["score", "Strongest signals"], ["priority", "Lead list priority"], ["list_score", "List score"], ["recent", "Newest signal"], ["size", "Largest first"], ["name", "Company name"]];
/** On/off filters, kept in the address so a filtered list survives opening a company and coming back. [address key, API parameter, API value, label] */
const TOGGLES: [string, string, string, string][] = [
  ["email", "has_email", "true", "Has email contacts"],
  ["signal", "has_signal", "true", "Verified signal"],
  ["recent", "signal_days", "30", "Signal in the last 30 days"],
  ["hot", "band", "hot", "Hot accounts"],
  ["watched", "watched", "true", "Watchlist"],
  ["shortlist", "shortlist", "true", "Shortlist"],
];
const ORIGIN: Record<string, string> = { added: "Added by the team", discovered: "Found through research" };
const PRIORITY = /^P[123]$/;

/** The search and region fields every company view shares. They write to the address so switching tabs keeps them. */
function SearchRegion({ q, region, set, leads }: { q: string; region: string; set: (values: Record<string, string>) => void; leads?: boolean }) {
  const label = leads ? tr("Search leads") : tr("Search companies");
  return <>
    <label className="acc-search">{label}
      <input type="search" maxLength={120} placeholder={leads ? tr("Company, website or buying signal") : tr("Company, website, sector or country code")} value={q} onChange={(e) => set({ q: e.target.value })} />
    </label>
    <label>{tr("Region")}
      <select value={region} onChange={(e) => set({ region: e.target.value })}>
        <option value="">{tr("All regions")}</option>
        {REGIONS.map(([key, name]) => <option key={key} value={key}>{tr(name)}</option>)}
      </select>
    </label>
  </>;
}

export function Accounts({ me }: { me: { permissions: string[] } }) {
  const [params, setParams] = useSearchParams();
  const research = me.permissions.includes("research.request");
  const view = params.get("view") === "leads" ? "leads" : params.get("view") === "review" && research ? "review" : "accounts";
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
    if (!("page" in values)) next.delete("page");
    setParams(next, { replace: true });
  };
  const switchView = (key: string) => {
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    if (region) next.set("region", region);
    if (key !== "accounts") next.set("view", key);
    setParams(next, { replace: true });
  };

  return (
    <>
      <div className="page-head directory-heading">
        <div><h1>{tr("Accounts & leads")}</h1><p className="sub">{view === "leads" ? tr("Find buying signals from the workbooks and open the companies they belong to.") : view === "review" ? tr("Companies your lead list marks for review, and companies found by research whose ownership is not confirmed yet.") : tr("Every company your lead list marks as eligible, plus privately held financial companies found by research.")}</p></div>
        {research && view !== "leads" && <button className="primary" onClick={() => update({ add: adding ? "" : "1" })}>{adding ? tr("Cancel") : tr("Add a company")}</button>}
      </div>
      {adding && research && view !== "leads" && <AddCompany onClose={() => update({ add: "", name: "" })} />}
      <Tabs label={tr("Company workspace")} value={view} onChange={switchView} items={[
        { key: "accounts", label: "Accounts" },
        { key: "leads", label: "Leads" },
        ...(research ? [{ key: "review", label: "Ownership to check" }] : []),
      ]}>
        {view === "leads" ? <Prospects q={q} region={region} canVerify={research} fields={<SearchRegion q={q} region={region} set={update} leads />} /> :
          <CompanyDirectory key={view} research={research} review={view === "review"} params={params} update={update}
            fields={<SearchRegion q={q} region={region} set={update} />} q={q} region={region} />}
      </Tabs>
    </>
  );
}

function CompanyDirectory({ q, region, research, review, params, update, fields }: {
  q: string; region: string; research: boolean; review: boolean; params: URLSearchParams; update: (values: Record<string, string>) => void; fields: ReactNode;
}) {
  const segment = params.get("segment") ?? "";
  const priority = PRIORITY.test(params.get("priority") ?? "") ? params.get("priority")! : "";
  const sort = SORTS.some(([key]) => key === params.get("sort")) ? params.get("sort")! : "score";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const on = Object.fromEntries(TOGGLES.map(([key]) => [key, params.get(key) === "1"]));
  const filters: Record<string, string> = { scope: review ? "review" : "target", by_region: "false", sort, q, region, segment, priority };
  TOGGLES.forEach(([key, name, value]) => { if (on[key]) filters[name] = value; });
  const filterQuery = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "")).toString();
  const query = `${filterQuery}&size=${SIZE}&page=${page}`;
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [facets, setFacets] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [selection, setSelection] = useState<{ query: string; ids: string[] }>({ query: filterQuery, ids: [] });
  const selected = selection.query === filterQuery ? selection.ids : [];
  const [msg, setMsg] = useState("");
  const top = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    const timer = setTimeout(() => {
      api(`/api/leads?${query}`, { signal: request.signal })
        .then((data) => { if (!request.signal.aborted) { setResult({ query, data }); setFacets(data.facets); } })
        .catch((error) => { if (!request.signal.aborted) setErr(error); });
    }, q ? 250 : 0);
    return () => { clearTimeout(timer); request.abort(); };
  }, [query, q, retry]);

  const toggle = (id: string) => setSelection({ query: filterQuery, ids: selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id] });
  const toggleWatch = async (r: any) => {
    try {
      await (r.watched ? del(`/api/accounts/${r.id}/watch`) : post(`/api/accounts/${r.id}/watch`));
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const decide = async (r: any, decision: "qualify" | "reject") => {
    try {
      await post(`/api/leads/${r.id}/qualify`, { decision });
      setSelection({ query: filterQuery, ids: selected.filter((id) => id !== r.id) });
      setRetry((n) => n + 1);
    } catch (e) { setErr(e); }
  };
  const exportCsv = async () => {
    try {
      const t = sessionStorage.getItem("ri_token");
      const response = await fetch(`/api/leads.csv?${filterQuery}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
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
      setMsg(tr("Checking {} {} for news. Findings will appear in All findings.", r.queued, r.queued === 1 ? tr("company") : tr("companies")));
      setSelection({ query: filterQuery, ids: [] });
    } catch (e) { setErr(e); }
  };
  const goTo = (next: number) => { update({ page: next > 1 ? String(next) : "" }); top.current?.scrollIntoView({ block: "start" }); };
  const clear = () => update({ q: "", region: "", segment: "", priority: "", ...Object.fromEntries(TOGGLES.map(([key]) => [key, ""])) });
  const filtered = Boolean(q || region || segment || priority || TOGGLES.some(([key]) => on[key]));

  const pages = d ? Math.max(1, Math.ceil(d.total / d.size)) : 1;
  const visiblePage = d?.page ?? page;
  const segments = Object.entries((facets?.segment ?? {}) as Record<string, number>).filter(([key]) => key !== "unknown").sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const counts: Record<string, number | undefined> = { email: facets?.has_email, signal: facets?.has_signal };
  const selectable = research && !review;
  return (
    <section aria-label={review ? tr("Companies to review") : tr("Matching accounts")}>
      {review && <p className="small muted">{tr("These companies match the sector and size filters. Open a profile to record who owns the company and where you checked.")}</p>}
      <div className="acc-filters" role="search" aria-label={tr("Filter companies")}>
        <div className="acc-filter-row">
          {fields}
          <label>{tr("Industry")}
            <select value={segment} onChange={(e) => update({ segment: e.target.value })}>
              <option value="">{tr("All industries")}</option>
              {segment && !segments.some(([key]) => key === segment) && <option value={segment}>{segment}</option>}
              {segments.map(([key, n]) => <option key={key} value={key}>{tr(key)} ({number(n)})</option>)}
            </select>
          </label>
          <label>{tr("Sort by")}
            <select value={sort} onChange={(e) => update({ sort: e.target.value === "score" ? "" : e.target.value })}>
              {SORTS.map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
            </select>
          </label>
        </div>
        <div className="acc-chips">
          <div className="acc-seg" role="group" aria-label={tr("Lead list priority")}>
            <span className="acc-seg-l" aria-hidden="true">{tr("Priority")}</span>
            <button type="button" aria-pressed={!priority} onClick={() => update({ priority: "" })}>{tr("All")}</button>
            {["P1", "P2", "P3"].map((p) => <button key={p} type="button" aria-pressed={priority === p} aria-label={tr("Priority {}", p)} onClick={() => update({ priority: priority === p ? "" : p })}>
              {p}{facets?.priority?.[p] != null && <b>{number(facets.priority[p])}</b>}</button>)}
          </div>
          {TOGGLES.filter(([key]) => !review || key === "email" || key === "signal").map(([key, , , label]) => <button key={key} type="button" className="acc-chip" aria-pressed={on[key]} onClick={() => update({ [key]: on[key] ? "" : "1" })}>
            {tr(label)}{counts[key] != null && <b>{number(counts[key]!)}</b>}</button>)}
        </div>
      </div>

      <div className="acc-resultbar" ref={top}>
        <p className="acc-count" role="status">{d ? counted(d.total, ["company", "companies"], ["firma", "firmy", "firm"]) : tr("Loading companies")}
          {filtered && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}</p>
        <div className="acc-tools">
          {selected.length > 0 && <span className="acc-selected">{tr("{} selected", number(selected.length))}
            <button type="button" className="linkish" onClick={() => setSelection({ query: filterQuery, ids: [] })}>{tr("Clear selection")}</button></span>}
          {selected.length > 0 && research && <button onClick={scanSelected}>{tr("Check news for {} companies", number(selected.length))}</button>}
          <button disabled={!d?.total} onClick={exportCsv}>{tr("Export CSV")}</button>
        </div>
      </div>
      {msg && <p className="okmsg" role="status">{msg}</p>}
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!d && !err && <Skeleton rows={6} label={tr("Loading companies")} />}
      {d && d.items.length === 0 && <div className="empty">
        <b>{tr("No matching companies")}</b>
        {filtered ? <button onClick={clear}>{tr("Clear filters")}</button> :
          review ? tr("There are no matching profiles waiting for an ownership check.") : <>{tr("Matching companies appear here once their ownership is recorded with a source.")}{research && <> <Link to="/accounts?view=review">{tr("Review company ownership")}</Link>.</>}</>}
      </div>}
      {d && d.items.length > 0 && <>
        <div className={`acc-head${selectable ? "" : " no-pick"}`} aria-hidden="true">
          <span className="acc-h-id">{tr("Company")}</span><span className="acc-h-sig">{review ? tr("Signal") : tr("Latest signal")}</span>
          <span className="acc-h-rank">{tr("Lead list")}</span><span className="acc-h-ppl">{tr("Contacts")}</span>
        </div>
        <ol className={`acc-list${selectable ? "" : " no-pick"}${review ? " is-review" : ""}`} aria-label={tr("Companies, page {} of {}", visiblePage, pages)}>
          {d.items.map((r: any) => <AccountRow key={r.id} r={r} review={review} research={research} selectable={selectable}
            selected={selected.includes(r.id)} full={!selected.includes(r.id) && selected.length >= 25}
            onSelect={() => toggle(r.id)} onWatch={() => toggleWatch(r)} onDecide={(decision) => decide(r, decision)} />)}
        </ol>
        {pages > 1 && <nav className="acc-pages" aria-label={tr("Pages")}>
          <button disabled={visiblePage <= 1} onClick={() => goTo(visiblePage - 1)}>{tr("Previous")}</button>
          <span>{tr("Page {} of {}", visiblePage, pages)}<span className="muted"> · {tr("{} to {} of {}", number((visiblePage - 1) * d.size + 1), number(Math.min(visiblePage * d.size, d.total)), number(d.total))}</span></span>
          <button disabled={visiblePage >= pages} onClick={() => goTo(visiblePage + 1)}>{tr("Next")}</button>
        </nav>}
      </>}
    </section>
  );
}

function SignalLine({ r }: { r: any }) {
  const s = r.buying_signal;
  if (r.stopped) return <p className="acc-sig-t"><Badge kind="warn">{tr("On hold")}</Badge> <span className="muted">{tr("A reason to stop or wait was found. Open the company before contacting them.")}</span></p>;
  if (s) {
    const href = safeHref(s.source_url);
    return <>
      <p className="acc-sig-k"><i className="acc-mk on" aria-hidden="true" /><b>{signalName(s.rule_id ?? "", s.label)}</b>
        <span className="muted">{ago(s.happened_at ?? s.published_at)}</span>
        {href && <a href={href} target="_blank" rel="noreferrer noopener" aria-label={tr("Original source for {}", r.name)}>{tr("Source")} ↗</a>}</p>
      <p className="acc-sig-t" title={s.summary}><span className="sr">{tr("Verified signal:")} </span>{s.summary}</p>
    </>;
  }
  if (r.signal_count > 0) return <p className="acc-sig-t"><i className="acc-mk on" aria-hidden="true" />{counted(r.signal_count, ["checked signal", "checked signals"], ["sprawdzony sygnał", "sprawdzone sygnały", "sprawdzonych sygnałów"])}
    <span className="muted"> {[r.groups.map(groupName).join(", "), r.last_signal_at && fmtDate(r.last_signal_at)].filter(Boolean).join(", ")}</span></p>;
  if (r.list_signal) return <p className="acc-sig-t acc-unchecked" title={r.list_signal}><i className="acc-mk" aria-hidden="true" /><span className="acc-sig-from">{tr("From your list, not checked yet:")}</span> {r.list_signal}</p>;
  return <p className="acc-sig-t acc-none">{tr("No signal yet")}</p>;
}

function AccountRow({ r, review, research, selectable, selected, full, onSelect, onWatch, onDecide }: {
  r: any; review: boolean; research: boolean; selectable: boolean; selected: boolean; full: boolean;
  onSelect: () => void; onWatch: () => void; onDecide: (decision: "qualify" | "reject") => void;
}) {
  const to = `/accounts/${r.id}${review ? "?profile=1" : ""}`;
  const facts = [r.segment, countryName(r.country_code), r.employees != null ? staffCount(r.employees, r.employees_uncertain) : null].filter(Boolean);
  return <li className={`acc-row${selected ? " is-sel" : ""}`}>
    {selectable && <label className="acc-pick"><input type="checkbox" aria-label={tr("Select {} for a news check", r.name)} checked={selected} disabled={full} onChange={onSelect} /></label>}
    <span className="acc-av"><CompanyAvatar name={r.name} /></span>
    <div className="acc-id">
      <span className="acc-name-row"><Link className="acc-name" to={to}>{r.name}</Link>
        {r.shortlist && <span className="acc-tag">{tr("Shortlist")}</span>}
        {ORIGIN[r.origin] && <span className="acc-tag">{tr(ORIGIN[r.origin])}</span>}</span>
      {r.canonical_domain && <span className="acc-domain">{r.canonical_domain}</span>}
      {facts.length > 0 && <span className="acc-facts">{facts.map((f, i) => <span key={i}>{f}</span>)}</span>}
    </div>
    <div className="acc-sig">
      <SignalLine r={r} />
      {r.candidate_status === "pending_qualification" && research && <div className="acc-decide">
        <button type="button" onClick={() => onDecide("qualify")}>{tr("Confirm account")}</button>
        <button type="button" onClick={() => onDecide("reject")}>{tr("Remove from list")}</button></div>}
    </div>
    <div className="acc-nums">
      <div className="acc-rank"><ListRank priority={r.list_priority} score={r.list_score} /></div>
      <div className="acc-ppl">
        {r.people ? <><span className="acc-ppl-n"><b>{number(r.people)}</b> {counted(r.people, ["person", "people"], ["osoba", "osoby", "osób"]).replace(/^[\d\s., ]+/, "")}</span>
          <span className={`acc-mail${r.people_with_email ? " on" : ""}`}>{tr("{} with email", number(r.people_with_email))}</span></>
          : <span className="acc-none">{tr("No contacts yet")}</span>}
      </div>
    </div>
    <div className="acc-end">
      {review ? <Link to={to} className="acc-check">{tr("Check ownership")}</Link>
        : <button className="star hit44" aria-pressed={r.watched} aria-label={r.watched ? tr("Stop watching {}", r.name) : tr("Watch {}", r.name)} onClick={onWatch}>{r.watched ? "★" : "☆"}</button>}
    </div>
  </li>;
}
