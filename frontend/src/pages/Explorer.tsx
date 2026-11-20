import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Badge, Coverage, Err, humanize } from "../components/ui";

export default function Explorer() {
  const [entity, setEntity] = useState("opportunities");
  const [q, setQ] = useState(""); const [country, setCountry] = useState(""); const [existing, setExisting] = useState("");
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const p = new URLSearchParams({ entity, q }); if (country) p.set("country", country); if (existing) p.set("existing", existing);
    const t = setTimeout(() => api(`/api/explorer?${p}`).then(setD).catch(setErr), 200); return () => clearTimeout(t);
  }, [entity, q, country, existing]);
  const cols = d?.items?.[0] ? Object.keys(d.items[0]).filter((k) => k !== "id") : [];
  return (
    <>
      <h2>Market Explorer</h2><p className="sub">Search and filter. Results are limited to your scope and never prove the absence of market activity.</p>
      <div className="row" style={{ marginBottom: 12 }}>
        <select aria-label="Entity" value={entity} onChange={(e) => setEntity(e.target.value)}><option value="opportunities">Opportunities</option><option value="companies">Companies</option><option value="procurement">Procurement notices</option><option value="events">Events</option></select>
        <input aria-label="Search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        <input aria-label="Country code" placeholder="Country (GB, DE…)" value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} style={{ width: 130 }} />
        <select aria-label="List membership" value={existing} onChange={(e) => setExisting(e.target.value)}><option value="">Existing + new</option><option value="true">Existing master list</option><option value="false">New only</option></select>
      </div>
      <Err e={err} />{d && <><Coverage c={d.coverage} /><p className="small"><b>Ranking:</b> {d.ranking_basis}</p>
        <div className="card table-wrap"><table><thead><tr>{cols.map((c) => <th key={c}>{humanize(c)}</th>)}</tr></thead><tbody>
          {d.items.map((r: any, i: number) => <tr key={i}>{cols.map((c) => <td key={c}>{c === "display_name" && entity === "companies" ? <Link to={`/accounts/${r.id}`}>{r[c]}</Link> : c === "title" && entity === "opportunities" ? <Link to={`/opportunities/${r.id}`}>{r[c]}</Link>
            : /_at$|date/.test(c) ? fmtDate(r[c]) : typeof r[c] === "boolean" ? (r[c] ? <Badge kind="ok">yes</Badge> : "no") : String(r[c] ?? "—")}</td>)}</tr>)}
          {d.items.length === 0 && <tr><td className="muted">No results. {d.coverage_note}</td></tr>}</tbody></table></div></>}
    </>
  );
}
