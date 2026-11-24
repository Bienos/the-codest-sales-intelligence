import { regionName } from "./targetMarket";
import { FindingCard, FindingsResult, SourceCoverage, useFindings } from "../components/Findings";
import { getLanguage, number, tr } from "../locale";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { actionReason, Badge, EVIDENCE_CONFIDENCE, Coverage, Err, Evidence, Fact, accessKind, demandKind, humanize, lab, priorityKind, unknownText, verdict, whyShort } from "../components/ui";
import { SignalSummary } from "../components/BuyingSignal";

const SECTIONS: [string, string][] = [["critical", "Urgent"], ["existing_accounts", "Your accounts"], ["new_discoveries", "New companies worth a look"],
  ["relationships", "People to get back in touch with"], ["deal_blockers", "Deals that are stuck"], ["research_review", "Research to review"]];
const REASONS = ["not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"];

function Deadlines() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api("/api/deadlines").then((r) => setItems(r.items.filter((x: any) => x.days_left <= 240).slice(0, 6))).catch(() => setItems([])); }, []);
  if (!items.length) return null;
  return <section><div className="section-title">{tr("Regulation deadlines")}</div><p className="small muted sec-note">{tr("Each date was checked against an official page and approved by a second person. Buyers facing a hard deadline tend to need extra delivery capacity.")}</p>
    <div className="card flush table-wrap" tabIndex={0} role="region" aria-label={tr("Deadline · Regulation · Who it applies to")}><table className="cards"><thead><tr><th>{tr("Deadline")}</th><th>{tr("Regulation")}</th><th>{tr("Who it applies to")}</th><th>{tr("Date")}</th></tr></thead><tbody>
      {items.map((x) => <tr key={x.id}><td><a href={x.source_url} target="_blank" rel="noreferrer noopener">{x.title}</a></td><td data-label={tr("Regulation")} className="small">{x.regime}</td><td data-label={tr("Who it applies to")} className="small">{x.applies_to ?? tr("Not specified")}</td>
        <td data-label={tr("Date")}><b>{fmtDate(x.deadline_date)}</b> <span className="small muted">{tr("in") + " "}{x.days_left}{" " + tr("days")}</span></td></tr>)}
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
          <Badge kind={c.account.in_master_list ? "ok" : "info"}>{c.account.in_master_list ? tr("On your lists") : tr("New company")}</Badge>
          {!c.account.ownership_verified && <Badge kind="warn">{tr("No owner in Pipedrive")}</Badge>}
        </div>
      </div>
      <div className="opp-grid">
        <div className="opp-body">
          {(() => { const v = verdict(c); return <div className={`verdict ${v.kind}`}><span className="k">{tr("Our read")}</span>{v.text}</div>; })()}
          {c.tender && <div className="tender"><Badge kind="bad">{tr("Tender")}</Badge>{" " + tr("Closes") + " "}<b>{fmtDateTime(c.tender.deadline_at)}</b> ({c.tender.deadline_tz ?? tr("timezone not given")}{tr("). We last checked it was still open on") + " "}{fmtDateTime(c.tender.last_status_check_at)}.</div>}
          {!compact && <p><span className="k">{tr("What happened")}</span>{c.what_happened.summary ?? tr("No summary")}
            <span className="prov">{" " + tr("Happened") + " "}{fmtDate(c.what_happened.event_date)}{" " + tr("· we saw it") + " "}{fmtDate(c.what_happened.first_observed)}</span></p>}
          {c.buying_signal && <SignalSummary signal={c.buying_signal} />}
          {c.plain_reason && <p><span className="k">{tr("In short")}</span>{c.plain_reason}</p>}
          {!compact && <p><span className="k">{tr("Why it matters")}</span>{whyShort(c.why_it_matters)}</p>}
          <div className="facts">
            <Fact label={tr("Will they use outside help?")} value={lab("demand", c.external_demand_status)} kind={demandKind(c.external_demand_status)} />
            <Fact label={tr("How we would get in")} value={`${lab("route", c.route.type)} (${c.route.counterparty}): ${lab("access", c.route.access_status).toLowerCase()}`} kind={accessKind(c.route.access_status)} />
            <Fact label={tr("Who buys")} value={lab("buyer", c.buyer_status)} kind={c.buyer_status === "none" ? "warn" : "info"} />
            <Fact label={tr("How well sourced")} value={<span title={EVIDENCE_CONFIDENCE}>{humanize(c.evidence_confidence)}</span>} kind={c.evidence_confidence === "high" ? "ok" : c.evidence_confidence === "low" ? "warn" : "info"} />
          </div>
          {c.reopened_after_dismissal && <p className="small"><Badge kind="info">{tr("Back again")}</Badge>{" " + tr("You dismissed this as “")}{lab("dismiss", c.reopened_after_dismissal.previous_reason)}”. {c.reopened_after_dismissal.note}.</p>}
          {c.changed && Object.keys(c.changed).length > 0 && <p className="small muted">{tr("Changed since you last looked:") + " "}{Object.entries(c.changed).map(([k, v]: any) => tr("{} {} to {}", k, v[0] ?? "none", v[1])).join(" · ")}</p>}
          {(c.counterevidence?.length > 0 || c.unknowns?.length > 0) && (
            <div className="before-act">
              <div><span className="k">{tr("Points against it")}</span>{c.counterevidence?.length ? <ul>{c.counterevidence.map((x: any, i: number) => <li key={i}>{x.detail}</li>)}</ul> : <span className="small muted">{tr("None found")}</span>}</div>
              <div><span className="k">{tr("Not known yet")}</span><ul>{c.unknowns.slice(0, 4).map((u: string, i: number) => <li key={i}>{unknownText(u)}</li>)}</ul></div>
            </div>)}
          {open && <div className="mt-4"><span className="k label-caps">{tr("Original sources")}</span><Evidence items={c.evidence} hypothesisId={c.hypothesis_id} /></div>}
        </div>
        <aside className="opp-side" aria-label={tr("Next action: {}, {}", c.initiative.title, lab("route", c.route.type))}>
          <div className="eyebrow m-0">{tr("Next action")}</div>
          {a ? <>
            <div className="atype">{lab("action", a.action_type)}</div>
            <dl><dt>{tr("Goal")}</dt><dd>{a.objective}</dd>{a.target_role && <><dt>{tr("Who to look for")}</dt><dd>{a.target_role}</dd></>}<dt>{tr("Owner")}</dt><dd>{a.owner_name ?? (a.owner_id ? tr("Assigned in Pipedrive") : tr("Unassigned"))}</dd><dt>{tr("Due")}</dt><dd>{a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</dd></dl>
            <div className="why"><b>{tr("Why:")}</b> {actionReason(a)}</div>
            {a.approval_required && <div className="small muted">{tr("A colleague must approve before anything is sent or changed.")}</div>}
          </> : <span className="muted small">{tr("Nothing to do right now.")}</span>}
          <div className="cta"><Link className="btn primary" to={`/opportunities/${c.hypothesis_id}`}>{tr("Open opportunity")}</Link></div>
        </aside>
      </div>
      <div className="opp-foot">
        <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? tr("Hide sources") : tr("Show sources")}</button>
        <button disabled={busy} onClick={() => decide("accepted")}>{tr("Pursue this")}</button>
        <button disabled={busy} onClick={() => decide("monitor")}>{tr("Keep watching")}</button>
        <details className="dismiss-menu"><summary>{tr("Dismiss…")}</summary>
          <div>{REASONS.map((r) => <button key={r} disabled={busy} onClick={() => decide("dismissed", r)}>{lab("dismiss", r)}</button>)}</div>
        </details>
        <span className="ml-auto"><Link to={`/accounts/${c.account.id}`} className="small">{tr("Open account")}</Link></span>
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
        <div className="opp-init">{c.initiative.title}<Badge>{humanize(c.initiative.stage)}</Badge><Badge kind="info">{g.length}{" " + tr("ways in")}</Badge></div>
        <p className="mt-2 mb-0"><b>{tr("What happened:")}</b> {c.what_happened.summary ?? tr("No summary")} <span className="prov">{tr("Happened") + " "}{fmtDate(c.what_happened.event_date)}</span></p>
        <p className="small muted mt-1 mb-0">{whyShort(c.why_it_matters)}{tr(". Each way in below has its own next step.")}</p></div>
      {g.map((x) => <Card key={x.hypothesis_id} c={x} reload={reload} compact />)}
    </div>
  );
}

