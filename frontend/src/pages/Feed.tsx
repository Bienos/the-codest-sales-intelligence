import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, trackSourceOpened } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const ICP_LABEL: Record<string, string> = { strategic_5000_plus: "5,000+ staff", enterprise_1000_plus: "1,000+ staff", scaleup_500_999: "500 to 999 staff", below_threshold: "Below size threshold", unknown: "Size unknown" };
const dayKey = (d: string | null) => (d ? new Date(d).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "Date unknown");
const LEAD: Record<string, [string, string]> = { queued: ["info", "Waiting to be checked"], fetching: ["info", "Reading the page"], extracting: ["info", "Extracting"], waiting_budget: ["warn", "Waiting for AI budget"],
  no_event: ["", "Checked: nothing quotable"], blocked: ["bad", "Page not allowed"], failed: ["bad", "Could not check"] };

export default function Feed() {
  const [q, setQ] = useState(""); const [play, setPlay] = useState(""); const [core, setCore] = useState(false); const [mine, setMine] = useState(true); const [days, setDays] = useState(60);
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const t = setTimeout(() => api(`/api/feed?q=${encodeURIComponent(q)}&days=${days}&play=${play}&market=${core ? "core" : ""}&accounts_only=${mine}`).then(setD).catch(setErr), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, play, core, days, mine]);
  const groups: [string, any[]][] = [];
  d?.items.forEach((it: any) => { const k = dayKey(it.happened); const g = groups.find((x) => x[0] === k); g ? g[1].push(it) : groups.push([k, [it]]); });
  const cov = d?.coverage;
  return (
    <>
      <div className="page-head"><div className="eyebrow">What was found</div><h2>Market feed</h2></div>
      <p className="small muted" style={{ marginTop: 0, maxWidth: "80ch" }}>{d?.note ?? "Verified findings only."} Each item shows the quote, where it came from, which signal it matches and which of our offers it could fit.</p>
      <div className="feedbar">
        <input aria-label="Search the feed" placeholder="Search company or keyword" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Sales play" value={play} onChange={(e) => setPlay(e.target.value)}><option value="">All plays</option>{d?.plays.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={14}>Last 2 weeks</option><option value={30}>Last 30 days</option><option value={60}>Last 60 days</option><option value={180}>Last 6 months</option></select>
        <label className="small row"><input type="checkbox" checked={core} onChange={(e) => setCore(e.target.checked)} /> Main markets only</label>
        <label className="small row"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only companies on your lists{d && d.totals.market_wide > 0 && mine ? ` (${d.totals.market_wide.toLocaleString()} market-wide items hidden)` : ""}</label>
      </div>
      <Err e={err} />
      {cov && !cov.all_monitored_healthy && <div className={`cov ${cov.no_active_monitoring ? "bad" : ""}`}><b>Coverage.</b> {cov.no_active_monitoring ? "No source is live yet, so this feed only shows what you verified yourself or what discovery found. A quiet feed says nothing about the market." : `${cov.unhealthy.length} source(s) are degraded: ${cov.unhealthy.map((u: any) => u.source).join(", ")}.`} <Link to="/admin/pipeline">See what is running</Link></div>}
      {d && d.count === 0 && <div className="empty"><b>No verified findings in this window</b>Verify a prospect from Today, activate sources in Administration, or widen the filters. {d.totals.unverified > 0 && `${d.totals.unverified} candidate event(s) exist but are not verified, so they are not shown.`}</div>}
      <div className="feed-grid">
        <div>
          {groups.map(([day, items]) => (
            <section key={day}><div className="feed-day">{day}</div>
              {items.map((it: any) => (
                <article key={it.id} className="fcard">
                  <div className="row spread" style={{ alignItems: "flex-start" }}>
                    <div className="row small">
                      <Badge kind="info">{humanize(it.type)}</Badge>
                      {it.organization && <><b>{it.organization.name}</b><span className="muted">{[it.organization.country, ICP_LABEL[it.organization.icp]].filter(Boolean).join(" · ")}</span>
                        {it.organization.market.fit !== "core" && <Badge kind="warn">{it.organization.market.label}</Badge>}{it.organization.in_master_list && <Badge kind="ok">Existing list</Badge>}</>}
                    </div>
                    {it.programme && <Link className="small" to="/today">Programme: {it.programme.title}</Link>}
                  </div>
                  <h3 className="fcard-t">{it.summary}</h3>
                  {it.evidence.map((e: any) => (
                    <blockquote key={e.claim_id} className="fquote">“{e.claim}”
                      <cite>{e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" onClick={() => it.organization && trackSourceOpened({ organization_id: it.organization.id, where: "evidence" })}>{e.source ?? "Original source"} ↗</a> : "Structured record"}
                        authority {e.authority ?? "n/a"} · published {fmtDate(e.published_at)} · quote {e.anchor_verified ? "found on the page" : "not matched"}</cite></blockquote>))}
                  <div className="small muted">Event {fmtDate(it.event_at)} · published {fmtDate(it.published)} · first seen {fmtDate(it.first_seen)}</div>
                  {it.signals.length > 0 && <div className="chips" style={{ marginTop: 10 }}>{it.signals.map((s: any) => <span key={s.rule_id} className="pill" title={s.name}>{s.rule_id} {s.name}</span>)}</div>}
                  {it.fit.plays.length > 0 ? (
                    <div className="fit"><span className="k">Why it matters for what we sell</span>
                      {it.fit.offers.length > 0 && <div className="chips">{it.fit.offers.map((o: string) => <Badge key={o} kind="ok">{o}</Badge>)}</div>}
                      {it.fit.plays.map((p: any) => <p key={p.play_id} className="small" style={{ margin: "6px 0 0" }}><b>{p.play}.</b> {p.why} {p.primary_buyer && <span className="muted">Usual buyer: {p.primary_buyer}.</span>}</p>)}
                      {it.questions.length > 0 && <><span className="k" style={{ marginTop: 8 }}>Ask next</span><ul className="tight small">{it.questions.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
                      <p className="small muted" style={{ margin: "6px 0 0" }}>{it.fit.label}</p>
                    </div>) : <p className="small muted" style={{ margin: "10px 0 0" }}>No signal matched this finding yet, so no offer is suggested.</p>}
                </article>))}
            </section>))}
        </div>
        <aside>
          <div className="card"><div className="eyebrow">Leads waiting</div>
            <p className="small muted" style={{ marginTop: 0 }}>Found by search. Not findings until the original page is read and quoted.</p>
            {d?.leads.length === 0 && <p className="small muted">None right now.</p>}
            <ul className="leads">{d?.leads.map((l: any, i: number) => <li key={i}><a href={l.url} target="_blank" rel="noreferrer noopener">{l.claimed_text || l.url.replace(/^https?:\/\//, "").slice(0, 50)} ↗</a>
              <div><Badge kind={LEAD[l.status]?.[0]}>{LEAD[l.status]?.[1] ?? l.status}</Badge></div></li>)}</ul></div>
        </aside>
      </div>
    </>
  );
}
