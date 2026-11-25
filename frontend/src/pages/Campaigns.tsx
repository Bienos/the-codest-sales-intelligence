import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { number, tr } from "../locale";
import { Badge, Empty, Err, PageHeader, Skeleton, Strength, groupName, safeHref } from "../components/ui";
import { SignalOption, SignalTag, signalName } from "../components/Signals";
import { REGIONS } from "./targetMarket";

const STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "info"], pending_approval: ["Waiting for a manager", "warn"], approved: ["Approved, ready to push", "ok"], rejected: ["Not approved", "bad"],
  expired: ["Approval expired", "bad"], pushing: ["Pushing to Lemlist", "info"], push_failed: ["Push failed", "bad"], pushed: ["In Lemlist, not launched", "ok"], archived: ["Archived", ""],
};
const status = (s: string) => { const [l, k] = STATUS[s] ?? [s, ""]; return <Badge kind={k}>{tr(l)}</Badge>; };
const STEPS = ["Pick signals", "Check the people", "Check the copy", "Manager approves", "Push to Lemlist"];

function HowItWorks() {
  return <ol className="flow-mini" aria-label={tr("How a signal campaign works")}>
    {[["Pick signals", "For example a funding round or a payments programme. Every company you can see with that signal live and verified is included."],
      ["Check the people", "Up to five people per company with an email address, verified ones first. Anyone who opted out or bounced is left out, with the reason shown."],
      ["Check the copy", "Each email opens with that company's own verified news. Every lead's copy is checked against the brand rules."],
      ["Manager approves", "A manager approves the exact list and copy. Nothing changes after approval without a new one."],
      ["Push to Lemlist", "The campaign is created in Lemlist with its leads, but not launched. You press Launch in Lemlist. Replies and opt-outs come back here."]].map(([t, d], i) =>
      <li key={t}><span className="flow-n">{i + 1}</span><b>{tr(t)}</b><span className="small">{tr(d)}</span></li>)}
  </ol>;
}

export function CampaignList() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api("/api/signal-campaigns").then(setData).catch(setErr); }, []);
  return <>
    <PageHeader eyebrow="Outreach" title="Signal campaigns" sub="Write to every company showing the same buying signal, with each email opening on that company's own news."
      right={<Link className="btn primary" to="/campaigns/new">{tr("New campaign")}</Link>} />
    <HowItWorks />
    {data && (!data.outbound_enabled || !data.lemlist_configured) && <div className="callout mb-3">{!data.lemlist_configured ? tr("Lemlist is not connected yet, so campaigns can be prepared and approved but not pushed. An administrator sets LEMLIST_API_KEY.")
      : tr("Sending to Lemlist is switched off in this environment (RI_OUTBOUND_ENABLED). Campaigns can be prepared and approved.")}</div>}
    {err ? <Err e={err} /> : !data ? <Skeleton rows={3} /> : data.items.length === 0 ? <Empty title="No campaigns yet">{tr("Start one from a buying signal: on Today, pick a signal and press Start a campaign, or use New campaign.")}</Empty> :
      <div className="card flush table-wrap" tabIndex={0} role="region" aria-label={tr("Campaigns")}><table className="cards"><thead><tr>
        <th>{tr("Campaign")}</th><th>{tr("Signals")}</th><th>{tr("Audience")}</th><th>{tr("Status")}</th><th>{tr("Replies")}</th></tr></thead><tbody>
        {data.items.map((c: any) => <tr key={c.id}>
          <td><Link to={`/campaigns/${c.id}`}><b>{c.name}</b></Link><div className="small muted">{tr("by {}", c.created_by)} · {fmtDate(c.updated_at)}</div></td>
          <td data-label={tr("Signals")}><div className="sigtags">{c.rule_ids.map((r: string) => <SignalTag key={r} s={{ rule_id: r, name: r, strength: 0 }} />)}</div></td>
          <td data-label={tr("Audience")}>{tr("{} people at {} companies", number(c.people), number(c.accounts))}{c.accounts_without_contact > 0 && <div className="small muted">{tr("{} more companies have no email contact", number(c.accounts_without_contact))}</div>}</td>
          <td data-label={tr("Status")}>{status(c.status)}</td>
          <td data-label={tr("Replies")}>{c.status === "pushed" ? number(c.replies) : <span className="muted">-</span>}</td>
        </tr>)}
      </tbody></table></div>}
  </>;
}

