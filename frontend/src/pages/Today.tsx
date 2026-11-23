import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { actionReason, Badge, EVIDENCE_CONFIDENCE, Coverage, Err, Evidence, Fact, accessKind, demandKind, humanize, lab, priorityKind, unknownText, verdict, Skeleton, whyShort } from "../components/ui";
import Prospects from "./Prospects";
import { SignalSummary } from "../components/BuyingSignal";

const SECTIONS: [string, string][] = [["critical", "Urgent"], ["existing_accounts", "Your accounts"], ["new_discoveries", "New companies worth a look"],
  ["relationships", "People to get back in touch with"], ["deal_blockers", "Deals that are stuck"], ["research_review", "Research to review"], ["public_tenders", "Public tenders"]];
const REASONS = ["not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"];
const TENDER_NOTE = "Public bodies buy through formal tenders, so these follow their own rules and deadlines.";

function Deadlines() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api("/api/deadlines").then((r) => setItems(r.items.filter((x: any) => x.days_left <= 240).slice(0, 6))).catch(() => setItems([])); }, []);
  if (!items.length) return null;
  return <section><div className="section-title">Regulation deadlines</div><p className="small muted sec-note">Each date was checked against an official page and approved by a second person. Buyers facing a hard deadline tend to need extra delivery capacity.</p>
    <div className="card flush table-wrap" tabIndex={0} role="region" aria-label="Deadline · Regulation · Who it applies to"><table className="cards"><thead><tr><th>Deadline</th><th>Regulation</th><th>Who it applies to</th><th>Date</th></tr></thead><tbody>
      {items.map((x) => <tr key={x.id}><td><a href={x.source_url} target="_blank" rel="noreferrer noopener">{x.title}</a></td><td data-label="Regulation" className="small">{x.regime}</td><td data-label="Who it applies to" className="small">{x.applies_to ?? "Not specified"}</td>
        <td data-label="Date"><b>{fmtDate(x.deadline_date)}</b> <span className="small muted">in {x.days_left} days</span></td></tr>)}
    </tbody></table></div></section>;
}

