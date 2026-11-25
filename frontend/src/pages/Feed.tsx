import { useState } from "react";
import { Navigate, useSearchParams, Link } from "react-router-dom";
import { post } from "../api";
import { tr } from "../locale";
import { Err } from "../components/ui";
import { FindingCard, FindingsPagination, FindingsResult, ResearchLeads, SourceCoverage, useFindings } from "../components/Findings";
import { REGIONS, regionName } from "./targetMarket";
import { SHORT, signalName } from "../components/Signals";
import "../styles/findings.css";

/** Tenders have their own page, covering every buyer; old links to the tender view of the findings land there. */
export default function Feed() {
  const [params] = useSearchParams();
  return params.get("kind") === "tender" ? <Navigate to="/tenders" replace /> : <Findings />;
}

const WINDOWS: [string, string][] = [["14", "Last 2 weeks"], ["30", "Last 30 days"], ["60", "Last 60 days"], ["180", "Last 6 months"], ["365", "Last 12 months"]];

function Findings() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const page = Math.max(1, Number(params.get("page")) || 1);
  const days = params.get("days") ?? "60";
  const query = new URLSearchParams({
    q: params.get("q") ?? "", days, kind: params.get("kind") ?? "",
    region: params.get("region") ?? "", qualification: params.get("qualification") ?? "all", page: String(page), rule: params.get("rule") ?? "",
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
  const filtered = ["q", "region", "rule", "kind", "qualification"].some((k) => params.get(k)) || days !== "60";
  const clear = () => { setSearch(""); setParams(new URLSearchParams()); };
  const windowLabel = tr(WINDOWS.find(([k]) => k === days)?.[1] ?? "Last 60 days").toLowerCase();
  return <>
    <div className="fd-intro">
      <div>
        <h1>{tr("All findings")}</h1>
        <p className="fd-explain">{tr("A finding is news we checked against the original page: which company, what happened, the buying signal it points to and the quote that proves it. Open the company to see who to contact.")}</p>
      </div>
      <p className="fd-legend" aria-label={tr("Colour key")}>
        <span><i aria-hidden="true" />{tr("Buying signal")}</span>
        <span><i className="tender" aria-hidden="true" />{tr("Open RFP or tender")}</span>
      </p>
    </div>
    {event && <p><Link to="/feed">{tr("Show all findings")}</Link></p>}
    <form className="fd-filters" role="search" aria-label={tr("Filter findings")} onSubmit={e => { e.preventDefault(); update("q", search.trim()); }}>
      <div className="fd-field"><label htmlFor="fd-q">{tr("Search")}</label>
        <span className="fd-search">
          <input id="fd-q" type="search" placeholder={tr("Company or keyword")} maxLength={120} value={search} onChange={e => setSearch(e.target.value)} />
          <button type="submit">{tr("Search")}</button>
        </span>
      </div>
      <label>{tr("Region")}
        <select value={params.get("region") ?? ""} onChange={e => update("region", e.target.value)}>
          <option value="">{tr("All regions")}</option>{REGIONS.map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
        </select>
      </label>
      <label>{tr("Buying signal")}
        <select value={params.get("rule") ?? ""} onChange={e => update("rule", e.target.value)}>
          <option value="">{tr("All buying signals")}</option>{Object.keys(SHORT).map(id => <option key={id} value={id}>{signalName(id)}</option>)}
        </select>
      </label>
      <label>{tr("Type")}
        <select value={params.get("kind") ?? ""} onChange={e => update("kind", e.target.value)}>
          <option value="">{tr("All types")}</option><option value="tender">{tr("Open RFPs and tenders")}</option><option value="signal">{tr("Buying signals")}</option>
        </select>
      </label>
      <label>{tr("Companies")}
        <select value={params.get("qualification") ?? "all"} onChange={e => update("qualification", e.target.value)}>
          <option value="all">{tr("Targets and ownership to check")}</option><option value="target">{tr("Confirmed target accounts")}</option><option value="review">{tr("Ownership to check")}</option>
        </select>
      </label>
      <label>{tr("When")}
        <select value={days} onChange={e => update("days", e.target.value)}>
          {WINDOWS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
        </select>
      </label>
    </form>
    <div className="feed-grid"><div>
      {data && data.items.length > 0 && <p className="fd-status" role="status">
        <span>{tr("Page {}: {} findings from the {}", page, data.items.length, windowLabel)}{params.get("region") ? tr(", {}", regionName(params.get("region") ?? "")) : ""}{params.get("rule") ? tr(", signal: {}", signalName(params.get("rule") ?? "")) : ""}</span>
        {filtered && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}
      </p>}
      <FindingsResult data={data} error={error} retry={retry} empty="No matching findings">
        {data?.items.map(item => <FindingCard key={item.id} item={item} />)}
      </FindingsResult>
      {data?.items.length === 0 && !event && <div className="card archive-next">
        <p>{tr("Workbook leads and their original sources are available while new findings are being verified.")}</p>
        <Link to="/accounts?view=leads">{tr("Browse workbook leads")}</Link>
        {filtered && <button type="button" className="linkish" onClick={clear}>{tr("Clear filters")}</button>}
      </div>}
      <FindingsPagination page={page} hasMore={data?.has_more} change={n => update("page", String(n))} />
    </div><aside className="fd-aside" aria-label={tr("Sources and missed news")}>
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
