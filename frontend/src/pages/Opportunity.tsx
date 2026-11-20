import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { Badge, Err, Evidence, gateKind, humanize, priorityKind } from "../components/ui";

const GATE_NAMES: Record<string, string> = { G1: "Account & ICP", G2: "Initiative is real", G3: "Engineering relevance", G4: "External demand", G5: "Supplier access", G6: "Buyer path", G7: "Delivery feasibility" };

export default function Opportunity({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [pb, setPb] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string>("");
  const [draft, setDraft] = useState<any>(null);
  const [crmForm, setCrm] = useState({ subject: "", justification: "" });
  const [proposal, setProposal] = useState<any>(null);
  const load = () => { Promise.all([api(`/api/hypotheses/${id}`), api(`/api/hypotheses/${id}/playbook`)]).then(([a, b]) => { setD(a); setPb(b); }).catch(setErr); };
  useEffect(load, [id]);
  if (err) return <Err e={err} />;
  if (!d || !pb) return <p className="muted">Loading…</p>;
  const can = (p: string) => me.permissions.includes(p);
  const run = async (f: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await f(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  return (
    <>
      <div className="row spread"><div><h2>{d.account.name}: {d.initiative.title}</h2>
        <div className="row"><Badge kind={priorityKind(d.priority)}>{d.priority}</Badge><Badge>state: {humanize(d.state)}</Badge><Badge>{humanize(d.route.type)} via {d.route.counterparty}</Badge>
          <Badge kind={d.evidence_confidence === "high" ? "ok" : "warn"}>evidence confidence: {d.evidence_confidence}</Badge><Badge>research completeness {Math.round(d.research_completeness * 100)}%</Badge></div></div>
        <Link to={`/accounts/${d.account.id}`}>← Account</Link></div>
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />

      <div className="section-title">Current qualification state</div>
      <div className="card"><div className="gates">{Object.entries(d.gates).map(([g, v]: any) => <div key={g} className="gate"><Badge kind={gateKind(v.status)}>{v.status}</Badge> <b>{g} {GATE_NAMES[g]}</b><div className="muted">{v.reason}</div></div>)}</div>
        <p className="small muted">A recommendation can be actionable with unknown budget, but is never <i>commercially qualified</i> while G4–G7 are unresolved. {d.score_note}</p></div>

      <div className="section-title">Next action</div>
      {d.next_action ? <div className="action"><div><b className="k">Action</b> <b>{humanize(d.next_action.action_type)}</b></div><div><b className="k">Objective</b> {d.next_action.objective}</div><div><b className="k">Due / trigger</b> {d.next_action.due_at ? fmtDateTime(d.next_action.due_at) : d.next_action.trigger_condition}</div>
        <div><b className="k">Expected</b> {d.next_action.expected_outcome}</div><div><b className="k">Stop when</b> {d.next_action.stop_condition}</div><div className="small muted"><b className="k">Why</b> {d.next_action.reason}</div></div> : <div className="empty">No active action.</div>}

      <div className="section-title">Problem and evidence</div>
      <div className="card"><p><b>What happened:</b> {d.what_happened.summary}</p><p><b>Why it matters:</b> {d.why_it_matters}</p><Evidence items={d.evidence} /></div>

      <div className="section-title">Buyer-confirmed facts vs assumptions</div>
      <div className="card"><div className="row"><Badge kind={pb.meeting_brief.buyer_confirmed ? "ok" : "warn"}>{pb.meeting_brief.buyer_confirmed ? "Buyer-confirmed need recorded" : "No buyer-confirmed need yet"}</Badge></div>
        <h3>Assumptions / unknowns</h3><ul className="tight">{pb.meeting_brief.assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
        <h3>Counterevidence</h3>{d.review?.contradicts?.length ? <ul className="tight">{d.review.contradicts.map((c: any, i: number) => <li key={i}><b>{c.question}</b> {c.detail}</li>)}</ul> : <p className="small muted">None recorded. {d.review ? "" : "(Independent review runs for high-priority items.)"}</p>}
        {d.review?.falsifiers && <details><summary>What would falsify this?</summary><ul className="tight">{d.review.falsifiers.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></details>}</div>

      <div className="section-title">Stakeholders and access</div>
      <div className="card"><p className="small">Play: <b>{pb.play.id ?? "none"} {pb.play.name}</b> · primary buyer: {pb.play.primary_buyer} · {pb.play.reason}</p>
        <p className="small"><b>Missing buying-committee roles:</b> {pb.committee.missing.join(", ") || "none"}{pb.committee.single_threaded && <> · <Badge kind="warn">single-threaded</Badge></>}</p>
        <p className="small muted">{pb.committee.caveat}</p>
        <h3>Access routes (ranked by verifiability)</h3>{pb.access_routes.length ? <ol className="tight">{pb.access_routes.map((r: any, i: number) => <li key={i}>{humanize(r.kind)} {r.verified ? <Badge kind="ok">verified</Badge> : <Badge kind="warn">unverified</Badge>} {r.context_only && <Badge>context only</Badge>} <span className="muted">{r.evidence}</span></li>)}</ol> : <p className="small muted">No access route recorded. A shared event is not a relationship.</p>}</div>

      <div className="section-title">Discovery preparation</div>
      <div className="card"><ul className="tight">{pb.discovery_questions.map((q: any, i: number) => <li key={i}><Badge>{q.gate}</Badge> {q.question}</li>)}</ul></div>

      <div className="section-title">Deal progress (Pipedrive is authoritative)</div>
      <div className="card">{pb.deal.has_open_deal ? <><p><b>{pb.deal.deal.title}</b> · stage {pb.deal.deal.stage} · {pb.deal.deal.age_days_since_update ?? "?"} days since update {pb.deal.aging_flag && <Badge kind="warn">aging</Badge>}</p>
        {pb.deal.stage_vs_evidence_gap && <p className="small"><Badge kind="warn">stage vs evidence</Badge> {pb.deal.stage_vs_evidence_gap}</p>}<p className="small"><b>Next step guidance:</b> {pb.deal.next_step_guidance.description}. Success is verified by: {pb.deal.next_step_guidance.verify_success}.</p></> : <p className="muted">{pb.deal.note}</p>}</div>

      <div className="section-title">Suggested communications (drafts only, never sent from here)</div>
      <div className="card">
        {can("draft.create") && <button onClick={() => run(async () => setDraft(await post(`/api/hypotheses/${id}/draft`, {})), "Draft generated")}>Generate draft</button>}
        {draft && (draft.status === "blocked" ? <div className="err" role="alert"><b>Draft blocked:</b><ul className="tight">{draft.blocked_reasons.map((b: any, i: number) => <li key={i}>{humanize(b.code)} — {b.detail}</li>)}</ul></div>
          : <div><p><b>{draft.subject}</b></p><pre style={{ whiteSpace: "pre-wrap", font: "inherit" }}>{draft.body}</pre><p className="small muted">{draft.note}</p></div>)}
      </div>

      <div className="section-title">CRM proposals (require a second person's approval)</div>
      <div className="card">
        {can("crm.propose") ? <>
          <div className="row"><input aria-label="Activity subject" placeholder="Activity subject (a specific purpose, not 'follow up')" value={crmForm.subject} onChange={(e) => setCrm({ ...crmForm, subject: e.target.value })} style={{ minWidth: 340 }} />
            <input aria-label="Justification" placeholder="Justification" value={crmForm.justification} onChange={(e) => setCrm({ ...crmForm, justification: e.target.value })} style={{ minWidth: 220 }} />
            <button className="primary" disabled={crmForm.subject.length < 3 || crmForm.justification.length < 3} onClick={() => run(async () => setProposal(await post("/api/crm/propose-activity", { hypothesis_id: id, ...crmForm })), "Proposal created: awaiting approval")}>Propose Pipedrive activity</button></div>
          {proposal && <div className="action"><b>Approval diff</b><div>Target: {proposal.diff.target}</div><div>Operation: {proposal.diff.operation}</div><pre style={{ margin: 0 }}>{JSON.stringify(proposal.diff.payload, null, 2)}</pre><div className="small muted">Status {proposal.status}; expires {fmtDateTime(proposal.expires_at)}. A different authorised person must approve before the executor runs.</div></div>}</> : <p className="muted">Your role cannot propose CRM actions.</p>}
        <Link to="/admin/approvals" className="small">Review approvals →</Link>
      </div>

      <div className="section-title">Why this recommendation?</div>
      <details className="card"><summary>Trace (scoring rule {d.why_this_recommendation.scoring_rule_version}, initiative revision {d.why_this_recommendation.initiative_revision})</summary>
        <h3>Score breakdown</h3><table><thead><tr><th>Dimension</th><th>Points</th><th>Basis</th></tr></thead><tbody>{Object.entries(d.score_breakdown).map(([k, v]: any) => <tr key={k}><td>{humanize(k)}</td><td>{v.points}/{v.max}</td><td>{v.why}</td></tr>)}</tbody></table>
        <h3>State history</h3><ul className="tight small">{d.why_this_recommendation.transitions.map((t: any, i: number) => <li key={i}>{fmtDate(t.at)}: {t.prior_state} → {t.new_state} by {t.actor}{t.reason ? ` (${t.reason})` : ""}</li>)}</ul>
        <h3>Research</h3><ul className="tight small">{d.why_this_recommendation.research.map((r: any, i: number) => <li key={i}>{r.research_question} — {r.method}, {r.status}{r.stop_reason ? ` (${humanize(r.stop_reason)})` : ""}</li>)}</ul></details>
    </>
  );
}