function Card({ c, reload, compact = false }: { c: any; reload: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const decide = async (decision: string, r?: string) => {
    setBusy(true); setErr(null);
    try { await post(`/api/hypotheses/${c.hypothesis_id}/decision`, { decision, reason: r }); reload(); }
    catch (e) { setErr(e); }
    finally { setBusy(false); }
  };
  const a = c.next_action;
  return (
    <article className={`opp ${c.priority}${compact ? " compact" : ""}`} aria-label={`${c.account.name}: ${c.initiative.title}, ${lab("route", c.route.type)}`}>
      <div className="opp-head">
        <div>
          {compact ? <div className="opp-title">{lab("route", c.route.type)} <span className="small muted">({c.route.counterparty})</span></div> : <>
            <div className="opp-title">{c.account.name}</div>
            <div className="opp-init">{c.initiative.title}
              {c.initiative.working_title_label && <Badge kind="warn">{c.initiative.working_title_label}</Badge>}<Badge>{humanize(c.initiative.stage)}</Badge></div></>}
        </div>
        <div className="row start">
          <Badge kind={priorityKind(c.priority)}>{lab("priority", c.priority)}</Badge>
          <Badge kind={c.account.in_master_list ? "ok" : "info"}>{c.account.in_master_list ? "On your lists" : "New company"}</Badge>
          {!c.account.ownership_verified && <Badge kind="warn">No owner in Pipedrive</Badge>}
        </div>
      </div>
      <div className="opp-grid">
        <div className="opp-body">
          {(() => { const v = verdict(c); return <div className={`verdict ${v.kind}`}><span className="k">Our read</span>{v.text}</div>; })()}
          {c.tender && <div className="tender"><Badge kind="bad">Tender</Badge> Closes <b>{fmtDateTime(c.tender.deadline_at)}</b> ({c.tender.deadline_tz ?? "timezone not given"}). We last checked it was still open on {fmtDateTime(c.tender.last_status_check_at)}.</div>}
          {!compact && <p><span className="k">What happened</span>{c.what_happened.summary ?? "No summary"}
            <span className="prov"> Happened {fmtDate(c.what_happened.event_date)} · we saw it {fmtDate(c.what_happened.first_observed)}</span></p>}
          {c.buying_signal && <SignalSummary signal={c.buying_signal} />}
          {c.plain_reason && <p><span className="k">In short</span>{c.plain_reason}</p>}
          {!compact && <p><span className="k">Why it matters</span>{whyShort(c.why_it_matters)}</p>}
          <div className="facts">
            <Fact label="Will they use outside help?" value={lab("demand", c.external_demand_status)} kind={demandKind(c.external_demand_status)} />
            <Fact label="How we would get in" value={`${lab("route", c.route.type)} (${c.route.counterparty}): ${lab("access", c.route.access_status).toLowerCase()}`} kind={accessKind(c.route.access_status)} />
            <Fact label="Who buys" value={lab("buyer", c.buyer_status)} kind={c.buyer_status === "none" ? "warn" : "info"} />
            <Fact label="How well sourced" value={<span title={EVIDENCE_CONFIDENCE}>{humanize(c.evidence_confidence)}</span>} kind={c.evidence_confidence === "high" ? "ok" : c.evidence_confidence === "low" ? "warn" : "info"} />
          </div>
          {c.reopened_after_dismissal && <p className="small"><Badge kind="info">Back again</Badge> You dismissed this as “{lab("dismiss", c.reopened_after_dismissal.previous_reason)}”. {c.reopened_after_dismissal.note}.</p>}
          {c.changed && Object.keys(c.changed).length > 0 && <p className="small muted">Changed since you last looked: {Object.entries(c.changed).map(([k, v]: any) => `${k} ${v[0] ?? "none"} to ${v[1]}`).join(" · ")}</p>}
          {(c.counterevidence?.length > 0 || c.unknowns?.length > 0) && (
            <div className="before-act">
              <div><span className="k">Points against it</span>{c.counterevidence?.length ? <ul>{c.counterevidence.map((x: any, i: number) => <li key={i}>{x.detail}</li>)}</ul> : <span className="small muted">None found</span>}</div>
              <div><span className="k">Not known yet</span><ul>{c.unknowns.slice(0, 4).map((u: string, i: number) => <li key={i}>{unknownText(u)}</li>)}</ul></div>
            </div>)}
          {open && <div className="mt-4"><span className="k label-caps">Original sources</span><Evidence items={c.evidence} hypothesisId={c.hypothesis_id} /></div>}
        </div>
        <aside className="opp-side" aria-label={`Next action: ${c.initiative.title}, ${lab("route", c.route.type)}`}>
          <div className="eyebrow m-0">Next action</div>
          {a ? <>
            <div className="atype">{lab("action", a.action_type)}</div>
            <dl><dt>Goal</dt><dd>{a.objective}</dd>{a.target_role && <><dt>Who to look for</dt><dd>{a.target_role}</dd></>}<dt>Owner</dt><dd>{a.owner_name ?? (a.owner_id ? "Assigned in Pipedrive" : "Unassigned")}</dd><dt>Due</dt><dd>{a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</dd></dl>
            <div className="why"><b>Why:</b> {actionReason(a)}</div>
            {a.approval_required && <div className="small muted">A colleague must approve before anything is sent or changed.</div>}
          </> : <span className="muted small">Nothing to do right now.</span>}
          <div className="cta"><Link className="btn primary" to={`/opportunities/${c.hypothesis_id}`}>Open opportunity</Link></div>
        </aside>
      </div>
      <div className="opp-foot">
        <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide sources" : "Show sources"}</button>
        <button disabled={busy} onClick={() => decide("accepted")}>Pursue this</button>
        <button disabled={busy} onClick={() => decide("monitor")}>Keep watching</button>
        <details className="dismiss-menu"><summary>Dismiss…</summary>
          <div>{REASONS.map((r) => <button key={r} disabled={busy} onClick={() => decide("dismissed", r)}>{lab("dismiss", r)}</button>)}</div>
        </details>
        <span className="ml-auto"><Link to={`/accounts/${c.account.id}`} className="small">Open account</Link></span>
      </div>
      <div className="opp-pad"><Err e={err} /></div>
    </article>
  );
}

/** Several opportunities on the same programme (one per route in) are shown as one group so the same news is not repeated. */
function groups(cards: any[]): any[][] {
  const out: any[][] = [];
  cards.forEach((c) => { const g = out.find((x) => x[0].initiative.id === c.initiative.id && x[0].account.id === c.account.id); g ? g.push(c) : out.push([c]); });
  return out;
}
function Group({ g, reload }: { g: any[]; reload: () => void }) {
  if (g.length === 1) return <Card c={g[0]} reload={reload} />;
  const c = g[0];
  return (
    <div className="progr">
      <div className="progr-head"><div className="opp-title">{c.account.name}</div>
        <div className="opp-init">{c.initiative.title}<Badge>{humanize(c.initiative.stage)}</Badge><Badge kind="info">{g.length} ways in</Badge></div>
        <p className="mt-2 mb-0"><b>What happened:</b> {c.what_happened.summary ?? "No summary"} <span className="prov">Happened {fmtDate(c.what_happened.event_date)}</span></p>
        <p className="small muted mt-1 mb-0">{whyShort(c.why_it_matters)}. Each way in below has its own next step.</p></div>
      {g.map((x) => <Card key={x.hypothesis_id} c={x} reload={reload} compact />)}
    </div>
  );
}

export default function Today() {
  const [d, setD] = useState<any>(null);
  const [focus, setFocus] = useState("finance");
  const [ov, setOv] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const load = () => setRevision((n) => n + 1);
  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    api(`/api/today?focus=${focus}`, { signal: request.signal })
      .then(setD).catch((error) => { if (!request.signal.aborted) setErr(error); });
    return () => request.abort();
  }, [focus, revision]);
  useEffect(() => { api("/api/overview").then(setOv).catch(() => undefined); }, []);
  const picker = <label className="row small mb-3">Sector focus
    <select value={focus} onChange={(e) => { setD(null); setErr(null); setFocus(e.target.value); }}>
      <option value="finance">Banking &amp; financial services</option>
      <option value="other">Other sectors and unclassified</option>
      <option value="public">Public tenders</option>
    </select>
  </label>;
  const all: any[] = d ? Object.values(d.sections).flat() : [];
  const urgent = all.filter(c => c.priority === "urgent").length;
  return (
    <>
      <header className="hero today-hero">
        <div className="eyebrow">{new Date(d?.generated_at ?? Date.now()).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
        <h1>Today</h1>
        <p className="sub">{err ? "Today could not load." : !d ? "Loading your opportunities." : d.count === 0 ? "No new opportunities in this view." : `${d.count} ${d.count === 1 ? "thing" : "things"} to review. Anything you dismiss stays hidden unless it changes.`}</p>
        <div className="stats">
          <div className="stat urgent"><div className="n">{!err && d ? urgent : "—"}</div><div className="l">Urgent</div></div>
          <div className="stat"><div className="n">{!err && d ? d.count : "—"}</div><div className="l">To review</div></div>
        </div>
      </header>
      <div className="page pt-5">
        {picker}
        {err ? <div className="first-results"><Err e={err} onRetry={load} /></div> : !d ? <div className="first-results"><Skeleton rows={4} label="Loading Today" /></div> : <>
        {d.count > 0 && <Coverage c={d.coverage} empty={d.empty_state} />}
        {d.count === 0 && <Setup ov={ov} reason={d.empty_state} />}
        {SECTIONS.map(([k, label]) => (d.sections[k]?.length ? <section key={k}><div className="section-title">{label}</div>{k === "public_tenders" && <p className="small muted sec-note">{TENDER_NOTE}</p>}{groups(d.sections[k]).map((g) => <Group key={g[0].hypothesis_id} g={g} reload={load} />)}</section> : null))}
        {d.hot_accounts?.length > 0 && <section><div className="section-title">Hot accounts</div><p className="small muted sec-note">Accounts with the strongest recent signals. Score adds up checked signals; different kinds count for more than repeats. It is not a probability.</p>
          <div className="card flush table-wrap" tabIndex={0} role="region" aria-label="Account · Signals · Latest"><table className="cards"><thead><tr><th>Account</th><th title="Adds up recent checked signals. Different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability.">Signal score</th><th>Buying signal and source</th><th>Latest</th></tr></thead><tbody>
            {d.hot_accounts.map((a: any) => <tr key={a.id}><td><Link to={`/accounts/${a.id}`}>{a.display_name}</Link><div className="small muted">{[a.country_code, a.segment].filter(Boolean).join(" · ")}</div></td>
              <td data-label="Signal score"><b>{a.score}</b> <Badge kind={a.band === "hot" ? "bad" : "warn"}>{a.band === "hot" ? "Hot" : "Warm"}</Badge></td>
              <td data-label="Buying signal and source"><SignalSummary signal={a.buying_signal} /></td><td className="small" data-label="Latest">{a.last_signal_at ? fmtDate(a.last_signal_at) : "None yet"}</td></tr>)}
          </tbody></table></div></section>}
        {focus === "public" && <Deadlines />}
        {focus !== "public" && <Prospects key={focus} focus={focus} />}
        </>}
      </div>
    </>
  );
}

function Setup({ ov, reason }: { ov: any; reason?: string | null }) {
  const steps = [
    { done: !!ov?.last_import, t: "Workbooks loaded", d: ov?.last_import ? `${ov.accounts.toLocaleString()} accounts and ${ov.contacts.toLocaleString()} contacts loaded.` : "Load both workbooks in Administration, Imports." },
    { done: (ov?.sources_live ?? 0) > 0, t: "Sources switched on and checking for news", d: ov ? `${ov.sources_live} of ${ov.sources_total} sources are switched on. The rest still need an address or a test.` : "" },
    { done: (ov?.verified_opportunities ?? 0) > 0, t: "First opportunity", d: "Appears here once a source finds something we can check against the original page." },
  ];
  return (
    <div className="card setup">
      <div className="eyebrow">Why Today is quiet</div>
      <p className="mt-0 mb-4">{reason ?? "Nothing has been checked against a source yet."} Until sources are switched on, a quiet screen says nothing about your market.</p>
      <ol className="steps">{steps.map((s) => <li key={s.t} className={s.done ? "done" : ""}><span className="setup-tick" aria-hidden="true">{s.done ? "✓" : "·"}</span><div><b>{s.t}</b><div className="small muted">{s.d}</div></div></li>)}</ol>
    </div>
  );
}
