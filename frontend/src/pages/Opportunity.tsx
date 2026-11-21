import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDateTime, post } from "../api";
import { Badge, Err, Evidence, GateBar, PageHeader, humanize, priorityKind } from "../components/ui";

export default function Opportunity({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [pb, setPb] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [draft, setDraft] = useState<any>(null);
  const [crmForm, setCrm] = useState({ subject: "", justification: "" });
  const [proposal, setProposal] = useState<any>(null);
  const load = () => { Promise.all([api(`/api/hypotheses/${id}`), api(`/api/hypotheses/${id}/playbook`)]).then(([a, b]) => { setD(a); setPb(b); }).catch(setErr); };
  useEffect(load, [id]);
  if (err && !d) return <Err e={err} />;
  if (!d || !pb) return <p className="muted">Loading</p>;
  const can = (p: string) => me.permissions.includes(p);
  const run = async (f: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await f(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  const a = d.next_action;
  return (
    <>
      <PageHeader eyebrow={`${d.account.name} · ${humanize(d.route.type)} route`} title={d.initiative.title}
        right={<Link to={`/accounts/${d.account.id}`}><button>Account view</button></Link>}
        sub={<span className="row" style={{ marginTop: 4 }}><Badge kind={priorityKind(d.priority)}>{d.priority}</Badge><Badge>State: {humanize(d.state)}</Badge><Badge>Via {d.route.counterparty}</Badge>
          <Badge kind={d.evidence_confidence === "high" ? "ok" : "warn"}>Evidence confidence: {d.evidence_confidence}</Badge><Badge>Research complete: {Math.round(d.research_completeness * 100)}%</Badge></span>} />
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />

      <div className="section-title" style={{ marginTop: 6 }}>Qualification gates</div>
      <GateBar gates={d.gates} />
      <p className="small muted" style={{ marginTop: 8 }}>A recommendation can be actionable with unknown budget, but is never commercially qualified while gates G4 to G7 are unresolved. {d.score_note}</p>

      <div className="ws" style={{ marginTop: 8 }}>
        <div>
          <div className="section-title">Problem and evidence</div>
          <div className="card"><p style={{ marginTop: 0 }}><b>What happened.</b> {d.what_happened.summary}</p><p><b>Why it matters.</b> {d.why_it_matters}</p><Evidence items={d.evidence} hypothesisId={id} /></div>

          <div className="section-title">Buyer-confirmed facts and assumptions</div>
          <div className="card"><Badge kind={pb.meeting_brief.buyer_confirmed ? "ok" : "warn"}>{pb.meeting_brief.buyer_confirmed ? "Buyer-confirmed need recorded" : "No buyer-confirmed need yet"}</Badge>
            <h3>Assumptions and unknowns</h3><ul className="tight">{pb.meeting_brief.assumptions.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            <h3>Counterevidence</h3>{d.review?.contradicts?.length ? <ul className="tight">{d.review.contradicts.map((c: any, i: number) => <li key={i}><b>{c.question}</b> {c.detail}</li>)}</ul> : <p className="small muted">None recorded. Independent review runs for high-priority items.</p>}
            {d.review?.falsifiers && <details><summary>What would falsify this?</summary><ul className="tight">{d.review.falsifiers.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></details>}</div>

          <div className="section-title">Stakeholders and access</div>
          <div className="card"><p className="small" style={{ marginTop: 0 }}>Play: <b>{pb.play.id ?? "none"} {pb.play.name}</b>. Primary buyer: {pb.play.primary_buyer}. {pb.play.reason}.</p>
            <p className="small"><b>Missing buying-committee roles:</b> {pb.committee.missing.join(", ") || "none"} {pb.committee.single_threaded && <Badge kind="warn">Single-threaded</Badge>}</p>
            <p className="small muted">{pb.committee.caveat}</p>
            <h3>Access routes, ranked by verifiability</h3>{pb.access_routes.length ? <ol className="tight">{pb.access_routes.map((r: any, i: number) => <li key={i}>{humanize(r.kind)} {r.verified ? <Badge kind="ok">verified</Badge> : <Badge kind="warn">unverified</Badge>} {r.context_only && <Badge>context only</Badge>} <span className="muted">{r.evidence}</span></li>)}</ol> : <p className="small muted">No access route recorded. A shared event is not a relationship.</p>}</div>

          <div className="section-title">Discovery preparation</div>
          <div className="card"><ul className="tight">{pb.discovery_questions.map((q: any, i: number) => <li key={i}><Badge>{q.gate}</Badge> {q.question}</li>)}</ul></div>

          <div className="section-title">Deal progress (Pipedrive is authoritative)</div>
          <div className="card">{pb.deal.has_open_deal ? <><p style={{ marginTop: 0 }}><b>{pb.deal.deal.title}</b>. Stage {pb.deal.deal.stage}, {pb.deal.deal.age_days_since_update ?? "?"} days since update {pb.deal.aging_flag && <Badge kind="warn">aging</Badge>}</p>
            {pb.deal.stage_vs_evidence_gap && <p className="small"><Badge kind="warn">Stage vs evidence</Badge> {pb.deal.stage_vs_evidence_gap}</p>}<p className="small"><b>Next step guidance.</b> {pb.deal.next_step_guidance.description}. Success is verified by: {pb.deal.next_step_guidance.verify_success}.</p></> : <p className="muted" style={{ margin: 0 }}>{pb.deal.note}</p>}</div>

          <div className="section-title">Why this recommendation</div>
          <details className="card"><summary>Trace: scoring rule {d.why_this_recommendation.scoring_rule_version}, initiative revision {d.why_this_recommendation.initiative_revision}</summary>
            <h3>Score breakdown</h3><div className="table-wrap"><table><thead><tr><th>Dimension</th><th>Points</th><th>Basis</th></tr></thead><tbody>{Object.entries(d.score_breakdown).map(([k, v]: any) => <tr key={k}><td>{humanize(k)}</td><td>{v.points}/{v.max}</td><td>{v.why}</td></tr>)}</tbody></table></div>
            <h3>State history</h3><ul className="tight small">{d.why_this_recommendation.transitions.map((t: any, i: number) => <li key={i}>{fmtDateTime(t.at)}: {t.prior_state} to {t.new_state} by {t.actor}{t.reason ? ` (${t.reason})` : ""}</li>)}</ul>
            <h3>Research</h3><ul className="tight small">{d.why_this_recommendation.research.map((r: any, i: number) => <li key={i}>{r.research_question}. {r.method}, {r.status}{r.stop_reason ? ` (${humanize(r.stop_reason)})` : ""}</li>)}</ul></details>
        </div>

        <aside className="ws-side" aria-label="Actions">
          <div className="action">
            <div className="eyebrow" style={{ marginBottom: 6 }}>Next action</div>
            {a ? <><div className="atype">{humanize(a.action_type)}</div><div><b className="k">Objective</b> {a.objective}</div><div><b className="k">Due or trigger</b> {a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</div>
              <div><b className="k">Expected</b> {a.expected_outcome}</div><div><b className="k">Stop when</b> {a.stop_condition}</div><div className="small muted" style={{ marginTop: 8 }}>{a.reason}</div></> : <span className="muted">No active action.</span>}
          </div>
          <div className="card" style={{ margin: 0 }}>
            <div className="eyebrow">Suggested communication</div>
            <p className="small" style={{ margin: "0 0 12px" }}>Drafts only. Nothing is sent from this system.</p>
            {can("draft.create") && <button onClick={() => run(async () => setDraft(await post(`/api/hypotheses/${id}/draft`, {})), "Draft generated")}>Generate draft</button>}
            {draft && (draft.status === "blocked" ? <div className="err" role="alert"><b>Draft blocked</b><ul className="tight">{draft.blocked_reasons.map((b: any, i: number) => <li key={i}>{humanize(b.code)}: {b.detail}</li>)}</ul></div>
              : <div style={{ marginTop: 12 }}><p style={{ margin: "0 0 6px" }}><b>{draft.subject}</b></p><pre style={{ whiteSpace: "pre-wrap", font: "inherit" }}>{draft.body}</pre><p className="small muted">{draft.note}</p></div>)}
          </div>
          <div className="card" style={{ margin: 0 }}>
            <div className="eyebrow">CRM proposal</div>
            <p className="small" style={{ margin: "0 0 12px" }}>A second person approves before anything reaches Pipedrive.</p>
            {can("crm.propose") ? <>
              <input aria-label="Activity subject" placeholder="A specific purpose, not 'follow up'" value={crmForm.subject} onChange={(e) => setCrm({ ...crmForm, subject: e.target.value })} style={{ width: "100%", marginBottom: 8 }} />
              <input aria-label="Justification" placeholder="Justification" value={crmForm.justification} onChange={(e) => setCrm({ ...crmForm, justification: e.target.value })} style={{ width: "100%", marginBottom: 12 }} />
              <button className="primary" disabled={crmForm.subject.length < 3 || crmForm.justification.length < 3} onClick={() => run(async () => setProposal(await post("/api/crm/propose-activity", { hypothesis_id: id, ...crmForm })), "Proposal created and waiting for approval")}>Propose Pipedrive activity</button>
              {proposal && <div className="action" style={{ marginTop: 16 }}><b>Approval diff</b><div>Target: {proposal.diff.target}</div><div>Operation: {proposal.diff.operation}</div><pre style={{ margin: "6px 0" }}>{JSON.stringify(proposal.diff.payload, null, 2)}</pre><div className="small muted">Status {proposal.status}. Expires {fmtDateTime(proposal.expires_at)}. A different authorised person must approve before the executor runs.</div></div>}</> : <p className="muted small">Your role cannot propose CRM actions.</p>}
            <p style={{ margin: "14px 0 0" }}><Link to="/admin/approvals" className="small">Review approvals</Link></p>
          </div>
        </aside>
      </div>
    </>
  );
}
