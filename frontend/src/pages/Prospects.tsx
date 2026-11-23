import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post, trackSourceOpened } from "../api";
import { Badge, Empty, Err, humanize } from "../components/ui";

type Lane = "shortlist" | "signals";

const VSTATE: Record<string, [string, string]> = {
  queued: ["info", "Waiting to be checked"], fetching: ["info", "Reading the page"], extracting: ["info", "Finding events"], verified: ["ok", "Checked"],
  no_event: ["", "Nothing we could quote"], failed: ["bad", "Could not check"], blocked: ["bad", "Page not allowed"], waiting_budget: ["warn", "Waiting for AI budget"] };

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
  const [msg, setMsg] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => api(`/api/prospects?lane=${lane}&q=${encodeURIComponent(q)}&limit=30`).then(setD).catch(setErr), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [lane, q, tick]);
  const busy = d?.items.some((i: any) => ["queued", "fetching", "extracting"].includes(i.verification?.status));
  useEffect(() => { if (!busy) return; const t = setInterval(() => setTick((n) => n + 1), 6000); return () => clearInterval(t); }, [busy]);
  const verify = async (id: string) => { setErr(null); try { await post("/api/prospects/verify", { organization_id: id }); setTick((n) => n + 1); } catch (e) { setErr(e); } };
  const batch = async () => { setErr(null); try { const r = await post("/api/prospects/verify-batch", { lane, limit: 10 }); setMsg(`${r.queued} ${r.queued === 1 ? "page" : "pages"} queued. Each check reads one page and uses some of the monthly AI budget.`); setTick((n) => n + 1); } catch (e) { setErr(e); } };
  return (
    <section aria-label="From your workbooks">
      <div className="section-title">From your workbooks</div>
      <div className="callout">
        <b>From your workbooks. Not checked yet.</b> Open the source and confirm it before you act, or press “Check this source” and we will read the page for you.
      </div>
      <div className="row spread" style={{ margin: "14px 0" }}>
        <div className="tabs" style={{ margin: 0, border: 0 }} role="tablist">
          <button role="tab" aria-selected={lane === "shortlist"} onClick={() => setLane("shortlist")}>Your shortlist</button>
          <button role="tab" aria-selected={lane === "signals"} onClick={() => setLane("signals")}>Recent signals</button>
        </div>
        <div className="row"><input aria-label="Search accounts" placeholder="Search accounts" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
          <button onClick={batch} title="Reads the first 10 unchecked items in this list and keeps only what it can quote from the page">Check the top 10</button></div>
      </div>
      {msg && <div className="okmsg" role="status" style={{ marginBottom: 12 }}>{msg}</div>}
      <Err e={err} />
      {!d && !err && <p className="muted">Loading your workbook list…</p>}
      {d && d.items.length === 0 && <Empty title="Nothing matches">Try the other list, or clear the search box.</Empty>}
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
                  {it.score !== null && <Badge kind={it.score >= 80 ? "ok" : "info"}><span title="The score column from your workbook. It is not calculated here.">Workbook score {it.score}/100</span></Badge>}
                  {it.confidence && <Badge>Workbook confidence: {String(it.confidence).split("(")[0].trim()}</Badge>}
                  {it.market && it.market.fit !== "core" && <Badge kind={it.market.fit === "backup" ? "warn" : ""}>{it.market.label}</Badge>}
                  {it.vendor_flag && <Badge kind="warn">Looks like a software vendor: approach as a partner</Badge>}
                </div>
              </div>
              <p className="pro-signal">{it.signal}</p>
              <div className="small muted">{it.signal_type ? `${it.signal_type} · ` : ""}{it.signal_date ?? "Date not given"} · {age(it.age_days)}</div>
              {it.why && <p className="small" style={{ margin: "8px 0 0" }}><b>Possible engineering need (our guess).</b> {it.why}</p>}
              {it.caveat && <p className="small" style={{ margin: "6px 0 0" }}><b>Be aware:</b> {it.caveat}</p>}
              <div className="row small" style={{ marginTop: 10 }}>
                {it.buyer_role && <span><b>Likely buyer</b> {it.buyer_name ? `${it.buyer_name}, ` : ""}{it.buyer_role}</span>}
                {it.service_fit && <span><b>Our offer</b> {({ staff_augmentation: "Staff augmentation", team_extension: "Team extension", ai_services: "AI services" } as Record<string, string>)[it.service_fit] ?? humanize(it.service_fit)}</span>}
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                {it.source_url ? <a className="pill" href={it.source_url} target="_blank" rel="noreferrer noopener" onClick={() => trackSourceOpened({ organization_id: it.organization.id, where: "prospect" })}>Open source ↗</a> : <Badge kind="warn">No source link: find one first</Badge>}
                {it.source_url && !it.verification && <button className="primary" onClick={() => verify(it.organization.id)}>Check this source</button>}
                {it.verification && <><Badge kind={VSTATE[it.verification.status]?.[0]}>{VSTATE[it.verification.status]?.[1] ?? it.verification.status}</Badge>{it.verification.status === "verified" && <span className="small">{it.verification.events} {it.verification.events === 1 ? "finding" : "findings"} added</span>}{it.verification.reason && <span className="small muted">{it.verification.reason}</span>}</>}
                <Link to={`/accounts/${it.organization.id}`} className="small">Open account</Link>
              </div>
            </div>
          </li>
        ))}
      </ol>
      {d && d.total > d.items.length && <p className="small muted">Showing {d.items.length} of {d.total}. Search to narrow it down.</p>}
    </section>
  );
}
