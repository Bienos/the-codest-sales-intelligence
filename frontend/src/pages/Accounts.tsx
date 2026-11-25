import { counted, number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, post } from "../api";
import { SignalSummary } from "../components/BuyingSignal";
import Prospects from "./Prospects";
import { REGIONS, countryName, regionName, staffCount } from "./targetMarket";
import { Badge, Err, groupName, Skeleton, Tabs } from "../components/ui";

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
        <div><h1>{tr("Accounts & leads")}</h1><p className="sub">{view === "leads" ? tr("Find buying signals from the workbooks and open the companies they belong to.") : view === "review" ? tr("Companies your lead list marks for review, and companies found by research whose ownership is not confirmed yet.") : tr("Every company your lead list marks as eligible, plus privately held financial companies found by research.")}</p></div>
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
            <th>{tr("Company")}</th><th>{tr("Headquarters")}</th><th>{tr("Size")}</th><th>{tr("People")}</th><th>{review ? tr("Ownership") : tr("Buying signal and source")}</th><th>{review ? tr("Profile") : tr("Actions")}</th>
          </tr></thead><tbody>
            {items.map((r: any) => <tr key={r.id}>
              <td className="directory-company"><Link to={`/accounts/${r.id}${review ? "?profile=1" : ""}`}>{r.name}</Link>
                {(r.list_priority || r.list_score != null) && <span className="small">{r.list_priority && /^P\d$/.test(r.list_priority) && <Badge kind={r.list_priority === "P1" ? "bad" : r.list_priority === "P2" ? "warn" : ""}>{tr("Priority {}", r.list_priority)}</Badge>}
                  {r.list_score != null && <span className="muted"> {tr("List score {}", number(r.list_score))}</span>}</span>}
                <span className="small muted">{[r.segment, r.canonical_domain].filter(Boolean).join(" · ")}</span>
                <span className="small muted">{r.shortlist ? tr("On your shortlist") : tr(ORIGIN[r.origin] ?? "")}</span>
              </td>
              <td data-label={tr("Headquarters")}>{countryName(r.country_code)}</td>
              <td data-label={tr("Size")} className="nw">{staffCount(r.employees, r.employees_uncertain)}</td>
              <td data-label={tr("People")}>{r.people ? <Link to={`/accounts/${r.id}`}>{counted(r.people, ["person", "people"], ["osoba", "osoby", "osób"])}</Link> : <span className="small muted">{tr("None yet")}</span>}
                {r.people > 0 && <span className="small muted block">{tr("{} with email", number(r.people_with_email))}</span>}</td>
              <td data-label={review ? tr("Ownership") : tr("Buying signal and source")}>
                {!review && r.buying_signal && <SignalSummary signal={r.buying_signal} />}
                {review ? tr("To check") : r.stopped ? <Badge kind="warn">{tr("On hold")}</Badge> : !r.buying_signal && r.signal_count > 0 ?
                  <><Badge kind={BAND[r.band]?.[0]}>{tr(BAND[r.band]?.[1] ?? "Activity")}</Badge> <span className="small">{counted(r.signal_count, ["checked signal", "checked signals"], ["sprawdzony sygnał", "sprawdzone sygnały", "sprawdzonych sygnałów"])}</span>
                    <span className="small muted">{[r.groups.map(groupName).join(", "), r.last_signal_at && fmtDate(r.last_signal_at)].filter(Boolean).join(" · ")}</span></> :
                  !r.buying_signal && (r.list_signal ? <span className="small"><span className="muted">{tr("From your list, not checked yet:")}</span> {r.list_signal}</span>
                    : <span className="small muted">{tr("No checked signals yet")}</span>)}
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
