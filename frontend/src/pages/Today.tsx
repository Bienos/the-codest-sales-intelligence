import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { Badge, Coverage, Err, Evidence, humanize, priorityKind } from "../components/ui";

const SECTIONS: [string, string][] = [["critical", "Critical and time-limited"], ["existing_accounts", "Developments in your existing accounts"], ["new_discoveries", "New qualified discoveries (outside the master lists)"],
  ["relationships", "Relationship re-engagement"], ["deal_blockers", "Active deal blockers"], ["research_review", "Research awaiting review"]];
const REASONS = ["not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"];

function Card({ c, reload }: { c: any; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const decide = async (decision: string, r?: string) => {
    try { await post(`/api/hypotheses/${c.hypothesis_id}/decision`, { decision, reason: r }); reload(); } catch (e) { setErr(e); }
  };
  const a = c.next_action;
  return (
    <article className={`card ${c.priority}`} aria-label={`${c.account.name}: ${c.initiative.title}`}>
      <div className="row spread">
        <div className="row">
          <Badge kind={priorityKind(c.priority)}>{c.priority.toUpperCase()}</Badge>
          <Badge kind={c.account.in_master_list ? "ok" : "info"}>{c.account.in_master_list ? "EXISTING LIST" : "NEW"}</Badge>
          {!c.account.ownership_verified && <Badge kind="warn">ownership unverified</Badge>}
          <span className="title">{c.account.name}</span>
        </div>
        <span className="muted small">last checked {fmtDateTime(c.last_checked)}</span>
      </div>
      <div style={{ marginTop: 6 }}>
        <b>{c.initiative.title}</b> {c.initiative.working_title_label && <Badge kind="warn">{c.initiative.working_title_label}</Badge>} <Badge>{humanize(c.initiative.stage)}</Badge>
      </div>
      <p style={{ margin: "8px 0" }}><b>What happened:</b> {c.what_happened.summary ?? "—"} <span className="muted small">(event {fmtDate(c.what_happened.event_date)}, published {fmtDate(c.what_happened.published)}, first seen {fmtDate(c.what_happened.first_observed)})</span></p>
      <p style={{ margin: "8px 0" }}><b>Why it matters:</b> {c.why_it_matters}</p>
      {c.tender && <p className="small"><Badge kind="bad">TENDER</Badge> deadline {fmtDateTime(c.tender.deadline_at)} ({c.tender.deadline_tz ?? "timezone unknown"}) · status last rechecked {fmtDateTime(c.tender.last_status_check_at)}</p>}
      <div className="row">
        <Badge>demand: {humanize(c.external_demand_status)}</Badge><Badge>route: {humanize(c.route.type)} → {c.route.counterparty} ({c.route.access_status})</Badge>
        <Badge>buyer: {humanize(c.buyer_status)}</Badge><Badge kind={c.evidence_confidence === "high" ? "ok" : c.evidence_confidence === "low" ? "warn" : "info"}>evidence confidence: {c.evidence_confidence}</Badge>
      </div>
      {c.reopened_after_dismissal && <p className="small"><Badge kind="info">Re-opened</Badge> you dismissed this as “{humanize(c.reopened_after_dismissal.previous_reason)}”; {c.reopened_after_dismissal.note}.</p>}
      {c.changed && Object.keys(c.changed).length > 0 && <p className="small muted">Changed since last view: {Object.entries(c.changed).map(([k, v]: any) => `${k}: ${v[0] ?? "—"} → ${v[1]}`).join(" · ")}</p>}
      {c.counterevidence?.length > 0 && <div className="small"><b>Counterevidence:</b><ul className="tight">{c.counterevidence.map((x: any, i: number) => <li key={i}>{x.detail}</li>)}</ul></div>}
      {c.unknowns?.length > 0 && <div className="small"><b>Still unknown:</b><ul className="tight">{c.unknowns.slice(0, 3).map((u: string, i: number) => <li key={i}>{u}</li>)}</ul></div>}
      {a && (
        <div className="action">
          <div><b className="k">Next action</b> <b>{humanize(a.action_type)}</b>{a.approval_required && <> <Badge>needs your approval before any external step</Badge></>}</div>
          <div><b className="k">Objective</b> {a.objective}</div>
          <div><b className="k">Owner</b> {a.owner_id ? `Pipedrive user ${a.owner_id}` : a.owner_note ?? "unassigned"}</div>
          <div><b className="k">Due / trigger</b> {a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</div>
          <div className="small muted"><b className="k">Why</b> {a.reason}</div>
        </div>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide sources" : "Review sources"}</button>
        <Link to={`/opportunities/${c.hypothesis_id}`}><button className="primary">Open opportunity</button></Link>
        <button onClick={() => decide("accepted")}>Accept</button>
        <button onClick={() => decide("monitor")}>Monitor</button>
        <select aria-label="Dismissal reason" value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">Dismiss with reason…</option>{REASONS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
        </select>
        <button disabled={!reason} onClick={() => decide("dismissed", reason)}>Dismiss</button>
        <Link to={`/accounts/${c.account.id}`}>Account</Link>
      </div>
      {open && <div style={{ marginTop: 10 }}><Evidence items={c.evidence} /></div>}
      <Err e={err} />
    </article>
  );
}

export default function Today() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const load = () => api("/api/today").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <>
      <h2>Today</h2>
      <p className="sub">{d.count} material change(s). Repeated coverage of one release is shown once; dismissed items stay hidden until something material changes.</p>
      <Coverage c={d.coverage} empty={d.empty_state} />
      {d.count === 0 && <div className="empty">{d.empty_state}</div>}
      {SECTIONS.map(([k, label]) => (d.sections[k]?.length ? <section key={k}><div className="section-title">{label}</div>{d.sections[k].map((c: any) => <Card key={c.hypothesis_id} c={c} reload={load} />)}</section> : null))}
    </>
  );
}
