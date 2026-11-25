import { counted, number, tr } from "../locale";
import { ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, post, trackSourceOpened } from "../api";
import { Empty, Err, Skeleton, safeHref } from "../components/ui";
import { CompanyAvatar, ListRank, ago } from "../components/Findings";
import { countryName, staffCount } from "./targetMarket";
import "../styles/accounts.css";

type Lane = "all" | "shortlist" | "signals";

const SOURCE_STATUS: Record<string, [string, string]> = {
  queued: ["info", "Check queued"],
  fetching: ["info", "Reading source"],
  extracting: ["info", "Checking the claim"],
  verified: ["ok", "Source checked"],
  no_event: ["warn", "Claim could not be confirmed"],
  failed: ["bad", "Source could not be read"],
  blocked: ["bad", "Access blocked"],
  waiting_budget: ["warn", "Check waiting for budget"],
};

/** The workbook leads: each lead-list signal with its company, the source to check, and what engineering need it may point to. */
export default function Prospects({ q = "", region = "", canVerify = false, fields }: { q?: string; region?: string; canVerify?: boolean; fields?: ReactNode }) {
  const [lane, setLane] = useState<Lane>("all");
  const [offset, setOffset] = useState({ page: 1, q, region, lane });
  const page = offset.q === q && offset.region === region && offset.lane === lane ? offset.page : 1;
  const limit = 25;
  const query = new URLSearchParams({ lane, q, region, workbook: "true", page: String(page), limit: String(limit) }).toString();
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [err, setErr] = useState<unknown>(null);
  const [feedback, setFeedback] = useState<number | "single" | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const top = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    const timer = setTimeout(() => {
      api(`/api/prospects?${query}`, { signal: request.signal })
        .then((data) => { if (!request.signal.aborted) setResult({ query, data }); })
        .catch((error) => { if (!request.signal.aborted) setErr(error); });
    }, q ? 250 : 0);
    return () => { clearTimeout(timer); request.abort(); };
  }, [query, q, tick]);
  const checking = d?.items.some((it: any) => ["queued", "fetching", "extracting"].includes(it.verification?.status));
  useEffect(() => {
    if (!checking) return;
    const timer = setInterval(() => { if (!document.hidden) setTick((n) => n + 1); }, 6000);
    return () => clearInterval(timer);
  }, [checking]);

  const check = async (item?: any) => {
    setBusy(true);
    setErr(null);
    setFeedback(null);
    try {
      if (item) {
        await post("/api/prospects/verify", { organization_id: item.organization.id, url: item.source_url });
        setFeedback("single");
      } else {
        const r = await post("/api/prospects/verify-batch", { lane, q, region, workbook: true, page: d?.page ?? page, page_size: limit, limit: 10 });
        setFeedback(r.queued);
      }
      setTick((n) => n + 1);
    } catch (error) { setErr(error); }
    finally { setBusy(false); }
  };

  const msg = feedback === "single" ? tr("Source check queued.") : feedback === 0 ? tr("There are no unchecked source links in this view.")
    : feedback === null ? "" : tr("Queued: {}", counted(feedback, ["source check", "source checks"], ["sprawdzenie źródła", "sprawdzenia źródeł", "sprawdzeń źródeł"]));
  const pages = d ? Math.max(1, Math.ceil(d.total / limit)) : 1;
  const visiblePage = d?.page ?? page;
  const goTo = (next: number) => { setOffset({ page: next, q, region, lane }); top.current?.scrollIntoView({ block: "start" }); };
  return (
    <section aria-label={tr("Buying signals from your workbooks")}>
      <div className="acc-filters" role="search" aria-label={tr("Filter leads")}>
        <div className="acc-filter-row is-leads">
          {fields}
          <label>{tr("Lead list")}
            <select value={lane} onChange={(e) => { setLane(e.target.value as Lane); setFeedback(null); }}>
              <option value="all">{tr("All workbook leads")}</option><option value="shortlist">{tr("Outreach shortlist")}</option><option value="signals">{tr("Workbook signals")}</option>
            </select>
          </label>
        </div>
      </div>
      <div className="acc-resultbar" ref={top}>
        <p className="acc-count" role="status">{d ? counted(d.total, ["lead", "leads"], ["lead", "leady", "leadów"]) : tr("Loading leads")}
          <span className="muted"> {tr("from your workbooks. Open the original source to check the claim.")}</span></p>
        <div className="acc-tools">
          {canVerify && d?.items.some((it: any) => it.source_url && !it.verification) && <button disabled={busy} onClick={() => check()}>{tr("Check up to 10 sources")}</button>}
        </div>
      </div>
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <Err e={err} onRetry={() => setTick((n) => n + 1)} />
      {!d && !err && <Skeleton rows={6} label={tr("Loading leads")} />}
      {d?.items.length === 0 && <Empty title={tr("No matching leads")}>{tr("Try the other lead list or broaden your search.")}</Empty>}
      {d?.items.length > 0 && <>
        <div className="acc-head is-leads" aria-hidden="true">
          <span className="acc-h-id">{tr("Company")}</span><span className="acc-h-sig">{tr("Signal from your list")}</span>
          <span className="acc-h-rank">{tr("Lead list")}</span><span className="acc-h-ppl">{tr("Source")}</span>
        </div>
        <ol className="acc-list is-leads" aria-label={tr("Leads, page {} of {}", visiblePage, pages)}>
          {d.items.map((it: any) => <LeadRow key={`${it.organization.id}-${it.source_url ?? it.signal}`} it={it} canVerify={canVerify} busy={busy} check={() => check(it)} />)}
        </ol>
      </>}
      {pages > 1 && <nav className="acc-pages" aria-label={tr("Pages")}>
        <button disabled={visiblePage <= 1} onClick={() => goTo(visiblePage - 1)}>{tr("Previous")}</button>
        <span>{tr("Page {} of {}", visiblePage, pages)}<span className="muted"> · {tr("{} to {} of {}", number((visiblePage - 1) * limit + 1), number(Math.min(visiblePage * limit, d.total)), number(d.total))}</span></span>
        <button disabled={visiblePage >= pages} onClick={() => goTo(visiblePage + 1)}>{tr("Next")}</button>
      </nav>}
    </section>
  );
}

