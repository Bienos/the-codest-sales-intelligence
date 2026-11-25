import { useEffect, useRef, useState } from "react";
import { Navigate, useSearchParams, Link } from "react-router-dom";
import { api, post } from "../api";
import { counted, number, tr } from "../locale";
import { Err, Skeleton } from "../components/ui";
import { FindingBrief, FindingItem, FindingsCoverage, FindingsData, FindingLead, groupByDay, signalFamily, useCan } from "../components/Findings";
import { REGIONS } from "./targetMarket";
import { signalName } from "../components/Signals";
import "../styles/findings.css";

/** Tenders have their own page, covering every buyer; old links to the tender view of the findings land there. */
export default function Feed() {
  const [params] = useSearchParams();
  return params.get("kind") === "tender" ? <Navigate to="/tenders" replace /> : <AllFindings />;
}

const WINDOWS: [string, string][] = [["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"]];
const DEFAULT_DAYS = "90";
const PAGE = 20;
const del = (p: string) => api(p, { method: "DELETE" });

const Ico = ({ children, className = "fx-ico" }: { children: React.ReactNode; className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" aria-hidden="true">{children}</svg>
);
const Chevron = () => <svg className="fx-chev" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M2.5 4.5L6 8l3.5-3.5" /></svg>;
const IconSearch = () => <Ico><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5l5 5" /></Ico>;
const IconPlace = () => <Ico><path d="M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0113 0c0 4.9-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></Ico>;
const IconClock = () => <Ico><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Ico>;
/** The three steps: sources read, finds matched, you act. */
const IconRead = () => <Ico className="fx-step-ico"><path d="M4 5h11v15H4zM15 8h5v12h-5M7 9h5M7 12.5h5M7 16h3" /></Ico>;
const IconMatch = () => <Ico className="fx-step-ico"><circle cx="7" cy="12" r="3.5" /><circle cx="17" cy="12" r="3.5" /><path d="M10.5 12h3" /></Ico>;
const IconAct = () => <Ico className="fx-step-ico"><path d="M4 12.5l4.5 4.5L20 6" /></Ico>;

/** Every page asked for so far, appended, so the timeline grows with "Show more" instead of paging. */
function useTimeline(base: string) {
  const [paging, setPaging] = useState({ base, page: 1, revision: 0 });
  const page = paging.base === base ? paging.page : 1;
  const revision = paging.base === base ? paging.revision : 0;
  const [state, setState] = useState<{ base: string; page: number; data?: FindingsData; error?: unknown }>();
  useEffect(() => {
    const request = new AbortController();
    api<FindingsData>(`/api/findings?${base}&page=${page}&limit=${PAGE}`, { signal: request.signal })
      .then((data) => {
        if (request.signal.aborted) return;
        setState((s) => {
          const before = page > 1 && s?.base === base && s.data ? s.data.items : [];
          const seen = new Set(before.map((i) => i.id));
          return { base, page, data: { ...data, items: [...before, ...data.items.filter((i) => !seen.has(i.id))] } };
        });
      })
      .catch((error) => { if (!request.signal.aborted) setState((s) => ({ base, page, data: s?.base === base ? s.data : undefined, error })); });
    return () => request.abort();
  }, [base, page, revision]);
  const current = state?.base === base;
  return {
    data: current ? state?.data : undefined,
    error: current && state?.page === page ? state?.error : undefined,
    loading: !current || state?.page !== page,
    more: () => setPaging({ base, page: page + 1, revision }),
    retry: () => setPaging({ base, page, revision: revision + 1 }),
  };
}

function AllFindings() {
  const [params, setParams] = useSearchParams();
  const can = useCan();
  const days = WINDOWS.some(([k]) => k === params.get("days")) ? params.get("days")! : DEFAULT_DAYS;
  const region = REGIONS.some(([k]) => k === params.get("region")) ? params.get("region")! : "";
  const rule = /^[A-Z]{3}-\d{2}$/.test(params.get("rule") ?? "") ? params.get("rule")! : "";
  const watched = params.get("watch") === "1";
  const hidden = params.get("hidden") === "1";
  const q = params.get("q") ?? "";
  const single = params.get("finding");
  const [search, setSearch] = useState(q);
  const base = new URLSearchParams(Object.entries({
    scope: "archive", qualification: "all", days, region, rule, q, watched: watched ? "true" : "", hidden: hidden ? "true" : "", event: single ?? "",
  }).filter(([, v]) => v)).toString();
  const { data, error, loading, more, retry } = useTimeline(base);
  // what the person changed on this visit: dismissed findings fold away in place (so undo stays close), stars flip at once
  const [answers, setAnswers] = useState<Record<string, string | null>>({});
  const [stars, setStars] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<unknown>(null);

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key); }
    next.delete("finding");
    next.delete("page");
    setParams(next, { replace: true });
  };
  // Search as you type, a moment after the last key.
  const latest = useRef(q);
  latest.current = q;
  useEffect(() => {
    const value = search.trim();
    if (value === latest.current) return;
    const timer = window.setTimeout(() => update({ q: value }), 350);
    return () => window.clearTimeout(timer);
  }, [search]);     // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = Boolean(q || region || rule || watched || days !== DEFAULT_DAYS);
  const clear = () => { setSearch(""); update({ q: "", region: "", rule: "", watch: "", days: "" }); };
  const windowLabel = tr(WINDOWS.find(([k]) => k === days)?.[1] ?? "Last 90 days").toLowerCase();
  const items: FindingItem[] = (data?.items ?? []).map((i) => ({
    ...i,
    dismissed: i.id in answers ? answers[i.id] : i.dismissed ?? null,
    watched: stars[i.organization.id] ?? i.watched,
  }));
  const groups = groupByDay(items);
  // nothing can be found while no source is switched on: the empty page then explains that, without filters to play with
  const quiet = Boolean(data && !filtered && !data.items.length && !data.has_more && (data.coverage.no_active_monitoring || !data.coverage.monitored_sources));
  const kinds = data?.kinds ?? [];
  const canWatch = can("accounts.read");
  const hiddenCount = Math.max(0, (data?.hidden_count ?? 0) + Object.entries(answers).filter(([id, a]) => a && !data?.items.find((i) => i.id === id)?.dismissed).length
    - Object.entries(answers).filter(([id, a]) => !a && data?.items.find((i) => i.id === id)?.dismissed).length);

  const dismiss = async (item: FindingItem, reason: string) => {
    setActionError(null);
    const before = answers[item.id];
    setAnswers((a) => ({ ...a, [item.id]: reason }));
    try { await post(`/api/findings/${item.id}/dismiss`, { reason }); }
    catch (e) { setAnswers((a) => ({ ...a, [item.id]: before ?? null })); setActionError(e); }
  };
  const restore = async (item: FindingItem) => {
    setActionError(null);
    const before = item.dismissed;
    setAnswers((a) => ({ ...a, [item.id]: null }));
    try { await del(`/api/findings/${item.id}/dismiss`); }
    catch (e) { setAnswers((a) => ({ ...a, [item.id]: before ?? null })); setActionError(e); }
  };
  const watch = async (item: FindingItem) => {
    setActionError(null);
    const on = !item.watched;
    setStars((s) => ({ ...s, [item.organization.id]: on }));
    try { await (on ? post(`/api/accounts/${item.organization.id}/watch`) : del(`/api/accounts/${item.organization.id}/watch`)); }
    catch (e) { setStars((s) => ({ ...s, [item.organization.id]: !on })); setActionError(e); }
  };

  return <div className="fx-page">
    <header className="fx-head">
      <h1>{tr("All findings")}</h1>
      <p className="fx-lede">{tr("Everything our sources found about the companies on your list, newest first. Skim it, open what matters and mark the rest not relevant.")}</p>
      <Steps coverage={data?.coverage} />
      <p className="fx-other">{tr("Want to know who to contact first?")} <Link to="/signals">{tr("Buying signals")}</Link> {tr("ranks the companies by how strong their signals are. Tenders have their own page:")} <Link to="/tenders">{tr("Open tenders")}</Link>.</p>
    </header>

    {single ? <p className="fx-single"><span>{tr("You are looking at one finding.")}</span> <Link to="/feed">{tr("Show all findings")}</Link></p>
      : !quiet && <section className="fx-tools" aria-label={tr("Search and filter")}>
        <form className="fx-bar" role="search" onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }}>
          <label className="fx-ctl fx-ctl-q"><span className="fb-vh">{tr("Search")}</span><IconSearch />
            <input type="search" maxLength={120} placeholder={tr("Search a company or what happened")} value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <label className="fx-ctl"><span className="fb-vh">{tr("Region")}</span><IconPlace />
            <select value={region} onChange={(e) => update({ region: e.target.value })}>
              <option value="">{tr("All regions")}</option>{REGIONS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
            </select><Chevron /></label>
          <label className="fx-ctl"><span className="fb-vh">{tr("Time window")}</span><IconClock />
            <select value={days} onChange={(e) => update({ days: e.target.value === DEFAULT_DAYS ? "" : e.target.value })}>
              {WINDOWS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
            </select><Chevron /></label>
          <label className="fx-ctl fx-watch">
            <input type="checkbox" checked={watched} onChange={(e) => update({ watch: e.target.checked ? "1" : "" })} />
            <span className="fx-switch" aria-hidden="true" />
            <span>{tr("Only my watchlist")}{Boolean(data?.watchlist) && <b>{number(data!.watchlist!)}</b>}</span>
          </label>
        </form>
        <div className="fx-kinds" role="group" aria-label={tr("Kind of buying signal")}>
          <button type="button" className="fx-kind" aria-pressed={!rule} onClick={() => update({ rule: "" })}>{tr("All news")}</button>
          {kinds.map((k) => <button key={k.rule_id} type="button" className={`fx-kind fam-${signalFamily(k.rule_id)}`} aria-pressed={rule === k.rule_id}
            onClick={() => update({ rule: rule === k.rule_id ? "" : k.rule_id })}><i aria-hidden="true" />{signalName(k.rule_id, k.name)}<b>{number(k.findings)}</b></button>)}
          {rule && !kinds.some((k) => k.rule_id === rule) && <button type="button" className={`fx-kind fam-${signalFamily(rule)}`} aria-pressed onClick={() => update({ rule: "" })}>
            <i aria-hidden="true" />{signalName(rule)}<b>0</b></button>}
          {data && kinds.length === 0 && !rule && <span className="fx-kinds-none">{tr("No buying signal in this window yet. Signal types appear here as findings come in.")}</span>}
        </div>
      </section>}

    <div className="fx-grid">
      <div className="fx-main">
        {data && items.length > 0 && <p className="fx-count" role="status" aria-live="polite">
          <span><b>{counted(items.length, ["finding", "findings"], ["znalezisko", "znaleziska", "znalezisk"])}</b>{!single && <> · {windowLabel} · {tr("newest first")}</>}</span>
          {!single && hiddenCount > 0 && <button type="button" className="fb-link" onClick={() => update({ hidden: hidden ? "" : "1" })}>
            {hidden ? tr("Hide the ones marked not relevant") : tr("Show {} marked not relevant", number(hiddenCount))}</button>}
          {filtered && !single && <button type="button" className="fb-link" onClick={clear}>{tr("Clear filters")}</button>}
        </p>}
        {actionError ? <Err e={actionError} /> : null}
        {error ? <Err e={error} onRetry={retry} /> : null}
        {!data && !error && <Skeleton rows={4} label={tr("Loading findings")} />}
        {data && items.length === 0 && !data.has_more && <Nothing data={data} filtered={filtered} watched={watched} q={q} days={days}
          clear={clear} longer={() => update({ days: "365" })} canAdmin={can("admin.sources")} />}
        {groups.length > 0 && <div className={`fx-rail${loading ? " is-loading" : ""}`} aria-busy={loading}>
          {groups.map((g) => <section key={g.key} className="fx-day" aria-labelledby={`fx-day-${g.key}`}>
            <h2 className="fx-day-h" id={`fx-day-${g.key}`}><span className="fx-day-l">{g.label}</span>
              <span className="fx-day-s">{g.span}{g.span && " · "}{counted(g.items.length, ["finding", "findings"], ["znalezisko", "znaleziska", "znalezisk"])}</span></h2>
            {g.items.map((item) => <FindingBrief key={item.id} item={item} canPeople={Boolean(can("contacts.read"))} canCampaign={Boolean(can("draft.create"))}
              onWatch={canWatch ? () => watch(item) : undefined} onDismiss={(reason) => dismiss(item, reason)} onRestore={() => restore(item)} />)}
          </section>)}
        </div>}
        {data?.has_more && <div className="fx-more">
          <button type="button" onClick={more} disabled={loading}>{loading ? tr("Loading…") : tr("Show older findings")}</button>
        </div>}
        {data && !data.has_more && items.length > 0 && !single && <p className="fx-end">{tr("That is everything in this time window.")}
          {days !== "365" && <> <button type="button" className="fb-link" onClick={() => update({ days: "365" })}>{tr("Look at the last 12 months")}</button></>}</p>}
      </div>
      <aside className="fx-aside" aria-label={tr("Help us find more")}>
        <ReportMiss />
        {data && data.leads.length > 0 && <Checking leads={data.leads} />}
      </aside>
    </div>
  </div>;
}

