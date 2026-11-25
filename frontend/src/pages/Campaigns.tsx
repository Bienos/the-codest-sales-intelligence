import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { counted, number, tr } from "../locale";
import { Badge, Empty, Err, Skeleton, Strength, groupName, safeHref } from "../components/ui";
import { SignalOption, signalName } from "../components/Signals";
import { REGIONS } from "./targetMarket";
import "../styles/campaigns.css";

/* A campaign writes to every company showing the same buying signal. It moves through four stages:
   Draft (you check the people and the emails) -> Approval (someone else approves) -> In Lemlist (created there, you press Launch)
   -> Running, then Done (Lemlist sends; opens, replies, bounces and opt-outs come back every hour). */

type Stage = "draft" | "approval" | "lemlist" | "running" | "done" | "archived";
type Tone = "now" | "wait" | "bad" | "ok" | "";
type Results = { in_lemlist: number; sent: number; opened: number; replied: number; interested: number; bounced: number; unsubscribed: number;
  finished: number; first_sent_at: string | null; last_activity_at: string | null; done: boolean };

const LEMLIST_APP = "https://app.lemlist.com/";
const STAGES: { key: Stage; label: string }[] = [
  { key: "draft", label: "Draft" }, { key: "approval", label: "Approval" }, { key: "lemlist", label: "In Lemlist" }, { key: "running", label: "Running" }];
const stageAt = (s: Stage) => (s === "done" ? 3 : Math.max(0, STAGES.findIndex((x) => x.key === s)));

/** Where a campaign is and what that means, in a few words. */
function where(c: { status: string; stage: Stage; review_passed?: boolean; issues?: number; people: number }): { tone: Tone; caption: string } {
  switch (c.status) {
    case "draft": return c.people === 0 ? { tone: "bad", caption: tr("Nobody to write to yet") }
      : c.review_passed === false || (c.issues ?? 0) > 0 ? { tone: "bad", caption: tr("Some emails need fixing") } : { tone: "now", caption: tr("Ready to send for approval") };
    case "pending_approval": return { tone: "wait", caption: tr("Waiting for approval") };
    case "rejected": return { tone: "bad", caption: tr("Not approved") };
    case "expired": return { tone: "bad", caption: tr("The approval request ran out") };
    case "approved": return { tone: "now", caption: tr("Approved: send it to Lemlist") };
    case "pushing": return { tone: "wait", caption: tr("Being sent to Lemlist") };
    case "push_failed": return { tone: "bad", caption: tr("Sending to Lemlist failed") };
    case "archived": return { tone: "", caption: tr("Archived") };
    default: return c.stage === "done" ? { tone: "ok", caption: tr("Done: everyone has had the emails") }
      : c.stage === "running" ? { tone: "wait", caption: tr("Emails are going out") } : { tone: "now", caption: tr("Press Launch in Lemlist") };
  }
}

/** The four stages as one strip. `small` is the version on a campaign card. */
function StageStrip({ stage, tone, caption, dates, small }: { stage: Stage; tone: Tone; caption: string; dates?: (string | null)[]; small?: boolean }) {
  const at = stageAt(stage);
  const strip = <ol className={`cg-strip${small ? " cg-strip-s" : ""}`} aria-label={tr("Stage: {}", caption)}>
    {STAGES.map((s, i) => {
      const state = i < at || stage === "done" ? "done" : i === at ? `at t-${tone || "none"}` : "todo";
      return <li key={s.key} className={`cg-st ${state}`} aria-current={i === at ? "step" : undefined}>
        <span className="cg-st-bar" aria-hidden="true" />
        <span className="cg-st-l">{i === 3 && stage === "done" ? tr("Done") : tr(s.label)}</span>
        {!small && <span className="cg-st-d">{i === at ? caption : dates?.[i] ?? ""}</span>}
      </li>;
    })}
  </ol>;
  return small ? strip : <div className="cg-stagebox">{strip}<p className="cg-strip-cap" aria-hidden="true">{caption}</p></div>;
}

/** "company" or "companies" (and the Polish forms) for a count shown in bold next to it. */
const noun = (n: number, en: [string, string], pl: [string, string, string]) => counted(n, en, pl).slice(number(n).length + 1);
const COMPANY: [[string, string], [string, string, string]] = [["company", "companies"], ["firma", "firmy", "firm"]];
const PERSON: [[string, string], [string, string, string]] = [["person", "people"], ["osoba", "osoby", "osób"]];
const Count = ({ n, of }: { n: number; of: typeof COMPANY }) => <span><b>{number(n)}</b> {noun(n, of[0], of[1])}</span>;

const SigChip = ({ id }: { id: string }) => <span className="cg-sig"><i>{groupName(id.slice(0, 3))}</i>{signalName(id)}</span>;
const regionName = (r?: string) => (r ? tr(REGIONS.find(([k]) => k === r)?.[1] ?? r) : tr("All regions"));

/* ------------------------------------------------------------------ how it works */
const HOW: { stage: string; title: string; text: string }[] = [
  { stage: "Draft", title: "Pick a buying signal", text: "The reason to write now, such as a funding round or a payments change." },
  { stage: "", title: "The app builds the list", text: "Every company with that signal, and up to five people there with an email address." },
  { stage: "", title: "You check it", text: "Leave people in or out and read the emails. Each one opens with that company's own news." },
  { stage: "Approval", title: "Someone else approves", text: "A manager checks the exact people and emails. Nothing is sent yet." },
  { stage: "In Lemlist", title: "It goes to Lemlist", text: "The app creates the campaign in Lemlist with everyone in it. You press Launch there." },
  { stage: "Running", title: "Replies come back", text: "Opens, replies, bounces and opt-outs show here, checked every hour." },
];

function HowItWorks() {
  const key = "ri_campaigns_how";
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== "hidden"; } catch { return true; } });
  const toggle = () => { const next = !open; setOpen(next); try { localStorage.setItem(key, next ? "shown" : "hidden"); } catch { /* storage unavailable */ } };
  return <section className={`cg-how${open ? "" : " closed"}`} aria-labelledby="cg-how-h">
    <div className="cg-how-top">
      <h2 id="cg-how-h">{tr("How a campaign works")}</h2>
      <button type="button" className="linkish" aria-expanded={open} onClick={toggle}>{open ? tr("Hide") : tr("Show the six steps")}</button>
    </div>
    {open && <>
      <div className="cg-how-bands" aria-hidden="true">
        <span style={{ gridColumn: "1 / span 3" }}>{tr("Draft")}</span><span>{tr("Approval")}</span><span>{tr("In Lemlist")}</span><span>{tr("Running")}</span>
      </div>
      <ol className="cg-how-rail">
        {HOW.map((s, i) => <li key={s.title} className="cg-how-step">
          {s.stage && <span className="cg-how-stage">{tr(s.stage)}</span>}
          <span className="cg-how-n" aria-hidden="true">{i + 1}</span>
          <b>{tr(s.title)}</b>
          <span>{tr(s.text)}</span>
        </li>)}
      </ol>
    </>}
  </section>;
}

