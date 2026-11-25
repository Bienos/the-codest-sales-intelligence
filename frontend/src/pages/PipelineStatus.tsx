import { getLanguage, number, tr } from "../locale";
import { ReactNode, useEffect, useRef, useState } from "react";
import { api, fmtDateTime, post, upload } from "../api";
import { Badge, Err, humanize, lab, Skeleton } from "../components/ui";
import "../styles/admin.css";

export type Kind = "ok" | "warn" | "bad" | "info" | "";

/** Loads one endpoint when `on` is true; `tick` reloads it. Errors stay with that one block instead of breaking the page. */
export function useLoad(path: string, on = true, tick = 0) {
  const [st, setSt] = useState<{ d?: any; err?: unknown; path?: string }>({});
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!on) return;
    let live = true;
    api(path).then((d) => live && setSt({ d, path })).catch((err) => live && setSt({ err, path }));
    return () => { live = false; };
  }, [path, on, tick, n]);
  const fresh = st.path === path;
  return { d: fresh ? st.d : undefined, err: fresh ? st.err : undefined, loading: on && !fresh, reload: () => setN((x) => x + 1) };
}

/** A status in words with a coloured square in front. */
export const Say = ({ k, children }: { k: Kind; children: ReactNode }) => <span className={`adot ${k}`}>{children}</span>;