function ReviewCompanies() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setError(null);
    api("/api/review-companies", { signal: request.signal }).then(setData).catch(e => {
      if (!request.signal.aborted) setError(e);
    });
    return () => request.abort();
  }, [revision]);
  if (error) return <Err e={error} onRetry={() => setRevision(n => n + 1)} />;
  if (!data || !data.total) return null;
  return <section className="mt-4" aria-labelledby="review-companies-title">
    <div className="row spread finding-heading"><h2 id="review-companies-title">{tr("Companies awaiting ownership verification")}</h2>
      <Link to="/accounts?view=review">{tr("View all {} companies", number(data.total))}</Link></div>
    <p className="small muted">{tr("These companies meet the sector, location and size criteria. Their private ownership needs a source.")}</p>
    {data.items.map((company: any) => <article className="card" key={company.id}>
      <div className="row spread"><Link to={`/accounts/${company.id}?profile=1`}><b>{company.name}</b></Link>
        <Badge kind="warn">{tr("Ownership to check")}</Badge></div>
      <div className="small muted">{regionName(company.region)}</div>
      {company.signal ? <p className="small">{company.signal.summary || tr("Source available")}
        {" · "}<a href={company.signal.url} target="_blank" rel="noopener noreferrer">{tr("Original source")} ↗</a>
        {" · "}{company.signal.verified ? tr("Verified signal") : tr("Workbook lead, not yet verified")}</p>
        : <p className="small muted">{tr("No source-backed signal yet")}</p>}
      <div className="small">{tr("Private ownership has not been confirmed with a source.")}</div>
    </article>)}
  </section>;
}