function OutboundNotice({ configured, enabled }: { configured: boolean; enabled: boolean }) {
  if (configured && enabled) return null;
  return <p className="cg-note" role="note">{!configured
    ? tr("Lemlist is not connected yet. You can prepare and approve campaigns, but not send them to Lemlist until an administrator connects it under Administration, Connected services.")
    : tr("Sending to Lemlist is switched off on this copy of the app. You can prepare and approve campaigns here.")}</p>;
}

/* ------------------------------------------------------------------ list */
const FILTERS: { key: string; label: string; match: (s: Stage) => boolean }[] = [
  { key: "", label: "All", match: () => true },
  { key: "draft", label: "Draft", match: (s) => s === "draft" },
  { key: "approval", label: "Approval", match: (s) => s === "approval" },
  { key: "lemlist", label: "In Lemlist", match: (s) => s === "lemlist" },
  { key: "running", label: "Running or done", match: (s) => s === "running" || s === "done" },
];

/** The one thing to do next on a campaign card. */
function nextStep(c: any): { label: string; to: string; external?: boolean; primary: boolean } {
  const to = `/campaigns/${c.id}`;
  switch (c.status) {
    case "draft": return c.people === 0 ? { label: tr("Refresh the list"), to: `${to}#next`, primary: false }
      : !c.review_passed ? { label: tr("Fix the emails"), to: `${to}#emails`, primary: true } : { label: tr("Send for approval"), to: `${to}#next`, primary: true };
    case "pending_approval": return c.can_approve ? { label: tr("Approve or reject"), to: `${to}#next`, primary: true } : { label: tr("Open"), to, primary: false };
    case "rejected": case "expired": return { label: tr("Move it back to draft"), to: `${to}#next`, primary: true };
    case "approved": return { label: tr("Send to Lemlist"), to: `${to}#next`, primary: true };
    case "push_failed": return { label: tr("See what went wrong"), to: `${to}#next`, primary: true };
    case "pushing": return { label: tr("Open"), to, primary: false };
    default: return c.stage === "lemlist" ? { label: tr("Launch in Lemlist"), to: LEMLIST_APP, external: true, primary: true }
      : { label: c.stage === "done" ? tr("See the results") : tr("See the replies"), to: `${to}#results`, primary: false };
  }
}

function CampaignCard({ c }: { c: any }) {
  const w = where(c);
  const go = nextStep(c);
  const r: Results = c.results;
  const f = c.filters ?? {};
  return <li className="cg-card">
    <div className="cg-card-main">
      <h3><Link to={`/campaigns/${c.id}`}>{c.name}</Link></h3>
      <div className="cg-sigs-row">{c.rule_ids.map((id: string) => <SigChip key={id} id={id} />)}</div>
      <div className="small muted">{tr("News from the last {} days", f.days ?? 90)} · {regionName(f.region)} · {tr("by {}", c.created_by)}</div>
    </div>
    <div className="cg-card-stage">
      <StageStrip stage={c.stage} tone={w.tone} caption={w.caption} small />
      <div className={`cg-cap t-${w.tone || "none"}`}>{w.caption}</div>
      <div className="cg-nums">
        <Count n={c.accounts} of={COMPANY} />
        <span><Count n={c.people} of={PERSON} /> <span className="cg-mailok">{tr("Email ✓")}</span></span>
        {c.accounts_without_contact > 0 && <span className="muted">{tr("{} with nobody to email", number(c.accounts_without_contact))}</span>}
      </div>
      {c.status === "pushed" && r.sent > 0 && <div className="cg-nums cg-res">
        <span><b>{number(r.sent)}</b> {tr("emailed")}</span><span><b>{number(r.opened)}</b> {tr("opened")}</span><span><b>{number(r.replied)}</b> {tr("replied")}</span>
        {r.bounced + r.unsubscribed > 0 && <span className="muted">{tr("{} bounced or opted out", number(r.bounced + r.unsubscribed))}</span>}
      </div>}
    </div>
    <div className="cg-card-go">
      {go.external ? <a className={`btn${go.primary ? " primary" : ""}`} href={go.to} target="_blank" rel="noreferrer noopener">{go.label} ↗</a>
        : <Link className={`btn${go.primary ? " primary" : ""}`} to={go.to}>{go.label}</Link>}
      <span className="small muted">{tr("Updated {}", fmtDate(c.updated_at))}</span>
    </div>
  </li>;
}

/** No campaign yet: the signals live right now are the obvious place to start. */
function StartFromSignals() {
  const [opts, setOpts] = useState<SignalOption[] | null>(null);
  useEffect(() => { api("/api/signal-campaigns/options?days=90").then((d) => setOpts(d.items)).catch(() => setOpts([])); }, []);
  const live = (opts ?? []).filter((o) => o.reachable_accounts > 0).sort((a, b) => b.reachable_accounts - a.reachable_accounts).slice(0, 4);
  return <div className="cg-empty">
    <b>{tr("No campaigns yet")}</b>
    <p>{tr("Start from a buying signal. The app finds the companies that show it and the people there you can email.")}</p>
    {!opts ? <Skeleton rows={1} /> : live.length === 0 ? <p className="small muted">{tr("No company has a buying signal from the last 90 days with someone to email yet. Signals appear as news is read and checked.")} <Link to="/signals">{tr("See buying signals")}</Link></p> :
      <ul className="cg-empty-sigs">{live.map((o) => <li key={o.rule_id}>
        <Link to={`/campaigns/new?rules=${o.rule_id}`}><SigChip id={o.rule_id} /><span className="small">{tr("{} companies with someone to email", number(o.reachable_accounts))}</span><span aria-hidden="true">→</span></Link>
      </li>)}</ul>}
  </div>;
}

