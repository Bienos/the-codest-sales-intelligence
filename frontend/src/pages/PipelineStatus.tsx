import { getLanguage, number, tr } from "../locale";
import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, post, put, upload } from "../api";
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

/* ------------------------------------------------------------------ small pieces every Administration table shares */

const loc = () => (getLanguage() === "pl" ? "pl-PL" : "en-GB");
const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** A date read at a glance: "Today, 13:24", "Yesterday, 09:10", "23 Nov, 16:56", or "23 Nov 2025" for another year. */
export function when(v?: string | null, withTime = true): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return "—";
  const now = new Date(); const yday = new Date(now); yday.setDate(now.getDate() - 1);
  const t = d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
  if (sameDay(d, now)) return withTime ? tr("Today, {}", t) : tr("Today");
  if (sameDay(d, yday)) return withTime ? tr("Yesterday, {}", t) : tr("Yesterday");
  const date = d.toLocaleDateString(loc(), d.getFullYear() === now.getFullYear() ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
  return withTime ? `${date}, ${t}` : date;
}

/** How long something took, in the unit that reads best. */
export const took = (s?: number | null) => (s === null || s === undefined ? "—" : s < 1 ? tr("under 1 s") : s < 90 ? tr("{} s", Math.round(s)) : s < 5400 ? tr("{} min", Math.round(s / 60)) : tr("{} h", (s / 3600).toFixed(1)));

/** The time windows every history view offers. */
export const windows = (): [string, string][] => [["24h", tr("Last 24 hours")], ["7d", tr("Last 7 days")], ["30d", tr("Last 30 days")], ["90d", tr("Last 90 days")], ["all", tr("Any time")]];

/** One labelled drop-down in a filter bar. */
export function Pick({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return <label className="fbar-f"><span>{tr(label)}</span><select value={value} onChange={(e) => onChange(e.target.value)}>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>;
}

/** Where a long list is, and the way to the next or previous page. */
export function Pager({ total, offset, limit, onPage, label }: { total: number; offset: number; limit: number; onPage: (o: number) => void; label: string }) {
  if (total <= 0) return null;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pager" aria-label={tr("Pages of {}", tr(label))}>
      <span>{total <= limit ? tr("{} in total", number(total)) : tr("{} to {} of {}", number(offset + 1), number(to), number(total))}</span>
      {total > limit && <span className="pager-b">
        <button type="button" className="ghost" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>{tr("Previous")}</button>
        <button type="button" className="ghost" disabled={to >= total} onClick={() => onPage(offset + limit)}>{tr("Next")}</button>
      </span>}
    </nav>
  );
}

/** Query-string state for a filterable list, so a link from the overview opens the list already filtered. */
export function useQueryState<T extends Record<string, string>>(defaults: T): [T, (patch: Partial<T>) => void] {
  const [sp, setSp] = useSearchParams();
  const state = Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, sp.get(k) ?? v])) as T;
  const set = (patch: Partial<T>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === defaults[k]) next.delete(k); else next.set(k, String(v)); }
    setSp(next, { replace: true });
  };
  return [state, set];
}

/** A short line naming a number from a job's result ("events_created: 3" becomes "3 events created"). */
export const resultLine = (pairs: [string, number][]) => pairs.map(([k, v]) => `${number(v)} ${tr(humanize(k).toLowerCase())}`).join(" · ");

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

/** Each stage in plain words. Stages whose server note carries live numbers keep that note; the others get a plain one here. */
const STAGE: Record<string, [string, string | null]> = {
  scheduler: ["Scheduler", "Starts work on time: every 5 minutes it decides which sources are due and queues the daily web searches."],
  worker: ["Worker", null],
  sources: ["Reading sources", null],
  discovery: ["Saved web searches", null],
  verification: ["Checking pages", "Opens the original page of a lead and keeps only what the page itself says. Starts when a seller sends a link or a web search finds a page."],
  opportunities: ["Findings to opportunities", null],
  digest: ["Daily Slack summary", null],
  sheets: ["Workbook import", "You upload the two workbooks in Sources & data, Workbooks. Hourly automatic sync needs a Google service account."],
};
const QUEUE: Record<string, string> = { scheduled: "Waiting", claimed: "Running", fetching: "Running", parsing: "Running", verifying: "Running", committing: "Running", completed: "Finished",
  retryable_failed: "Will retry", permanent_failed: "Failed for good", quarantined: "Could not run", cancelled: "Cancelled", requires_approval: "Waiting for approval" };