export function NewCampaign() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [days, setDays] = useState(90);
  const [opts, setOpts] = useState<SignalOption[] | null>(null);
  const [picked, setPicked] = useState<string[]>((params.get("rules") ?? "").split(",").filter(Boolean));
  const [region, setRegion] = useState("");
  const [per, setPer] = useState(2);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setOpts(null); api(`/api/signal-campaigns/options?days=${days}`).then((d) => setOpts(d.items)).catch(setErr); }, [days]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const chosen = useMemo(() => (opts ?? []).filter((o) => picked.includes(o.rule_id)), [opts, picked]);
  const create = async () => {
    setBusy(true); setErr(null);
    try { const r = await post("/api/signal-campaigns", { rule_ids: picked, days, region, per_account: per, name: name.trim() || picked.map((id) => signalName(id)).join(" + ") }); nav(`/campaigns/${r.id}`); }
    catch (e) { setErr(e); setBusy(false); }
  };
  const sorted = (opts ?? []).slice().sort((a, b) => b.reachable_accounts - a.reachable_accounts || b.accounts - a.accounts);
  return <>
    <PageHeader eyebrow="Signal campaigns" title="New campaign" sub="Pick the buying signals to write about. The audience is every company you can see where one of them is live and verified." />
    <div className="card">
      <h2 className="title-sm">{tr("1. Pick signals")}</h2>
      <label className="small">{tr("Signals seen in the last")} <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 60, 90, 180].map((d) => <option key={d} value={d}>{tr("{} days", d)}</option>)}</select></label>
      {!opts ? <Skeleton rows={4} /> : <div className="sigpick mt-3">
        {sorted.map((o) => <label key={o.rule_id} className={`sigpick-i${picked.includes(o.rule_id) ? " on" : ""}${o.accounts === 0 ? " empty" : ""}`}>
          <input type="checkbox" checked={picked.includes(o.rule_id)} onChange={() => toggle(o.rule_id)} disabled={o.accounts === 0 && !picked.includes(o.rule_id)} />
          <span><span className="sigtile-g">{groupName(o.rule_id.slice(0, 3))} <Strength n={o.strength} /></span>
            <b>{signalName(o.rule_id, o.name)}</b>
            <span className="small">{o.accounts ? tr("{} companies, {} with an email contact", number(o.accounts), number(o.reachable_accounts)) : tr("Nothing right now")}</span>
            <span className="small muted">{tr(o.what_it_means)}</span></span>
        </label>)}
      </div>}
    </div>
    <div className="card">
      <h2 className="title-sm">{tr("2. Who to include")}</h2>
      <div className="verification-filters">
        <label>{tr("Region")}<select value={region} onChange={(e) => setRegion(e.target.value)}><option value="">{tr("All regions")}</option>{REGIONS.filter(([id]) => id !== "international").map(([id, l]) => <option key={id} value={id}>{tr(l)}</option>)}</select></label>
        <label>{tr("People per company")}<select value={per} onChange={(e) => setPer(Number(e.target.value))}>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>{tr("Name (optional)")}<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder={chosen.map((c) => signalName(c.rule_id)).join(" + ")} /></label>
      </div>
      <p className="small muted">{tr("Technology leaders are chosen first (for a payments signal, payments leaders). You can leave anyone out on the next screen.")}</p>
      <Err e={err} />
      <button className="primary" disabled={!picked.length || busy} onClick={create}>{busy ? tr("Building the audience") : tr("Build the audience")}</button>
    </div>
  </>;
}