export function CampaignList() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [params, setParams] = useSearchParams();
  const filter = params.get("stage") ?? "";
  useEffect(() => { api("/api/signal-campaigns").then(setData).catch(setErr); }, []);
  const items: any[] = data?.items ?? [];
  const f = FILTERS.find((x) => x.key === filter) ?? FILTERS[0];
  const shown = items.filter((c) => f.match(c.stage));
  return <>
    <div className="page-head row spread end cg-head">
      <div>
        <div className="eyebrow">{tr("Outreach")}</div>
        <h1>{tr("Campaigns")}</h1>
        <p className="sub">{tr("Email every company that shows the same buying signal. Each email opens with that company's own news, and Lemlist does the sending.")}</p>
      </div>
      <Link className="btn primary" to="/campaigns/new">{tr("New campaign")}</Link>
    </div>
    <HowItWorks />
    {data && <OutboundNotice configured={data.lemlist_configured} enabled={data.outbound_enabled} />}
    {err ? <Err e={err} /> : !data ? <Skeleton rows={3} /> : items.length === 0 ? <StartFromSignals /> : <>
      <div className="cg-filter" role="group" aria-label={tr("Show campaigns by stage")}>
        {FILTERS.map((x) => { const n = items.filter((c) => x.match(c.stage)).length;
          return <button key={x.key} type="button" aria-pressed={x.key === f.key} onClick={() => setParams(x.key ? { stage: x.key } : {}, { replace: true })}>
            {tr(x.label)} <b>{number(n)}</b></button>; })}
      </div>
      {shown.length === 0 ? <Empty title="Nothing at this stage">{tr("Pick another stage above.")}</Empty>
        : <ul className="cg-list" aria-label={tr("Campaigns")}>{shown.map((c) => <CampaignCard key={c.id} c={c} />)}</ul>}
    </>}
  </>;
}

/* ------------------------------------------------------------------ new campaign: a guided form */
const WIZARD = ["Why now", "Which companies", "Who gets it", "The emails", "Approval"];

function WizardBar({ at, done, onGo }: { at: number; done: number; onGo?: (i: number) => void }) {
  return <ol className="cg-wiz-bar" aria-label={tr("Steps")}>
    {WIZARD.map((s, i) => {
      const state = i < at ? "done" : i === at ? "at" : "todo";
      const can = onGo && i <= done && i !== at && i < 3;
      return <li key={s} className={`cg-wz ${state}`} aria-current={i === at ? "step" : undefined}>
        {can ? <button type="button" onClick={() => onGo!(i)}><span className="cg-wz-n">{i < at ? "✓" : i + 1}</span><span className="cg-wz-l">{tr(s)}</span></button>
          : <span className="cg-wz-in"><span className="cg-wz-n">{i < at ? "✓" : i + 1}</span><span className="cg-wz-l">{tr(s)}</span></span>}
      </li>;
    })}
  </ol>;
}

type Preview = { companies: number; reachable: number; people: number; left_out: number; capped: boolean;
  items: { organization_id: string; company: string; country: string | null; signal: { rule_id: string; name: string; date: string | null; source_url: string | null }; hook: string;
    people: { name: string; title: string | null }[]; reason: string | null }[] };