function LeadRow({ it, canVerify, busy, check }: { it: any; canVerify: boolean; busy: boolean; check: () => void }) {
  const org = it.organization;
  const state = it.verification && SOURCE_STATUS[it.verification.status];
  const facts = [org.segment, countryName(org.country), org.employees != null ? staffCount(org.employees, org.employees_uncertain) : null].filter(Boolean);
  const href = safeHref(it.source_url);
  const headline = it.signal_type && it.signal_type !== it.signal ? it.signal_type : null;
  return <li className="acc-row">
    <span className="acc-av"><CompanyAvatar name={org.name} /></span>
    <div className="acc-id">
      <span className="acc-name-row"><Link className="acc-name" to={`/accounts/${org.id}`}>{org.name}</Link>
        {it.target_fit === false && <span className="acc-tag">{tr("Account fit unconfirmed")}</span>}</span>
      {facts.length > 0 && <span className="acc-facts">{facts.map((f, i) => <span key={i}>{f}</span>)}</span>}
    </div>
    <div className="acc-sig">
      {headline && <p className="acc-sig-k"><i className={`acc-mk${it.verification?.status === "verified" ? " on" : ""}`} aria-hidden="true" /><b>{headline}</b>
        {it.signal_date && <span className="muted">{fmtDate(it.signal_date)}, {ago(it.signal_date)}</span>}</p>}
      {it.signal && <p className={`acc-sig-t${headline ? "" : " acc-unchecked"}`} title={it.signal}>
        {!headline && <i className="acc-mk" aria-hidden="true" />}{it.signal}</p>}
      {!headline && it.signal_date && <p className="acc-sig-date muted">{fmtDate(it.signal_date)}, {ago(it.signal_date)}</p>}
      {it.why && <p className="acc-need"><b>{tr("Possible engineering need")}</b> {it.why}</p>}
      {(it.caveat || it.buyer_role) && <details className="acc-notes">
        <summary>{tr("Workbook notes")}</summary>
        {it.buyer_role && <p><b>{tr("Suggested contact")}</b> {[it.buyer_name, it.buyer_role].filter(Boolean).join(", ")}</p>}
        {it.caveat && <p>{it.caveat}</p>}
      </details>}
    </div>
    <div className="acc-nums">
      <div className="acc-rank"><ListRank priority={it.priority} score={it.score} /></div>
      <div className="acc-ppl acc-src">
        <span className={`acc-state ${state?.[0] ?? (href ? "todo" : "none")}`}>{state ? tr(state[1]) : href ? tr("Not checked yet") : tr("No source link")}</span>
        {href && <a href={href} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "prospect" })}>{tr("Open source")} ↗</a>}
        {canVerify && href && !it.verification && <button type="button" className="linkish" disabled={busy} onClick={check}>{tr("Check source")}</button>}
      </div>
    </div>
  </li>;
}
