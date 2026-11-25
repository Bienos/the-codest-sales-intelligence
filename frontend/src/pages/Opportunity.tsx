import { tr } from "../locale";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDateTime, post } from "../api";
import { SignalSummary } from "../components/BuyingSignal";
import { actionReason, Badge, EVIDENCE_CONFIDENCE, Err, Evidence, GateBar, PageHeader, humanize, lab, priorityKind, unknownText, verdict, Skeleton, whyShort } from "../components/ui";

export default function Opportunity({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [pb, setPb] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [draft, setDraft] = useState<any>(null);
  const load = () => { Promise.all([api(`/api/hypotheses/${id}`), api(`/api/hypotheses/${id}/playbook`)]).then(([a, b]) => { setD(a); setPb(b); }).catch(setErr); };
  useEffect(load, [id]);
  if (!d || !pb) return <><PageHeader title={tr("Opportunity")} />{err ? <Err e={err} onRetry={() => { setErr(null); load(); }} /> : <Skeleton label={tr("Loading opportunity")} />}</>;
  const can = (p: string) => me.permissions.includes(p);
  const run = async (f: () => Promise<any>, ok: string) => { setErr(null); setMsg(""); try { await f(); setMsg(ok); load(); } catch (e) { setErr(e); } };
  const a = d.next_action;
  return (
    <>
      <PageHeader eyebrow={`${d.account.name} · ${lab("route", d.route.type)}`} title={d.initiative.title}
        right={<Link className="btn" to={`/accounts/${d.account.id}`}>{tr("Open account")}</Link>}
        sub={<span className="row mt-1"><Badge kind={priorityKind(d.priority)}>{lab("priority", d.priority)}</Badge><Badge>{lab("state", d.state)}</Badge>{d.route.counterparty && <Badge>{tr("Via") + " "}{d.route.counterparty}</Badge>}
          <Badge kind={d.evidence_confidence === "high" ? "ok" : "warn"}><span title={EVIDENCE_CONFIDENCE}>{tr("Evidence:") + " "}{d.evidence_confidence}</span></Badge><Badge><span title={tr("How many of the seven checks below have an answer")}>{Math.round(d.research_completeness * 7)}{" " + tr("of 7 checks answered")}</span></Badge>{d.score != null && <Badge><span title={tr("Ranks what to look at first. It is not a chance of winning.")}>{tr("Opportunity score") + " "}{d.score}/100</span></Badge>}</span>} />
      {msg && <div className="okmsg" role="status">{tr(msg)}</div>}<Err e={err} />

      <h2 className="section-title">{tr("Buying signal for this programme")}</h2>
      <div className="card">
        <SignalSummary signal={d.buying_signal} detailed />
        {d.buying_signal?.event_id && <Link to={`/accounts/${d.account.id}?event=${d.buying_signal.event_id}`}>{tr("Open this finding on the company record")}</Link>}
      </div>
      <div className={`verdict ${verdict(d).kind} mt-2`}><span className="k">{tr("Our read")}</span>{verdict(d).text}</div>
          <div className="action">
            <div className="eyebrow mb-2">{tr("Next action")}</div>
            {a ? <><div className="atype">{lab("action", a.action_type)}</div><div><b className="k">{tr("Goal")}</b> {a.objective}</div>{a.target_role && <div><b className="k">{tr("Who to look for")}</b> {a.target_role}</div>}<div><b className="k">{tr("Owner")}</b> {a.owner_name ?? (a.owner_id ? tr("Assigned") : tr("Unassigned"))}</div><div><b className="k">{tr("When")}</b> {a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</div>
              <div><b className="k">{tr("Good result")}</b> {a.expected_outcome}</div><div><b className="k">{tr("Stop when")}</b> {a.stop_condition}</div><div className="small muted mt-2">{actionReason(a)}</div></> : <span className="muted">{tr("Nothing to do right now.")}</span>}
          </div>
      <h2 className="section-title mt-2">{tr("Seven qualification checks")}</h2>
      <GateBar gates={d.gates} />
      <p className="small muted mt-2">{tr("The outside demand, supplier access, buyer and delivery checks often need a conversation. Use the score to decide what to review first.")}</p>

      <div className="ws mt-2">
        <div>
          <h2 className="section-title">{tr("What happened and why it matters")}</h2>
          <div className="card"><p className="mt-0"><b>{tr("What happened:")}</b> {d.what_happened.summary}</p><p><b>{tr("Why it matters:")}</b> {whyShort(d.why_it_matters)}</p><Evidence items={d.evidence} hypothesisId={id} /></div>

          <h2 className="section-title">{tr("What we know and what we are guessing")}</h2>
          <div className="card"><Badge kind={pb.meeting_brief.buyer_confirmed ? "ok" : "warn"}>{pb.meeting_brief.buyer_confirmed ? tr("The buyer has confirmed the need") : tr("The buyer has not confirmed the need yet")}</Badge>
            <h3>{tr("Guesses and gaps")}</h3><ul className="tight">{pb.meeting_brief.assumptions.map((x: string, i: number) => <li key={i}>{unknownText(x)}</li>)}</ul>
            <h3>{tr("Points against it")}</h3>{d.review?.contradicts?.length ? <ul className="tight">{d.review.contradicts.map((c: any, i: number) => <li key={i}><b>{c.question}</b> {c.detail}</li>)}</ul> : <p className="small muted">{tr("None found. High-priority items get an independent second look.")}</p>}
            {d.review?.falsifiers && <details><summary>{tr("What would prove this wrong?")}</summary><ul className="tight">{d.review.falsifiers.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></details>}</div>

          <h2 className="section-title">{tr("Who to talk to and how to get in")}</h2>
          <div className="card"><p className="small mt-0">{tr("Sales play:") + " "}<b>{pb.play.name ?? "none"}</b>{tr(". Usual buyer:") + " "}{String(pb.play.primary_buyer ?? tr("not known")).replace(/\.$/, "")}.{(() => { const r = String(pb.play.reason ?? "").replace(/\s*active signal\(s\) \[.*?\]\.?/i, "").replace(/[.\s]+$/, ""); return r ? ` ${r}.` : ""; })()}</p>
            <p className="small"><b>{tr("Not yet identified:")}</b> {pb.committee.missing.map((m: string) => lab("committee", m)).join(", ") || "none"} {pb.committee.single_threaded && <Badge kind="warn">{tr("Only one contact so far")}</Badge>}</p>
            <p className="small muted">{tr("A job title does not prove they hold the budget. We treat budget authority as a guess until the buyer confirms it.")}</p>
            <h3>{tr("Ways in, most reliable first")}</h3>{pb.access_routes.length ? <ol className="tight">{pb.access_routes.map((r: any, i: number) => <li key={i}>{humanize(r.kind)} {r.verified ? <Badge kind="ok">{tr("confirmed")}</Badge> : <Badge kind="warn">{tr("not confirmed")}</Badge>} {r.context_only && <Badge>{tr("background only")}</Badge>} <span className="muted">{r.evidence}</span></li>)}</ol> : <p className="small muted">{tr("No way in recorded yet. Attending the same event is not a relationship.")}</p>}</div>

          <h2 className="section-title">{tr("Questions to ask")}</h2>
          <div className="card"><ul className="tight">{pb.discovery_questions.map((q: any, i: number) => <li key={i}><Badge>{({ G1: tr("Account fit"), G2: tr("Real programme"), G3: tr("Engineering fit"), G4: tr("Outside demand"), G5: tr("Supplier access"), G6: tr("Buyer path"), G7: tr("Delivery"), play: tr("Sales play") } as Record<string, string>)[q.gate] ?? q.gate}</Badge> {q.question}</li>)}</ul></div>

          <h2 className="section-title">{tr("How this was scored")}</h2>
          <details className="card"><summary>{tr("Show the score breakdown and history")}</summary>
            <h3>{tr("Score breakdown")}</h3><p className="small muted">{tr("Six parts add up to the opportunity score out of 100. Scoring version") + " "}{d.why_this_recommendation.scoring_rule_version}.</p><div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Part · Points · Why")}><table><thead><tr><th>{tr("Part")}</th><th>{tr("Points")}</th><th>{tr("Why")}</th></tr></thead><tbody>{Object.entries(d.score_breakdown).map(([k, v]: any) => <tr key={k}><td>{({ icp: tr("Company fit"), engineering_relevance: tr("Relevance to our engineering"), external_demand: tr("Outside demand"), access: tr("Way in"), timing: "Timing", relationship_delivery: tr("Relationship and delivery") } as Record<string, string>)[k] ?? humanize(k)}</td><td>{v.points}/{v.max}</td><td>{v.why}</td></tr>)}</tbody></table></div>
            <h3>{tr("History")}</h3><ul className="tight small">{d.why_this_recommendation.transitions.map((t: any, i: number) => <li key={i}>{fmtDateTime(t.at)}: {lab("state", t.prior_state)} → {lab("state", t.new_state)} ({String(t.actor).startsWith("system:") ? "automatic" : t.actor}{t.reason ? `, ${String(t.reason).replace(/^auto: /, "")}` : ""})</li>)}</ul>
            <h3>{tr("Research done")}</h3><ul className="tight small">{d.why_this_recommendation.research.map((r: any, i: number) => <li key={i}>{r.research_question}. {r.method}, {r.status}{r.stop_reason ? ` (${humanize(r.stop_reason)})` : ""}</li>)}</ul></details>
        </div>

        <aside className="ws-side" aria-label={tr("Actions")}>
          <div className="card m-0">
            <div className="eyebrow">{tr("Draft a message")}</div>
            <p className="small mt-0 mb-3">{tr("This only writes a draft. Nothing is sent from here.")}</p>
            {can("draft.create") && <button onClick={() => run(async () => setDraft(await post(`/api/hypotheses/${id}/draft`, {})), "Draft written below")}>{tr("Write a draft")}</button>}
            {draft && (draft.status === "blocked" ? <div className="err" role="alert"><b>{tr("We could not write this draft")}</b><ul className="tight">{draft.blocked_reasons.map((b: any, i: number) => <li key={i}>{humanize(b.code)}: {b.detail}</li>)}</ul></div>
              : <div className="mt-3"><p className="mt-0 mb-2"><b>{draft.subject}</b></p><pre className="plain-pre">{draft.body}</pre><p className="small muted">{draft.note}</p></div>)}
          </div>
        </aside>
      </div>
    </>
  );
}