export function NewCampaign() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [step, setStep] = useState(0);
  const [days, setDays] = useState(90);
  const [opts, setOpts] = useState<SignalOption[] | null>(null);
  const [picked, setPicked] = useState<string[]>((params.get("rules") ?? "").split(",").filter(Boolean));
  const [region, setRegion] = useState("");
  const [per, setPer] = useState(2);
  const [name, setName] = useState("");
  const [pv, setPv] = useState<Preview | null>(null);
  const [pvBusy, setPvBusy] = useState(false);
  const [pvErr, setPvErr] = useState<unknown>(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const top = useRef<HTMLHeadingElement>(null);

  useEffect(() => { setOpts(null); api(`/api/signal-campaigns/options?days=${days}`).then((d) => setOpts(d.items)).catch(setErr); }, [days]);
  const key = `${picked.join(",")}|${days}|${region}|${per}`;
  useEffect(() => {
    if (!picked.length) { setPv(null); return; }
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      setPvBusy(true); setPvErr(null);
      api<Preview>("/api/signal-campaigns/preview", { method: "POST", body: JSON.stringify({ rule_ids: picked, days, region, per_account: per }), signal: ctl.signal })
        .then(setPv).catch((e) => { if (!ctl.signal.aborted) setPvErr(e); }).finally(() => { if (!ctl.signal.aborted) setPvBusy(false); });
    }, 250);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [key]);

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const sorted = (opts ?? []).slice().sort((a, b) => b.reachable_accounts - a.reachable_accounts || b.accounts - a.accounts || b.strength - a.strength);
  const live = sorted.filter((o) => o.accounts > 0 || picked.includes(o.rule_id));
  const quiet = sorted.filter((o) => o.accounts === 0 && !picked.includes(o.rule_id));
  const defaultName = picked.map((id) => signalName(id)).join(" + ");
  const go = (i: number) => { setStep(i); window.setTimeout(() => top.current?.focus(), 0); };
  const create = async () => {
    setBusy(true); setErr(null);
    try { const r = await post("/api/signal-campaigns", { rule_ids: picked, days, region, per_account: per, name: name.trim() || undefined }); nav(`/campaigns/${r.id}#emails`); }
    catch (e) { setErr(e); setBusy(false); }
  };
  const reached = picked.length ? 2 : 0;
  const shownItems = (pv?.items ?? []).filter((i) => i.people.length);
  const leftOut = (pv?.items ?? []).filter((i) => !i.people.length);

  return <>
    <p className="small cg-back"><Link to="/campaigns">← {tr("All campaigns")}</Link></p>
    <div className="page-head cg-head">
      <div className="eyebrow">{tr("New campaign")}</div>
      <h1>{tr("Start a campaign")}</h1>
      <p className="sub">{tr("Five short steps. Nothing is sent until someone else approves it and you press Launch in Lemlist.")}</p>
    </div>
    <WizardBar at={step} done={reached} onGo={go} />
    <div className="cg-wiz">
      <section className="cg-wiz-main" aria-labelledby="cg-step-h">
        {step === 0 && <>
          <h2 id="cg-step-h" ref={top} tabIndex={-1}><span className="cg-step-n">1</span>{tr("Why now: pick a buying signal")}</h2>
          <p className="cg-lede">{tr("A buying signal is recent news that suggests a company needs engineering help now. Pick one or more. Every email opens with that company's own news.")}</p>
          <label className="cg-inline">{tr("News from the last")}
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 60, 90, 180].map((d) => <option key={d} value={d}>{tr("{} days", d)}</option>)}</select></label>
          {!opts ? <Skeleton rows={4} /> : live.length === 0 ? <p className="cg-note">{tr("No company has a buying signal in this window yet. Try a longer window, or come back when more news has been checked.")}</p> :
            <fieldset className="cg-sigpick"><legend className="cg-vh">{tr("Buying signals")}</legend>
              {live.map((o) => <SignalPick key={o.rule_id} o={o} on={picked.includes(o.rule_id)} toggle={toggle} />)}</fieldset>}
          {quiet.length > 0 && <details className="cg-quiet"><summary>{tr("Signals with no company right now: {}", quiet.length)}</summary>
            <p className="small muted">{tr("Nothing to write about for these in this window.")} {quiet.map((o) => signalName(o.rule_id, o.name)).join(", ")}</p></details>}
          <div className="cg-wiz-go">
            <button type="button" className="primary" disabled={!picked.length} onClick={() => go(1)}>{tr("Next: which companies")} →</button>
            {!picked.length && <span className="small muted">{tr("Pick at least one signal.")}</span>}
          </div>
        </>}

        {step === 1 && <>
          <h2 id="cg-step-h" ref={top} tabIndex={-1}><span className="cg-step-n">2</span>{tr("Which companies")}</h2>
          <p className="cg-lede">{tr("Narrow the list if you want to. The defaults suit most campaigns.")}</p>
          <fieldset className="cg-choice"><legend>{tr("Region")}</legend>
            <div className="cg-chips">{[["", "All regions"], ...REGIONS.filter(([id]) => id !== "international")].map(([id, l]) =>
              <label key={id || "all"}><input type="radio" name="cg-region" value={id} checked={region === id} onChange={() => setRegion(id)} />{tr(l)}</label>)}</div>
          </fieldset>
          <fieldset className="cg-choice"><legend>{tr("People per company")}</legend>
            <div className="cg-chips">{[1, 2, 3, 4, 5].map((n) => <label key={n}><input type="radio" name="cg-per" value={n} checked={per === n} onChange={() => setPer(n)} />{n}</label>)}</div>
            <p className="small muted">{tr("Technology leaders are picked first: CTO, head of engineering, CIO. For a payments signal, payments leaders first; for an AI signal, data and AI leaders.")}</p>
          </fieldset>
          <label className="cg-field">{tr("Campaign name (optional)")}
            <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder={defaultName} /></label>
          <div className="cg-wiz-go">
            <button type="button" onClick={() => go(0)}>← {tr("Back")}</button>
            <button type="button" className="primary" onClick={() => go(2)}>{tr("Next: see who gets it")} →</button>
          </div>
        </>}

        {step === 2 && <>
          <h2 id="cg-step-h" ref={top} tabIndex={-1}><span className="cg-step-n">3</span>{tr("Who gets it")}</h2>
          <p className="cg-lede">{tr("This is the list the campaign starts with. Each company is here because of the news shown. You can still leave anyone out after saving.")}</p>
          <Err e={pvErr} />
          {!pv ? <Skeleton rows={4} /> : <div className={pvBusy ? "cg-loading" : ""}>
            {shownItems.length === 0 ? <p className="cg-note">{tr("None of these companies has a person with an email address yet. Open a company and add people first, or pick another signal.")}</p> :
              <ul className="cg-pv">{(showAll ? shownItems : shownItems.slice(0, 8)).map((i) => <li key={i.organization_id}>
                <div className="cg-pv-co"><div><b>{i.company}</b>{i.country && <span className="small muted"> · {i.country}</span>}</div>
                  <div className="cg-pv-sig"><SigChip id={i.signal.rule_id} />{i.signal.date && <span className="small muted">{fmtDate(i.signal.date)}</span>}</div></div>
                <p className="cg-hook">{i.hook}</p>
                <ul className="cg-pv-people">{i.people.map((x) => <li key={x.name}><b>{x.name}</b>{x.title && <span className="muted">, {x.title}</span>} <span className="cg-mailok">{tr("Email ✓")}</span></li>)}</ul>
              </li>)}</ul>}
            {shownItems.length > 8 && <button type="button" className="linkish mt-2" onClick={() => setShowAll(!showAll)}>{showAll ? tr("Show fewer") : tr("Show all {} companies", shownItems.length)}</button>}
            {leftOut.length > 0 && <details className="cg-quiet"><summary>{tr("Companies with the signal but nobody to write to: {}", leftOut.length)}</summary>
              <ul className="tight small">{leftOut.map((i) => <li key={i.organization_id}><b>{i.company}</b>: {i.reason}</li>)}</ul></details>}
          </div>}
          <Err e={err} />
          <div className="cg-wiz-go">
            <button type="button" onClick={() => go(1)}>← {tr("Back")}</button>
            <button type="button" className="primary" disabled={busy || !picked.length} onClick={create}>{busy ? tr("Saving the draft") : tr("Save as a draft and check the emails")} →</button>
          </div>
          <p className="small muted">{tr("Next you read the three emails and send the campaign for approval. Nothing is sent from this step.")}</p>
        </>}
      </section>

      <aside className="cg-sum" aria-label={tr("Your campaign so far")}>
        <div className="eyebrow">{tr("Your campaign")}</div>
        <div className="cg-sum-sigs">{picked.length ? picked.map((id) => <SigChip key={id} id={id} />) : <span className="small muted">{tr("No signal picked yet")}</span>}</div>
        <dl className={pvBusy ? "cg-loading" : ""}>
          <div><dt>{tr("Companies with the signal")}</dt><dd>{pv ? number(pv.companies) : "–"}</dd></div>
          <div><dt>{tr("With someone to email")}</dt><dd>{pv ? number(pv.reachable) : "–"}</dd></div>
          <div><dt>{tr("People")} <span className="cg-mailok">{tr("Email ✓")}</span></dt><dd>{pv ? number(pv.people) : "–"}</dd></div>
        </dl>
        <p className="small m-0">{tr("News from the last {} days", days)} · {regionName(region)} · {tr("up to {} per company", per)}</p>
        {pv?.capped && <p className="small muted">{tr("Only the first 200 companies are used.")}</p>}
      </aside>
    </div>
  </>;
}

