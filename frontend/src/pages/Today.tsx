import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { Badge, Coverage, Empty, Err, Evidence, Fact, accessKind, demandKind, humanize, priorityKind } from "../components/ui";

const SECTIONS: [string, string][] = [["critical", "Critical and time-limited"], ["existing_accounts", "Developments in your existing accounts"], ["new_discoveries", "New qualified discoveries outside the master lists"],
  ["relationships", "Relationship re-engagement"], ["deal_blockers", "Active deal blockers"], ["research_review", "Research awaiting review"]];
const REASONS = ["not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"];

function Card({ c, reload }: { c: any; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const decide = async (decision: string, r?: string) => { try { await post(`/api/hypotheses/${c.hypothesis_id}/decision`, { decision, reason: r }); reload(); } catch (e) { setErr(e); } };
  const a = c.next_action;
  return (
    <article className={`opp ${c.priority}`} aria-label={`${c.account.name}: ${c.initiative.title}`}>
      <div className="opp-head">
        <div>
          <div className="opp-title">{c.account.name}</div>
          <div className="opp-init">{c.initiative.title}
            {c.initiative.working_title_label && <Badge kind="warn">{c.initiative.working_title_label}</Badge>}<Badge>{humanize(c.initiative.stage)}</Badge></div>
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <Badge kind={priorityKind(c.priority)}>{c.priority.toUpperCase()}</Badge>
          <Badge kind={c.account.in_master_list ? "ok" : "info"}>{c.account.in_master_list ? "Existing list" : "New"}</Badge>
          {!c.account.ownership_verified && <Badge kind="warn">Ownership unverified</Badge>}
        </div>
      </div>
      <div className="opp-grid">
        <div className="opp-body">
          {c.tender && <div className="tender"><Badge kind="bad">Tender</Badge> Deadline <b>{fmtDateTime(c.tender.deadline_at)}</b> ({c.tender.deadline_tz ?? "timezone unknown"}). Status last rechecked {fmtDateTime(c.tender.last_status_check_at)}.</div>}
          <p><span className="k">What happened</span>{c.what_happened.summary ?? "No summary"}
            <span className="prov"> Event {fmtDate(c.what_happened.event_date)} · published {fmtDate(c.what_happened.published)} · first seen {fmtDate(c.what_happened.first_observed)} · checked {fmtDateTime(c.last_checked)}</span></p>
          <p><span className="k">Why it matters</span>{c.why_it_matters}</p>
          <div className="facts">
            <Fact label="External demand" value={humanize(c.external_demand_status)} kind={demandKind(c.external_demand_status)} />
            <Fact label="Route and access" value={`${humanize(c.route.type)} via ${c.route.counterparty}: ${c.route.access_status}`} kind={accessKind(c.route.access_status)} />
            <Fact label="Buyer" value={humanize(c.buyer_status)} kind={c.buyer_status === "none" ? "warn" : "info"} />
            <Fact label="Evidence confidence" value={c.evidence_confidence} kind={c.evidence_confidence === "high" ? "ok" : c.evidence_confidence === "low" ? "warn" : "info"} />
          </div>
          {c.reopened_after_dismissal && <p className="small"><Badge kind="info">Re-opened</Badge> You dismissed this as “{humanize(c.reopened_after_dismissal.previous_reason)}”. {c.reopened_after_dismissal.note}.</p>}
          {c.changed && Object.keys(c.changed).length > 0 && <p className="small muted">Changed since you last looked: {Object.entries(c.changed).map(([k, v]: any) => `${k} ${v[0] ?? "none"} to ${v[1]}`).join(" · ")}</p>}
          {(c.counterevidence?.length > 0 || c.unknowns?.length > 0) && (
            <div className="before-act">
              <div><span className="k">Counterevidence</span>{c.counterevidence?.length ? <ul>{c.counterevidence.map((x: any, i: number) => <li key={i}>{x.detail}</li>)}</ul> : <span className="small muted">None recorded</span>}</div>
              <div><span className="k">Still unknown</span><ul>{c.unknowns.slice(0, 4).map((u: string, i: number) => <li key={i}>{u}</li>)}</ul></div>
            </div>)}
          {open && <div style={{ marginTop: 14 }}><span className="k" style={{ font: "800 11px var(--font-head)", letterSpacing: ".12em", textTransform: "uppercase" }}>Original evidence</span><Evidence items={c.evidence} /></div>}
        </div>
        <aside className="opp-side" aria-label="Next action">
          <div className="eyebrow" style={{ margin: 0 }}>Next action</div>
          {a ? <>
            <div className="atype">{humanize(a.action_type)}</div>
            <dl><dt>Objective</dt><dd>{a.objective}</dd><dt>Owner</dt><dd>{a.owner_id ? `Pipedrive user ${a.owner_id}` : a.owner_note ?? "Unassigned"}</dd><dt>Due</dt><dd>{a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</dd></dl>
            <div className="why"><b>Why.</b> {a.reason}</div>
            {a.approval_required && <div className="small muted">Needs your approval before any external step.</div>}
          </> : <span className="muted small">No active action.</span>}
          <div className="cta"><Link to={`/opportunities/${c.hypothesis_id}`}><button className="primary">Open opportunity</button></Link></div>
        </aside>
      </div>
      <div className="opp-foot">
        <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide sources" : "Review sources"}</button>
        <button onClick={() => decide("accepted")}>Accept</button>
        <button onClick={() => decide("monitor")}>Monitor</button>
        <select aria-label="Dismissal reason" value={reason} onChange={(e) => setReason(e.target.value)}><option value="">Dismiss with reason</option>{REASONS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</select>
        <button disabled={!reason} onClick={() => decide("dismissed", reason)}>Dismiss</button>
        <span style={{ marginLeft: "auto" }}><Link to={`/accounts/${c.account.id}`} className="small">Account view</Link></span>
      </div>
      <div style={{ padding: "0 24px" }}><Err e={err} /></div>
    </article>
  );
}

export default function Today() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const load = () => api("/api/today").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <div className="page"><Err e={err} /></div>;
  if (!d) return <div className="page muted">Loading</div>;
  const all: any[] = Object.values(d.sections).flat() as any[];
  const count = (f: (c: any) => boolean) => all.filter(f).length;
  return (
    <>
      <header className="hero">
        <div className="eyebrow">{new Date(d.generated_at).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
        <h2>Today</h2>
        <p className="sub">{d.count === 0 ? "Nothing new needs your attention." : `${d.count} material change${d.count === 1 ? "" : "s"} across your accounts. Repeated coverage of one release is shown once, and dismissed items stay hidden until something material changes.`}</p>
        <div className="stats">
          <div className="stat urgent"><div className="n">{count((c) => c.priority === "urgent")}</div><div className="l">Urgent</div></div>
          <div className="stat"><div className="n">{count((c) => c.priority === "high")}</div><div className="l">High priority</div></div>
          <div className="stat"><div className="n">{count((c) => c.account.in_master_list)}</div><div className="l">Existing accounts</div></div>
          <div className="stat"><div className="n">{count((c) => !c.account.in_master_list)}</div><div className="l">New discoveries</div></div>
          <div className="stat"><div className="n">{d.coverage.monitored_sources}<span style={{ fontSize: 18, opacity: .7 }}>/{d.coverage.monitored_sources + d.coverage.pending_validation_sources}</span></div><div className="l">Sources live</div></div>
        </div>
      </header>
      <div className="page" style={{ paddingTop: 24 }}>
        <Coverage c={d.coverage} empty={d.empty_state} />
        {d.count === 0 && <Empty title="All quiet">{d.empty_state}</Empty>}
        {SECTIONS.map(([k, label]) => (d.sections[k]?.length ? <section key={k}><div className="section-title">{label}</div>{d.sections[k].map((c: any) => <Card key={c.hypothesis_id} c={c} reload={load} />)}</section> : null))}
      </div>
    </>
  );
}
