import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Badge, Err } from "../components/ui";

const COUNTRY: Record<string, string> = { GB: "UK", DE: "Germany", AT: "Austria", CH: "Switzerland", SE: "Sweden", DK: "Denmark", NO: "Norway", FI: "Finland", PL: "Poland" };
const SIZE: Record<string, string> = { sme: "small or medium company", large: "large company" };
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
      <div className="page-head"><div className="eyebrow">Work that comes round again</div><h2>Renewals</h2></div>
      <p className="small muted" style={{ marginTop: 0, maxWidth: "80ch" }}>Public bodies award IT contracts for a fixed term, then usually buy again. These are official award notices from the last few years, with the supplier who won each one. The end date is the award date plus the contract length in the notice. Where a notice gives no length, we assume 36 months and say so.</p>
      <div className="tabs" role="tablist" aria-label="What to show">
        <button role="tab" aria-selected={tab === "ending"} onClick={() => setTab("ending")}>Contracts ending</button>
        <button role="tab" aria-selected={tab === "winners"} onClick={() => setTab("winners")}>Who wins this work</button>
      </div>
      <div className="feedbar">
        {tab === "ending" && <input aria-label="Search buyer, subject or supplier" placeholder="Search buyer, subject or supplier" value={q} onChange={(e) => setQ(e.target.value)} />}
        <select aria-label="Country" value={country} onChange={(e) => setCountry(e.target.value)}><option value="">All our markets</option>{Object.entries(COUNTRY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        {tab === "ending" && <select aria-label="Ending within" value={months} onChange={(e) => setMonths(Number(e.target.value))}>{[6, 12, 18, 24, 36].map((m) => <option key={m} value={m}>Ending within {m} months</option>)}</select>}
        {tab === "ending" && <label className="small"><input type="checkbox" checked={stated} onChange={(e) => setStated(e.target.checked)} /> Only contracts with a stated length</label>}
      </div>
      {err ? <Err e={err} /> : !d ? <p className="muted">Loading…</p> : tab === "ending" ? (
        <>
          <p className="small muted">{d.items.length === 0 ? "Nothing matches." : `${d.items.length} contracts, soonest first.`} {d.contracts_known > 0 && `We hold ${d.contracts_known.toLocaleString()} IT contracts awarded since ${fmtDate(d.records_since)}.`}</p>
          {d.contracts_known === 0 && <div className="empty">The contract history is still loading. It is read in the background and fills in over the next hours.</div>}
          <div className="stack">{d.items.map((r: any) => (
            <article key={r.notice_id} className="card">
              <div className="row"><Badge kind={r.months_left <= 6 ? "warn" : "info"}>{when(r.months_left)}</Badge><Badge>{COUNTRY[r.buyer_country] ?? r.buyer_country}</Badge>
                {r.on_lists && <Badge kind="ok">On your lists</Badge>}<Badge>{r.end_basis === "stated" ? "Length stated in notice" : "Length assumed (36 months)"}</Badge></div>
              <h3 style={{ margin: "8px 0 2px" }}>{r.buyer_org_id ? <Link to={`/accounts/${r.buyer_org_id}`}>{r.buyer_name}</Link> : r.buyer_name ?? "Buyer not named"}</h3>
              <p style={{ margin: "0 0 6px" }}>{(r.title ?? "").replace(/^[^–]+ – [^–]+ – /, "")}</p>
              <p className="small muted" style={{ margin: 0 }}><b>Won by:</b> {r.winners.length ? r.winners.join(", ") : "not named in the notice"}{r.winner_size ? ` (${SIZE[r.winner_size] ?? r.winner_size})` : ""}</p>
              <p className="small muted" style={{ margin: "2px 0 0" }}>Awarded {fmtDate(r.awarded_on)} · probably ends {fmtDate(r.expected_end)} · <a href={r.official_url} target="_blank" rel="noreferrer noopener">official notice ↗</a></p>
            </article>))}</div>
        </>
      ) : (
        <>
          <p className="small muted">Suppliers named as winners of IT contracts from public buyers in our markets over the last three years. A large number of wins means a strong incumbent, and often a partner or a competitor worth knowing.</p>
          <div className="card table-wrap"><table><thead><tr><th>Supplier</th><th>Awards</th><th>Countries</th><th>Latest</th></tr></thead><tbody>
            {d.items.map((r: any) => <tr key={r.supplier}><td>{r.supplier}{r.size && <div className="small muted">{SIZE[r.size] ?? r.size}</div>}</td><td>{r.awards}</td><td className="small">{r.countries.map((c: string) => COUNTRY[c] ?? c).join(", ")}</td><td className="small">{fmtDate(r.latest)}</td></tr>)}
            {d.items.length === 0 && <tr><td colSpan={4} className="muted">None yet.</td></tr>}</tbody></table></div>
        </>
      )}
    </>
  );
}
