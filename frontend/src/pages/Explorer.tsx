import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { countryName } from "./targetMarket";
import { api, fmtDate } from "../api";
import { Badge, Coverage, Err, Empty, lab, priorityKind, Skeleton } from "../components/ui";

type Row = Record<string, any>;
type Col = { h: string; cell: (r: Row) => React.ReactNode };

const SORT_NOTE: Record<string, string> = { opportunities: "Sorted by opportunity score, highest first. The score ranks what to look at first; it is not a chance of winning.", companies: "Sorted by name.", events: "Newest first." };
const COLUMNS: Record<string, Col[]> = {
  opportunities: [
    { h: "Programme", cell: (r) => <Link to={`/opportunities/${r.id}`}>{r.title ?? r.display_name}</Link> },
    { h: "Account", cell: (r) => r.display_name },
    { h: "Priority", cell: (r) => <Badge kind={`${priorityKind(r.priority)} nw`}>{lab("priority", r.priority)}</Badge> },
    { h: "Score", cell: (r) => (r.score_total != null ? `${r.score_total}/100` : tr("None")) },
    { h: "How we would get in", cell: (r) => `${lab("route", r.route_type)}${r.counterparty_name ? ` (${r.counterparty_name})` : ""}` },
    { h: "Will they use outside help?", cell: (r) => lab("demand", r.external_demand_status) },
    { h: "Sources", cell: (r) => (r.evidence_confidence ? <span className="nw">{String(r.evidence_confidence).replace(/^./, (c: string) => c.toUpperCase())}</span> : tr("None")) },
  ],
  companies: [
    { h: "Company", cell: (r) => <Link to={`/accounts/${r.id}`}>{r.display_name}</Link> },
    { h: "Country", cell: (r) => countryName(r.country_code) ?? tr("Not known") },
    { h: "Segment", cell: (r) => r.segment ?? tr("Not known") },
    { h: "Staff", cell: (r) => (r.employees_estimate ? number(r.employees_estimate) : tr("Not known")) },
    { h: "On your lists", cell: (r) => (r.in_master_list ? <Badge kind="ok">{tr("Yes")}</Badge> : tr("No")) },
  ],
  events: [
    { h: "What happened", cell: (r) => r.summary },
    { h: "Type", cell: (r) => lab("event", r.event_type) },
    { h: "Date", cell: (r) => fmtDate(r.event_at ?? r.pub_at) },
    { h: "We saw it", cell: (r) => fmtDate(r.first_observed_at) },
    { h: "Checked", cell: (r) => (r.verification_status === "verified" ? <Badge kind="ok">{tr("Yes")}</Badge> : <Badge kind="warn">{tr("Not yet")}</Badge>) },
  ],
};
const SHOW: [string, string][] = [["opportunities", "Opportunities"], ["companies", "Companies"], ["events", "Findings"]];

const COUNTRIES = [
  ["GB", "United Kingdom"], ["AT", "Austria"], ["BE", "Belgium"], ["CH", "Switzerland"],
  ["CZ", "Czechia"], ["DE", "Germany"], ["DK", "Denmark"], ["ES", "Spain"], ["FI", "Finland"],
  ["FR", "France"], ["IE", "Ireland"], ["IS", "Iceland"], ["IT", "Italy"], ["NL", "Netherlands"],
  ["NO", "Norway"], ["PL", "Poland"], ["PT", "Portugal"], ["SE", "Sweden"], ["US", "United States"],
];

export default function Explorer() {
  const [searchParams] = useSearchParams();
  const [entity, setEntity] = useState("opportunities");
  const [q, setQ] = useState(searchParams.get("q") ?? ""); const [country, setCountry] = useState(""); const [existing, setExisting] = useState("");
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setD(null);
    const p = new URLSearchParams({ entity, q, target: "true" }); if (country) p.set("country", country); if (existing) p.set("existing", existing);
    const t = setTimeout(() => api(`/api/explorer?${p}`, { signal: request.signal }).then(setD).catch((error) => { if (!request.signal.aborted) setErr(error); }), 200);
    return () => { clearTimeout(t); request.abort(); };
  }, [entity, q, country, existing, retry]);
  useEffect(() => { setQ(searchParams.get("q") ?? ""); }, [searchParams]);
  const cols = COLUMNS[entity];
  const ready = d && d.entity === entity;
  return (
    <>
      <div className="page-head"><div className="eyebrow">{tr("Research")}</div><h1>{tr("Explorer")}</h1><p className="sub">{tr("Search across companies, verified findings and opportunities. Use All findings to browse by date or tender type.")}</p></div>
      <div className="row mb-3">
        <label className="sr" htmlFor="ex-show">{tr("Show")}</label>
        <select id="ex-show" value={entity} onChange={(e) => setEntity(e.target.value)}>{SHOW.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}</select>
        <input aria-label={tr("Search")} placeholder={tr("Search…")} value={q} onChange={(e) => setQ(e.target.value)} />
        {(entity === "opportunities" || entity === "companies") && <>
          <input aria-label={tr("Country code")} placeholder={tr("Country code")} list="explorer-countries" maxLength={2} value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} className="field-sm" />
          <datalist id="explorer-countries">{COUNTRIES.map(([code, name]) => <option key={code} value={code} label={countryName(code) ?? name} />)}</datalist>
        </>}
        {entity === "companies" && <select aria-label={tr("List membership")} value={existing} onChange={(e) => setExisting(e.target.value)}><option value="">{tr("All companies")}</option><option value="true">{tr("On your lists")}</option><option value="false">{tr("Not on your lists")}</option></select>}
      </div>
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!ready && !err && <Skeleton />}
      {ready && <>
        {d.coverage && (d.coverage.unhealthy?.length > 0 || d.coverage.no_active_monitoring) && <Coverage c={d.coverage} />}
        <p className="small muted">{tr(SORT_NOTE[entity])}</p>
        {d.items.length === 0 ? <Empty title={tr("Nothing matches")}>{tr("Try fewer filters. Remember we only see the sources we watch.")}</Empty> :
          <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Explorer results")}><table><thead><tr>{cols.map((c) => <th key={c.h}>{tr(c.h)}</th>)}</tr></thead><tbody>
            {d.items.map((r: Row, i: number) => <tr key={r.id ?? r.notice_id ?? i}>{cols.map((c) => <td key={c.h}>{c.cell(r)}</td>)}</tr>)}</tbody></table></div>}
      </>}
    </>
  );
}