/** What runs by itself, what waits for a person and what is switched off. */
export function Pipeline({ d }: { d: any }) {
  const next = (d.next_steps as string[]).filter((n) => !/pipedrive/i.test(n));
  const queue = Object.entries(d.queue as Record<string, number>).reduce<Record<string, number>>((a, [k, v]) => { const l = QUEUE[k] ?? humanize(k); a[l] = (a[l] ?? 0) + Number(v); return a; }, {});
  return (
    <>
      {next.length > 0 && <div className="adm-next"><h4 className="adm-h4">{tr("What to do next")}</h4><ol className="tight m-0">{next.map((n, i) => <li key={i}>{tr(n)}</li>)}</ol></div>}
      <ol className="adm-stages" aria-label={tr("Pipeline stages")}>
        {visibleStages(d).map((s: any) => {
          const [kind, label] = STATE[s.state] ?? ["", s.state];
          const [name, note] = STAGE[s.key] ?? [s.label, null];
          return (
            <li key={s.key} className={`adm-stage ${kind}`}>
              <div className="adm-stage-head"><b>{tr(name)}</b><span className="adm-stage-tags">{s.mode !== "off" && <Badge>{({ automatic: tr("Runs by itself"), manual: tr("A person does this"), off: tr("Switched off") } as Record<string, string>)[s.mode] ?? tr(humanize(s.mode))}</Badge>}<Say k={kind}>{tr(label)}</Say></span></div>
              <p className="small m-0">{note ? tr(note) : tr(s.note)}</p>
              {s.last && <div className="small muted">{tr("Last activity: {}", when(s.last))}</div>}
              {s.detail?.length > 0 && <ul className="tight small">{s.detail.map((f: any, k: number) => <li key={k}><b>{f.display_name}</b>: {f.error_class === "server_error" ? tr("the site returned an error") : humanize(f.error_class).toLowerCase()} {f.consecutive_failures}{" " + tr("times in a row")}</li>)}</ul>}
              {s.counts && Object.keys(s.counts).length > 0 && <div className="chips mt-2 mb-0">{Object.entries(s.counts).map(([k, v]: any) => <Badge key={k}>{humanize(k)} {v}</Badge>)}</div>}
            </li>);
        })}
      </ol>
      <div className="adm-panel">
        <div className="row spread"><h4 className="adm-h4 m-0">{tr("Background jobs, all time")}</h4><Link to="/admin/job-history" className="small">{tr("Open the job history")}</Link></div>
        <div className="chips mb-0 mt-2">{Object.entries(queue).map(([k, v]) => <Badge key={k} kind={k === "Failed for good" && v ? "bad" : ""}>{`${tr(k)}: ${number(v)}`}</Badge>)}{Object.keys(queue).length === 0 && <span className="small muted">{tr("No background jobs yet.")}</span>}</div>
        {d.oldest_waiting_s !== null && <p className="small muted mb-0">{tr("The job waiting longest has waited {}.", took(d.oldest_waiting_s))}</p>}
      </div>
      {d.funnel && <Funnel f={d.funnel} accounts={d.accounts} candidates={d.candidates} />}
    </>
  );
}
const pipelineStatus = (d: any): [Kind, string] => {
  const st = visibleStages(d);
  const down = st.filter((s: any) => s.state === "stale" || s.state === "never").map((s: any) => tr((STAGE[s.key] ?? [s.label])[0]));
  const waiting = st.filter((s: any) => s.state === "waiting").length;
  if (down.length) return ["bad", tr("Not reporting: {}", down.join(", "))];
  if (waiting) return ["warn", tr("{} waiting for you", waiting)];
  return ["ok", tr("Everything is running")];
};

/* ------------------------------------------------------------------ failed jobs */

/** What each kind of background job does, in words. */
export const JOB_KIND: Record<string, string> = {
  source_run: "Reading a source", verify_url: "Checking a page", extract_document: "Reading a page with AI", assess_fit: "Writing fit reasons", tender_summaries: "Tender summaries",
  account_scan: "Scanning a company", account_scan_background: "Scanning a company", discovery_run: "Web search run", sheet_import_xlsx: "Workbook upload", sheet_sync: "Workbook sync", account_brief: "Company brief",
  campaign_draft: "Email draft", account_profile: "Company research", person_profile: "Person research", people_search: "Finding people", identity_autoresolve: "Company matching",
  sources_validate_all: "Testing sources", sources_validate_daily: "Daily source test", crm_sync: "CRM sync", lemlist_sync: "Lemlist sync", ted_awards_window: "TED award notices",
  evaluate_event: "Scoring a finding", ted_probe: "TED check", company_facts: "Company facts",
};
export const kindName = (k: string) => tr(JOB_KIND[k] ?? humanize(k));