/** The three steps, with the live state of the first one: are the sources actually being read? */
function Steps({ coverage }: { coverage?: FindingsCoverage }) {
  const monitored = coverage?.monitored_sources ?? 0;
  const broken = coverage?.unhealthy.length ?? 0;
  const off = coverage && (coverage.no_active_monitoring || monitored === 0);
  const state = !coverage ? "" : off || broken > monitored / 2 ? "is-warn" : broken > 0 ? "is-part" : "is-ok";
  const status = !coverage ? tr("Checking the sources…")
    : off ? tr("No source is switched on yet, so nothing new can come in.")
    : broken > 0 ? tr("{} of {} sources are working.", number(monitored - broken), number(monitored))
    : tr("All {} sources are working.", number(monitored));
  return <ol className="fx-steps" aria-label={tr("How findings get here")}>
    <li className={`fx-step ${state}`}>
      <IconRead /><span className="fx-step-n">1</span>
      <b>{tr("We read the sources every day")}</b>
      <span>{tr("News sites, job boards, company pages and official registers.")}</span>
      <span className="fx-step-live"><i aria-hidden="true" />{status} <Link to="/library?tab=sources">{tr("See the sources")}</Link></span>
      {coverage?.extraction_waiting_for_credit && <span className="fx-step-live is-warn"><i aria-hidden="true" />{tr("New pages are waiting: the reading service is out of credit.")}</span>}
    </li>
    <li className="fx-step">
      <IconMatch /><span className="fx-step-n">2</span>
      <b>{tr("We match each find to an account")}</b>
      <span>{tr("We check it against the original page, then tag the buying signal it points to, if any.")}</span>
    </li>
    <li className="fx-step">
      <IconAct /><span className="fx-step-n">3</span>
      <b>{tr("You skim and act")}</b>
      <span>{tr("Open the company or the source. Mark the rest not relevant and it stops showing.")}</span>
    </li>
  </ol>;
}

