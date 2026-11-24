import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, post, trackSourceOpened } from "../api";
import { Badge, Empty, Err, Skeleton } from "../components/ui";
import { REGIONS, countryName, regionName, staffCount } from "./targetMarket";

type Lane = "shortlist" | "signals";

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
  const [lane, setLane] = useState<Lane>("shortlist");
  const [offset, setOffset] = useState({ page: 1, q, region, lane });
  const page = offset.q === q && offset.region === region && offset.lane === lane ? offset.page : 1;
  const limit = 20;
  const query = new URLSearchParams({ lane, q, region, target: "true", page: String(page), limit: String(limit) }).toString();
  const [result, setResult] = useState<{ query: string; data: any } | null>(null);
  const d = result?.query === query ? result.data : null;
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
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
    setMsg("");
    try {
      if (item) {
        await post("/api/prospects/verify", { organization_id: item.organization.id, url: item.source_url });
        setMsg("Source check queued.");
      } else {
        const r = await post("/api/prospects/verify-batch", { lane, q, region, target: true, page: d?.page ?? page, page_size: limit, limit: 10 });
        setMsg(r.queued ? `${r.queued} source ${r.queued === 1 ? "check" : "checks"} queued.` : "There are no unchecked source links in this view.");
      }
      setTick((n) => n + 1);
    } catch (error) { setErr(error); }
    finally { setBusy(false); }
  };

  const pages = d ? Math.max(1, Math.ceil(d.total / limit)) : 1;
  const visiblePage = d?.page ?? page;
  const groups = REGIONS.map(([key, title]) => ({ key, title, items: d?.items.filter((item: any) => item.organization.region === key) ?? [] })).filter(({ items }) => items.length > 0);
  return (
    <section aria-label="Buying signals from your workbooks">
      <p className="small muted">Buying signals from your workbooks. Open the original source to check the claim.</p>
      <div className="directory-toolbar">
        <label className="row small">Lead list
          <select aria-label="Lead list" value={lane} onChange={(e) => { setLane(e.target.value as Lane); setMsg(""); }}>
            <option value="shortlist">Outreach shortlist</option><option value="signals">Workbook signals</option>
          </select>
        </label>
        {canVerify && d?.items.some((it: any) => it.source_url && !it.verification) && <button disabled={busy} onClick={() => check()}>Check up to 10 sources</button>}
      </div>
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <Err e={err} onRetry={() => setTick((n) => n + 1)} />
      {!d && !err && <Skeleton label="Loading leads" />}
      {d && <p className="small muted directory-count" role="status">{d.total.toLocaleString()} {d.total === 1 ? "lead" : "leads"}{regionName(region) && ` in ${regionName(region)}`}</p>}
      {d?.items.length === 0 && <Empty title="No matching leads">Try the other lead list or broaden your search.</Empty>}
      {groups.map(({ key, title, items }) => <section key={key} className="directory-region">
        <h2 className={region ? "sr" : undefined}>{title}</h2>
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
              <Badge kind={state?.[0] ?? "warn"}>{state?.[1] ?? (it.source_url ? "Source unchecked" : "Source missing")}</Badge>
            </div>
            {it.signal && <p className="lead-description">{it.signal}</p>}
            {it.signal_date && <p className="small muted mt-1">{fmtDate(it.signal_date)}</p>}
            {it.why && <p><strong>Possible engineering need</strong><br />{it.why}</p>}
            {(it.caveat || it.buyer_role || it.score !== null) && <details className="small">
              <summary>Workbook notes</summary>
              {it.buyer_role && <p><strong>Suggested contact</strong><br />{[it.buyer_name, it.buyer_role].filter(Boolean).join(", ")}</p>}
              {it.caveat && <p>{it.caveat}</p>}
              {it.score !== null && <p>Workbook score {it.score}/100</p>}
            </details>}
            <div className="row mt-3">
              {it.source_url && <a href={it.source_url} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: org.id, where: "prospect" })}>Open original source ↗</a>}
              {canVerify && it.source_url && !it.verification && <button disabled={busy} onClick={() => check(it)}>Check source</button>}
            </div>
          </article>;
        })}
        </div></section>)}
      {pages > 1 && <div className="directory-pagination">
        <button disabled={visiblePage <= 1} onClick={() => setOffset({ page: visiblePage - 1, q, region, lane })}>Previous</button>
        <span>Page {visiblePage} of {pages}</span>
        <button disabled={visiblePage >= pages} onClick={() => setOffset({ page: visiblePage + 1, q, region, lane })}>Next</button>
      </div>}
    </section>
  );
}