/** A collapsible block: title, one plain line about what it is for, and its current status. The inside is only built when opened. */
export function Fold({ title, desc, status, open = false, id, children }: { title: string; desc?: string; status?: [Kind, string] | null; open?: boolean; id?: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [o, setO] = useState(open);
  useEffect(() => {
    if (!open || !ref.current) return;
    ref.current.open = true; setO(true);
    // after the app has reset the scroll position for the new address
    const t = setTimeout(() => ref.current?.scrollIntoView({ block: "start" }), 80);
    return () => clearTimeout(t);
  }, [open]);
  return (
    <details ref={ref} id={id} className={`fold ${status?.[0] ?? ""}`} onToggle={(e) => setO((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <span className="fold-main"><span className="fold-t">{tr(title)}</span>{desc && <span className="fold-d">{tr(desc)}</span>}</span>
        {status !== undefined && <span className="fold-s">{status ? <Say k={status[0]}>{status[1]}</Say> : <span className="muted small">{tr("Checking")}</span>}</span>}
      </summary>
      {o && <div className="fold-body">{children}</div>}
    </details>
  );
}

/** When a block has nothing behind it, one quiet line instead of an empty table. */
export const Quiet = ({ children }: { children: ReactNode }) => <p className="quiet">{children}</p>;

/* ------------------------------------------------------------------ processing pipeline */

const STATE: Record<string, [Kind, string]> = { ok: ["ok", "Running"], waiting: ["warn", "Waiting for you"], stale: ["bad", "Stopped reporting"], never: ["bad", "Never reported"], off: ["", "Switched off"] };

function Funnel({ f, accounts, candidates }: { f: any[]; accounts: number; candidates: number }) {
  const max = Math.max(...f.map((x) => x.n), 1);
  // the biggest relative drop between neighbouring stages is where things are stopping
  let worst = -1, ratio = 1;
  f.forEach((x, i) => { if (i > 0 && f[i - 1].n > 0) { const r = x.n / f[i - 1].n; if (r < ratio) { ratio = r; worst = i; } } });
  return (
    <div className="adm-panel">
      <h4 className="adm-h4">{tr("Where things drop out (last 7 days)")}</h4>
      <ol className="funnel">
        {f.map((x, i) => (
          <li key={x.key} className={i === worst && ratio < 0.5 ? "drop" : ""}>
            <div className="fn-l"><b>{tr(x.label)}</b><span className="small muted">{tr(x.note)}</span></div>
            <div className="fn-bar"><i style={{ width: `${Math.max((x.n / max) * 100, x.n ? 2 : 0)}%` }} /></div>
            <div className="fn-n">{number(x.n)}</div>
          </li>))}
      </ol>
      {worst > 0 && ratio < 0.5 && <p className="small mt-3 mb-0"><b>{tr("Biggest drop:")}</b> {tr(f[worst - 1].label).toLowerCase()} → {tr(f[worst].label).toLowerCase()}. {f[worst].key === "linked" && accounts === 0 ? tr("There are no accounts yet. Load the workbooks or add companies.") : f[worst].key === "linked" ? tr("{} {} waiting to be added to your accounts.", candidates, candidates === 1 ? "company is" : "companies are") : ""}</p>}
    </div>
  );
}

const visibleStages = (d: any) => d.stages.filter((s: any) => s.key !== "pipedrive");

/** What runs by itself, what waits for a person and what is switched off. */
export function Pipeline({ d }: { d: any }) {
  return (
    <>
      {d.next_steps.length > 0 && <div className="adm-next"><h4 className="adm-h4">{tr("What to do next")}</h4><ol className="tight m-0">{d.next_steps.map((n: string, i: number) => <li key={i}>{tr(n)}</li>)}</ol></div>}
      <ol className="adm-stages" aria-label={tr("Pipeline stages")}>
        {visibleStages(d).map((s: any) => {
          const [kind, label] = STATE[s.state] ?? ["", s.state];
          return (
            <li key={s.key} className={`adm-stage ${kind}`}>
              <div className="adm-stage-head"><b>{tr(s.label)}</b><span className="adm-stage-tags">{s.mode !== "off" && <Badge>{({ automatic: tr("Runs by itself"), manual: tr("A person does this"), off: tr("Switched off") } as Record<string, string>)[s.mode] ?? humanize(s.mode)}</Badge>}<Say k={kind}>{tr(label)}</Say></span></div>
              <p className="small m-0">{tr(s.note)}</p>
              {s.last && <div className="small muted">{tr("Last activity") + " "}{fmtDateTime(s.last)}</div>}
              {s.detail?.length > 0 && <ul className="tight small">{s.detail.map((f: any, k: number) => <li key={k}><b>{f.display_name}</b>: {f.error_class === "server_error" ? tr("the site returned an error") : humanize(f.error_class).toLowerCase()} {f.consecutive_failures}{" " + tr("times in a row")}</li>)}</ul>}
              {s.counts && Object.keys(s.counts).length > 0 && <div className="chips mt-2 mb-0">{Object.entries(s.counts).map(([k, v]: any) => <Badge key={k}>{humanize(k)} {v}</Badge>)}</div>}
            </li>);
        })}
      </ol>
      <div className="adm-panel">
        <h4 className="adm-h4">{tr("Jobs waiting")}</h4>
        <div className="chips mb-0">{Object.entries(d.queue).map(([k, v]: any) => <Badge key={k} kind={k === "permanent_failed" && v ? "bad" : ""}>{({ queued: tr("Waiting"), running: tr("Running"), retrying: tr("Retrying"), permanent_failed: tr("Failed for good"), cancelled: tr("Cancelled") } as Record<string, string>)[k] ?? humanize(k)} {v}</Badge>)}{Object.keys(d.queue).length === 0 && <span className="small muted">{tr("None waiting")}</span>}</div>
        {d.oldest_waiting_s !== null && <p className="small muted mb-0">{tr("Longest wait:") + " "}{Math.round(d.oldest_waiting_s / 60)} min</p>}
      </div>
      {d.funnel && <Funnel f={d.funnel} accounts={d.accounts} candidates={d.candidates} />}
    </>
  );
}
const pipelineStatus = (d: any): [Kind, string] => {
  const st = visibleStages(d);
  const down = st.filter((s: any) => s.state === "stale" || s.state === "never").map((s: any) => tr(s.label));
  const waiting = st.filter((s: any) => s.state === "waiting").length;
  if (down.length) return ["bad", tr("Not reporting: {}", down.join(", "))];
  if (waiting) return ["warn", tr("{} waiting for you", waiting)];
  return ["ok", tr("Everything is running")];
};

/* ------------------------------------------------------------------ failed jobs */

/** What each kind of background job does, in words. */
export const JOB_KIND: Record<string, string> = {
  source_run: "Reading a source", verify_url: "Checking a page", extract_document: "Reading a page with AI", assess_fit: "Writing fit reasons", tender_summaries: "Tender summaries",
  account_scan: "Scanning a company", discovery_run: "Web search run", sheet_import_xlsx: "Workbook upload", sheet_sync: "Workbook sync", account_brief: "Company brief",
  campaign_draft: "Email draft", account_profile: "Company research", person_profile: "Person research", people_search: "Finding people", identity_autoresolve: "Company matching",
  sources_validate_all: "Testing sources", sources_validate_daily: "Daily source test", crm_sync: "CRM sync", lemlist_sync: "Lemlist sync", ted_awards_window: "TED award notices",
  evaluate_event: "Scoring a finding", ted_probe: "TED check",
};
const kindName = (k: string) => tr(JOB_KIND[k] ?? humanize(k));

/** Each cause in plain English: a title ({} is the service) and what it means. */
const CAUSE: Record<string, [string, string]> = {
  ai_credit: ["AI credit ran out", "The AI provider's account ran out of credit, so pages, summaries and checks could not be read. Nothing was wrong with the work itself."],
  budget: ["Monthly budget used up", "The monthly limit for AI or web search was reached, so the work stopped as designed. A new month, or a higher limit, lets it run."],
  fit_bug: ["Old fit-check bug", "A bug in the fit check left a database transaction broken, so the whole batch stopped. The bug is fixed; these can be retried."],
  empty_output: ["The AI returned an empty answer", "The model answered with nothing usable, usually for a very long page or one it would not summarise. A retry usually works."],
  timeout: ["Timeouts", "The other side did not answer in time. Usually a slow or busy site; a retry later normally works."],
  http_400: ["{} returned 400 (bad request)", "The service rejected our request as malformed. Usually a change on their side, such as a query or field it no longer accepts."],
  rate_limited: ["{} asked us to slow down", "Too many requests in a short time. A retry later works."],
  server_error: ["{} had server errors", "The other side failed (an error 5xx). That is their problem, not ours; a retry later normally works."],
  not_found: ["{}: address not found", "A page or record we asked for no longer exists (404)."],
  blocked: ["{} refused or blocked us", "The site refused access (robots.txt, a bot check or a missing permission). Retrying will not help until access is sorted out."],
  format: ["{} answered in an unexpected format", "The answer did not look like what the reader expects, often after a site redesign. The reader may need a fix."],
  no_handler: ["Jobs no worker could run", "The job's type was not known to the worker or its details were incomplete."],
  other: ["{}: {}", "A failure that does not match a known cause. The error text below says what happened."],
};
const STATE_KIND: Record<string, Kind> = { ongoing: "bad", quiet: "warn", resolved: "ok", handled: "" };
const day = (v?: string | null) => (v ? new Date(v).toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { day: "numeric", month: "short" }) : "");

function stateLine(g: any): string {
  if (g.state === "handled") return tr("Handled: {} retried, {} dismissed.", g.retried, g.dismissed);
  if (g.state === "ongoing") return tr("Still happening: the last failure was on {}.", fmtDateTime(g.last_at));
  if (g.state === "quiet") return tr("Not seen since {}. The cause may be gone; nothing has proved it yet.", day(g.last_at));
  return ({ ai_ok: tr("Cause fixed: AI calls have worked again since {}.", day(g.state_since)), new_month: tr("Cause fixed: a new month's budget is available."),
    pages_ok: tr("Cause fixed: pages have been read fine since {}.", day(g.state_since)), job_ok: tr("Cause fixed: the same work has succeeded since {}.", day(g.state_since)) } as Record<string, string>)[g.state_basis] ?? tr("Cause fixed.");
}

function FailureGroup({ g, reload }: { g: any; reload: () => void }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState(""); const [confirm, setConfirm] = useState(false);
  const [title, explain] = CAUSE[g.cause] ?? CAUSE.other;
  const who = g.source_name ?? (g.source ? g.source : tr("The service"));
  const heading = g.cause === "other" ? tr(title, kindName(g.kinds[0]), humanize(g.samples?.[0]?.split(":")[0] ?? "error").toLowerCase()) : tr(title, who);
  const act = async (what: "retry" | "dismiss") => {
    setBusy(true); setErr(null); setMsg("");
    try {
      const r = await post(`/api/admin/failures/${what}`, { key: g.key, note: what === "dismiss" ? tr("Marked handled in System health") : undefined });
      setMsg(what === "dismiss" ? tr("{} marked as handled. They stay in the audit log.", r.dismissed)
        : [r.queued && tr("{} queued to run again", r.queued), r.pages_requeued && tr("{} pages queued to be read again", r.pages_requeued), r.covered && tr("{} duplicates covered by the same retry", r.covered), r.skipped && tr("{} could not be repeated and were set aside", r.skipped)].filter(Boolean).join(" · ") + ".");
      setConfirm(false); reload();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  return (
    <li className={`fl fl-${g.state}`}>
      <div className="fl-n"><b>{number(g.state === "handled" ? g.total : g.open)}</b><span>{g.state === "handled" ? tr("jobs, handled") : g.open === 1 ? tr("job") : tr("jobs")}</span></div>
      <div className="fl-main">
        <h4>{heading}</h4>
        <p className="fl-meta">{g.when}{g.kinds.length > 0 && <> · {g.kinds.map(kindName).join(", ")}</>}</p>
        <Say k={STATE_KIND[g.state] ?? ""}>{stateLine(g)}</Say>
        <p className="fl-explain">{tr(explain)}</p>
        {g.items.length > 0 && <details className="fl-items"><summary>{g.open > g.items.length ? tr("Show the latest {} of {}", g.items.length, g.open) : g.open === 1 ? tr("Show the job") : tr("Show the {} jobs", g.open)}</summary>
          <ul>{g.items.map((x: any) => <li key={x.id}><span className="fl-when">{fmtDateTime(x.created_at)}</span><span><b>{kindName(x.kind)}</b>{x.subject && <> · {x.subject}</>}<span className="fl-err">{x.error}</span></span></li>)}</ul>
        </details>}
        {msg && <div className="okmsg m-0" role="status">{msg}</div>}<Err e={err} />
      </div>
      {g.open > 0 && <div className="fl-act">
        {!confirm ? <>
          {g.retryable && <button className={g.state === "resolved" ? "primary" : undefined} disabled={busy} onClick={() => act("retry")}>{tr("Retry these")}</button>}
          <button className={g.retryable ? "ghost" : undefined} disabled={busy} onClick={() => setConfirm(true)}>{tr("Dismiss")}</button>
        </> : <>
          <span className="small">{tr("Mark {} as handled?", g.open)}</span>
          <button className="primary" disabled={busy} onClick={() => act("dismiss")}>{tr("Yes, dismiss")}</button><button className="ghost" disabled={busy} onClick={() => setConfirm(false)}>{tr("Cancel")}</button>
        </>}
      </div>}
    </li>
  );
}

/** Failed jobs, grouped by cause: what happened, when, whether it is still happening, and one action per group. */
function Jobs({ d, reload }: { d: any; reload: () => void }) {
  const open = d.groups.filter((g: any) => g.open > 0);
  const handled = d.groups.filter((g: any) => g.open === 0);
  const fixed = open.filter((g: any) => g.state === "resolved").reduce((a: number, g: any) => a + g.open, 0);
  return <>
    <div className="fl-sum">
      <div className={d.still_happening ? "bad" : "ok"}><b>{number(d.still_happening)}</b><span>{tr("still happening")}</span></div>
      <div className={fixed ? "ok" : ""}><b>{number(fixed)}</b><span>{tr("old, cause fixed")}</span></div>
      <div><b>{number(d.open - d.still_happening - fixed)}</b><span>{tr("not seen lately")}</span></div>
      {d.retrying_now > 0 && <p className="small muted m-0">{d.retrying_billing > 0 ? tr("{} more jobs will retry by themselves every hour until the AI account has credit again.", d.retrying_now) : tr("{} more jobs are waiting to retry by themselves.", d.retrying_now)}</p>}
    </div>
    {open.length === 0 ? <Quiet>{tr("Nothing has failed for good. Earlier failures are handled.")}</Quiet> : <ul className="fls">{open.map((g: any) => <FailureGroup key={g.key} g={g} reload={reload} />)}</ul>}
    {handled.length > 0 && <details className="fl-handled"><summary>{tr("Handled earlier ({})", handled.length)}</summary><ul className="fls">{handled.map((g: any) => <FailureGroup key={g.key} g={g} reload={reload} />)}</ul></details>}
  </>;
}
const jobsStatus = (d: any): [Kind, string] => {
  if (d.still_happening) return ["bad", tr("{} failing now", d.still_happening)];
  if (d.open) return ["warn", tr("{} old failures to retry or dismiss", d.open)];
  return ["ok", tr("Nothing has failed")];
};

/* ------------------------------------------------------------------ are we finding things? */

function Coverage({ d }: { d: any }) {
  return <>
    <p className="small muted mt-0">{tr("Each cell counts what we found in the last {} days. A red 0 is worth a look: a source may be broken, or the market may just be quiet.", d.grid.days)}</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Findings by market and topic")}><table className="grid-table"><thead><tr><th>{tr("Market")}</th>{d.grid.kinds.map((k: string) => <th key={k}>{tr(k)}</th>)}</tr></thead><tbody>
      {d.grid.markets.map((m: string) => <tr key={m}><td><b>{tr(m)}</b></td>{d.grid.kinds.map((k: string) => { const c = d.grid.cells.find((x: any) => x.market === m && x.kind === k); return <td key={k}>{c.empty ? <Badge kind="bad">0</Badge> : c.count}</td>; })}</tr>)}</tbody></table></div>
    <h4 className="adm-h4">{tr("Sources finding less than usual")}</h4>
    {d.yield_drops.length === 0 ? <Quiet>{tr("None. We only judge a source after two weeks of history, and only if it normally finds at least 5 items a week.")}</Quiet>
      : <ul className="tight small">{d.yield_drops.map((x: any) => <li key={x.source_id}><b>{x.source_id}</b>: {x.last_7_days}{" " + tr("this week, usually about") + " "}{x.usual_per_week}</li>)}</ul>}
    <h4 className="adm-h4">{tr("Pages sellers say we missed")}</h4>
    {d.misses.total === 0 ? <Quiet>{tr("None yet. Sellers can paste a link on the Market feed.")}</Quiet> : <>
      <p className="small muted">{tr("{} reported: we already had {}, and missed {}. Each missed page shows which source should have found it.", d.misses.total, d.misses.caught, d.misses.missed)}</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Pages sellers say we missed")}><table><thead><tr><th>{tr("Page")}</th><th>{tr("Did we have it?")}</th><th>{tr("Result")}</th><th>{tr("Reported")}</th></tr></thead><tbody>
        {d.misses.items.map((m: any) => <tr key={m.id}><td><a href={m.url} target="_blank" rel="noreferrer noopener">{m.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a>{m.note && <div className="small muted">{m.note}</div>}</td>
          <td>{m.we_already_had_it ? <Badge kind="ok">{tr("Yes ({})", m.from_source)}</Badge> : <Badge kind="bad">{tr("No")}</Badge>}</td><td className="small">{humanize(m.status)}{m.events_created > 0 ? ` · ${tr("{} findings", m.events_created)}` : ""}</td><td className="small">{fmtDateTime(m.reported_at)}</td></tr>)}</tbody></table></div>
    </>}
  </>;
}
const coverageStatus = (d: any): [Kind, string] => {
  if (d.yield_drops.length) return ["warn", tr("{} sources finding less than usual", d.yield_drops.length)];
  if (d.grid.empty.length) return ["warn", tr("{} of {} market and topic pairs found nothing", d.grid.empty.length, d.grid.cells.length)];
  return ["ok", tr("Finding things in every market")];
};

/* ------------------------------------------------------------------ health checks */

const QUALITY_LABELS: Record<string, [string, string]> = {
  stale_sources: ["Sources gone quiet", "Active sources that haven't produced anything recently."],
  dead_letters_unreplayed: ["Failed jobs not retried", "Jobs that failed for good and haven't been replayed."],
  alerts_sent: ["Alerts sent", "Alerts sent to the team."],
  duplicate_alert_groups: ["Duplicate alerts", "Stories alerted more than once. Should be zero."],
  high_priority_without_citation: ["High priority without a source", "High-priority cards with no cited source. Should be zero."],
  open_identity_reviews: ["Company matches to review", "Companies we couldn't match with confidence."],
  unverified_events: ["Findings not checked yet", "Findings waiting to be checked against the original page."],
  unauthorised_writes_detected: ["Unapproved changes detected", "Outreach or other outside changes made without an approval. Should be zero."],
  pending_approvals: ["Approvals waiting", "Requests waiting for a second person."],
};
/** Counts that must stay at zero: anything else means something went wrong. */
export const MUST_BE_ZERO = ["duplicate_alert_groups", "high_priority_without_citation", "unauthorised_writes_detected"];

function Quality({ d, f }: { d: any; f: any }) {
  const nums = Object.entries(d).filter(([, v]) => typeof v === "number") as [string, number][];
  const shown = nums.filter(([, v]) => v !== 0);
  const zero = nums.filter(([, v]) => v === 0);
  return <>
    {shown.length > 0 && <dl className="checks">{shown.map(([k, v]) => { const [t, hint] = QUALITY_LABELS[k] ?? [humanize(k), ""]; return <div key={k} className={MUST_BE_ZERO.includes(k) ? "bad" : ""}><dt>{tr(t)}</dt><dd>{number(v)}</dd>{hint && <p>{tr(hint)}</p>}</div>; })}</dl>}
    {zero.length > 0 && <Quiet>{tr("At zero:") + " "}{zero.map(([k]) => tr((QUALITY_LABELS[k] ?? [humanize(k)])[0])).join(", ")}.</Quiet>}
    <h4 className="adm-h4">{tr("Outcome funnel")}</h4>
    {!f ? <Skeleton rows={1} /> : Object.keys(f.cohorts).length === 0 ? <Quiet>{tr("Nothing to show: no outcomes recorded yet. Nothing here is inferred.")}</Quiet> :
      <ul className="tight small">{Object.entries(f.cohorts).map(([k, c]: any) => <li key={k}><b>{humanize(k)}</b>{" " + tr("· presented") + " "}{c.presented} {c.insufficient_sample && <Badge kind="warn">{tr("too few to judge")}</Badge>}</li>)}</ul>}
    {f?.caveats?.length > 0 && <ul className="tight small muted">{f.caveats.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
  </>;
}
const qualityStatus = (d: any): [Kind, string] => {
  const bad = MUST_BE_ZERO.filter((k) => Number(d[k]) > 0);
  return bad.length ? ["bad", tr("Needs a look: {}", bad.map((k) => tr(QUALITY_LABELS[k][0])).join(", "))] : ["ok", tr("Every check that should be zero is zero")];
};

/* ------------------------------------------------------------------ which signals work */

function Precision({ d }: { d: any }) {
  return <>
    {d.flagged.length > 0 && <div className="err" role="alert">{tr("Dismissed more than 60% of the time, with enough decisions to judge:") + " "}<b>{d.flagged.join(", ")}</b></div>}
    {d.rules.length === 0 ? <Quiet>{tr("Nothing to show: no signals fired in the last {} days.", d.window_days)}</Quiet> :
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("What sellers did with each signal")}><table><thead><tr><th>{tr("Signal")}</th><th>{tr("Fired")}</th><th>{tr("Pursued")}</th><th>{tr("Dismissed")}</th><th>{tr("Dismissed share")}</th><th>{tr("Main reason")}</th><th><span className="sr">{tr("Flag")}</span></th></tr></thead><tbody>
        {d.rules.map((r: any) => <tr key={r.rule_id}><td>{r.rule_id}</td><td>{r.fired}</td><td>{r.accepted}</td><td>{r.dismissed}</td><td>{r.dismissal_rate === null ? "n/a" : `${Math.round(r.dismissal_rate * 100)}%`}<div className="small muted">{r.enough_data ? tr("likely range {} to {}%", Math.round(r.interval_95[0] * 100), Math.round(r.interval_95[1] * 100)) : tr("Too few decisions to judge")}</div></td>
          <td className="small">{r.top_dismiss_reason ? lab("dismiss", r.top_dismiss_reason) : tr("None")}</td><td>{r.flag && <Badge kind="bad">{tr("Look at this")}</Badge>}</td></tr>)}</tbody></table></div>}
    <h4 className="adm-h4">{tr("Outreach results")}</h4>
    {Object.keys(d.outreach).length === 0 ? <Quiet>{tr("Nobody added to a sequence yet.")}</Quiet> : <div className="chips">{Object.entries(d.outreach).map(([k, n]) => <Badge key={k}>{humanize(k)}: {n as number}</Badge>)}</div>}
    {d.caveats.length > 0 && <ul className="tight small muted">{d.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>}
  </>;
}
const precisionStatus = (d: any): [Kind, string] => {
  if (d.flagged.length) return ["warn", tr("{} signals dismissed too often", d.flagged.length)];
  if (d.rules.length === 0) return ["", tr("No signals fired in {} days", d.window_days)];
  return ["ok", tr("No signal is dismissed too often")];
};

/* ------------------------------------------------------------------ the System health page */

export const HEALTH_PARTS = ["pipeline", "jobs", "coverage", "quality", "precision"] as const;

/** Everything about whether the platform is running, on one page: each check is one line until you open it. */
export function Health({ can, part }: { can: (p: string) => boolean; part?: string }) {
  const [tick, setTick] = useState(0);
  const pipe = useLoad("/api/admin/pipeline", can("admin.jobs"), tick);
  const jobs = useLoad("/api/admin/failures", can("admin.jobs"), tick);
  const recall = useLoad("/api/admin/recall", can("admin.rules"), tick);
  const metrics = useLoad("/api/admin/metrics", can("admin.rules"), tick);
  const funnel = useLoad("/api/admin/funnel", can("admin.rules"), tick);
  const prec = useLoad("/api/admin/precision", can("admin.rules"), tick);
  const blocks: { key: string; show: boolean; title: string; desc: string; r: ReturnType<typeof useLoad>; status: (d: any) => [Kind, string]; body: (d: any) => ReactNode }[] = [
    { key: "pipeline", show: can("admin.jobs"), title: "Processing pipeline", desc: "What runs by itself, what waits for a person, and what is switched off.", r: pipe, status: pipelineStatus, body: (d) => <Pipeline d={d} /> },
    { key: "jobs", show: can("admin.jobs"), title: "Failed jobs", desc: "Background jobs that failed for good, grouped by cause, with a way to retry or dismiss each group.", r: jobs, status: jobsStatus, body: (d) => <Jobs d={d} reload={jobs.reload} /> },
    { key: "coverage", show: can("admin.rules"), title: "Are we finding things?", desc: "Findings per market and topic, sources that went quiet, and pages sellers say we missed.", r: recall, status: coverageStatus, body: (d) => <Coverage d={d} /> },
    { key: "quality", show: can("admin.rules"), title: "Health checks", desc: "Counts that should stay at zero, and how presented opportunities turned out.", r: metrics, status: qualityStatus, body: (d) => <Quality d={d} f={funnel.d} /> },
    { key: "precision", show: can("admin.rules"), title: "Which signals work", desc: "What sellers did with each signal: pursued it or dismissed it, and why.", r: prec, status: precisionStatus, body: (d) => <Precision d={d} /> },
  ];
  const shown = blocks.filter((b) => b.show);
  if (!shown.length) return <Quiet>{tr("You do not have access to the health checks. Ask an administrator.")}</Quiet>;
  return (
    <>
      <div className="adm-toolbar"><span className="small muted">{tr("Open a check to see the detail. Each line shows its state now.")}</span><button className="ghost" onClick={() => setTick((t) => t + 1)}>{tr("Check again")}</button></div>
      <div className="folds">
        {shown.map((b) => (
          <Fold key={b.key} id={`health-${b.key}`} title={b.title} desc={b.desc} open={part === b.key}
            status={b.r.d ? b.status(b.r.d) : b.r.err ? ["bad", tr("Could not load this check")] : null}>
            {b.r.err ? <Err e={b.r.err} onRetry={b.r.reload} /> : b.r.d ? b.body(b.r.d) : <Skeleton rows={2} />}
          </Fold>))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ knowledge base documents */

export function KnowledgeAdmin() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [msg, setMsg] = useState("");
  const load = () => api("/api/kb").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const send = async (id: string, f?: File) => {
    if (!f) return; setErr(null); setMsg("");
    try { const r = await upload(`/api/admin/kb/${id}`, f); setMsg(tr("Uploaded ({} KB). It can be searched now.", (r.bytes / 1024).toFixed(0))); load(); } catch (e) { setErr(e); }
  };
  if (!d) return err ? <Err e={err} onRetry={load} /> : <Skeleton />;
  return (
    <>
      <p className="adm-lead">{tr("These internal documents are kept behind sign-in and shown through short-lived links. They are never public. Upload the .html file for each one.")}</p>
      {msg && <div className="okmsg" role="status">{tr(msg)}</div>}<Err e={err} />
      <ul className="adm-items">
        {d.items.map((k: any) => (
          <li key={k.id} className={`adm-item ${k.available ? "ok" : "warn"}`}>
            <div className="adm-item-head"><div><b>{k.title}</b><div className="small muted">{k.blurb}</div></div><Say k={k.available ? "ok" : "warn"}>{k.meta ? tr("Uploaded") : k.available ? tr("Built in") : tr("Not uploaded yet")}</Say></div>
            {k.meta && <p className="small muted m-0">{tr("{} KB, uploaded by {} on {}", (k.meta.size_bytes / 1024).toFixed(0), k.meta.uploaded_by, fmtDateTime(k.meta.uploaded_at))}</p>}
            <label className="adm-file"><span>{tr("Upload a new copy (.html)")}</span><input type="file" accept=".html,text/html" aria-label={tr("Upload {}", k.title)} onChange={(e) => { void send(k.id, e.target.files?.[0]); e.target.value = ""; }} /></label>
          </li>))}
      </ul>
    </>
  );
}