function StepEditor({ steps, onSave, disabled }: { steps: any[]; onSave: (s: any[]) => Promise<void>; disabled: boolean }) {
  const [draft, setDraft] = useState(steps);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(steps), [steps]);
  const change = (i: number, k: string, v: string | number) => setDraft((d) => d.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  const dirty = JSON.stringify(draft) !== JSON.stringify(steps);
  return <div>
    {draft.map((s, i) => <fieldset key={i} className="stepedit" disabled={disabled}>
      <legend>{i === 0 ? tr("First email") : tr("Follow-up {}", i)}</legend>
      <div className="verification-filters">
        <label>{tr("Send on day")}<input type="number" min={0} max={60} value={s.day} onChange={(e) => change(i, "day", Number(e.target.value))} className="field-xs" /></label>
        <label className="grow">{tr("Subject")}<input value={s.subject} maxLength={200} onChange={(e) => change(i, "subject", e.target.value)} /></label>
      </div>
      <label>{tr("Text")}<textarea rows={8} value={s.body} onChange={(e) => change(i, "body", e.target.value)} /></label>
    </fieldset>)}
    {!disabled && <div className="row"><button className="primary" disabled={!dirty || busy} onClick={async () => { setBusy(true); try { await onSave(draft); } finally { setBusy(false); } }}>{tr("Save and check the copy")}</button>
      {dirty && <button onClick={() => setDraft(steps)}>{tr("Undo changes")}</button>}</div>}
  </div>;
}

