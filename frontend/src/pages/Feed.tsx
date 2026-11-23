import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, post, trackSourceOpened } from "../api";
import { Badge, Err, Skeleton, TIER, lab, reloadPage } from "../components/ui";
const SERVICE: Record<string, string> = { staff_augmentation: "Staff Augmentation", team_extension: "Team Extension", ai_services: "AI Services" };
const LANG: Record<string, string> = { de: "German", pl: "Polish", sv: "Swedish", no: "Norwegian", nb: "Norwegian", da: "Danish", fi: "Finnish", fr: "French", nl: "Dutch", es: "Spanish", it: "Italian", is: "Icelandic" };

const ICP_LABEL: Record<string, string> = { strategic_5000_plus: "5,000+ staff", enterprise_1000_plus: "1,000+ staff", scaleup_500_999: "500 to 999 staff", below_threshold: "Below size threshold", unknown: "Size unknown" };
const dayKey = (d: string | null) => (d ? new Date(d).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "Date unknown");
const LEAD: Record<string, [string, string]> = { queued: ["info", "Waiting to be checked"], fetching: ["info", "Reading the page"], extracting: ["info", "Finding events"], waiting_budget: ["warn", "Waiting for AI budget"],
  no_event: ["", "Checked: nothing we could quote"], blocked: ["bad", "Page not allowed"], failed: ["bad", "Could not check"] };

