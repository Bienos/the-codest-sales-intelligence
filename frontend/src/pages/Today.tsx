import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { Badge, Coverage, Err, Evidence, Fact, accessKind, demandKind, groupName, humanize, lab, priorityKind, unknownText, verdict } from "../components/ui";
import Prospects from "./Prospects";

const SECTIONS: [string, string][] = [["critical", "Urgent"], ["existing_accounts", "News on your accounts"], ["new_discoveries", "New companies worth a look"],
  ["relationships", "People to get back in touch with"], ["deal_blockers", "Deals that are stuck"], ["research_review", "Research to review"], ["public_tenders", "Public tenders"]];
const REASONS = ["not_relevant", "wrong_entity", "stale", "duplicate", "no_external_demand", "not_commercially_relevant", "owned_elsewhere", "other"];
const TENDER_NOTE = "Public bodies buy through formal tenders, so these follow their own rules and deadlines.";

function Deadlines() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api("/api/deadlines").then((r) => setItems(r.items.filter((x: any) => x.days_left <= 240).slice(0, 6))).catch(() => setItems([])); }, []);
  if (!items.length) return null;
  return <section><div className="section-title">Regulation deadlines</div><p className="small muted" style={{ margin: "-4px 0 10px" }}>Each date was checked against an official page and approved by a second person. Buyers facing a hard deadline tend to need extra delivery capacity.</p>
    <div className="card flush table-wrap"><table><thead><tr><th>Deadline</th><th>Regulation</th><th>Who it applies to</th><th>Date</th></tr></thead><tbody>
      {items.map((x) => <tr key={x.id}><td><a href={x.source_url} target="_blank" rel="noreferrer noopener">{x.title}</a></td><td className="small">{x.regime}</td><td className="small">{x.applies_to ?? "Not specified"}</td>
        <td><b>{fmtDate(x.deadline_date)}</b> <span className="small muted">in {x.days_left} days</span></td></tr>)}
    </tbody></table></div></section>;
}

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
          <Badge kind={priorityKind(c.priority)}>{lab("priority", c.priority)}</Badge>
          <Badge kind={c.account.in_master_list ? "ok" : "info"}>{c.account.in_master_list ? "On your lists" : "New company"}</Badge>
          {!c.account.ownership_verified && <Badge kind="warn">No owner in Pipedrive</Badge>}
        </div>
      </div>
      <div className="opp-grid">
        <div className="opp-body">
          {(() => { const v = verdict(c); return <div className={`verdict ${v.kind}`}><span className="k">What to do</span>{v.text}</div>; })()}
          {c.tender && <div className="tender"><Badge kind="bad">Tender</Badge> Closes <b>{fmtDateTime(c.tender.deadline_at)}</b> ({c.tender.deadline_tz ?? "timezone not given"}). We last checked it was still open on {fmtDateTime(c.tender.last_status_check_at)}.</div>}
          <p><span className="k">What happened</span>{c.what_happened.summary ?? "No summary"}
            <span className="prov"> Happened {fmtDate(c.what_happened.event_date)} · we saw it {fmtDate(c.what_happened.first_observed)}</span></p>
          {c.plain_reason && <p><span className="k">In short</span>{c.plain_reason}</p>}
          <p><span className="k">Why it matters</span>{c.why_it_matters}</p>
          <div className="facts">
            <Fact label="Will they use outside help?" value={lab("demand", c.external_demand_status)} kind={demandKind(c.external_demand_status)} />
            <Fact label="How we would get in" value={`${lab("route", c.route.type)} (${c.route.counterparty}): ${lab("access", c.route.access_status).toLowerCase()}`} kind={accessKind(c.route.access_status)} />
            <Fact label="Who buys" value={lab("buyer", c.buyer_status)} kind={c.buyer_status === "none" ? "warn" : "info"} />
            <Fact label="How well sourced" value={humanize(c.evidence_confidence)} kind={c.evidence_confidence === "high" ? "ok" : c.evidence_confidence === "low" ? "warn" : "info"} />
          </div>
          {c.reopened_after_dismissal && <p className="small"><Badge kind="info">Back again</Badge> You dismissed this as “{lab("dismiss", c.reopened_after_dismissal.previous_reason)}”. {c.reopened_after_dismissal.note}.</p>}
          {c.changed && Object.keys(c.changed).length > 0 && <p className="small muted">Changed since you last looked: {Object.entries(c.changed).map(([k, v]: any) => `${k} ${v[0] ?? "none"} to ${v[1]}`).join(" · ")}</p>}
          {(c.counterevidence?.length > 0 || c.unknowns?.length > 0) && (
            <div className="before-act">
              <div><span className="k">Points against it</span>{c.counterevidence?.length ? <ul>{c.counterevidence.map((x: any, i: number) => <li key={i}>{x.detail}</li>)}</ul> : <span className="small muted">None found</span>}</div>
              <div><span className="k">Not known yet</span><ul>{c.unknowns.slice(0, 4).map((u: string, i: number) => <li key={i}>{unknownText(u)}</li>)}</ul></div>
            </div>)}
          {open && <div style={{ marginTop: 14 }}><span className="k" style={{ font: "800 11px var(--font-head)", letterSpacing: ".12em", textTransform: "uppercase" }}>Original sources</span><Evidence items={c.evidence} hypothesisId={c.hypothesis_id} /></div>}
        </div>
        <aside className="opp-side" aria-label="Next action">
          <div className="eyebrow" style={{ margin: 0 }}>Next action</div>
          {a ? <>
            <div className="atype">{lab("action", a.action_type)}</div>
            <dl><dt>Goal</dt><dd>{a.objective}</dd><dt>Owner</dt><dd>{a.owner_id ? `Pipedrive user ${a.owner_id}` : a.owner_note ?? "Not assigned"}</dd><dt>Due</dt><dd>{a.due_at ? fmtDateTime(a.due_at) : a.trigger_condition}</dd></dl>
            <div className="why"><b>Why:</b> {a.reason}</div>
            {a.approval_required && <div className="small muted">A colleague must approve before anything is sent or changed.</div>}
          </> : <span className="muted small">Nothing to do right now.</span>}
          <div className="cta"><Link to={`/opportunities/${c.hypothesis_id}`}><button className="primary">Open opportunity</button></Link></div>
        </aside>
      </div>
      <div className="opp-foot">
        <button onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide sources" : "Show sources"}</button>
        <button onClick={() => decide("accepted")}>Pursue this</button>
        <button onClick={() => decide("monitor")}>Keep watching</button>
        <select aria-label="Reason for dismissing" value={reason} onChange={(e) => setReason(e.target.value)}><option value="">Why dismiss?</option>{REASONS.map((r) => <option key={r} value={r}>{lab("dismiss", r)}</option>)}</select>
        <button disabled={!reason} onClick={() => decide("dismissed", reason)}>Dismiss</button>
        <span style={{ marginLeft: "auto" }}><Link to={`/accounts/${c.account.id}`} className="small">Open account</Link></span>
      </div>
      <div style={{ padding: "0 24px" }}><Err e={err} /></div>
    </article>
  );
}