export function CampaignDetail({ canDecide }: { canDecide: boolean }) {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [rev, setRev] = useState(0);
  useEffect(() => { api(`/api/signal-campaigns/${id}`).then(setD).catch(setErr); }, [id, rev]);
  const act = async (fn: () => Promise<any>, ok?: (r: any) => string) => {
    setBusy(true); setErr(null); setMsg("");
    try { const r = await fn(); if (ok) setMsg(ok(r)); setRev((n) => n + 1); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  if (err && !d) return <Err e={err} />;
  if (!d) return <Skeleton rows={6} />;
  const c = d.campaign, review = c.review ?? {};
  const included = d.members.filter((m: any) => m.included && m.person_id);
  const leftOut = d.members.filter((m: any) => !m.included && m.person_id);
  const noContact = d.members.filter((m: any) => !m.person_id);
  const issues: Record<string, any> = Object.fromEntries((review.leads_with_issues ?? []).map((x: any) => [x.member_id, x]));
  const stage = { draft: review.passed && included.length ? 3 : 2, pending_approval: 3, rejected: 2, expired: 2, approved: 4, pushing: 4, push_failed: 4, pushed: 5 }[c.status as string] ?? 1;
  return <>
    <p className="small"><Link to="/campaigns">← {tr("All campaigns")}</Link></p>
    <PageHeader eyebrow="Signal campaign" title={c.name} sub={<>{status(c.status)} <span className="small muted">{tr("Created by {} on {}", c.created_by, fmtDate(c.created_at))}</span></>} />
    <ol className="stepper" aria-label={tr("Progress")}>{STEPS.map((s, i) => <li key={s} className={i < stage ? "done" : i === stage ? "now" : ""}>{tr(s)}</li>)}</ol>
    {msg && <div className="okmsg" role="status">{msg}</div>}
    <Err e={err} />

    <div className="card campaign-actions">
      {c.status === "draft" && <>
        <p className="m-0">{review.passed ? tr("The copy passes the brand review for all {} people. Ask a manager to approve it.", included.length) : tr("Fix the copy or leave out the people with issues before asking for approval.")}</p>
        <div className="row mt-2">
          <button className="primary" disabled={busy || !review.passed || !included.length || !d.can_edit} onClick={() => act(() => post(`/api/signal-campaigns/${id}/request-approval`), (r) => tr("Sent to a manager for approval ({} people at {} companies).", r.members, r.accounts))}>{tr("Ask a manager to approve")}</button>
          <button disabled={busy || !d.can_edit} onClick={() => act(() => post(`/api/signal-campaigns/${id}/rebuild`), (r) => tr("Audience rebuilt: {} people.", r.included))}>{tr("Refresh the audience")}</button>
          <button disabled={busy || !d.can_edit} onClick={() => act(() => post(`/api/signal-campaigns/${id}/archive`), () => tr("Archived."))}>{tr("Archive")}</button>
        </div></>}
      {(c.status === "pending_approval" || c.status === "rejected" || c.status === "expired") && <>
        <p className="m-0">{c.status === "pending_approval" ? tr("Waiting for a manager. They see the exact people and copy.") : tr("This version was not approved. Move it back to draft to change it.")}</p>
        <div className="row mt-2">
          {c.status === "pending_approval" && canDecide && d.approval && <Link className="btn primary" to="/admin/approvals">{tr("Open Approvals")}</Link>}
          <button disabled={busy} onClick={() => act(() => post(`/api/signal-campaigns/${id}/withdraw`), () => tr("Back to draft."))}>{tr("Back to draft")}</button>
        </div></>}
      {(c.status === "approved" || c.status === "push_failed") && d.approval && <>
        <p className="m-0">{tr("Approved by {}. Pushing creates the campaign and its leads in Lemlist. It is not launched until you press Launch in Lemlist.", d.approval.approver)}</p>
        {!d.lemlist_configured && <p className="small">{tr("Lemlist is not connected yet (LEMLIST_API_KEY).")}</p>}
        {d.lemlist_configured && !d.outbound_enabled && <p className="small">{tr("Sending is switched off in this environment (RI_OUTBOUND_ENABLED).")}</p>}
        <div className="row mt-2">
          <button className="primary" disabled={busy || !d.lemlist_configured || !d.outbound_enabled} onClick={() => act(() => post(`/api/approvals/${d.approval.id}/execute`), (r) => tr("{} leads are in Lemlist. {}", r.leads, r.note ?? ""))}>{tr("Push to Lemlist")}</button>
          <button disabled={busy} onClick={() => act(() => post(`/api/signal-campaigns/${id}/withdraw`), () => tr("Back to draft."))}>{tr("Back to draft")}</button>
        </div></>}
      {c.status === "pushed" && <p className="m-0">{tr("In Lemlist since {}. Press Launch in Lemlist when you are ready. Replies, bounces and opt-outs appear below and in People.", fmtDateTime(c.pushed_at))}</p>}
    </div>

    <div className="card">
      <div className="row spread"><h2 className="title-sm m-0">{tr("Who gets it")}</h2>
        <span className="small muted">{tr("{} people at {} companies", included.length, new Set(included.map((m: any) => m.organization_id)).size)}</span></div>
      <div className="sigtags mt-2">{c.rule_ids.map((r: string) => <SignalTag key={r} s={{ rule_id: r, name: r, strength: 0 }} />)}
        <span className="small muted">{tr("seen in the last {} days", c.filters?.days ?? 90)}{c.filters?.region ? ` · ${tr(REGIONS.find(([k]) => k === c.filters.region)?.[1] ?? c.filters.region)}` : ""}</span></div>
      {included.length + leftOut.length === 0 ? <Empty title="Nobody can be included yet">{tr("None of these companies has a contact with an email address. Open a company and add people first.")}</Empty> :
        <div className="table-wrap mt-3" tabIndex={0} role="region" aria-label={tr("Audience")}><table className="cards audience"><thead><tr><th>{tr("Include")}</th><th>{tr("Person")}</th><th>{tr("Company and signal")}</th><th>{tr("Opening line")}</th><th>{tr("Status")}</th></tr></thead><tbody>
          {[...included, ...leftOut].map((m: any) => <tr key={m.id} className={m.included ? "" : "muted-row"}>
            <td><input type="checkbox" aria-label={tr("Include {}", m.name)} checked={m.included} disabled={!d.can_edit || busy} onChange={(e) => act(() => api(`/api/signal-campaigns/${id}/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ included: e.target.checked }) }))} /></td>
            <td data-label={tr("Person")}><b>{m.name}</b><div className="small muted">{m.title}</div></td>
            <td data-label={tr("Company and signal")}><Link to={`/accounts/${m.organization_id}`}>{m.company}</Link><div className="small">{signalName(m.signal.rule_id, m.signal.name)} · {m.signal.date ? fmtDate(m.signal.date) : ""}</div>
              {safeHref(m.signal.source_url) && <a className="small" href={safeHref(m.signal.source_url)} target="_blank" rel="noreferrer noopener">{tr("Source")} ↗</a>}</td>
            <td data-label={tr("Opening line")} className="small hook">{m.hook}
              {issues[m.id] && <div className="err small mt-1">{issues[m.id].breaks.map((b: any) => b.detail).join("; ")}</div>}
              {issues[m.id] && d.can_edit && <HookFix id={id!} m={m} done={() => setRev((n) => n + 1)} />}</td>
            <td data-label={tr("Status")}>{m.status && m.status !== "planned" ? <Badge kind={m.status === "replied" || m.status === "interested" ? "ok" : m.status === "bounced" || m.status === "unsubscribed" ? "bad" : "info"}>{tr(m.status.replace(/_/g, " "))}</Badge> : <span className="small muted">{m.included ? tr("Ready") : tr("Left out")}</span>}</td>
          </tr>)}
        </tbody></table></div>}
      {noContact.length > 0 && <details className="mt-3"><summary>{tr("{} companies with the signal but nobody to write to", noContact.length)}</summary>
        <ul className="tight small">{noContact.map((m: any) => <li key={m.id}><Link to={`/accounts/${m.organization_id}`}>{m.company}</Link>: {m.exclusion}</li>)}</ul></details>}
    </div>

    <div className="card">
      <h2 className="title-sm">{tr("The emails")}</h2>
      <p className="small muted">{tr("Use {} for the person's first name and {} for the company. {} is the opening line built from that company's verified news, and {} says why that kind of news matters. Lemlist fills them in for each person.", "{{firstName}}", "{{companyName}}", "{{signalHook}}", "{{signalAngle}}")}</p>
      {review.template && !review.template.passed && <div className="err"><b>{tr("Brand review")}</b><ul className="tight">{review.template.breaks.map((b: any, i: number) => <li key={i}>{tr("Email {}", b.step)}: {b.detail}</li>)}</ul></div>}
      {review.passed && <div className="okmsg">{tr("Passes the brand review for every included person.")}</div>}
      <div className="campaign-copy">
        <StepEditor steps={c.steps} disabled={!d.can_edit} onSave={(steps) => act(() => put(`/api/signal-campaigns/${id}/steps`, { steps }), (r) => r.review.passed ? tr("Saved. The copy passes the brand review.") : tr("Saved, but the copy does not pass the brand review yet."))} />
        <aside className="preview" aria-label={tr("Preview")}>
          <div className="eyebrow">{tr("Preview for {}", included[0]?.name ?? tr("the first person"))}</div>
          {d.preview.map((p: any, i: number) => <div key={i} className="mail"><div className="small muted">{tr("Day {}", p.day)}</div><b>{p.subject}</b><div className="mail-body">{p.body}</div></div>)}
        </aside>
      </div>
    </div>
  </>;
}

function HookFix({ id, m, done }: { id: string; m: any; done: () => void }) {
  const [v, setV] = useState(m.hook ?? "");
  const [err, setErr] = useState<unknown>(null);
  return <div className="mt-1"><textarea rows={2} value={v} onChange={(e) => setV(e.target.value)} aria-label={tr("Opening line for {}", m.name)} />
    <button type="button" onClick={async () => { setErr(null); try { await api(`/api/signal-campaigns/${id}/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ hook: v }) }); done(); } catch (e) { setErr(e); } }}>{tr("Save opening line")}</button><Err e={err} /></div>;
}