export default function Today({ canReview }: { canReview: boolean }) {
  const [d, setD] = useState<any>(null);
  const findings = useFindings("scope=today&days=14&limit=12");
  const [err, setErr] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const load = () => setRevision((n) => n + 1);
  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    api("/api/today?target=true", { signal: request.signal })
      .then(setD).catch((error) => { if (!request.signal.aborted) setErr(error); });
    return () => request.abort();
  }, [revision]);
  return (
    <>
      <header className="today-heading">
        <div className="page today-heading-inner">
          <div>
            <div className="eyebrow">{new Date(d?.generated_at ?? Date.now()).toLocaleDateString(getLanguage(), { weekday: "long", day: "numeric", month: "long" })}</div>
            <h1>{tr("Today")}</h1>
          </div>
        </div>
      </header>
      <div className="page today-page">
        <section aria-labelledby="latest-findings">
          <div className="row spread finding-heading">
            <h2 id="latest-findings">{tr("Latest findings")}</h2>
            <Link to="/feed">{tr("All findings")}</Link>
          </div>
          <FindingsResult data={findings.data} error={findings.error} retry={findings.retry} empty="No new verified findings in the last 14 days">
            {findings.data?.items.map(item => <FindingCard key={item.id} item={item} />)}
          </FindingsResult>
          {findings.data && <SourceCoverage coverage={findings.data.coverage} />}
        </section>
        {canReview && <ReviewCompanies />}
        {err ? <Err e={err} onRetry={load} /> : null}
        {d?.count > 0 && <details className="today-work">
          <summary>{tr("Sales opportunities to review")} · {number(d.count)}</summary>
          <Coverage c={d.coverage} empty={d.empty_state} />
        {SECTIONS.map(([k, label]) => (d.sections[k]?.length ? <section key={k}><div className="section-title">{tr(label)}</div>{groups(d.sections[k]).map((g) => <Group key={g[0].hypothesis_id} g={g} reload={load} />)}</section> : null))}
        {d.hot_accounts?.length > 0 && <section><div className="section-title">{tr("Hot accounts")}</div><p className="small muted sec-note">{tr("Accounts with the strongest recent signals. Score adds up checked signals; different kinds count for more than repeats. It is not a probability.")}</p>
          <div className="card flush table-wrap" tabIndex={0} role="region" aria-label={tr("Account · Signals · Latest")}><table className="cards"><thead><tr><th>{tr("Account")}</th><th title={tr("Adds up recent checked signals. Different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability.")}>{tr("Signal score")}</th><th>{tr("Buying signal and source")}</th><th>{tr("Latest")}</th></tr></thead><tbody>
            {d.hot_accounts.map((a: any) => <tr key={a.id}><td><Link to={`/accounts/${a.id}`}>{a.display_name}</Link><div className="small muted">{[a.country_code, a.segment].filter(Boolean).join(" · ")}</div></td>
              <td data-label={tr("Signal score")}><b>{a.score}</b> <Badge kind={a.band === "hot" ? "bad" : "warn"}>{a.band === "hot" ? tr("Hot") : tr("Warm")}</Badge></td>
              <td data-label={tr("Buying signal and source")}><SignalSummary signal={a.buying_signal} /></td><td className="small" data-label={tr("Latest")}>{a.last_signal_at ? fmtDate(a.last_signal_at) : tr("None yet")}</td></tr>)}
          </tbody></table></div></section>}
        </details>}
        <Deadlines />
      </div>
    </>
  );
}
