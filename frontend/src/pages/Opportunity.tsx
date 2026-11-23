import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDateTime, post } from "../api";
import { Badge, Err, Evidence, GateBar, PageHeader, humanize, lab, priorityKind, unknownText, verdict } from "../components/ui";

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
  if (!d || !pb) return <p className="muted">Loading the opportunity…</p>;
  const can = (p: string) => me.permissions.includes(p);
  const run = async (f: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await f(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  const a = d.next_action;
  return (
    <>
      <PageHeader eyebrow={`${d.account.name} · ${lab("route", d.route.type)}`} title={d.initiative.title}
        right={<Link to={`/accounts/${d.account.id}`}><button>Open account</button></Link>}
        sub={<span className="row mt-1"><Badge kind={priorityKind(d.priority)}>{lab("priority", d.priority)}</Badge><Badge>{lab("state", d.state)}</Badge><Badge>Via {d.route.counterparty}</Badge>
          <Badge kind={d.evidence_confidence === "high" ? "ok" : "warn"}>Sources: {d.evidence_confidence}</Badge><Badge><span title="How many of the seven checks below have an answer">{Math.round(d.research_completeness * 7)} of 7 checks answered</span></Badge>{d.score != null && <Badge><span title="Ranks what to look at first. It is not a chance of winning.">Opportunity score {d.score}/100</span></Badge>}</span>} />
      {msg && <div className="okmsg" role="status">{msg}</div>}<Err e={err} />

      <div className={`verdict ${verdict(d).kind} mt-2`}><span className="k">What to do</span>{verdict(d).text}</div>
      <div className="section-title mt-2">Seven checks before this is a real deal</div>
      <GateBar gates={d.gates} />
      <p className="small muted mt-2">You can act on this now. It is not qualified until checks 4 to 7 are answered, and most of those need a conversation with the buyer. The score ranks what to look at first; it is not a chance of winning.</p>

      <div className="ws mt-2">
        <div>
          <div className="section-title">What happened and why it matters</div>
          <div className="card"><p className="mt-0"><b>What happened:</b> {d.what_happened.summary}</p><p><b>Why it matters:</b> {d.why_it_matters}</p><Evidence items={d.evidence} hypothesisId={id} /></div>

          <div className="section-title">What we know and what we are guessing</div>
          <div className="card"><Badge kind={pb.meeting_brief.buyer_confirmed ? "ok" : "warn"}>{pb.meeting_brief.buyer_confirmed ? "The buyer has confirmed the need" : "The buyer has not confirmed the need yet"}</Badge>
            <h3>Guesses and gaps</h3><ul className="tight">{pb.meeting_brief.assumptions.map((x: string, i: number) => <li key={i}>{unknownText(x)}</li>)}</ul>
            <h3>Points against it</h3>{d.review?.contradicts?.length ? <ul className="tight">{d.review.contradicts.map((c: any, i: number) => <li key={i}><b>{c.question}</b> {c.detail}</li>)}</ul> : <p className="small muted">None found. High-priority items get an independent second look.</p>}
            {d.review?.falsifiers && <details><summary>What would prove this wrong?</summary><ul className="tight">{d.review.falsifiers.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></details>}</div>

          <div className="section-title">Who to talk to and how to get in</div>
          <div className="card"><p className="small mt-0">Sales play: <b>{pb.play.name ?? "none"}</b>. Usual buyer: {String(pb.play.primary_buyer ?? "not known").replace(/\.$/, "")}.{(() => { const r = String(pb.play.reason ?? "").replace(/\s*active signal\(s\) \[.*?\]\.?/i, "").replace(/[.\s]+$/, ""); return r ? ` ${r}.` : ""; })()}</p>
            <p className="small"><b>Not yet identified:</b> {pb.committee.missing.map((m: string) => lab("committee", m)).join(", ") || "none"} {pb.committee.single_threaded && <Badge kind="warn">Only one contact so far</Badge>}</p>
            <p className="small muted">A job title does not prove they hold the budget. We treat budget authority as a guess until the buyer confirms it.</p>
            <h3>Ways in, most reliable first</h3>{pb.access_routes.length ? <ol className="tight">{pb.access_routes.map((r: any, i: number) => <li key={i}>{humanize(r.kind)} {r.verified ? <Badge kind="ok">confirmed</Badge> : <Badge kind="warn">not confirmed</Badge>} {r.context_only && <Badge>background only</Badge>} <span className="muted">{r.evidence}</span></li>)}</ol> : <p className="small muted">No way in recorded yet. Attending the same event is not a relationship.</p>}</div>

          <div className="section-title">Questions to ask</div>
          <div className="card"><ul className="tight">{pb.discovery_questions.map((q: any, i: number) => <li key={i}><Badge>{({ G1: "Account fit", G2: "Real programme", G3: "Engineering fit", G4: "Outside demand", G5: "Supplier access", G6: "Buyer path", G7: "Delivery", play: "Sales play" } as Record<string, string>)[q.gate] ?? q.gate}</Badge> {q.question}</li>)}</ul></div>

          <div className="section-title">Pipedrive deal</div>
          <div className="card">{pb.deal.has_open_deal ? <><p className="mt-0"><b>{pb.deal.deal.title}</b>. Stage {pb.deal.deal.stage}, last updated {pb.deal.deal.age_days_since_update ?? "an unknown number of"} days ago {pb.deal.aging_flag && <Badge kind="warn">going stale</Badge>}</p>
            {pb.deal.stage_vs_evidence_gap && <p className="small"><Badge kind="warn">Stage looks ahead of the evidence</Badge> {pb.deal.stage_vs_evidence_gap}</p>}<p className="small"><b>Suggested next step:</b> {pb.deal.next_step_guidance.description}. You will know it worked when: {pb.deal.next_step_guidance.verify_success}.</p></> : <p className="muted m-0">There is no Pipedrive deal for this yet. An opportunity here is not a deal. Propose one below and a colleague will approve it first.</p>}</div>

          <div className="section-title">How this was scored</div>
          <details className="card"><summary>Show the score breakdown and history</summary>
            <h3>Score breakdown</h3><p className="small muted">Six parts add up to the opportunity score out of 100. Scoring version {d.why_this_recommendation.scoring_rule_version}.</p><div className="table-wrap"><table><thead><tr><th>Part</th><th>Points</th><th>Why</th></tr></thead><tbody>{Object.entries(d.score_breakdown).map(([k, v]: any) => <tr key={k}><td>{({ icp: "Company fit", engineering_relevance: "Relevance to our engineering", external_demand: "Outside demand", access: "Way in", timing: "Timing", relationship_delivery: "Relationship and delivery" } as Record<string, string>)[k] ?? humanize(k)}</td><td>{v.points}/{v.max}</td><td>{v.why}</td></tr>)}</tbody></table></div>
            <h3>History</h3><ul className="tight small">{d.why_this_recommendation.transitions.map((t: any, i: number) => <li key={i}>{fmtDateTime(t.at)}: {lab("state", t.prior_state)} → {lab("state", t.new_state)} ({String(t.actor).startsWith("system:") ? "automatic" : t.actor}{t.reason ? `, ${String(t.reason).replace(/^auto: /, "")}` : ""})</li>)}</ul>
            <h3>Research done</h3><ul className="tight small">{d.why_this_recommendation.research.map((r: any, i: number) => <li key={i}>{r.research_question}. {r.method}, {r.status}{r.stop_reason ? ` (${humanize(r.stop_reason)})` : ""}</li>)}</ul></details>
        </div>

        <aside className="ws-side" aria-label="Actions">
          <div className="action">
            <div className="eyebrow mb-2">Next action</div>
            {a ? <><div className="atype">{lab("action", a.action_type)}</div><div><b className="k">Goal</b> {a.objective}</div><div><b className="k">When</b> {a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</div>
              <div><b className="k">Good result</b> {a.expected_outcome}</div><div><b className="k">Stop when</b> {a.stop_condition}</div><div className="small muted mt-2">{a.reason}</div></> : <span className="muted">Nothing to do right now.</span>}
          </div>
          <div className="card m-0">
            <div className="eyebrow">Draft a message</div>
            <p className="small mt-0 mb-3">This only writes a draft. Nothing is sent from here.</p>
            {can("draft.create") && <button onClick={() => run(async () => setDraft(await post(`/api/hypotheses/${id}/draft`, {})), "Draft written below")}>Write a draft</button>}
            {draft && (draft.status === "blocked" ? <div className="err" role="alert"><b>We could not write this draft</b><ul className="tight">{draft.blocked_reasons.map((b: any, i: number) => <li key={i}>{humanize(b.code)}: {b.detail}</li>)}</ul></div>
              : <div className="mt-3"><p className="mt-0 mb-2"><b>{draft.subject}</b></p><pre className="plain-pre">{draft.body}</pre><p className="small muted">{draft.note}</p></div>)}
          </div>
          <div className="card m-0">
            <div className="eyebrow">Add a Pipedrive activity</div>
            <p className="small mt-0 mb-3">A colleague has to approve it before anything reaches Pipedrive.</p>
            {can("crm.propose") ? <>
              <label className="small" htmlFor="crm-subject">What is the activity for?</label><input id="crm-subject" placeholder="A specific purpose, not 'follow up'" value={crmForm.subject} onChange={(e) => setCrm({ ...crmForm, subject: e.target.value })} className="w-full mb-2" />
              <label className="small" htmlFor="crm-why">Why now?</label><input id="crm-why" placeholder="One sentence" value={crmForm.justification} onChange={(e) => setCrm({ ...crmForm, justification: e.target.value })} className="w-full mb-3" />
              <button className="primary" disabled={crmForm.subject.length < 3 || crmForm.justification.length < 3} onClick={() => run(async () => setProposal(await post("/api/crm/propose-activity", { hypothesis_id: id, ...crmForm })), "Sent for approval. A colleague will see it under Administration, Approvals.")}>Ask for approval</button>
              {proposal && <div className="action mt-4"><b>Waiting for approval</b><div className="small mt-2 mb-2">{Object.entries(proposal.diff.payload ?? {}).map(([k, v]: any) => <div key={k}><b>{humanize(k)}:</b> {String(v)}</div>)}</div><div className="small muted">Someone other than you must approve it by {fmtDateTime(proposal.expires_at)}.</div></div>}</> : <p className="muted small">Your role cannot add Pipedrive activities.</p>}
            <p className="mt-4 mb-0"><Link to="/admin/approvals" className="small">See what is waiting for approval</Link></p>
          </div>
        </aside>
      </div>
    </>
  );
}