/** Why the list is empty, in plain words, and what to do about it. */
function Nothing({ data, filtered, watched, q, days, clear, longer, canAdmin }: {
  data: FindingsData; filtered: boolean; watched: boolean; q: string; days: string; clear: () => void; longer: () => void; canAdmin?: boolean;
}) {
  const off = data.coverage.no_active_monitoring || data.coverage.monitored_sources === 0;
  if (watched && !data.watchlist) return <div className="fx-empty">
    <b>{tr("Your watchlist is empty")}</b>
    <p>{tr("Star a company here or in Accounts & leads to follow it. Its news then shows when this switch is on.")}</p>
    <p className="fx-empty-acts"><button type="button" onClick={clear}>{tr("Show all findings")}</button><Link to="/accounts">{tr("Go to Accounts & leads")}</Link></p>
  </div>;
  if (filtered && !(off && !q)) return <div className="fx-empty">
    <b>{tr("Nothing matches these filters")}</b>
    <p>{q ? tr("Nothing matches “{}”. Check the spelling, or search for a company name or a word from the news.", q) : tr("Try another region or kind of signal, or a longer time window.")}</p>
    <p className="fx-empty-acts"><button type="button" onClick={clear}>{tr("Clear filters")}</button>
      {days !== "365" && <button type="button" className="fb-link" onClick={longer}>{tr("Look at the last 12 months")}</button>}</p>
  </div>;
  if (off) return <div className="fx-empty is-off">
    <b>{tr("Nothing found yet, because no source is switched on")}</b>
    <p>{tr("This page fills up by itself once sources are being read: every day, new findings about the companies on your list appear here, newest first.")}</p>
    <p>{canAdmin ? tr("Sources are tested every day and switch on by themselves when they pass. You can also test them and switch them on now.") : tr("Ask an administrator to switch sources on.")}</p>
    <p className="fx-empty-acts">{canAdmin && <Link className="fx-cta" to="/admin/sources">{tr("Switch on sources")}</Link>}
      <Link to="/accounts">{tr("Meanwhile, browse your accounts")}</Link></p>
  </div>;
  return <div className="fx-empty">
    <b>{tr("No findings in this time window")}</b>
    <p>{tr("The sources were read, but nothing new about the companies on your list was found in this time.")}</p>
    {days !== "365" && <p className="fx-empty-acts"><button type="button" onClick={longer}>{tr("Look at the last 12 months")}</button></p>}
  </div>;
}

