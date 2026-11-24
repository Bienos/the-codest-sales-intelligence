import { counted, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, post, trackSourceOpened } from "../api";
import { Badge, Empty, Err, Skeleton } from "../components/ui";
import { REGIONS, countryName, regionName, staffCount } from "./targetMarket";

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

export default function Prospects({ q = "", region = "", canVerify = false }: { q?: string; region?: string; canVerify?: boolean }) {
  const [lane, setLane] = useState<Lane>("all");
  const [offset, setOffset] = useState({ page: 1, q, region, lane });
  const page = offset.q === q && offset.region === region && offset.lane === lane ? offset.page : 1;
  const limit = 20;
  const query = new URLSearchParams({ lane, q, region, workbook: "true", page: String(page), limit: String(limit) }).toString();
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [err, setErr] = useState<unknown>(null);
  const [feedback, setFeedback] = useState<number | "single" | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
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
  const groups = REGIONS.map(([key, title]) => ({ key, title, items: d?.items.filter((item: any) => item.organization.region === key) ?? [] })).filter(({ items }) => items.length > 0);
  return (
    <section aria-label={tr("Buying signals from your workbooks")}>
      <p className="small muted">{tr("Buying signals from your workbooks. Open the original source to check the claim.")}</p>
      <div className="directory-toolbar">
        <label className="row small">{tr("Lead list")}<select aria-label={tr("Lead list")} value={lane} onChange={(e) => { setLane(e.target.value as Lane); setFeedback(null); }}>
            <option value="all">{tr("All workbook leads")}</option><option value="shortlist">{tr("Outreach shortlist")}</option><option value="signals">{tr("Workbook signals")}</option>
          </select>
        </label>
        {canVerify && d?.items.some((it: any) => it.source_url && !it.verification) && <button disabled={busy} onClick={() => check()}>{tr("Check up to 10 sources")}</button>}
      </div>
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <Err e={err} onRetry={() => setTick((n) => n + 1)} />
      {!d && !err && <Skeleton label={tr("Loading leads")} />}
      {d && <p className="small muted directory-count" role="status">{counted(d.total, ["lead", "leads"], ["lead", "leady", "leadów"])}{regionName(region) && tr(" · {}", regionName(region))}</p>}
      {d?.items.length === 0 && <Empty title={tr("No matching leads")}>{tr("Try the other lead list or broaden your search.")}</Empty>}
      {groups.map(({ key, title, items }) => <section key={key} className="directory-region">
        <h2 className={region ? "sr" : undefined}>{tr(title)}</h2>
        <div className="directory-leads">
        {items.map((it: any) => {
          const org = it.organization;
          const state = it.verification && SOURCE_STATUS[it.verification.status];
          return <article key={org.id} className="card directory-lead">
            <div className="directory-lead-head">
              <div>
                <h3><Link to={`/accounts/${org.id}`}>{org.name}</Link></h3>
                <p className="small muted">{[countryName(org.country), org.segment, staffCount(org.employees, org.employees_uncertain)].filter(Boolean).join(" · ")}</p>
              </div>
              <div className="row">{it.target_fit === false && <Badge>{tr("Account fit unconfirmed")}</Badge>}<Badge kind={state?.[0] ?? "warn"}>{state ? tr(state[1]) : it.source_url ? tr("Source unchecked") : tr("Source missing")}</Badge></div>
            </div>
            {it.signal && <p className="lead-description">{it.signal}</p>}
            {it.signal_date && <p className="small muted mt-1">{fmtDate(it.signal_date)}</p>}
            {it.why && <p><strong>{tr("Possible engineering need")}</strong><br />{it.why}</p>}
            {(it.caveat || it.buyer_role || it.score !== null) && <details className="small">
              <summary>{tr("Workbook notes")}</summary>
              {it.buyer_role && <p><strong>{tr("Suggested contact")}</strong><br />{[it.buyer_name, it.buyer_role].filter(Boolean).join(", ")}</p>}
              {it.caveat && <p>{it.caveat}</p>}
              {it.score !== null && <p>{tr("Workbook score") + " "}{it.score}/100</p>}
            </details>}
            <div className="row mt-3">
              {it.source_url && <a href={it.source_url} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "prospect" })}>{tr("Open original source ↗")}</a>}
              {canVerify && it.source_url && !it.verification && <button disabled={busy} onClick={() => check(it)}>{tr("Check source")}</button>}
            </div>
          </article>;
        })}
        </div></section>)}
      {pages > 1 && <div className="directory-pagination">
        <button disabled={visiblePage <= 1} onClick={() => setOffset({ page: visiblePage - 1, q, region, lane })}>{tr("Previous")}</button>
        <span>{tr("Page") + " "}{visiblePage}{" " + tr("of") + " "}{pages}</span>
        <button disabled={visiblePage >= pages} onClick={() => setOffset({ page: visiblePage + 1, q, region, lane })}>{tr("Next")}</button>
      </div>}
    </section>
  );
}