/** Each cause in plain English: a title ({} is the service) and what it means. */
export const CAUSE: Record<string, [string, string]> = {
  ai_credit: ["AI credit ran out", "The AI provider's account ran out of credit, so pages, summaries and checks could not be read. Nothing was wrong with the work itself."],
  budget: ["Monthly budget used up", "The monthly limit for AI or web search was reached, so the work stopped as designed. A new month, or a higher limit, lets it run."],
  fit_bug: ["Old fit-check bug", "A bug in the fit check left a database transaction broken, so the whole batch stopped. The bug is fixed; these can be retried."],
  cut_off: ["The AI answer was too long and got cut off", "The model hit its length limit before it finished, so the answer could not be used. Smaller batches fix it; a retry alone usually fails the same way."],
  unreadable_answer: ["The AI answer could not be read", "The model answered, but not in the expected format (often a list that broke off or a stray quote). A retry usually works; it keeps failing if the batch is too large."],
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
  if (g.state === "ongoing") return tr("Still happening. Last failure: {}.", when(g.last_at));
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
          <ul>{g.items.map((x: any) => <li key={x.id}><span className="fl-when">{when(x.created_at)}</span><span><b>{kindName(x.kind)}</b>{x.subject && <> · {x.subject}</>}<span className="fl-err">{x.error}</span></span></li>)}</ul>
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

/** Names for source ids and signal rule ids, so no table shows an internal code. Loaded only when a table needs them. */
function useNames(path: string, on: boolean, key: string, name: string): Record<string, string> {
  const r = useLoad(path, on);
  return Object.fromEntries((r.d?.items ?? []).map((x: any) => [x[key], x[name]]));
}

function Coverage({ d }: { d: any }) {
  const names = useNames("/api/admin/sources", d.yield_drops.length > 0, "source_id", "display_name");
  return <>
    <p className="small muted mt-0">{tr("Each cell counts what we found in the last {} days. A red 0 is worth a look: a source may be broken, or the market may just be quiet.", d.grid.days)}</p>
    <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Findings by market and topic")}><table className="grid-table"><thead><tr><th>{tr("Market")}</th>{d.grid.kinds.map((k: string) => <th key={k}>{tr(k)}</th>)}</tr></thead><tbody>
      {d.grid.markets.map((m: string) => <tr key={m}><td><b>{tr(m)}</b></td>{d.grid.kinds.map((k: string) => { const c = d.grid.cells.find((x: any) => x.market === m && x.kind === k); return <td key={k} className="num">{c.empty ? <Badge kind="bad">0</Badge> : number(c.count)}</td>; })}</tr>)}</tbody></table></div>
    <h4 className="adm-h4">{tr("Sources finding less than usual")}</h4>
    {d.yield_drops.length === 0 ? <Quiet>{tr("None. We only judge a source after two weeks of history, and only if it normally finds at least 5 items a week.")}</Quiet>
      : <ul className="tight small">{d.yield_drops.map((x: any) => <li key={x.source_id}><b>{names[x.source_id] ?? x.source_id}</b>: {tr("{} this week, usually about {}", number(x.last_7_days), number(x.usual_per_week))}</li>)}</ul>}
    <h4 className="adm-h4">{tr("Pages sellers say we missed")}</h4>
    {d.misses.total === 0 ? <Quiet>{tr("None yet. Sellers can paste a link on the Market feed.")}</Quiet> : <>
      <p className="small muted">{tr("{} reported: we already had {}, and missed {}. Each missed page shows which source should have found it.", d.misses.total, d.misses.caught, d.misses.missed)}</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Pages sellers say we missed")}><table><thead><tr><th>{tr("Page")}</th><th>{tr("Did we have it?")}</th><th>{tr("Result")}</th><th>{tr("Reported")}</th></tr></thead><tbody>
        {d.misses.items.map((m: any) => <tr key={m.id}><td><a href={m.url} target="_blank" rel="noreferrer noopener">{m.url.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a>{m.note && <div className="small muted">{m.note}</div>}</td>
          <td>{m.we_already_had_it ? <Badge kind="ok">{tr("Yes ({})", names[m.from_source] ?? m.from_source)}</Badge> : <Badge kind="bad">{tr("No")}</Badge>}</td><td className="small">{humanize(m.status)}{m.events_created > 0 ? ` · ${tr("{} findings", m.events_created)}` : ""}</td><td className="small">{when(m.reported_at)}</td></tr>)}</tbody></table></div>
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
  const sig = useNames("/api/library/signals", d.rules.length > 0, "rule_id", "signal");
  const nm = (id: string) => sig[id] ?? id;
  return <>
    <p className="small muted mt-0">{tr("For each kind of buying signal in the last {} days: how often it appeared, and whether sellers followed it up or dismissed it.", d.window_days)}</p>
    {d.flagged.length > 0 && <div className="err" role="alert">{tr("Dismissed more than 60% of the time, with enough decisions to judge:") + " "}<b>{d.flagged.map(nm).join("; ")}</b></div>}
    {d.rules.length === 0 ? <Quiet>{tr("Nothing to show: no signals fired in the last {} days.", d.window_days)}</Quiet> :
      <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("What sellers did with each signal")}><table className="cards adm-t"><thead><tr><th>{tr("Signal")}</th><th className="num">{tr("Seen")}</th><th className="num">{tr("Followed up")}</th><th className="num">{tr("Dismissed")}</th><th className="num">{tr("Dismissed share")}</th><th>{tr("Main reason for dismissing")}</th><th><span className="sr">{tr("Flag")}</span></th></tr></thead><tbody>
        {d.rules.map((r: any) => <tr key={r.rule_id}><td data-label={tr("Signal")} className="wide">{nm(r.rule_id)}</td><td data-label={tr("Seen")} className="num">{number(r.fired)}</td><td data-label={tr("Followed up")} className="num">{number(r.accepted)}</td><td data-label={tr("Dismissed")} className="num">{number(r.dismissed)}</td>
          <td data-label={tr("Dismissed share")} className="num">{r.dismissal_rate === null ? "—" : `${Math.round(r.dismissal_rate * 100)}%`}<div className="small muted">{r.enough_data ? tr("likely range {} to {}%", Math.round(r.interval_95[0] * 100), Math.round(r.interval_95[1] * 100)) : tr("Too few decisions to judge")}</div></td>
          <td data-label={tr("Main reason for dismissing")} className="small full">{r.top_dismiss_reason ? lab("dismiss", r.top_dismiss_reason) : "—"}</td><td>{r.flag && <Badge kind="bad">{tr("Look at this")}</Badge>}</td></tr>)}</tbody></table></div>}
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
    { key: "quality", show: can("admin.rules"), title: "Safety checks", desc: "Counts that should stay at zero, and how presented opportunities turned out.", r: metrics, status: qualityStatus, body: (d) => <Quality d={d} f={funnel.d} /> },
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

/* ------------------------------------------------------------------ job history: every piece of background work */

const GROUPS: [string, string, Kind][] = [["failed", "Failed", "bad"], ["retrying", "Will retry", "warn"], ["running", "Running", "info"], ["waiting", "Waiting", "info"], ["done", "Finished", "ok"], ["cancelled", "Cancelled", ""]];
const GROUP: Record<string, [string, Kind]> = Object.fromEntries(GROUPS.map(([k, l, c]) => [k, [l, c]]));
const CAUSE_TITLE = (c: string, who: string) => (c === "other" || !CAUSE[c] ? tr("Error") : tr(CAUSE[c][0], who || tr("The service")));

/** Status filter as a row of buttons, each with its count. */
function Chips({ value, onChange, counts, items, label }: { value: string; onChange: (v: string) => void; counts: Record<string, number>; items: [string, string, Kind][]; label: string }) {
  const all = Object.values(counts).reduce((a, n) => a + n, 0);
  return (
    <div className="fchips" role="group" aria-label={tr(label)}>
      <button type="button" aria-pressed={value === ""} onClick={() => onChange("")}>{tr("All")} <b>{number(all)}</b></button>
      {items.filter(([k]) => counts[k] || value === k).map(([k, l, c]) => <button key={k} type="button" aria-pressed={value === k} className={c} onClick={() => onChange(value === k ? "" : k)}>{tr(l)} <b>{number(counts[k] ?? 0)}</b></button>)}
    </div>
  );
}

export function JobHistory() {
  const [f, setF] = useQueryState({ since: "7d", kind: "", status: "", sort: "newest", page: "0" });
  const off = Number(f.page) || 0, LIMIT = 50;
  const r = useLoad(`/api/admin/jobs/history?${new URLSearchParams({ since: f.since, kind: f.kind, status: f.status, sort: f.sort, limit: String(LIMIT), offset: String(off) })}`);
  const d = r.d;
  const set = (p: Partial<typeof f>) => setF({ ...p, page: "0" });
  return (
    <div className="hist">
      <p className="adm-lead">{tr("Every piece of background work: reading sources, checking pages, research, imports and matching. See what ran, how long it took, and why something failed.")}</p>
      <div className="fbar">
        <Pick label="When" value={f.since} onChange={(v) => set({ since: v })} options={windows()} />
        <Pick label="Kind of job" value={f.kind} onChange={(v) => set({ kind: v })} options={[["", tr("Every kind")], ...(d?.kinds ?? []).map((k: any) => [k.kind, `${kindName(k.kind)} (${number(k.n)})`] as [string, string]),
          ...(f.kind && !(d?.kinds ?? []).some((k: any) => k.kind === f.kind) ? [[f.kind, kindName(f.kind)] as [string, string]] : [])]} />
        <Pick label="Order" value={f.sort} onChange={(v) => set({ sort: v })} options={[["newest", tr("Newest first")], ["oldest", tr("Oldest first")], ["slowest", tr("Slowest first")]]} />
        <button type="button" className="ghost" onClick={r.reload}>{tr("Refresh")}</button>
      </div>
      {d && <Chips label="Status" value={f.status} onChange={(v) => set({ status: v })} counts={d.groups} items={GROUPS} />}
      {r.err ? <Err e={r.err} onRetry={r.reload} /> : !d ? <Skeleton /> : d.items.length === 0 ? (
        <Quiet>{f.status || f.kind ? tr("No job matches these filters. Choose All, or a longer time.") : <>{tr("No background work in this time. If nothing has run for a day, check that the worker is running:") + " "}<Link to="/admin/health/pipeline">{tr("Processing pipeline")}</Link>.</>}</Quiet>
      ) : <>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Job history")}>
          <table className="cards adm-t">
            <thead><tr><th>{tr("Last activity")}</th><th>{tr("Job")}</th><th>{tr("Status")}</th><th className="num">{tr("Took")}</th><th className="num">{tr("Tries")}</th><th>{tr("What happened")}</th></tr></thead>
            <tbody>{d.items.map((j: any) => {
              const [gl, gk] = GROUP[j.group] ?? [humanize(j.status), ""];
              return <tr key={j.id}>
                <td data-label={tr("Last activity")} className="nowrap">{when(j.updated_at)}</td>
                <td data-label={tr("Job")} className="wide"><b>{kindName(j.kind)}</b>{j.subject && <div className="small muted">{j.subject}</div>}</td>
                <td data-label={tr("Status")} className="nowrap"><Say k={gk}>{tr(gl)}</Say>{j.group === "retrying" && j.run_after && <div className="small muted">{tr("next try {}", when(j.run_after))}</div>}</td>
                <td data-label={tr("Took")} className="num">{took(j.duration_s)}</td>
                <td data-label={tr("Tries")} className="num">{j.group === "retrying" || j.group === "failed" ? tr("{} of {}", number(j.attempts), number(j.max_attempts)) : j.attempts > 0 ? number(j.attempts) : "—"}</td>
                <td data-label={tr("What happened")} className="small full">{j.error ? <><b>{j.cause ? CAUSE_TITLE(j.cause, j.subject) : tr("Error")}</b><span className="fl-err">{j.error}</span></> : j.result?.length ? resultLine(j.result) : <span className="muted">—</span>}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <Pager total={d.total} offset={off} limit={LIMIT} onPage={(o) => setF({ page: String(o) })} label="Job history" />
      </>}
    </div>
  );
}

/* ------------------------------------------------------------------ source checks: each time a source was read */

const RUN: Record<string, [string, Kind]> = { success: ["Worked", "ok"], partial: ["Partly worked", "warn"], failed: ["Failed", "bad"], skipped: ["Skipped (not allowed)", ""], running: ["Running", "info"] };

export function SourceChecks() {
  const [f, setF] = useQueryState({ since: "7d", source: "", status: "", page: "0" });
  const off = Number(f.page) || 0, LIMIT = 50;
  const r = useLoad(`/api/admin/sources/checks?${new URLSearchParams({ since: f.since, source: f.source, status: f.status, limit: String(LIMIT), offset: String(off) })}`);
  const d = r.d; const t = d?.today;
  const set = (p: Partial<typeof f>) => setF({ ...p, page: "0" });
  return (
    <div className="hist">
      <p className="adm-lead">{tr("Each time a source was read: when, how long it took, what was new and any error. Use it to check that the sources you rely on are really being read.")}</p>
      {t && <dl className="glance-facts">
        <div><dt>{tr("Sources read in the last 24 hours")}</dt><dd>{number(t.checked)}<small>{tr("of {} live", number(t.live))}</small></dd></div>
        <div className={t.failing ? "bad" : ""}><dt>{tr("Last read failed")}</dt><dd>{number(t.failing)}</dd></div>
        <div><dt>{tr("New items found")}</dt><dd>{number(t.items_new)}</dd></div>
        <div><dt>{tr("Company pages checked")}</dt><dd>{number(t.company_pages)}</dd></div>
        <div><dt>{tr("Last read")}</dt><dd className="sm">{when(t.last_at)}</dd></div>
      </dl>}
      <div className="fbar">
        <Pick label="When" value={f.since} onChange={(v) => set({ since: v })} options={windows()} />
        <Pick label="Source" value={f.source} onChange={(v) => set({ source: v })} options={[["", tr("Every source")], ...(d?.sources ?? []).map((x: any) => [x.source_id, `${x.name} (${number(x.n)})`] as [string, string])]} />
        <button type="button" className="ghost" onClick={r.reload}>{tr("Refresh")}</button>
      </div>
      {d && <Chips label="Result" value={f.status} onChange={(v) => set({ status: v })} counts={d.by_status} items={Object.entries(RUN).map(([k, [l, c]]) => [k, l, c])} />}
      {r.err ? <Err e={r.err} onRetry={r.reload} /> : !d ? <Skeleton /> : d.items.length === 0 ? (
        <Quiet>{f.status || f.source ? tr("No source check matches these filters. Choose All, or a longer time.") : <>{tr("No source was read in this time. Sources are read on a schedule once they are live; see which are live in") + " "}<Link to="/admin/sources">{tr("Sources")}</Link>.</>}</Quiet>
      ) : <>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Source checks")}>
          <table className="cards adm-t">
            <thead><tr><th>{tr("Started")}</th><th>{tr("Source")}</th><th>{tr("Result")}</th><th className="num">{tr("Took")}</th><th className="num">{tr("New")}</th><th className="num">{tr("Changed")}</th><th>{tr("Error")}</th></tr></thead>
            <tbody>{d.items.map((x: any) => {
              const [l, k] = RUN[x.status] ?? [humanize(x.status), ""];
              return <tr key={x.id}>
                <td data-label={tr("Started")} className="nowrap">{when(x.started_at)}</td>
                <td data-label={tr("Source")} className="wide"><b>{x.display_name ?? x.source_id}</b></td>
                <td data-label={tr("Result")} className="nowrap"><Say k={k}>{tr(l)}</Say></td>
                <td data-label={tr("Took")} className="num">{took(x.duration_s)}</td>
                <td data-label={tr("New")} className="num">{number(x.items_new)}</td>
                <td data-label={tr("Changed")} className="num">{number(x.items_changed)}</td>
                <td data-label={tr("Error")} className="small full">{x.error ? <span className="fl-err">{x.error}</span> : <span className="muted">—</span>}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <Pager total={d.total} offset={off} limit={LIMIT} onPage={(o) => setF({ page: String(o) })} label="Source checks" />
      </>}
    </div>
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
            {k.meta && <p className="small muted m-0">{tr("{} KB, uploaded by {} on {}", (k.meta.size_bytes / 1024).toFixed(0), k.meta.uploaded_by, when(k.meta.uploaded_at))}</p>}
            <label className="adm-file"><span>{tr("Upload a new copy (.html)")}</span><input type="file" accept=".html,text/html" aria-label={tr("Upload {}", k.title)} onChange={(e) => { void send(k.id, e.target.files?.[0]); e.target.value = ""; }} /></label>
          </li>))}
      </ul>
    </>
  );
}

/* ------------------------------------------------------------------ data health: every source and the scheduler's automatic work */

/** How often, in words. */
export function every(s?: number | null): string {
  if (!s) return "—";
  if (s % 604800 === 0) return s === 604800 ? tr("Weekly") : tr("Every {} weeks", number(s / 604800));
  if (s % 86400 === 0) return s === 86400 ? tr("Daily") : tr("Every {} days", number(s / 86400));
  if (s % 3600 === 0) return s === 3600 ? tr("Hourly") : tr("Every {} hours", number(s / 3600));
  return tr("Every {} minutes", number(Math.round(s / 60)));
}

/** When something happens next: "Due now", "In 40 min", "Today, 14:00". */
function nextWhen(v?: string | null): string {
  if (!v) return tr("Paused");
  const ms = Date.parse(v) - Date.now();
  if (ms <= 60_000) return tr("At the next check");
  if (ms < 3_600_000) return tr("In {} min", number(Math.round(ms / 60_000)));
  return when(v);
}

/** Each source state: colour, name, and what it means. */
const DH_STATE: Record<string, [Kind, string, string]> = {
  failing: ["bad", "Failing", "Its last reads failed."],
  late: ["warn", "Late", "Not read on its schedule. The scheduler or the worker may have stopped, or the site keeps timing out."],
  never: ["warn", "Not read yet", "Switched on but not read yet. The next check reads it."],
  no_findings: ["warn", "Finds nothing useful", "It reads fine, but nothing it read in the last 30 days became a finding."],
  nothing_new: ["info", "Nothing new", "Read on schedule, but the page has not changed in 30 days."],
  ok: ["ok", "Working", "Read on schedule and finding things."],
  per_account: ["info", "Read in company checks", "Read for each account during its company-page check, not on a schedule of its own."],
  off: ["", "Switched off", "Not switched on. See Sources for why and how to switch it on."],
  on_demand: ["", "Used on request", "Used when someone asks for it, never on a timer."],
};
const DH_FILTERS: [string, string, Kind, string[]][] = [
  ["attention", "Needs a look", "warn", ["failing", "late", "never", "no_findings"]],
  ["working", "Working", "ok", ["ok", "nothing_new", "per_account"]],
  ["off", "Off or on request", "", ["off", "on_demand"]],
];
const DH_FLAG: Record<string, string> = {
  mostly_skipped: "Mostly off-topic: nine in ten articles fail the cheap word check.",
  waiting_to_be_read: "Some pages wait for the AI to read them (the monthly AI limit for background reading is reached, or the AI was unavailable).",
};
const FAMILY: Record<string, string> = {
  procurement: "Tenders", attention: "News and trade press", regulatory: "Regulators and standards", vendor: "Vendors and integrators", jobs: "Job boards",
  company_ir: "Company pages", registry: "Company registers", verification: "Page checks", discovery: "Web search", internal: "Internal",
};
const familyName = (f?: string | null) => tr(FAMILY[f ?? ""] ?? humanize(f));
/** What a lane's bare number counts: [one, many]. */
const LANE_UNIT: Record<string, [string, string]> = {
  finance_pilot_scans: ["company check queued", "company checks queued"], watchlist_scans: ["company check queued", "company checks queued"],
  fit_queued: ["fit check queued", "fit checks queued"], tender_summaries_queued: ["summary run queued", "summary runs queued"],
  renewals_refreshed: ["award re-read queued", "award re-reads queued"], identity_auto_queued: ["matching run queued", "matching runs queued"],
  source_check_queued: ["source test queued", "source tests queued"], market_tagged: ["finding tagged", "findings tagged"],
  public_bodies_tagged: ["public buyer marked", "public buyers marked"], retriaged: ["article sent back for reading", "articles sent back for reading"],
  procurement_events_backfilled: ["tender finding made", "tender findings made"], tenders_expired: ["tender closed", "tenders closed"],
  renewals_rechecked: ["award re-judged", "awards re-judged"], degraded_sources: ["source marked late", "sources marked late"],
  relinked_events: ["finding matched", "findings matched"], hypotheses_built: ["card built", "cards built"], verifications_requeued: ["page check retried", "page checks retried"],
  billing_jobs_recovered: ["page check re-queued", "page checks re-queued"], reevaluated: ["card re-scored", "cards re-scored"],
};
const HELD_BY: Record<string, string> = {
  ai_budget: "paused: the AI limit for this month is nearly used", daily_limit: "today's limit is reached", queue_busy: "waiting for earlier checks to finish",
};
const laneResult = (key: string, r: Record<string, unknown>): string => {
  if (key === "scan_rotation" && typeof r?.targets_total === "number") {          // progress through the target accounts, not bare counters
    const bits = [tr("{} of {} target accounts have had their own pages checked", number(Number(r.targets_checked ?? 0)), number(r.targets_total))];
    if (typeof r.queued_today === "number") bits.push(tr("{} queued today", number(r.queued_today)));
    if (typeof r.held_by === "string" && HELD_BY[r.held_by]) bits.push(tr(HELD_BY[r.held_by]));
    return bits.join(" · ");
  }
  if (typeof r?.accounts_still_without_a_search === "number") {
    return [tr("{} accounts searched in the last day", number(Number(r.accounts_searched ?? 0))), tr("{} still waiting for their first search", number(r.accounts_still_without_a_search))].join(" · ");
  }
  const pairs = Object.entries(r ?? {}).filter(([, v]) => typeof v === "number") as [string, number][];
  if (!pairs.length) return "";
  return pairs.map(([k, v]) => (k === "n" ? `${number(v)} ${tr((LANE_UNIT[key] ?? ["done", "done"])[v === 1 ? 0 : 1])}` : `${number(v)} ${tr(humanize(k).toLowerCase())}`)).join(" · ");
};
const LANE_CHOICES = [300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400];
const STORED: Record<string, string> = {
  organizations: "Companies", people: "People", legacy_account_research: "Workbook rows", events: "Findings", source_documents: "Pages and articles read",
  procurement_notices: "Tenders", awarded_contracts: "Award notices", job_postings: "Job adverts", signals: "Buying signals", jobs: "Background jobs (all time)",
  provider_usage: "Paid calls (all time)", audit_events: "Audit log entries",
};
const size = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

/** The way information flows, with the number at each step: switched-on sources, what they read, what became a finding, what is about one of
 * our accounts, and what is a buying signal. */
function Flow({ d }: { d: any }) {
  const src = d.sources as any[];
  const on = src.filter((s) => !["off", "on_demand"].includes(s.state)).length;
  const sum = (k: string) => src.reduce((a, s) => a + Number(s[k] ?? 0), 0);
  const steps: [string, number, string][] = [
    ["Sources switched on", on, tr("of {} in the registry", number(src.length))],
    ["New items read", sum("new_14d"), tr("last 14 days")],
    ["Became findings", sum("findings_30d"), tr("last 30 days")],
    ["About our accounts", sum("matched_30d"), tr("matched to a company")],
    ["Buying signals", sum("signals_30d"), tr("active now")],
  ];
  return (
    <ol className="dh-flow" aria-label={tr("How information flows")}>
      {steps.map(([l, n, sub], i) => <li key={l} className={i === steps.length - 1 ? "last" : undefined}><span className="dh-flow-n">{number(n)}</span><span className="dh-flow-l">{tr(l)}</span><span className="dh-flow-s">{sub}</span></li>)}
    </ol>
  );
}

function SourceRow({ s, onDone }: { s: any; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null);
  const [k, label] = DH_STATE[s.state] ?? ["", humanize(s.state)];
  const act = async (f: () => Promise<any>, ok: (r: any) => string) => { setBusy(true); setErr(null); try { onDone(ok(await f())); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const readNow = () => act(() => post(`/api/admin/sources/${s.source_id}/read-now`), (r) => r.queued ? tr("{}: queued to be read now.", s.name) : r.reason === "read_recently" ? tr("{} was read a few minutes ago; try again later.", s.name) : tr("{} is already waiting to be read.", s.name));
  const cadence = (v: number) => act(() => put(`/api/admin/sources/${s.source_id}/cadence`, { every_s: v }), () => tr("{} is now read {}.", s.name, every(v).toLowerCase()));
  const choices: number[] = [21600, 43200, 86400, 172800, 604800];
  return (
    <tr>
      <td data-label={tr("Source")} className="wide"><b>{s.name}</b><div className="small muted">{familyName(s.family)}</div>
        {s.note && <div className="small dh-flag">{s.note}</div>}
        {s.flags.map((f: string) => <div key={f} className="small dh-flag">{tr(DH_FLAG[f] ?? humanize(f))}</div>)}
        {s.error && <div className="small fl-err">{s.error}</div>}</td>
      <td data-label={tr("How often")} className="nowrap">{s.can_change_cadence && s.every_s ? (
        <select aria-label={tr("How often to read {}", s.name)} value={choices.includes(s.every_s) ? s.every_s : ""} disabled={busy} onChange={(e) => cadence(Number(e.target.value))}>
          {!choices.includes(s.every_s) && <option value="">{every(s.every_s)}</option>}
          {choices.map((c) => <option key={c} value={c}>{every(c)}</option>)}
        </select>) : s.state === "per_account" ? tr("With each company check") : every(s.every_s)}</td>
      <td data-label={tr("Read")} className="nowrap small"><div>{s.last_success_at ?? s.last_attempt_at ? tr("Last: {}", when(s.last_success_at ?? s.last_attempt_at)) : tr("Never read")}</div>{s.scheduled && <div className="muted">{tr("Next: {}", nextWhen(s.next_at))}</div>}</td>
      <td data-label={tr("New, 14 days")} className="num">{s.scheduled ? number(s.new_14d) : "—"}</td>
      <td data-label={tr("Findings, 30 days")} className="num">{number(s.findings_30d)}</td>
      <td data-label={tr("On our accounts")} className="num">{number(s.matched_30d)}</td>
      <td data-label={tr("Status")} className="dh-st"><Say k={k}>{tr(label)}</Say>{s.failures > 1 && <div className="small muted">{tr("{} failed reads in a row", number(s.failures))}</div>}
        {s.can_read_now && <button type="button" className="ghost dh-read" disabled={busy} onClick={readNow}>{tr("Read now")}</button>}
        <Err e={err} /></td>
    </tr>
  );
}

function LaneRow({ l, onDone }: { l: any; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null);
  const act = async (f: () => Promise<any>, ok: string) => { setBusy(true); setErr(null); try { await f(); onDone(ok); } catch (e) { setErr(e); } finally { setBusy(false); } };
  const res = laneResult(l.key, l.result);
  const state: [Kind, string] = !l.enabled ? ["", tr("Paused")] : l.error ? ["bad", tr("Last run failed")] : l.catching_up ? ["info", tr("Catching up")] : l.last_run_at ? ["ok", tr("Running")] : ["info", tr("Not run yet")];
  return (
    <li className={`dh-lane ${state[0]}`}>
      <div className="dh-lane-main">
        <b>{tr(l.label)}</b>
        <span className="small">{tr(l.what)}</span>
        <span className="small muted">{[tr("Runs: {}", every(l.every_s).toLowerCase()), l.last_run_at ? tr("last run {}", when(l.last_run_at)) : tr("not run yet"), l.enabled ? tr("next {}", nextWhen(l.next_at).toLowerCase()) : ""].filter(Boolean).join(" · ")}</span>
        {(res || l.error) && <span className="small">{l.error ? <span className="fl-err">{l.error}</span> : tr("Last run: {}", res)}</span>}
        {l.requested_by && <span className="small muted">{tr("{} asked for it to run at the next check.", l.requested_by)}</span>}
      </div>
      <div className="dh-lane-act">
        <Say k={state[0]}>{state[1]}</Say>
        {l.adjustable && <select aria-label={tr("How often: {}", tr(l.label))} value={l.every_s} disabled={busy || !l.enabled} onChange={(e) => act(() => put(`/api/admin/lanes/${l.key}`, { every_s: Number(e.target.value) }), tr("{} now runs {}.", tr(l.label), every(Number(e.target.value)).toLowerCase()))}>
          {Array.from(new Set([...LANE_CHOICES, l.every_s])).sort((a, b) => a - b).map((c) => <option key={c} value={c}>{every(c)}{c === l.default_every_s ? ` (${tr("usual")})` : ""}</option>)}
        </select>}
        {l.can_run && l.enabled && <button type="button" className="ghost" disabled={busy || !!l.requested_by} onClick={() => act(() => post(`/api/admin/lanes/${l.key}/run`), tr("{} runs at the next check, within five minutes.", tr(l.label)))}>{tr("Run at next check")}</button>}
        {l.can_pause && <button type="button" className={l.enabled ? "ghost" : ""} disabled={busy} aria-pressed={!l.enabled}
          onClick={() => act(() => put(`/api/admin/lanes/${l.key}`, { enabled: !l.enabled }), l.enabled ? tr("{} is paused. Nothing new of this kind is queued until you switch it on.", tr(l.label)) : tr("{} is switched on again.", tr(l.label)))}>{l.enabled ? tr("Pause") : tr("Switch on")}</button>}
        <Err e={err} />
      </div>
    </li>
  );
}

/** Data health: where each piece of information comes from, how often it is read, whether it still finds anything, and what the scheduler
 * does by itself. The things that need a look come first. */
export function DataHealth() {
  const [tick, setTick] = useState(0); const [msg, setMsg] = useState("");
  const [f, setF] = useQueryState({ show: "" });
  const r = useLoad("/api/admin/data-health", true, tick);
  const d = r.d;
  const done = (m: string) => { setMsg(m); setTick((t) => t + 1); };
  if (r.err) return <Err e={r.err} onRetry={r.reload} />;
  if (!d) return <Skeleton rows={4} />;
  const src = d.sources as any[];
  const countOf = (states: string[]) => src.filter((s) => states.includes(s.state)).length;
  const attention = countOf(DH_FILTERS[0][3]);
  const show = f.show || (attention ? "attention" : countOf(DH_FILTERS[1][3]) ? "working" : "all");
  const filt = DH_FILTERS.find(([k]) => k === show);
  const rows = filt ? src.filter((s) => filt[3].includes(s.state)) : src;
  const lanes = d.lanes as any[];
  const main = lanes.filter((l) => l.main), rest = lanes.filter((l) => !l.main);
  const restBad = rest.filter((l) => l.error).length;
  const jobs = (d.jobs as any[]).filter((j) => j.done + j.failed + j.retrying + j.waiting > 0);
  const failingJobs = jobs.filter((j) => j.failing);
  const sched = d.scheduler_at ? (Date.now() - Date.parse(d.scheduler_at)) / 60000 : null;
  const bl = d.backlog ?? {};
  return (
    <div className="dh">
      <p className="adm-lead">{tr("Where each piece of information comes from, how often it is read, and whether it still finds anything useful. Deal with the ones marked Needs a look first.")}</p>
      {msg && <div className="okmsg" role="status">{msg}</div>}
      <Flow d={d} />
      <div className="dh-notes">
        <Say k={sched === null ? "bad" : sched > 20 ? "bad" : "ok"}>{sched === null ? tr("The scheduler has never reported, so nothing is read by itself.") : sched > 20 ? tr("The scheduler last reported {}. Nothing new is collected until it runs again.", when(d.scheduler_at)) : tr("The scheduler checks every {} minutes; it last ran {}.", number(Math.round(d.tick_seconds / 60)), when(d.scheduler_at))}</Say>
        {d.ai.pct !== null && <Say k={d.ai.background_paused ? "bad" : d.ai.pct >= d.ai.background_stops_at_pct - 10 ? "warn" : "ok"}>{d.ai.background_paused
          ? tr("{}% of this month's AI limit is used. New articles are stored but not read by the AI until the 1st, so they become findings later. Your own research still works.", number(d.ai.pct))
          : tr("{}% of this month's AI limit is used. Background reading pauses at {}% so your own research always has room.", number(d.ai.pct), number(d.ai.background_stops_at_pct))}</Say>}
      </div>

      <section aria-labelledby="dh-src-h" className="dh-sec">
        <h3 id="dh-src-h" className="ov-h">{tr("Sources")}</h3>
        <p className="small muted dh-sub">{tr("One line per source: how often it is read, when it was last and will next be read, what it brought in and how much of that is about our accounts.")}</p>
        <div className="fchips" role="group" aria-label={tr("Show sources")}>
          {DH_FILTERS.map(([k, l, c, states]) => <button key={k} type="button" className={c} aria-pressed={show === k} onClick={() => setF({ show: k })}>{tr(l)} <b>{number(countOf(states))}</b></button>)}
          <button type="button" aria-pressed={show === "all"} onClick={() => setF({ show: "all" })}>{tr("All")} <b>{number(src.length)}</b></button>
        </div>
        {rows.length === 0 ? <Quiet>{show === "attention" ? tr("Nothing needs a look: every switched-on source is read on time and finds things.") : tr("No source in this group.")}</Quiet> : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Sources")}>
            <table className="cards adm-t dh-t">
              <thead><tr><th>{tr("Source")}</th><th>{tr("How often")}</th><th>{tr("Read")}</th><th className="num">{tr("New, 14 days")}</th>
                <th className="num">{tr("Findings, 30 days")}</th><th className="num">{tr("On our accounts")}</th><th>{tr("Status")}</th></tr></thead>
              <tbody>{rows.map((s) => <SourceRow key={s.source_id} s={s} onDone={done} />)}</tbody>
            </table>
          </div>)}
        {show === "attention" && rows.length > 0 && <ul className="dh-key small">{DH_FILTERS[0][3].filter((k) => rows.some((s) => s.state === k)).map((k) => <li key={k}><b>{tr(DH_STATE[k][1])}</b>: {tr(DH_STATE[k][2])}</li>)}</ul>}
      </section>

      <section aria-labelledby="dh-lanes-h" className="dh-sec">
        <h3 id="dh-lanes-h" className="ov-h">{tr("Automatic work")}</h3>
        <p className="small muted dh-sub">{tr("What the platform does by itself, how often, and what it did last time. Pausing stops new work of that kind; nothing already found is lost.")}</p>
        <ul className="dh-lanes">{main.map((l) => <LaneRow key={l.key} l={l} onDone={done} />)}</ul>
        <Fold title={tr("Housekeeping ({} small steps)", number(rest.length))} desc={tr("Steps that keep the data right: tags, expiry, matching, scores. Most run at every check.")}
          status={restBad ? ["bad", tr("{} failed on their last run", number(restBad))] : ["ok", tr("All ran fine")]}>
          <ul className="dh-lanes">{rest.map((l) => <LaneRow key={l.key} l={l} onDone={done} />)}</ul>
        </Fold>
      </section>

      <section aria-labelledby="dh-jobs-h" className="dh-sec">
        <h3 id="dh-jobs-h" className="ov-h">{tr("Background jobs, last 7 days")}</h3>
        <p className="small muted dh-sub">{tr("Each kind of job the worker ran: how many finished, failed or wait. A kind that keeps failing is named with its cause; see Job history for each one.")}</p>
        {jobs.length === 0 ? <Quiet>{tr("No background job ran in the last 7 days.")}</Quiet> : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Background jobs, last 7 days")}>
            <table className="cards adm-t">
              <thead><tr><th>{tr("Job")}</th><th className="num">{tr("Finished")}</th><th className="num">{tr("Failed")}</th><th className="num">{tr("Will retry")}</th><th className="num">{tr("Waiting")}</th><th>{tr("Last finished")}</th><th>{tr("Problem")}</th></tr></thead>
              <tbody>{jobs.map((j) => <tr key={j.kind}>
                <td data-label={tr("Job")} className="wide"><Link to={`/admin/job-history?kind=${j.kind}&since=7d`}><b>{kindName(j.kind)}</b></Link></td>
                <td data-label={tr("Finished")} className="num">{number(j.done)}</td>
                <td data-label={tr("Failed")} className="num">{j.failed ? <b className="dh-bad">{number(j.failed)}</b> : "0"}</td>
                <td data-label={tr("Will retry")} className="num">{number(j.retrying)}</td>
                <td data-label={tr("Waiting")} className="num">{number(j.waiting)}</td>
                <td data-label={tr("Last finished")} className="nowrap">{when(j.last_done)}</td>
                <td data-label={tr("Problem")} className="small full">{j.cause ? <><Say k={j.failing ? "bad" : "warn"}>{tr(CAUSE[j.cause]?.[0] ?? "Error", tr("The service"), "")}</Say>{j.failing && <div className="small muted">{tr("Still failing: the last attempt failed after the last success.")}</div>}</> : <span className="muted">—</span>}</td>
              </tr>)}</tbody>
            </table>
          </div>)}
        {failingJobs.length > 0 && <p className="small"><Link to="/admin/health/jobs">{tr("See the failed jobs grouped by cause, and retry or dismiss them")}</Link></p>}
      </section>

      <div className="adm-two dh-sec">
        <section className="adm-panel" aria-labelledby="dh-wait-h">
          <h3 id="dh-wait-h" className="adm-h4">{tr("Waiting to be done")}</h3>
          <dl className="dh-dl">
            <div><dt>{tr("Pages waiting for the AI to read them")}</dt><dd>{bl.pages_to_read ?? "—"}</dd></div>
            <div><dt>{tr("Page checks in progress")}</dt><dd>{bl.page_checks ?? "—"}</dd></div>
            <div><dt>{tr("Open tenders waiting for a summary")}</dt><dd>{bl.tender_summaries ?? "—"}</dd></div>
            <div><dt>{tr("Company names waiting for a match")}</dt><dd>{bl.company_names ?? "—"}</dd></div>
          </dl>
        </section>
        <section className="adm-panel" aria-labelledby="dh-store-h">
          <h3 id="dh-store-h" className="adm-h4">{tr("What is stored")}</h3>
          <dl className="dh-dl">{(d.stored as any[]).map((t) => <div key={t.table}><dt>{tr(STORED[t.table] ?? humanize(t.table))}</dt><dd>{number(t.rows)}<small>{size(t.bytes)}</small></dd></div>)}</dl>
          <p className="small muted m-0">{tr("Row counts are the database's own estimate, refreshed as tables change.")}</p>
        </section>
      </div>
    </div>
  );
}
