import { useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { post } from "../api";
import { tr } from "../locale";
import { Err } from "../components/ui";
import { FindingCard, FindingsPagination, FindingsResult, ResearchLeads, SourceCoverage, useFindings } from "../components/Findings";
import { REGIONS } from "./targetMarket";

export default function Feed() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const page = Math.max(1, Number(params.get("page")) || 1);
  const query = new URLSearchParams({
    q: params.get("q") ?? "", days: params.get("days") ?? "60", kind: params.get("kind") ?? "",
    region: params.get("region") ?? "", qualification: params.get("qualification") ?? "all", page: String(page),
  });
  const event = params.get("finding");
  if (event) query.set("event", event);
  const { data, error, retry } = useFindings(query.toString());
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    next.delete("finding");
    setParams(next);
  };
  return <>
    <div className="page-head"><div><h1>{tr("All findings")}</h1><p className="sub">{tr("Search verified activity and open requests by company, region or date.")}</p></div></div>
    {event && <p><Link to="/feed">{tr("Show all findings")}</Link></p>}
    <form className="feedbar" onSubmit={e => { e.preventDefault(); update("q", search.trim()); }}>
      <input type="search" aria-label={tr("Search the feed")} placeholder={tr("Search company or keyword")}
        maxLength={120} value={search} onChange={e => setSearch(e.target.value)} />
      <button type="submit">{tr("Search")}</button>
      <select aria-label={tr("Region")} value={query.get("region") ?? ""} onChange={e => update("region", e.target.value)}>
        <option value="">{tr("All regions")}</option>{REGIONS.map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
      </select>
      <select aria-label={tr("Finding type")} value={query.get("kind") ?? ""} onChange={e => update("kind", e.target.value)}>
        <option value="">{tr("All types")}</option><option value="tender">{tr("Open RFPs and tenders")}</option><option value="signal">{tr("Buying signals")}</option>
      </select>
      <select aria-label={tr("Company eligibility")} value={query.get("qualification") ?? "all"} onChange={e => update("qualification", e.target.value)}>
        <option value="all">{tr("All eligible and pending accounts")}</option><option value="target">{tr("Confirmed target accounts")}</option><option value="review">{tr("Ownership to check")}</option>
      </select>
      <select aria-label={tr("Time window")} value={query.get("days") ?? "60"} onChange={e => update("days", e.target.value)}>
        <option value="14">{tr("Last 2 weeks")}</option><option value="30">{tr("Last 30 days")}</option>
        <option value="60">{tr("Last 60 days")}</option><option value="180">{tr("Last 6 months")}</option>
      </select>
    </form>
    <div className="feed-grid"><div>
      <FindingsResult data={data} error={error} retry={retry} empty="No matching findings">
        {data?.items.map(item => <FindingCard key={item.id} item={item} />)}
      </FindingsResult>
      {data?.items.length === 0 && !event && <div className="card archive-next">
        <p>{tr("Workbook leads and their original sources are available while new findings are being verified.")}</p>
        <Link to="/accounts?view=leads">{tr("Browse workbook leads")} →</Link>
        {params.get("kind") && <Link to="/feed">{tr("Show all findings")}</Link>}
      </div>}
      <FindingsPagination page={page} hasMore={data?.has_more} change={n => update("page", String(n))} />
    </div><aside>
      {data && <><SourceCoverage coverage={data.coverage} /><ResearchLeads data={data} /></>}
      <ReportMiss />
    </aside></div>
  </>;
}

function ReportMiss() {
  const [url, setUrl] = useState(""); const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [res, setRes] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const send = async () => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await post("/api/misses", { url: url.trim(), note: note.trim() })); setUrl(""); setNote(""); } catch (e) { setErr(e); }
    setBusy(false);
  };
  return (
    <div className="card mb-4"><div className="eyebrow">{tr("Saw something we missed?")}</div>
      <p className="small muted mt-0">{tr("Paste the link. We will tell you if we already had it, and read it if not. It also shows us which source to improve.")}</p>
      <div className="report-fields"><label className="small" htmlFor="miss-url">{tr("Link to the page")}</label>
      <input id="miss-url" type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
      <label className="small" htmlFor="miss-note">{tr("What is it? (optional)")}</label>
      <input id="miss-note" maxLength={400} placeholder={tr("e.g. Bank X chose a new payments vendor")} value={note} onChange={(e) => setNote(e.target.value)} /></div>
      <button className="primary mt-2" disabled={busy || !/^https?:\/\//i.test(url.trim())} onClick={send}>{busy ? tr("Sending…") : tr("Report it")}</button>
      {res && <p className="small mb-0" role="status">{res.already_known ? tr("This page is already on file from {}.", res.source_id) : res.duplicate ? tr("Someone already reported this. It is being checked.") : tr("Thanks. It is queued and will be read shortly.")}</p>}
      {err ? <Err e={err} /> : null}</div>
  );
}