export default function Feed() {
  const [q, setQ] = useState(""); const [play, setPlay] = useState(""); const [core, setCore] = useState(false); const [view, setView] = useState("accounts"); const [days, setDays] = useState(60); const [fitOnly, setFitOnly] = useState(false);
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const t = setTimeout(() => api(`/api/feed?q=${encodeURIComponent(q)}&days=${days}&play=${play}&market=${core ? "core" : ""}&accounts_only=true&view=${view}&fit_only=${fitOnly}`).then(setD).catch(setErr), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, play, core, days, view, fitOnly]);
  const groups: [string, any[]][] = [];
  d?.items.forEach((it: any) => { const k = dayKey(it.happened); const g = groups.find((x) => x[0] === k); g ? g[1].push(it) : groups.push([k, [it]]); });
  const cov = d?.coverage;
  return (
    <>
      <div className="page-head"><div className="eyebrow">What we found</div><h1>Market feed</h1></div>
      <p className="small muted mt-0 measure">Everything here was checked against the original page. Each item shows the quote, where it came from and which of our services it could fit. Links we have not read yet wait on the right.</p>
      <div className="tabs" role="tablist" aria-label="What to show">
        {[["accounts", "Your accounts"], ["market", "New companies: main markets"], ["rest", "New companies: elsewhere"], ["public", "Public sector"]].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}>{l}{d?.view_counts ? <span className="tabn">{Number(d.view_counts[k] ?? 0).toLocaleString()}</span> : null}</button>))}
      </div>
      <p className="small muted sub-tight">{{ accounts: "Checked news about companies on your lists. These can become signals and opportunities.", market: "Checked news about companies not on your lists yet, in the UK, DACH, Nordics and Poland. Each could be added as an account.", rest: "The same for the rest of Europe and the US. A second priority.", public: "Government and public bodies. Kept apart because they buy through tenders." }[view]}</p>
      <div className="feedbar">
        <input aria-label="Search the feed" placeholder="Search company or keyword" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Sales play" value={play} onChange={(e) => setPlay(e.target.value)}><option value="">All plays</option>{d?.plays.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={14}>Last 2 weeks</option><option value={30}>Last 30 days</option><option value={60}>Last 60 days</option><option value={180}>Last 6 months</option></select>
        {view === "accounts" && <label className="small row"><input type="checkbox" checked={core} onChange={(e) => setCore(e.target.checked)} /> Main markets only</label>}
        <label className="small row"><input type="checkbox" checked={fitOnly} onChange={(e) => setFitOnly(e.target.checked)} /> <span title="Hides findings that none of our sales plays match to a service">Only likely work for us</span></label>
      </div>
      <Err e={err} onRetry={reloadPage} />
      {!d && !err && <Skeleton rows={3} label="Loading the feed" />}
      {cov && !cov.all_monitored_healthy && <div className={`cov ${cov.no_active_monitoring ? "bad" : ""}`}><b>Source health.</b> {cov.no_active_monitoring ? "No source is switched on yet, so this feed only shows what you checked yourself or what search found. A quiet feed says nothing about the market." : `${cov.unhealthy.length} ${cov.unhealthy.length === 1 ? "source is" : "sources are"} having problems: ${cov.unhealthy.map((u: any) => u.source).join(", ")}.`} <Link to="/admin/pipeline">See what is running</Link></div>}
      {d && d.count === 0 && <div className="empty"><b>Nothing checked in this window</b>Check a company from Today, switch sources on in Administration, or widen the filters. {d.totals.unverified > 0 && `${d.totals.unverified} ${d.totals.unverified === 1 ? "item is" : "items are"} not checked yet, so ${d.totals.unverified === 1 ? "it is" : "they are"} not shown.`}</div>}
      <div className="feed-grid">
        <div>
          {groups.map(([day, items]) => (
            <section key={day}><div className="feed-day">{day}</div>
              {items.map((it: any) => (
                <article key={it.id} className="fcard">
                  <div className="row spread start">
                    <div className="row small">
                      <Badge kind="info">{lab("event", it.type)}</Badge>
                      {!it.organization && it.scope && <><b>{it.scope.buyer ?? "Company not named"}</b><span className="muted">{[it.scope.country, it.scope.lane === "public" ? "public body" : null].filter(Boolean).join(" · ")}</span><Badge kind="info">New company</Badge></>}
                      {it.organization && <><b>{it.organization.name}</b><span className="muted">{[it.organization.country, ICP_LABEL[it.organization.icp]].filter(Boolean).join(" · ")}</span>
                        {it.organization.market.fit !== "core" && <Badge kind="warn">{it.organization.market.label}</Badge>}{it.organization.in_master_list && <Badge kind="ok">On your lists</Badge>}</>}
                    </div>
                    {it.programme && <Link className="small" to="/today">Programme: {it.programme.title}</Link>}
                  </div>
                  <h2 className="fcard-t">{it.summary_en ?? it.summary}</h2>
                  {it.summary_en && <p className="small muted sec-note">Original wording: {it.summary}</p>}
                  {it.why && <div className={`why ${it.why.level}`}>
                    <span className="k">{it.why.level === "strong" ? "Likely work for The Codest" : it.why.level === "possible" ? "Could become work for The Codest" : "Probably not work for us"}</span>
                    {it.why.service && it.why.service !== "none" && <Badge kind={it.why.level === "strong" ? "ok" : "info"}>{SERVICE[it.why.service] ?? it.why.service}</Badge>}
                    <p className="small mt-1 mb-0">{it.why.reason}</p>
                  </div>}
                  {it.evidence.map((e: any) => (
                    <blockquote key={e.claim_id} className="fquote">“{e.claim}”{e.translation && <span className="trans">In English{it.language && it.language !== "en" ? ` (from ${LANG[it.language] ?? it.language})` : ""}: “{e.translation}”</span>}
                      <cite>{e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" onClick={() => it.organization && trackSourceOpened({ organization_id: it.organization.id, where: "evidence" })}>{e.source ?? "Original source"} ↗</a> : "Structured record"}
                        {[e.authority ? TIER[e.authority] ?? e.authority : null, e.anchor_verified ? "quote found on the page" : "quote not matched"].filter(Boolean).join(" · ")}</cite></blockquote>))}
                  <div className="small muted">Happened {fmtDate(it.event_at)} · we saw it {fmtDate(it.first_seen)}</div>
                  {it.signals.length > 0 && <div className="chips mt-3">{it.signals.map((s: any) => <span key={s.rule_id} className="pill" title={`Signal ${s.rule_id}`}>{s.name}</span>)}</div>}
                  {it.fit.plays.length > 0 ? (
                    <div className="fit"><span className="k">Why it matters for what we sell</span>
                      {it.fit.offers.length > 0 && <div className="chips">{it.fit.offers.map((o: string) => <Badge key={o} kind="ok">{o}</Badge>)}</div>}
                      {it.fit.plays.map((p: any) => <p key={p.play_id} className="small mt-2 mb-0"><b>{p.play}.</b> {p.why} {p.primary_buyer && <span className="muted">Usual buyer: {p.primary_buyer}.</span>}</p>)}
                      {it.questions.length > 0 && <><span className="k mt-2">Ask them</span><ul className="tight small">{it.questions.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
                      <p className="small muted mt-2 mb-0">{it.fit.label}</p>
                    </div>) : <p className="small muted mt-3 mb-0">No signal matches this finding yet, so we are not suggesting a service.</p>}
                </article>))}
            </section>))}
        </div>
        <aside>
          <ReportMiss />
          <div className="card"><div className="eyebrow">Links to check</div>
            <p className="small muted mt-0">Found by search. We will read each page and only list it here if it holds up.</p>
            {d?.leads.length === 0 && <p className="small muted">None waiting.</p>}
            <ul className="leads">{d?.leads.map((l: any, i: number) => <li key={i}><a href={l.url} target="_blank" rel="noreferrer noopener">{l.claimed_text || l.url.replace(/^https?:\/\//, "").slice(0, 50)} ↗</a>
              <div><Badge kind={LEAD[l.status]?.[0]}>{LEAD[l.status]?.[1] ?? l.status}</Badge></div></li>)}</ul></div>
        </aside>
      </div>
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
      <label className="small" htmlFor="miss-url">Link to the page</label>
      <input id="miss-url" type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
      <label className="small" htmlFor="miss-note">What is it? (optional)</label>
      <input id="miss-note" maxLength={400} placeholder="e.g. Bank X chose a new payments vendor" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn mt-2" disabled={busy || !/^https?:\/\//i.test(url.trim())} onClick={send}>{busy ? "Sending…" : "Report it"}</button>
      {res && <p className="small mb-0" role="status">{res.already_known ? `We already had this page (from ${res.source_id}). If it is not in the feed, it did not pass our checks.` : res.duplicate ? "Someone already reported this. It is being checked." : "Thanks. It is queued and will be read shortly."}</p>}
      {err ? <Err e={err} /> : null}</div>
  );
}
