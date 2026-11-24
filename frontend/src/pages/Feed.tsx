import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, post, trackSourceOpened } from "../api";
import { Badge, Err, Skeleton, TIER, lab } from "../components/ui";
import { demandLabel } from "../components/BuyingSignal";
import { countryName, staffCount } from "./targetMarket";
const SERVICE: Record<string, string> = { staff_augmentation: "Staff Augmentation", team_extension: "Team Extension", ai_services: "AI Services" };
const LANG: Record<string, string> = { de: "German", pl: "Polish", sv: "Swedish", no: "Norwegian", nb: "Norwegian", da: "Danish", fi: "Finnish", fr: "French", nl: "Dutch", es: "Spanish", it: "Italian", is: "Icelandic" };

const dayKey = (d: string | null) => (d ? new Date(d).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "Date unknown");
const LEAD: Record<string, [string, string]> = { queued: ["info", "Waiting to be checked"], fetching: ["info", "Reading the page"], extracting: ["info", "Finding events"], waiting_budget: ["warn", "Waiting for AI budget"],
  no_event: ["", "Checked: nothing we could quote"], blocked: ["bad", "Page not allowed"], failed: ["bad", "Could not check"] };

export default function Feed() {
  const [q, setQ] = useState(""); const [play, setPlay] = useState(""); const [days, setDays] = useState(60); const [fitOnly, setFitOnly] = useState(false);
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const [visible, setVisible] = useState(30);
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setD(null); setVisible(30);
    const t = setTimeout(() => api(`/api/feed?q=${encodeURIComponent(q)}&days=${days}&play=${play}&target=true&fit_only=${fitOnly}`, { signal: request.signal }).then(setD).catch((error) => { if (!request.signal.aborted) setErr(error); }), q ? 250 : 0);
    return () => { clearTimeout(t); request.abort(); };
  }, [q, play, days, fitOnly, retry]);
  const groups: [string, any[]][] = [];
  d?.items.slice(0, visible).forEach((it: any) => { const k = dayKey(it.happened); const g = groups.find((x) => x[0] === k); g ? g[1].push(it) : groups.push([k, [it]]); });
  const cov = d?.coverage;
  return (
    <>
      <div className="page-head"><div className="eyebrow">What we found</div><h1>Market feed</h1></div>
      <p className="small muted mt-0 measure">Checked news about your target companies. Open a source to see the original report.</p>
      <details className="feed-filters"><summary>Filter findings</summary>
      <div className="feedbar mt-2">
        <input aria-label="Search the feed" placeholder="Search company or keyword" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Sales play" value={play} onChange={(e) => setPlay(e.target.value)}><option value="">All plays</option>{d?.plays.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={14}>Last 2 weeks</option><option value={30}>Last 30 days</option><option value={60}>Last 60 days</option><option value={180}>Last 6 months</option></select>
        <label className="small row"><input type="checkbox" checked={fitOnly} onChange={(e) => setFitOnly(e.target.checked)} /> <span>Only likely work for us</span></label>
      </div>
      <p className="small muted mt-0">“Only likely work for us” hides findings our sales plays do not match to a service.</p>
      </details>
      <Err e={err} onRetry={() => setRetry((n) => n + 1)} />
      {!d && !err && <Skeleton rows={3} label="Loading the feed" />}
      {cov && !cov.all_monitored_healthy && <div className={`cov ${cov.no_active_monitoring ? "bad" : ""}`}><b>Source health.</b> {cov.no_active_monitoring ? "No source is switched on yet, so this feed only shows what you checked yourself or what search found. A quiet feed says nothing about the market." : `${cov.unhealthy.length} ${cov.unhealthy.length === 1 ? "source is" : "sources are"} having problems: ${cov.unhealthy.map((u: any) => u.source).join(", ")}.`} <Link to="/admin/pipeline">See what is running</Link></div>}
      {d && d.count === 0 && <div className="empty"><b>No matching findings</b>Try a longer date range or check a company for news from <Link to="/accounts">Accounts</Link>.</div>}
      {(d || err) && <div className="feed-grid">
        <div>
          {groups.map(([day, items]) => (
            <section key={day}><div className="feed-day">{day}</div>
              {items.map((it: any) => {
                const signal = [...it.signals].sort((a, b) => a.demand_tier.localeCompare(b.demand_tier) || b.strength - a.strength)[0];
                const hasEvidence = it.evidence.some((e: any) => e.anchor_verified && e.url);
                return (
                <article key={it.id} className="fcard">
                  <div className="row spread start">
                    <div className="row small">
                      <Badge kind="info">{lab("event", it.type)}</Badge>
                      {it.organization && <>
                        <Link to={`/accounts/${it.organization.id}`}><b>{it.organization.name}</b></Link>
                        <span className="muted">{[countryName(it.organization.country), staffCount(it.organization.employees)].filter(Boolean).join(" · ")}</span>
                      </>}
                    </div>
                    {it.programme && (it.organization ? <Link className="small" to={`/accounts/${it.organization.id}?event=${it.id}`}>Programme: {it.programme.title}</Link> : <span className="small">Programme: {it.programme.title}</span>)}
                  </div>
                  <h2 className="fcard-t">{it.summary_en ?? it.summary}</h2>
                  {it.summary_en && !it.evidence.some((e: any) => e.claim === it.summary) && <p className="small muted sec-note">Original wording: {it.summary}</p>}
                  {signal && hasEvidence ? <p className="buying-signal small mt-2 mb-2"><b>{signal.name}</b><br />{demandLabel(signal.demand_tier)}</p>
                    : <p className="small muted mt-2 mb-2">{it.scope.linked ? "No current source-backed buying signal." : "Buyer to confirm"}</p>}
                  {it.contact_hint && <p className="small">
                    <b>Who to look for</b> {it.contact_hint.role}. {it.contact_hint.question}
                  </p>}
                  {it.why && <div className={`why ${it.why.level}`}>
                    <span className="k">{it.why.level === "none" ? "Low likely fit" : "Suggested fit · qualify before acting"}</span>
                    {it.why.service && it.why.service !== "none" && <Badge kind={it.why.level === "strong" ? "ok" : "info"}>{SERVICE[it.why.service] ?? it.why.service}</Badge>}
                    <p className="small mt-1 mb-0">{it.why.reason}</p>
                  </div>}
                  {it.evidence.map((e: any) => (
                    <blockquote key={e.claim_id} className="fquote">{e.claim !== (it.summary_en ?? it.summary) && <>“{e.claim}”</>}{e.translation && <span className="trans">In English{it.language && it.language !== "en" ? ` (from ${LANG[it.language] ?? it.language})` : ""}: “{e.translation}”</span>}
                      <cite>{e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" onClick={() => it.organization && trackSourceOpened({ organization_id: it.organization.id, where: "evidence" })}>{e.source ?? "Original source"} ↗</a> : "Structured record"}
                        {[e.authority ? TIER[e.authority] ?? e.authority : null, e.anchor_verified ? "quote found in collected text" : "quote not matched"].filter(Boolean).join(" · ")}</cite></blockquote>))}
                  {it.story_sources?.length > 1 && <details className="finding-details">
                    <summary>Source coverage ({it.story_sources.length})</summary>
                    <ul className="tight small">{it.story_sources.map((source: any, i: number) => {
                      const repeated = source.origin_group && it.story_sources.slice(0, i).some((other: any) => other.origin_group === source.origin_group);
                      return <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer noopener">{source.source}</a>
                        {source.published_at && <> · {fmtDate(source.published_at)}</>}
                        {repeated && <> · repeated coverage</>}</li>;
                    })}</ul>
                  </details>}
                  <details className="finding-details"><summary>Dates, signals and sales play</summary>
                  <div className="small muted mt-1">{[it.event_at && `Happened ${fmtDate(it.event_at)}`, it.published && `Published ${fmtDate(it.published)}`, it.first_seen && `We saw it ${fmtDate(it.first_seen)}`].filter(Boolean).join(" · ")}</div>
                  {it.signals.length > 0 && <div className="chips mt-3">{it.signals.map((s: any) => <span key={s.rule_id} className="pill" title={`Signal ${s.rule_id}`}>{s.name}</span>)}</div>}
                  {it.fit.plays.length > 0 ? (
                    <div className="fit"><span className="k">Why it matters for what we sell</span>
                      {it.fit.offers.length > 0 && <div className="chips">{it.fit.offers.map((o: string) => <Badge key={o} kind="ok">{o}</Badge>)}</div>}
                      {it.fit.plays.map((p: any) => <p key={p.play_id} className="small mt-2 mb-0"><b>{p.play}.</b> {p.why} {p.primary_buyer && <span className="muted">Usual buyer: {p.primary_buyer}.</span>}</p>)}
                      {it.questions.length > 0 && <><span className="k mt-2">Ask them</span><ul className="tight small">{it.questions.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
                      <p className="small muted mt-2 mb-0">{it.fit.label}</p>
                    </div>) : <p className="small muted mt-3 mb-0">No signal matches this finding yet, so we are not suggesting a service.</p>}
                  </details>
                </article>); })}
            </section>))}
          {d && visible < d.items.length && <button onClick={() => setVisible((n) => n + 30)}>Show more findings</button>}
        </div>
        <aside>
          <ReportMiss />
          <div className="card" id="links-to-check"><div className="eyebrow">Links to check</div>
            <p className="small muted mt-0">Found by search. A page appears in the feed after we check the original source.</p>
            {d?.leads.length === 0 && <p className="small muted">None waiting.</p>}
            <ul className="leads">{d?.leads.map((l: any, i: number) => <li key={i}><a href={l.url} target="_blank" rel="noreferrer noopener">{l.claimed_text || l.url.replace(/^https?:\/\//, "").slice(0, 50)} ↗</a>
              <div><Badge kind={LEAD[l.status]?.[0]}>{LEAD[l.status]?.[1] ?? l.status}</Badge></div></li>)}</ul></div>
        </aside>
      </div>}
    </>
  );
}


function ReportMiss() {
  const [url, setUrl] = useState(""); const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [res, setRes] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const send = async () => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await post("/api/misses", { url: url.trim(), note: note.trim() })); setUrl(""); setNote(""); } catch (e) { setErr(e); }
    setBusy(false);
  };
  return (
    <div className="card mb-4"><div className="eyebrow">Saw something we missed?</div>
      <p className="small muted mt-0">Paste the link. We will tell you if we already had it, and read it if not. It also shows us which source to improve.</p>
      <div className="report-fields"><label className="small" htmlFor="miss-url">Link to the page</label>
      <input id="miss-url" type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
      <label className="small" htmlFor="miss-note">What is it? (optional)</label>
      <input id="miss-note" maxLength={400} placeholder="e.g. Bank X chose a new payments vendor" value={note} onChange={(e) => setNote(e.target.value)} /></div>
      <button className="primary mt-2" disabled={busy || !/^https?:\/\//i.test(url.trim())} onClick={send}>{busy ? "Sending…" : "Report it"}</button>
      {res && <p className="small mb-0" role="status">{res.already_known ? `We already had this page (from ${res.source_id}). If it is not in the feed, it did not pass our checks.` : res.duplicate ? "Someone already reported this. It is being checked." : "Thanks. It is queued and will be read shortly."}</p>}
      {err ? <Err e={err} /> : null}</div>
  );
}