function SignalPick({ o, on, toggle }: { o: SignalOption; on: boolean; toggle: (id: string) => void }) {
  return <label className={`cg-sigopt${on ? " on" : ""}`}>
    <input type="checkbox" checked={on} onChange={() => toggle(o.rule_id)} />
    <span className="cg-sigopt-b">
      <span className="cg-sigopt-g">{groupName(o.rule_id.slice(0, 3))} <Strength n={o.strength} /></span>
      <b>{signalName(o.rule_id, o.name)}</b>
      <span className="small">{tr("{} companies, {} with someone to email", number(o.accounts), number(o.reachable_accounts))}</span>
      <span className="small muted">{tr(o.what_it_means)}</span>
    </span>
  </label>;
}

/* ------------------------------------------------------------------ one campaign */
const PERSON_STATE: Record<string, [string, string]> = {
  planned: ["Ready", ""], pushed: ["In Lemlist", "info"], sent: ["Emailed", "info"], opened: ["Opened", "info"], clicked: ["Clicked a link", "info"],
  replied: ["Replied", "ok"], interested: ["Interested", "ok"], not_interested: ["Not interested", ""], bounced: ["Bounced", "bad"], unsubscribed: ["Opted out", "bad"],
  skipped_in_lemlist: ["Skipped: already in another Lemlist campaign", ""], failed: ["Not added", "bad"],
};
const PLACEHOLDERS: [string, string][] = [["{{firstName}}", "first name"], ["{{companyName}}", "company name"], ["{{signalHook}}", "the company's news"], ["{{signalAngle}}", "why that news matters"]];

