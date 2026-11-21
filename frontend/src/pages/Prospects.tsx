import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Empty, Err, humanize } from "../components/ui";

type Lane = "shortlist" | "signals";

function age(d: number | null): string {
  if (d === null) return "date unknown";
  if (d < 0) return "future date";
  if (d === 0) return "today";
  if (d < 60) return `${d} days ago`;
  return `${Math.round(d / 30)} months ago`;
}

export default function Prospects() {
  const [lane, setLane] = useState<Lane>("shortlist");
  const [q, setQ] = useState("");
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    const t = setTimeout(() => api(`/api/prospects?lane=${lane}&q=${encodeURIComponent(q)}&limit=30`).then(setD).catch(setErr), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [lane, q]);
  return (
    <section aria-label="From your lists">
      <div className="section-title">From your lists</div>
      <div className="callout">
        <b>Starting point, not verified.</b> These come from your two workbooks. The platform has not checked the sources yet, so open the link and confirm the event before you act.
      </div>
      <div className="row spread" style={{ margin: "14px 0" }}>
        <div className="tabs" style={{ margin: 0, border: 0 }} role="tablist">
          <button role="tab" aria-selected={lane === "shortlist"} onClick={() => setLane("shortlist")}>Your shortlist</button>
          <button role="tab" aria-selected={lane === "signals"} onClick={() => setLane("signals")}>Recent signals</button>
        </div>
        <input aria-label="Search accounts" placeholder="Search accounts" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
      </div>
      <Err e={err} />
      {!d && !err && <p className="muted">Loading</p>}
      {d && d.items.length === 0 && <Empty title="No matches">Try the other list or clear the search.</Empty>}
      <ol className="pros">
        {d?.items.map((it: any) => (
          <li key={it.organization.id} className="pro">
            <div className="pro-rank" aria-label={it.rank ? `Rank ${it.rank}` : "Signal"}>{it.rank ?? (it.priority || "·")}</div>
            <div className="pro-main">
              <div className="row spread" style={{ alignItems: "flex-start" }}>
                <div>
                  <div className="pro-name">{it.organization.name}</div>
                  <div className="small muted">{[it.organization.country, it.organization.segment, it.organization.employees ? `${it.organization.employees.toLocaleString()} staff` : null].filter(Boolean).join(" · ")}</div>
                </div>
                <div className="row">
                  {it.score !== null && <Badge kind={it.score >= 80 ? "ok" : "info"}>Sheet score {it.score}</Badge>}
                  {it.confidence && <Badge>{String(it.confidence).split("(")[0].trim()} confidence</Badge>}
                </div>
              </div>
              <p className="pro-signal">{it.signal}</p>
              <div className="small muted">{it.signal_type ? `${it.signal_type} · ` : ""}{it.signal_date ?? "date unknown"} · {age(it.age_days)}</div>
              {it.why && <p className="small" style={{ margin: "8px 0 0" }}><b>Possible engineering need (inference).</b> {it.why}</p>}
              {it.caveat && <p className="small" style={{ margin: "6px 0 0" }}><b>Caveat.</b> {it.caveat}</p>}
              <div className="row small" style={{ marginTop: 10 }}>
                {it.buyer_role && <span><b>Buyer</b> {it.buyer_name ? `${it.buyer_name}, ` : ""}{it.buyer_role}</span>}
                {it.service_fit && <span><b>Fit</b> {humanize(it.service_fit)}</span>}
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                {it.source_url ? <a className="pill" href={it.source_url} target="_blank" rel="noreferrer noopener">Open source ↗</a> : <Badge kind="warn">No source link</Badge>}
                <Link to={`/accounts/${it.organization.id}`} className="small">Account view</Link>
              </div>
            </div>
          </li>
        ))}
      </ol>
      {d && d.total > d.items.length && <p className="small muted">Showing {d.items.length} of {d.total}. Search to narrow the list.</p>}
    </section>
  );
}
