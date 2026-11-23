import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Badge, Err, Skeleton, reloadPage, Tabs } from "../components/ui";

const COUNTRY: Record<string, string> = { GB: "UK", DE: "Germany", AT: "Austria", CH: "Switzerland", SE: "Sweden", DK: "Denmark", NO: "Norway", FI: "Finland", PL: "Poland" };
const SIZE: Record<string, string> = { sme: "small or medium company", large: "large company" };
const endMonth = (d?: string | null) => (d ? new Date(d).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "unknown");
const when = (m: number) => (m <= 0 ? "ends within a month" : m === 1 ? "about 1 month left" : `about ${m} months left`);

export default function Renewals() {
  const [tab, setTab] = useState<"ending" | "winners">("ending");
  const [months, setMonths] = useState(12); const [country, setCountry] = useState(""); const [q, setQ] = useState(""); const [stated, setStated] = useState(false);
  const [loaded, setLoaded] = useState<{ tab: string; data: any } | null>(null); const [err, setErr] = useState<unknown>(null);
  const d = loaded && loaded.tab === tab ? loaded.data : null;           // data from the other tab is never drawn with this tab's layout
  useEffect(() => {
    setErr(null);
    const p = new URLSearchParams(); if (country) p.set("country", country);
    const path = tab === "ending" ? (p.set("months", String(months)), q.trim() && p.set("q", q.trim()), stated && p.set("stated_only", "true"), `/api/renewals?${p}`) : `/api/renewals/incumbents?${p}`;
    const t = setTimeout(() => api(path).then((data) => setLoaded({ tab, data })).catch(setErr), q ? 300 : 0);
    return () => clearTimeout(t);
  }, [tab, months, country, q, stated]);
  return (
    <>
      <div className="page-head"><div className="eyebrow">Work that comes round again</div><h1>Renewals</h1></div>
      <p className="small muted mt-0 measure">Public bodies award IT contracts for a fixed term, then usually buy again. Each card is an official award notice with the supplier who won. We add the contract length in the notice to the date the notice was sent to estimate when it ends. If the notice gives no length, we assume 3 years and label the date as an estimate.</p>
      <Tabs label="What to show" items={[{ key: "ending", label: "Contracts ending" }, { key: "winners", label: "Current suppliers" }]} value={tab} onChange={(k) => setTab(k as "ending" | "winners")} />
      <div className="feedbar">
        {tab === "ending" && <input aria-label="Search buyer, subject or supplier" placeholder="Search buyer, subject or supplier" value={q} onChange={(e) => setQ(e.target.value)} />}
        <select aria-label="Country" value={country} onChange={(e) => setCountry(e.target.value)}><option value="">All our markets</option>{Object.entries(COUNTRY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        {tab === "ending" && <select aria-label="Ending within" value={months} onChange={(e) => setMonths(Number(e.target.value))}>{[6, 12, 18, 24, 36].map((m) => <option key={m} value={m}>Ending within {m} months</option>)}</select>}
        {tab === "ending" && <label className="small"><input type="checkbox" checked={stated} onChange={(e) => setStated(e.target.checked)} /> Only contracts with a stated length</label>}
      </div>
      {err ? <Err e={err} onRetry={reloadPage} /> : !d ? <Skeleton /> : tab === "ending" ? (
        <>
          <p className="small muted">{d.items.length === 0 ? "Nothing matches." : `${d.items.length} ${d.items.length === 1 ? "contract" : "contracts"}, ending soonest first.`} {d.contracts_known > 0 && `We hold ${d.contracts_known.toLocaleString()} IT contract awards since ${fmtDate(d.records_since)}.`}</p>
          {d.contracts_known === 0 && <div className="empty">We are still loading the contract history. It fills in over the next few hours, so check back later.</div>}
          <div className="stack">{d.items.map((r: any) => (
            <article key={r.notice_id} className="card">
              <div className="row"><Badge kind={r.months_left <= 6 ? "warn" : "info"}>{when(r.months_left)}</Badge><Badge>{COUNTRY[r.buyer_country] ?? r.buyer_country}</Badge>
                {r.on_lists && <Badge kind="ok">On your lists</Badge>}<Badge kind={r.end_basis === "stated" ? "" : "warn"}>{r.end_basis === "stated" ? `Ends about ${endMonth(r.expected_end)}` : `Ends about ${endMonth(r.expected_end)} (estimate: no length in the notice)`}</Badge></div>
              <h2 className="mt-2 mb-1 h-sub">{r.buyer_org_id ? <Link to={`/accounts/${r.buyer_org_id}`}>{r.buyer_name}</Link> : r.buyer_name ?? "Buyer not named in the notice"}</h2>
              <p className="mt-0 mb-2">{(r.title ?? "").replace(/^[^–]+ – [^–]+ – /, "")}</p>
              <p className="small muted m-0"><b>Current supplier:</b> {r.winners.length ? r.winners.join(", ") : "not named in the notice"}{r.winner_size ? ` (${SIZE[r.winner_size] ?? r.winner_size})` : ""}</p>
              <p className="small muted mt-1 mb-0">Notice sent {fmtDate(r.awarded_on)} · <a href={r.official_url} target="_blank" rel="noreferrer noopener">official notice ↗</a></p>
            </article>))}</div>
        </>
      ) : (
        <>
          <p className="small muted">Who won the most public IT contracts in our markets over the last three years. A supplier with many wins is a strong incumbent, and often a competitor or a partner worth knowing.</p>
          <div className="card table-wrap"><table className="cards"><thead><tr><th>Supplier</th><th>Contracts won</th><th>Countries</th><th>Latest win</th></tr></thead><tbody>
            {d.items.map((r: any) => <tr key={r.supplier}><td>{r.supplier}{r.size && <div className="small muted">{SIZE[r.size] ?? r.size}</div>}</td><td data-label="Contracts won">{r.awards}</td><td className="small" data-label="Countries">{r.countries.map((c: string) => COUNTRY[c] ?? c).join(", ")}</td><td className="small" data-label="Latest win">{fmtDate(r.latest)}</td></tr>)}
            {d.items.length === 0 && <tr><td colSpan={4} className="muted">No suppliers yet. The contract history is still loading.</td></tr>}</tbody></table></div>
        </>
      )}
    </>
  );
}