/** Pages a web search turned up that may mention a tender: they become findings once checked. */
function Checking({ leads }: { leads: FindingLead[] }) {
  const STATUS: Record<string, string> = {
    queued: "Waiting to be checked", fetching: "Reading the page", extracting: "Reading the page", waiting_budget: "Waiting for AI budget",
    no_event: "Nothing to report", blocked: "Page not allowed", failed: "Could not check",
  };
  return <details className="fx-card fx-checking">
    <summary>{tr("Links we are still checking")} <b>{number(leads.length)}</b></summary>
    <p className="small muted">{tr("Pages a web search found that may mention a tender. Each becomes a finding once it has been checked.")}</p>
    <ul>{leads.map((lead) => <li key={lead.id}>
      <a href={lead.url} target="_blank" rel="noreferrer noopener">{lead.claimed_text || lead.url} ↗</a>
      <span className="muted">{tr(STATUS[lead.status] ?? "Could not check")}</span>
    </li>)}</ul>
  </details>;
}

function ReportMiss() {
  const [url, setUrl] = useState(""); const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [res, setRes] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const send = async () => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await post("/api/misses", { url: url.trim(), note: note.trim() })); setUrl(""); setNote(""); } catch (e) { setErr(e); }
    setBusy(false);
  };
  return (
    <form className="fx-card fx-miss" onSubmit={(e) => { e.preventDefault(); if (!busy && /^https?:\/\//i.test(url.trim())) void send(); }}>
      <h2>{tr("Saw something we missed?")}</h2>
      <p className="small muted">{tr("Paste the link. We tell you if we already had it, and read it if not. It also shows us which source to improve.")}</p>
      <label htmlFor="miss-url">{tr("Link to the page")}</label>
      <input id="miss-url" type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
      <label htmlFor="miss-note">{tr("What is it? (optional)")}</label>
      <input id="miss-note" maxLength={400} placeholder={tr("e.g. Bank X chose a new payments vendor")} value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="primary" type="submit" disabled={busy || !/^https?:\/\//i.test(url.trim())}>{busy ? tr("Sending…") : tr("Report it")}</button>
      {res && <p className="small mb-0" role="status">{res.already_known ? tr("We already have this page.") : res.duplicate ? tr("Someone already reported this. It is being checked.") : tr("Thanks. It is queued and will be read shortly.")}</p>}
      {err ? <Err e={err} /> : null}
    </form>
  );
}