export default function Today() {
  const [d, setD] = useState<any>(null);
  const [ov, setOv] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const load = () => api("/api/today").then(setD).catch(setErr);
  useEffect(() => { load(); api("/api/overview").then(setOv).catch(() => undefined); }, []);
  if (err) return <div className="page"><Err e={err} /></div>;
  if (!d) return <div className="page muted">Loading Today…</div>;
  const all: any[] = Object.values(d.sections).flat() as any[];
  const count = (f: (c: any) => boolean) => all.filter(f).length;
  return (
    <>
      <header className="hero">
        <div className="eyebrow">{new Date(d.generated_at).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
        <h2>Today</h2>
        <p className="sub">{d.count === 0 ? (ov?.accounts ? `${ov.accounts.toLocaleString()} ${ov.accounts === 1 ? "account is" : "accounts are"} loaded from your workbooks. Nothing has been checked against a live source yet, so start with your shortlist below.` : "Nothing new needs your attention.") : `${d.count} ${d.count === 1 ? "thing" : "things"} to review. Each story is shown once. Anything you dismiss stays hidden unless it changes.`}</p>
        <div className="stats">
          <div className="stat urgent"><div className="n">{count((c) => c.priority === "urgent")}</div><div className="l">Urgent</div></div>
          <div className="stat"><div className="n">{d.count}</div><div className="l">To review</div></div>
          <div className="stat"><div className="n">{ov ? ov.shortlist : "·"}</div><div className="l">On your shortlist</div></div>
          <div className="stat"><div className="n">{ov ? ov.accounts.toLocaleString() : "·"}</div><div className="l">Accounts</div></div>
          <div className="stat"><div className="n">{ov ? ov.sources_live : d.coverage.monitored_sources}<span style={{ fontSize: 18, opacity: .7 }}>/{ov ? ov.sources_total : d.coverage.monitored_sources + d.coverage.pending_validation_sources}</span></div><div className="l">Sources switched on</div></div>
        </div>
      </header>
      <div className="page" style={{ paddingTop: 24 }}>
        {d.count > 0 && <Coverage c={d.coverage} empty={d.empty_state} />}
        {d.count === 0 && <Setup ov={ov} reason={d.empty_state} />}
        {d.hot_accounts?.length > 0 && <section><div className="section-title">Hot accounts</div><p className="small muted" style={{ margin: "-4px 0 10px" }}>Accounts with the strongest recent signals. Score adds up checked signals; different kinds count for more than repeats. It is not a probability.</p>
          <div className="card flush table-wrap"><table><thead><tr><th>Account</th><th title="Adds up recent checked signals. Different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability.">Signal score</th><th>Signals</th><th>Latest</th></tr></thead><tbody>
            {d.hot_accounts.map((a: any) => <tr key={a.id}><td><Link to={`/accounts/${a.id}`}>{a.display_name}</Link><div className="small muted">{[a.country_code, a.segment].filter(Boolean).join(" · ")}</div></td>
              <td><b>{a.score}</b> <Badge kind={a.band === "hot" ? "bad" : "warn"}>{a.band === "hot" ? "Hot" : "Warm"}</Badge></td>
              <td className="small">{a.signal_count} · {a.groups.map(groupName).join(", ")}</td><td className="small">{a.last_signal_at ? fmtDate(a.last_signal_at) : "None yet"}</td></tr>)}
          </tbody></table></div></section>}
        <Deadlines />
        {SECTIONS.map(([k, label]) => (d.sections[k]?.length ? <section key={k}><div className="section-title">{label}</div>{k === "public_tenders" && <p className="small muted" style={{ margin: "-4px 0 10px" }}>{TENDER_NOTE}</p>}{d.sections[k].map((c: any) => <Card key={c.hypothesis_id} c={c} reload={load} />)}</section> : null))}
        <Prospects />
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
      <p style={{ margin: "0 0 14px" }}>{reason ?? "Nothing has been checked against a source yet."} Until sources are switched on, a quiet screen says nothing about your market.</p>
      <ol className="steps">{steps.map((s) => <li key={s.t} className={s.done ? "done" : ""}><span className="setup-tick" aria-hidden="true">{s.done ? "✓" : "·"}</span><div><b>{s.t}</b><div className="small muted">{s.d}</div></div></li>)}</ol>
    </div>
  );
}