export function CampaignDetail({ canDecide }: { canDecide: boolean }) {
  const { id } = useParams();
  const loc = useLocation();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [rev, setRev] = useState(0);
  const scrolled = useRef(false);
  useEffect(() => { api(`/api/signal-campaigns/${id}`).then(setD).catch(setErr); }, [id, rev]);
  useEffect(() => {
    if (!d || scrolled.current || !loc.hash) return;
    scrolled.current = true;
    window.setTimeout(() => document.getElementById(loc.hash.slice(1))?.scrollIntoView({ block: "start" }), 0);
  }, [d, loc.hash]);
  const act = async (fn: () => Promise<any>, ok?: (r: any) => string) => {
    setBusy(true); setErr(null); setMsg("");
    try { const r = await fn(); if (ok) setMsg(ok(r)); setRev((n) => n + 1); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const groups = useMemo(() => {
    type Group = { org: string; company: string; country: string | null; signal: any; people: any[]; reason: string | null };
    const out = new Map<string, Group>();
    for (const m of (d?.members ?? []) as any[]) {
      const g: Group = out.get(m.organization_id) ?? { org: m.organization_id, company: m.company, country: m.country, signal: m.signal, people: [], reason: null };
      if (m.person_id) g.people.push(m); else g.reason = m.exclusion;
      out.set(m.organization_id, g);
    }
    return [...out.values()];
  }, [d]);
  if (err && !d) return <Err e={err} />;
  if (!d) return <Skeleton rows={6} />;

  const c = d.campaign, review = c.review ?? {}, s = d.summary, r: Results = d.results, a = d.approval;
  // a problem in the shared template shows once, under The emails; a company only lists what is wrong with its own news line
  const tplCodes = new Set((review.template?.breaks ?? []).map((b: any) => b.code));
  const issues: Record<string, any> = Object.fromEntries((review.leads_with_issues ?? [])
    .map((x: any) => [x.member_id, { ...x, breaks: x.breaks.filter((b: any) => !tplCodes.has(b.code)) }]).filter(([, x]: any) => x.breaks.length));
  const issueCount = (review.template?.breaks ?? []).length + Object.keys(issues).length;
  const w = where({ status: c.status, stage: c.stage, review_passed: review.passed, issues: issueCount, people: s.people_in });
  const withPeople = groups.filter((g) => g.people.length);
  const nobody = groups.filter((g) => !g.people.length);
  const dates = [tr("Started {}", fmtDate(c.created_at)), a ? (a.approved_at ? tr("Approved {}", fmtDate(a.approved_at)) : tr("Asked {}", fmtDate(a.created_at))) : null,
    c.pushed_at ? tr("Sent to Lemlist {}", fmtDate(c.pushed_at)) : null, r.first_sent_at ? tr("First email {}", fmtDate(r.first_sent_at)) : null];
  const f = c.filters ?? {};
  const sigNames = c.rule_ids.map((x: string) => signalName(x)).join(", ");

  return <>
    <p className="small cg-back"><Link to="/campaigns">← {tr("All campaigns")}</Link></p>
    <div className="page-head cg-head">
      <div className="eyebrow">{tr("Campaign")}</div>
      <h1>{c.name}</h1>
      <p className="sub">{tr("Emails to {} at {}, written because of recent news: {}. News from the last {} days, {}.", counted(s.people_in, ...PERSON), counted(s.companies_in, ...COMPANY), sigNames, f.days ?? 90, regionName(f.region).toLowerCase())}</p>
    </div>
    <StageStrip stage={c.stage} tone={w.tone} caption={w.caption} dates={dates} />
    {msg && <div className="okmsg" role="status">{msg}</div>}
    <Err e={err} />

    <section id="next" className={`cg-next t-${w.tone || "none"}`} aria-labelledby="cg-next-h">
      <div className="eyebrow" id="cg-next-h">{tr("Next step")}</div>
      <NextStep d={d} issueCount={issueCount} canDecide={canDecide} busy={busy} act={act} />
    </section>
    {["draft", "pending_approval", "approved", "rejected", "expired"].includes(c.status) && <OutboundNotice configured={d.lemlist_configured} enabled={d.outbound_enabled} />}

    {c.status === "pushed" && <section id="results" className="cg-sec" aria-labelledby="cg-res-h">
      <h2 id="cg-res-h">{r.sent > 0 ? tr("Results") : tr("What went to Lemlist")}</h2>
      <p className="cg-lede">{r.sent > 0 ? tr("Counts of people, read from Lemlist every hour. Bounces and opt-outs stop any further email to that address, here and in Lemlist.")
        : tr("No email has gone out yet. Results appear here within an hour of the first emails, once someone presses Launch in Lemlist.")}</p>
      <div className={`cg-results${r.sent > 0 ? "" : " one"}`}>
        {r.sent > 0 && <Funnel r={r} />}
        {d.lemlist && <dl className="cg-facts">
          <div><dt>{tr("Campaign in Lemlist")}</dt><dd>{d.lemlist.campaign_name}</dd></div>
          <div><dt>{tr("Sent to Lemlist")}</dt><dd>{fmtDateTime(d.lemlist.pushed_at)}</dd></div>
          <div><dt>{tr("People added")}</dt><dd>{number(d.lemlist.leads ?? r.in_lemlist)}</dd></div>
          <div><dt>{tr("Emails per person")}</dt><dd>{tr("{}: first email, then follow-ups on day {}", d.lemlist.emails, c.steps.slice(1).map((x: any) => x.day).join(", "))}</dd></div>
          {(d.lemlist.skipped_existing ?? 0) > 0 && <div><dt>{tr("Skipped")}</dt><dd>{tr("{} already in another Lemlist campaign", number(d.lemlist.skipped_existing))}</dd></div>}
          {(d.lemlist.dropped_since_approval ?? 0) > 0 && <div><dt>{tr("Left out at sending")}</dt><dd>{tr("{} opted out or were taken by someone else after the approval", number(d.lemlist.dropped_since_approval))}</dd></div>}
          <div><dt>{tr("Last activity")}</dt><dd>{r.last_activity_at ? fmtDateTime(r.last_activity_at) : tr("Nothing yet")}</dd></div>
        </dl>}
      </div>
    </section>}

    <section id="people" className="cg-sec" aria-labelledby="cg-people-h">
      <div className="row spread"><h2 id="cg-people-h">{tr("Who is in it and why")}</h2>
        <span className="cg-nums"><Count n={s.companies_in} of={COMPANY} /><span><Count n={s.people_in} of={PERSON} /> <span className="cg-mailok">{tr("Email ✓")}</span></span>
          {s.people_left_out > 0 && <span className="muted">{tr("{} left out by you", number(s.people_left_out))}</span>}</span></div>
      <p className="cg-lede">{tr("Each company is here because of the news shown, and its email opens with that news. People are picked from the company's contacts with an email address, technology leaders first.")}</p>
      {withPeople.length === 0 ? <Empty title="Nobody can be included yet">{tr("None of these companies has a person with an email address. Open a company and add people, then refresh the list.")}</Empty> :
        <ul className="cg-cos">{withPeople.map((g) => <CompanyBlock key={g.org} g={g} campaignId={id!} canEdit={d.can_edit} busy={busy} issues={issues} act={act} reload={() => setRev((n) => n + 1)} />)}</ul>}
      {nobody.length > 0 && <details className="cg-quiet"><summary>{tr("Companies with the signal but nobody to write to: {}", nobody.length)}</summary>
        <ul className="tight small">{nobody.map((g) => <li key={g.org}><Link to={`/accounts/${g.org}`}>{g.company}</Link>: {g.reason}</li>)}</ul></details>}
    </section>

    <section id="emails" className="cg-sec" aria-labelledby="cg-mail-h">
      <h2 id="cg-mail-h">{tr("The emails")}</h2>
      <p className="cg-lede">{tr("A first email and follow-ups in the same thread. Words in double braces are filled in by Lemlist for each person. Every save checks the emails against our writing rules.")}</p>
      {review.template && !review.template.passed && <div className="err"><b>{tr("Fix these before asking for approval")}</b>
        <ul className="tight">{review.template.breaks.map((b: any, i: number) => <li key={i}>{tr("Email {}", b.step)}: {b.detail}</li>)}</ul></div>}
      {review.passed ? <div className="okmsg">{tr("The emails pass the writing check for every person in the campaign.")}</div>
        : review.template?.passed && (review.leads_with_issues ?? []).length > 0 && <div className="err">{tr("The template is fine, but the news line for {} people breaks a rule. Fix it under Who is in it and why, or leave them out.", (review.leads_with_issues ?? []).length)}</div>}
      <div className="cg-copy">
        <StepEditor steps={c.steps} disabled={!d.can_edit} onSave={(steps) => act(() => put(`/api/signal-campaigns/${id}/steps`, { steps }), (x) => x.review.passed ? tr("Saved. The emails pass the writing check.") : tr("Saved, but the emails do not pass the writing check yet."))} />
        <aside className="cg-preview" aria-label={tr("Preview")}>
          <div className="eyebrow">{withPeople[0]?.people[0] ? tr("How it reads for {} at {}", withPeople[0].people.find((m: any) => m.included)?.name ?? withPeople[0].people[0].name, withPeople[0].company) : tr("Preview")}</div>
          {d.preview.map((p: any, i: number) => <div key={i} className="cg-mail"><div className="small muted">{i === 0 ? tr("Day {}: first email", p.day) : tr("Day {}: follow-up", p.day)}</div><b>{p.subject}</b><div className="cg-mail-body">{p.body}</div></div>)}
        </aside>
      </div>
    </section>
  </>;
}

/** The one thing to do now, with the reason, and the smaller actions next to it. */
function NextStep({ d, issueCount, canDecide, busy, act }: { d: any; issueCount: number; canDecide: boolean; busy: boolean;
  act: (fn: () => Promise<any>, ok?: (r: any) => string) => Promise<void> }) {
  const c = d.campaign, s = d.summary, r: Results = d.results, a = d.approval, id = c.id;
  const back = d.can_change && <button type="button" disabled={busy} onClick={() => act(() => post(`/api/signal-campaigns/${id}/withdraw`), () => tr("Back to draft. You can change it now."))}>{tr("Back to draft")}</button>;
  const archive = d.can_change && <button type="button" className="ghost" disabled={busy} onClick={() => act(() => post(`/api/signal-campaigns/${id}/archive`), () => tr("Archived. It no longer shows in the list."))}>{tr("Archive")}</button>;
  const lemlistLink = <a className="btn primary" href={LEMLIST_APP} target="_blank" rel="noreferrer noopener">{tr("Open Lemlist")} ↗</a>;
  switch (c.status) {
    case "draft": {
      const passed = !!c.review?.passed;
      return <>
        <p className="cg-next-say">{s.people_in === 0 ? tr("Nobody can be emailed yet. Add people with an email address to these companies, then refresh the list.")
          : passed ? tr("Everything is checked. Send it for approval: a manager who is not you checks the exact people and emails.") : tr("Fix the emails first. The problems are listed under The emails.")}</p>
        <ul className="cg-check">
          <li className={s.people_in ? "ok" : "bad"}>{s.people_in ? tr("The list: {} at {}", counted(s.people_in, ...PERSON), counted(s.companies_in, ...COMPANY)) : tr("The list: nobody to write to yet")}</li>
          <li className={passed ? "ok" : "bad"}>{passed ? tr("The emails pass the writing check") : <a href="#emails">{tr("Problems in the emails: {}", issueCount || 1)}</a>}</li>
          <li className="todo">{tr("Send for approval")}</li>
        </ul>
        <div className="row mt-2">
          <button type="button" className="primary" disabled={busy || !passed || !s.people_in || !d.can_edit}
            onClick={() => act(() => post(`/api/signal-campaigns/${id}/request-approval`), (x) => tr("Sent for approval: {} at {}.", counted(x.members, ...PERSON), counted(x.accounts, ...COMPANY)))}>{tr("Send for approval")}</button>
          <button type="button" disabled={busy || !d.can_edit} onClick={() => act(() => post(`/api/signal-campaigns/${id}/rebuild`), (x) => tr("List refreshed: {}.", counted(x.included, ...PERSON)))}>{tr("Refresh the list")}</button>
          {archive}
        </div>
        <p className="small muted m-0">{tr("Refresh the list picks up new signals and new contacts, and keeps the people you left out.")}</p>
      </>;
    }
    case "pending_approval": return <>
      <p className="cg-next-say">{tr("Waiting for approval. {} asked on {}. A manager or administrator who is not the requester approves it before {}.", a?.requested_by ?? "", fmtDate(a?.created_at), fmtDateTime(a?.expires_at))}</p>
      <div className="row mt-2">
        {d.can_approve && <>
          <button type="button" className="primary" disabled={busy} onClick={() => act(() => post(`/api/approvals/${a.id}/decision`, { approve: true }), () => tr("Approved. It can now be sent to Lemlist."))}>{tr("Approve")}</button>
          <button type="button" disabled={busy} onClick={() => act(() => post(`/api/approvals/${a.id}/decision`, { approve: false }), () => tr("Not approved. The requester can move it back to draft."))}>{tr("Reject")}</button></>}
        {!d.can_approve && canDecide && <Link className="btn" to="/admin/approvals">{tr("Open Approvals")}</Link>}
        {back}
      </div>
      {!d.can_approve && <p className="small muted m-0">{tr("You cannot approve your own request. Ask a manager.")}</p>}
    </>;
    case "rejected": case "expired": return <>
      <p className="cg-next-say">{c.status === "rejected" ? tr("Not approved by {}. Move it back to draft, change what is needed and send it for approval again.", a?.approver ?? tr("the manager"))
        : tr("Nobody approved it in time. Move it back to draft and send it for approval again.")}</p>
      <div className="row mt-2">{d.can_change && <button type="button" className="primary" disabled={busy} onClick={() => act(() => post(`/api/signal-campaigns/${id}/withdraw`), () => tr("Back to draft. You can change it now."))}>{tr("Move it back to draft")}</button>}</div>
    </>;
    case "approved": return <>
      <p className="cg-next-say">{tr("Approved by {} on {}. Sending creates the campaign in Lemlist with {}. Nobody gets an email until you press Launch in Lemlist.", a?.approver ?? "", fmtDate(a?.approved_at), counted(a?.approved_people ?? s.people_in, ...PERSON))}</p>
      <div className="row mt-2">
        <button type="button" className="primary" disabled={busy || !d.can_send || !d.lemlist_configured || !d.outbound_enabled}
          onClick={() => act(() => post(`/api/approvals/${a.id}/execute`), (x) => tr("{} people are in the Lemlist campaign. Open Lemlist and press Launch when you are ready.", x.leads))}>{tr("Send to Lemlist")}</button>
        {back}
      </div>
      {!d.can_send && <p className="small muted m-0">{tr("The requester, the approver or a manager can send it.")}</p>}
    </>;
    case "pushing": return <p className="cg-next-say">{tr("Being sent to Lemlist now. Reload this page in a minute.")}</p>;
    case "push_failed": return <>
      <p className="cg-next-say">{tr("Sending to Lemlist failed, so nothing was sent.")} {a?.error && <span className="small">({a.error})</span>} {tr("Move it back to draft and send it for approval again.")}</p>
      <div className="row mt-2">{back}</div>
    </>;
    case "archived": return <p className="cg-next-say">{tr("Archived. Nothing more happens with this campaign.")}</p>;
    default:
      if (c.stage === "lemlist") return <>
        <p className="cg-next-say">{tr("It is in Lemlist as “{}”, not launched yet. Open Lemlist, check it and press Launch. The first results show here within an hour of the first emails.", d.lemlist?.campaign_name ?? c.name)}</p>
        <div className="row mt-2">{lemlistLink}</div>
      </>;
      if (c.stage === "running") return <>
        <p className="cg-next-say">{tr("Lemlist is sending. {} of {} people have had at least one email; {} replied. Answer replies from your inbox.", number(r.sent), number(r.in_lemlist), number(r.replied))}</p>
        <div className="row mt-2"><a className="btn" href="#results">{tr("See the results")}</a></div>
      </>;
      return <>
        <p className="cg-next-say">{tr("Done. Everyone has had every email, or has replied, bounced or opted out. {} replied.", number(r.replied))}</p>
        <div className="row mt-2"><a className="btn" href="#results">{tr("See the results")}</a>{archive}</div>
      </>;
  }
}

function Funnel({ r }: { r: Results }) {
  const base = Math.max(r.in_lemlist, 1);
  const rows: [string, number, string][] = [["In Lemlist", r.in_lemlist, ""], ["Emailed", r.sent, ""], ["Opened", r.opened, ""], ["Replied", r.replied, "ok"],
    ["Interested", r.interested, "ok"], ["Bounced", r.bounced, "bad"], ["Opted out", r.unsubscribed, "bad"]];
  return <ul className="cg-funnel" aria-label={tr("Results")}>{rows.map(([l, n, k]) => <li key={l} className={k}>
    <span className="cg-fl">{tr(l)}</span>
    <span className="cg-fb" aria-hidden="true"><i style={{ width: `${Math.round((n / base) * 100)}%` }} /></span>
    <b>{number(n)}</b>
  </li>)}</ul>;
}

function CompanyBlock({ g, campaignId, canEdit, busy, issues, act, reload }: { g: any; campaignId: string; canEdit: boolean; busy: boolean; issues: Record<string, any>;
  act: (fn: () => Promise<any>, ok?: (r: any) => string) => Promise<void>; reload: () => void }) {
  const hook = (g.people.find((m: any) => m.included) ?? g.people[0])?.hook;
  const broken = g.people.filter((m: any) => issues[m.id]);
  const src = safeHref(g.signal?.source_url);
  return <li className="cg-co">
    <div className="cg-co-head">
      <Link className="cg-co-name" to={`/accounts/${g.org}`}>{g.company}</Link>
      {g.country && <span className="small muted">{g.country}</span>}
      <span className="cg-co-why"><SigChip id={g.signal.rule_id} />{g.signal.date && <span className="small muted">{fmtDate(g.signal.date)}</span>}
        {src && <a className="small" href={src} target="_blank" rel="noreferrer noopener">{tr("Source")} ↗</a>}</span>
    </div>
    {hook && <p className="cg-hook">{hook}</p>}
    {broken.length > 0 && <div className="err small">{[...new Set(broken.flatMap((m: any) => issues[m.id].breaks.map((b: any) => b.detail)))].join("; ")}
      {canEdit && <HookFix campaignId={campaignId} members={g.people.filter((m: any) => m.included)} hook={hook ?? ""} done={reload} />}</div>}
    <ul className="cg-people">{g.people.map((m: any) => {
      const [label, kind] = PERSON_STATE[m.engagement ?? m.status] ?? PERSON_STATE.planned;
      return <li key={m.id} className={m.included ? "" : "out"}>
        {canEdit ? <input type="checkbox" aria-label={tr("Include {}", m.name)} checked={m.included} disabled={busy}
          onChange={(e) => act(() => api(`/api/signal-campaigns/${campaignId}/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ included: e.target.checked }) }))} />
          : <span className="cg-dot" aria-hidden="true" />}
        <span className="cg-pp"><b>{m.name}</b>{m.title && <span className="muted">{m.title}</span>}</span>
        <span className="cg-mailok">{tr("Email ✓")}</span>
        <span className="cg-pstate">{!m.included ? <span className="small muted">{tr("Left out")}</span>
          : m.status === "planned" ? <span className="small muted">{tr("Ready")}</span> : <Badge kind={kind}>{tr(label)}</Badge>}</span>
      </li>;
    })}</ul>
  </li>;
}

/** Fix the opening news line for everyone at one company. */
function HookFix({ campaignId, members, hook, done }: { campaignId: string; members: any[]; hook: string; done: () => void }) {
  const [v, setV] = useState(hook);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setErr(null); setBusy(true);
    try { for (const m of members) await api(`/api/signal-campaigns/${campaignId}/members/${m.id}`, { method: "PATCH", body: JSON.stringify({ hook: v }) }); done(); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return <div className="cg-hookfix"><label>{tr("Opening line for everyone at this company")}<textarea rows={2} value={v} onChange={(e) => setV(e.target.value)} /></label>
    <button type="button" disabled={busy || v.trim().length < 10} onClick={save}>{tr("Save the opening line")}</button><Err e={err} /></div>;
}

function StepEditor({ steps, onSave, disabled }: { steps: any[]; onSave: (s: any[]) => Promise<void>; disabled: boolean }) {
  const [draft, setDraft] = useState(steps);
  const [busy, setBusy] = useState(false);
  const last = useRef<{ i: number; k: "subject" | "body"; el: HTMLInputElement | HTMLTextAreaElement } | null>(null);
  useEffect(() => setDraft(steps), [steps]);
  const change = (i: number, k: string, v: string | number) => setDraft((d) => d.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  const dirty = JSON.stringify(draft) !== JSON.stringify(steps);
  const insert = (ph: string) => {
    const t = last.current;
    if (!t) { change(0, "body", `${draft[0].body}${ph}`); return; }
    const el = t.el, at = el.selectionStart ?? el.value.length, end = el.selectionEnd ?? at;
    change(t.i, t.k, el.value.slice(0, at) + ph + el.value.slice(end));
    window.setTimeout(() => { el.focus(); el.setSelectionRange(at + ph.length, at + ph.length); }, 0);
  };
  const remember = (i: number, k: "subject" | "body") => (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => { last.current = { i, k, el: e.currentTarget }; };
  return <div className="cg-editor">
    {!disabled && <div className="cg-ph" role="group" aria-label={tr("Insert a personal part")}>
      <span className="small muted">{tr("Insert:")}</span>
      {PLACEHOLDERS.map(([ph, l]) => <button key={ph} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => insert(ph)} title={ph}>{tr(l)}</button>)}
    </div>}
    {disabled && <p className="small muted">{tr("Personal parts:")} {PLACEHOLDERS.map(([ph, l], i) => <Fragment key={ph}>{i > 0 && ", "}<code>{ph}</code> {tr(l)}</Fragment>)}</p>}
    {draft.map((s, i) => <fieldset key={i} className="cg-step" disabled={disabled}>
      <legend>{i === 0 ? tr("First email") : tr("Follow-up {}", i)}</legend>
      <div className="cg-step-row">
        <label className="cg-day">{tr("Day")}<input type="number" min={0} max={60} value={s.day} onChange={(e) => change(i, "day", Number(e.target.value))} /></label>
        {i === 0 ? <label className="cg-subj">{tr("Subject")}<input value={s.subject} maxLength={200} onFocus={remember(i, "subject")} onChange={(e) => change(i, "subject", e.target.value)} /></label>
          : <p className="small muted cg-subj">{tr("Sent as a reply in the same thread, so it keeps the first email's subject.")}</p>}
      </div>
      <label>{tr("Text")}<textarea rows={Math.max(6, String(s.body).split("\n").length + 3)} value={s.body} onFocus={remember(i, "body")} onChange={(e) => change(i, "body", e.target.value)} /></label>
    </fieldset>)}
    {!disabled && <div className="row">
      <button type="button" className="primary" disabled={!dirty || busy} onClick={async () => { setBusy(true); try { await onSave(draft); } finally { setBusy(false); } }}>{tr("Save and check the emails")}</button>
      {dirty && <button type="button" onClick={() => setDraft(steps)}>{tr("Undo changes")}</button>}</div>}
    {disabled && <p className="small muted">{tr("The emails can only be changed while the campaign is a draft.")}</p>}
  </div>;
}
