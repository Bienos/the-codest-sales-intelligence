import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { counted, number, tr, useLanguage } from "../locale";
import { Empty, Err, PageHeader, Skeleton, safeHref } from "../components/ui";
import { REGIONS, countryName } from "./targetMarket";
import "../styles/tenders.css";

type FitLevel = "strong" | "possible" | "weak" | "none";
type Summary = {
  title_en: string; summary_en?: string; wants?: string[]; work_type?: string; technologies?: string[]; requirements?: string[];
  fit?: { level: FitLevel; reason?: string; reason_pl?: string }; title_pl?: string; summary_pl?: string; wants_pl?: string[]; requirements_pl?: string[];
};
type Tender = {
  id: string; notice_id: string; title: string; category: string | null; buyer: string; buyer_kind: "finance" | "public";
  account: { id: string; name: string } | null; country: string | null; region: string; deadline: string; deadline_tz: string | null;
  days_left: number; status: string; url: string; source: string; scope: string | null; value: number | null; currency?: string | null;
  checked_at: string | null; check_is_old: boolean; published?: string | null;
  summary?: Summary | null; summary_state?: "written" | "waiting" | "skipped"; lots?: number | null; duration?: string | null;
};
type Result = {
  items: Tender[]; has_more: boolean; page: number;
  counts: { total: number; finance: number; closing_this_week: number; closing_too_soon: number; other_regions?: number; summarised?: number; waiting_summary?: number };
  facets?: { fit: Record<string, number>; work_type: Record<string, number> };
  sources: { source_id: string; display_name: string; status: string; last_success_at: string | null }[]; last_updated?: string | null; refreshing?: boolean;
  summaries?: { waiting: number; running: boolean };
};
type Refresh = { queued: string[]; already_waiting: string[]; not_switched_on: string[] };
type Summarise = { queued: boolean; already_running: boolean; waiting: number; cap: number };

const OUR_MARKETS = "core";      // the default: UK, DACH, Nordics and Poland. "all" in the address means every region.
const WORK_TYPES: [string, string][] = [
  ["software_development", "Software development"], ["maintenance_support", "Maintenance and support"], ["data_ai", "Data and AI"],
  ["testing_qa", "Testing and QA"], ["consulting", "Consulting"], ["licences_products", "Licences and products"],
  ["infrastructure_hardware", "Infrastructure and hardware"], ["other", "Other work"],
];
const FIT_LABEL: Record<FitLevel, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit", none: "Not a fit" };
const FIT_STEPS: Record<FitLevel, number> = { strong: 3, possible: 2, weak: 1, none: 0 };
const WITHIN = ["7", "14", "30", "60"];
const SORTS: [string, string][] = [["deadline", "Soonest deadline"], ["fit", "Best fit first"], ["value", "Highest value"], ["newest", "Newest first"]];

/** TED shows each notice in every EU language: open it in the language the app is in. Other sites' links are left as they are. */
const tedPage = (url: string, language: string) => url.replace(/^https:\/\/ted\.europa\.eu\/[a-z]{2}\/notice\//, `https://ted.europa.eu/${language}/notice/`);
const sameTitle = (a?: string, b?: string) => (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();
const workLabel = (key?: string) => tr(WORK_TYPES.find(([k]) => k === key)?.[1] ?? "Other work");

/** The summary in the app's language: Polish when there is a Polish version, English otherwise. */
function localised(s: Summary | null | undefined, language: string) {
  if (!s) return null;
  const pl = language === "pl";
  const pick = <T,>(p: T | undefined, e: T | undefined) => (pl && p && (!Array.isArray(p) || p.length) ? p : e);
  return {
    title: pick(s.title_pl, s.title_en) ?? s.title_en, text: pick(s.summary_pl, s.summary_en), wants: pick(s.wants_pl, s.wants) ?? [],
    requirements: pick(s.requirements_pl, s.requirements) ?? [], reason: pick(s.fit?.reason_pl, s.fit?.reason), technologies: s.technologies ?? [],
    fit: s.fit?.level, work: s.work_type, lang: pl && s.title_pl ? "pl" : "en",
  };
}

function money(value: number, currency: string | null | undefined, language: string) {
  if (currency) {
    try { return new Intl.NumberFormat(language === "pl" ? "pl-PL" : "en-GB", { style: "currency", currency, maximumFractionDigits: 0 }).format(value); }
    catch { /* an unknown currency code: plain number below */ }
  }
  return number(Math.round(value));
}

/** "24 month, 12 month" (per lot, as TED gives it) in words. */
function durationText(d: string) {
  return d.split(",").map((part) => {
    const m = part.trim().match(/^(\d+)\s*(day|week|month|year)/i);
    if (!m) return part.trim();
    const n = Number(m[1]);
    const forms: Record<string, [[string, string], [string, string, string]]> = {
      day: [["day", "days"], ["dzień", "dni", "dni"]], week: [["week", "weeks"], ["tydzień", "tygodnie", "tygodni"]],
      month: [["month", "months"], ["miesiąc", "miesiące", "miesięcy"]], year: [["year", "years"], ["rok", "lata", "lat"]],
    };
    const [en, pl] = forms[m[2].toLowerCase()];
    return counted(n, en, pl);
  }).join(", ");
}

function FitMeter({ level }: { level?: FitLevel }) {
  const on = level ? FIT_STEPS[level] : 0;
  return <span className={`tn-meter tn-fit-${level ?? "unknown"}`} aria-hidden="true">{[1, 2, 3].map((i) => <i key={i} className={i <= on ? "on" : ""} />)}</span>;
}

function Due({ t }: { t: Tender }) {
  return <div className="tn-due" role="img" aria-label={tr("Closes in {}", counted(t.days_left, ["day", "days"], ["dzień", "dni", "dni"]))}>
    <b>{t.days_left}</b>
    <span>{t.days_left === 1 ? tr("day left") : tr("days left")}</span>
  </div>;
}

function Buyer({ t }: { t: Tender }) {
  return <p className="tn-buyer">
    <span className="tn-buyer-name">{t.account ? <Link to={`/accounts/${t.account.id}`}>{t.buyer}</Link> : t.buyer}</span>
    <span className="tn-country">{countryName(t.country) ?? tr("Country not given")}</span>
    {t.buyer_kind === "finance" && <span className="tn-tag">{tr("Finance or insurance")}</span>}
  </p>;
}

function OfficialLink({ t, language }: { t: Tender; language: string }) {
  const href = safeHref(tedPage(t.url, language));
  return href ? <a className="tn-official" href={href} target="_blank" rel="noreferrer noopener">{tr("Open official notice")} <span aria-hidden="true">↗</span></a> : null;
}

function TenderCard({ t, language }: { t: Tender; language: string }) {
  const s = localised(t.summary, language);
  const waiting = !s;
  return <li className={`tn-card${t.days_left <= 7 ? " is-soon" : ""}`}>
    <Due t={t} />
    <div className="tn-body">
      <h3 className="tn-title" lang={s ? s.lang : undefined}>{s ? s.title : t.title}</h3>
      <Buyer t={t} />
      {s ? <>
        {s.text && <p className="tn-summary" lang={s.lang}>{s.text}</p>}
        {s.wants.length > 0 && <div className="tn-wants">
          <h4>{tr("What they want")}</h4>
          <ul lang={s.lang}>{s.wants.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>}
        {(s.work || s.technologies.length > 0) && <ul className="tn-chips" aria-label={tr("Kind of work and technologies")}>
          {s.work && <li className="tn-chip tn-chip-work">{workLabel(s.work)}</li>}
          {s.technologies.map((x) => <li key={x} className="tn-chip">{x}</li>)}
        </ul>}
        {s.requirements.length > 0 && <p className="tn-req"><b>{tr("Bidders need")}</b> <span lang={s.lang}>{s.requirements.join("; ")}</span></p>}
      </> : <>
        {t.category && <p className="tn-category">{t.category}</p>}
        <p className="tn-pending">{t.summary_state === "skipped" ? tr("No summary for this one: read the official notice.") : tr("Summary being written")}</p>
        {t.scope && <p className="tn-scope">{t.scope}</p>}
      </>}
      <div className="tn-foot">
        {!waiting && !sameTitle(t.title, s?.title) && <p className="tn-original"><span>{tr("Original title")}</span> {t.title}</p>}
        <p className="tn-source">
          <OfficialLink t={t} language={language} />
          <span>{t.source}</span>
          <span>{tr("Notice {}", t.notice_id)}</span>
          <span className={t.check_is_old ? "warnline" : undefined}>{t.checked_at ? tr("Status checked {}", fmtDate(t.checked_at)) : tr("Status not rechecked")}</span>
        </p>
      </div>
    </div>
    <aside className="tn-side">
      <div className="tn-closes">
        <span className="tn-k">{tr("Closes")}</span>
        <span className="tn-v">{fmtDateTime(t.deadline)}{t.deadline_tz ? ` (${t.deadline_tz})` : ""}</span>
      </div>
      <div className={`tn-fit${s?.fit ? "" : " is-empty"}`}>
        <span className="tn-k"><FitMeter level={s?.fit} /> {s?.fit ? tr(FIT_LABEL[s.fit]) : tr("Fit not judged yet")}</span>
        {s?.reason && <span className="tn-reason" lang={s.lang}>{s.reason}</span>}
      </div>
      {t.value != null && <div><span className="tn-k">{tr("Value")}</span><span className="tn-v tn-money">{money(t.value, t.currency, language)}</span></div>}
      {(t.lots || t.duration) && <div><span className="tn-k">{tr("Lots and duration")}</span>
        <span className="tn-v">{[t.lots ? counted(t.lots, ["lot", "lots"], ["część", "części", "części"]) : null, t.duration ? durationText(t.duration) : null].filter(Boolean).join(", ")}</span></div>}
    </aside>
  </li>;
}

function TenderRow({ t, language }: { t: Tender; language: string }) {
  const [open, setOpen] = useState(false);
  const s = localised(t.summary, language);
  const id = `tn-row-${t.id}`;
  return <li className={`tn-row${t.days_left <= 7 ? " is-soon" : ""}${open ? " is-open" : ""}`}>
    <span className="tn-row-due" aria-label={tr("Closes in {}", counted(t.days_left, ["day", "days"], ["dzień", "dni", "dni"]))}><b>{t.days_left}</b><span aria-hidden="true">{tr("d")}</span></span>
    <div className="tn-row-main">
      <button type="button" className="tn-row-title" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)} lang={s ? s.lang : undefined}>{s ? s.title : t.title}</button>
      <span className="tn-row-sub">{t.buyer}, {countryName(t.country) ?? tr("Country not given")}{t.buyer_kind === "finance" && <span className="tn-tag">{tr("Finance or insurance")}</span>}</span>
    </div>
    <span className="tn-row-fit"><FitMeter level={s?.fit} /> <span>{s?.fit ? tr(FIT_LABEL[s.fit]) : s ? "" : tr("Summary being written")}</span></span>
    <span className="tn-row-value">{t.value != null ? money(t.value, t.currency, language) : ""}</span>
    <span className="tn-row-link"><OfficialLink t={t} language={language} /></span>
    {open && <div className="tn-row-more" id={id}>
      {s?.text ? <p lang={s.lang}>{s.text}</p> : <p className="tn-pending">{t.summary_state === "skipped" ? tr("No summary for this one: read the official notice.") : tr("Summary being written")}</p>}
      {s && s.wants.length > 0 && <ul lang={s.lang}>{s.wants.map((w) => <li key={w}>{w}</li>)}</ul>}
      {s?.reason && <p className="small"><b>{tr(FIT_LABEL[s.fit ?? "none"])}:</b> {s.reason}</p>}
      {s && !sameTitle(t.title, s.title) && <p className="tn-original small"><span>{tr("Original title")}</span> {t.title}</p>}
      <p className="small muted">{tr("Closes {}", fmtDateTime(t.deadline))}{t.deadline_tz ? ` (${t.deadline_tz})` : ""}</p>
    </div>}
  </li>;
}

/** Open tenders for software work from TED, UK Find a Tender, UK Contracts Finder and e-Zamówienia, each with a plain-language summary. */
export default function Tenders({ canRefresh = false }: { canRefresh?: boolean }) {
  const language = useLanguage();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [data, setData] = useState<Result | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [refresh, setRefresh] = useState<{ busy: boolean; done: Refresh | null; err: unknown }>({ busy: false, done: null, err: null });
  const [writing, setWriting] = useState<{ busy: boolean; done: Summarise | null; err: unknown }>({ busy: false, done: null, err: null });
  const page = Math.max(1, Number(params.get("page")) || 1);
  const region = params.get("region") || OUR_MARKETS;
  const view = params.get("view") === "list" ? "list" : "cards";
  const query = new URLSearchParams({ page: String(page), limit: "30", min_days: params.get("soon") === "1" ? "0" : "3" });
  if (region !== "all") query.set("region", region);
  ["q", "buyer", "fit", "work_type", "sort"].forEach((k) => { const v = params.get(k); if (v) query.set(k, v); });
  if (WITHIN.includes(params.get("within") ?? "")) query.set("max_days", params.get("within")!);
  const qs = query.toString();
  const qParam = params.get("q") ?? "";
  useEffect(() => { setSearch(qParam); }, [qParam]);
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setData(null);
    api<Result>(`/api/tenders?${qs}`, { signal: request.signal }).then(setData).catch((e) => { if (!request.signal.aborted) setErr(e); });
    return () => request.abort();
  }, [qs]);
  // While the sources are being checked or summaries written, read the list again every 30 seconds without blanking it.
  const reload = useCallback((signal?: AbortSignal) => api<Result>(`/api/tenders?${qs}`, { signal }).then(setData).catch(() => undefined), [qs]);
  useEffect(() => {
    if (!data?.refreshing && !data?.summaries?.running) return;
    const request = new AbortController();
    const timer = window.setTimeout(() => { void reload(request.signal); }, 30000);
    return () => { window.clearTimeout(timer); request.abort(); };
  }, [data, reload]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setParams(n); };
  const clearFilters = () => { const n = new URLSearchParams(); if (params.get("view")) n.set("view", params.get("view")!); setParams(n); setSearch(""); };
  const checkNow = async () => {
    setRefresh({ busy: true, done: null, err: null });
    try { const done = await post<Refresh>("/api/tenders/refresh"); setRefresh({ busy: false, done, err: null }); void reload(); }
    catch (e) { setRefresh({ busy: false, done: null, err: e }); }
  };
  const writeNow = async () => {
    setWriting({ busy: true, done: null, err: null });
    try { const done = await post<Summarise>("/api/tenders/summarise"); setWriting({ busy: false, done, err: null }); void reload(); }
    catch (e) { setWriting({ busy: false, done: null, err: e }); }
  };
  const live = data?.sources.filter((s) => s.status === "active").length ?? 0;
  const elsewhere = data?.counts.other_regions ?? 0;
  const facets = data?.facets;
  const fitCount = (k: "strong" | "possible") => facets ? (facets.fit.strong ?? 0) + (k === "possible" ? facets.fit.possible ?? 0 : 0) : null;
  const filtered = ["q", "buyer", "fit", "work_type", "within", "soon"].some((k) => params.get(k)) || region !== OUR_MARKETS;
  const done = refresh.done;
  const waitingSummaries = data?.summaries?.waiting ?? 0;
  return <div className="tn-page">
    <PageHeader title="Open tenders" sub="Public and private tenders for software development and IT services from the official procurement sites, each summarised in plain language."
      right={data && <div className="tn-head-actions">
        <span className="small muted">{data.last_updated ? tr("Last updated {}", fmtDateTime(data.last_updated)) : tr("Not checked yet")}
          {data.refreshing && <>, {tr("checking the sources now")}</>}</span>
        {canRefresh && <div className="row">
          <button type="button" disabled={refresh.busy || !!data.refreshing} onClick={checkNow}>{tr("Refresh now")}</button>
          {(waitingSummaries > 0 || data.summaries?.running) && <button type="button" disabled={writing.busy || !!data.summaries?.running} onClick={writeNow}>{tr("Write summaries now")}</button>}
        </div>}
      </div>} />
    {done && <div className="okmsg" role="status">{done.queued.length > 0
      ? tr("Checking {} now. New tenders appear here within a few minutes.", counted(done.queued.length, ["tender source", "tender sources"], ["źródło przetargów", "źródła przetargów", "źródeł przetargów"]))
      : done.already_waiting.length > 0 ? tr("A check is already running. New tenders appear here when it finishes.")
        : tr("No tender source is switched on yet. An administrator switches them on in Administration, Sources.")}</div>}
    {writing.done && <div className="okmsg" role="status">{writing.done.queued
      ? tr("Writing summaries for up to {} tenders now. They appear here as each batch finishes.", number(Math.min(writing.done.cap, writing.done.waiting)))
      : writing.done.already_running ? tr("Summaries are already being written. They appear here as each batch finishes.")
        : tr("Every open tender already has a summary.")}</div>}
    <Err e={refresh.err} />
    <Err e={writing.err} />
    <form className="tn-filters" role="search" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
      <div className="tn-search">
        <input type="search" aria-label={tr("Search tenders")} placeholder={tr("What they buy, buyer or technology")} maxLength={120} value={search} onChange={(e) => setSearch(e.target.value)} />
        <button type="submit" className="primary">{tr("Search")}</button>
      </div>
      <div className="tn-selects">
        <label><span>{tr("Region")}</span>
          <select value={region} onChange={(e) => set("region", e.target.value === OUR_MARKETS ? "" : e.target.value)}>
            <option value={OUR_MARKETS}>{tr("Our markets (UK, DACH, Nordics, Poland)")}</option>
            <option value="all">{tr("All regions")}</option>{REGIONS.map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
          </select></label>
        <label><span>{tr("Fit for us")}</span>
          <select value={params.get("fit") ?? ""} onChange={(e) => set("fit", e.target.value)}>
            <option value="">{tr("Any fit")}</option>
            <option value="strong">{tr("Strong fit only")}{fitCount("strong") != null ? ` (${number(fitCount("strong")!)})` : ""}</option>
            <option value="possible">{tr("Possible or strong fit")}{fitCount("possible") != null ? ` (${number(fitCount("possible")!)})` : ""}</option>
          </select></label>
        <label><span>{tr("Kind of work")}</span>
          <select value={params.get("work_type") ?? ""} onChange={(e) => set("work_type", e.target.value)}>
            <option value="">{tr("Any kind of work")}</option>
            {WORK_TYPES.map(([k, label]) => <option key={k} value={k}>{tr(label)}{facets ? ` (${number(facets.work_type[k] ?? 0)})` : ""}</option>)}
          </select></label>
        <label><span>{tr("Closing within")}</span>
          <select value={params.get("within") ?? ""} onChange={(e) => set("within", e.target.value)}>
            <option value="">{tr("Any time")}</option>
            {WITHIN.map((d) => <option key={d} value={d}>{counted(Number(d), ["day", "days"], ["dzień", "dni", "dni"])}</option>)}
          </select></label>
        <label><span>{tr("Buyer")}</span>
          <select value={params.get("buyer") ?? ""} onChange={(e) => set("buyer", e.target.value)}>
            <option value="">{tr("All buyers")}</option><option value="finance">{tr("Banks, insurers and payments")}</option><option value="public">{tr("Public sector and other buyers")}</option>
          </select></label>
      </div>
      <div className="tn-filter-foot">
        <label className="check"><input type="checkbox" checked={params.get("soon") === "1"} onChange={(e) => set("soon", e.target.checked ? "1" : "")} /> {tr("Include tenders closing within 3 days")}</label>
        {filtered && <button type="button" className="linkish" onClick={clearFilters}>{tr("Clear filters")}</button>}
      </div>
    </form>
    <div className="tn-resultbar">
      {data && !err ? <p className="tn-counts">
        <b>{tr("{} open", number(data.counts.total))}</b>
        <span>{tr("{} closing within a week", number(data.counts.closing_this_week))}</span>
        {data.counts.closing_too_soon > 0 && params.get("soon") !== "1" && <span>{tr("{} more close within 3 days", number(data.counts.closing_too_soon))}</span>}
        {elsewhere > 0 && <button type="button" className="linkish" onClick={() => set("region", "all")}>{tr("{} more in other regions", number(elsewhere))}</button>}
        {(data.counts.waiting_summary ?? 0) > 0 && <span>{data.summaries?.running ? tr("{} summaries being written now", number(data.counts.waiting_summary ?? 0)) : tr("{} waiting for a summary", number(data.counts.waiting_summary ?? 0))}</span>}
        <span className="muted">{tr("Read from {} of {} tender sources", live, data.sources.length)}</span>
      </p> : <span />}
      <div className="tn-arrange">
        <label className="tn-sort"><span>{tr("Sort by")}</span>
          <select value={params.get("sort") ?? "deadline"} onChange={(e) => set("sort", e.target.value === "deadline" ? "" : e.target.value)}>
            {SORTS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select></label>
        <div className="tn-view" role="group" aria-label={tr("Layout")}>
          <button type="button" aria-pressed={view === "cards"} onClick={() => set("view", "")}>{tr("Cards")}</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => set("view", "list")}>{tr("Compact list")}</button>
        </div>
      </div>
    </div>
    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading tenders")} /> : <>
      {data.items.length === 0 ? <Empty title="No open tender matches">{live === 0 ? tr("No tender source is switched on yet. An administrator switches them on in Administration, Sources.") : tr("Clear a filter, or include tenders closing within 3 days.")}</Empty>
        : view === "list"
          ? <ol className="tn-list" aria-label={tr("Open tenders")}>{data.items.map((t) => <TenderRow key={t.id} t={t} language={language} />)}</ol>
          : <ol className="tn-cards" aria-label={tr("Open tenders")}>{data.items.map((t) => <TenderCard key={t.id} t={t} language={language} />)}</ol>}
      {(page > 1 || data.has_more) && <div className="row mt-2">
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("Page {}", page)}</span>
        <button type="button" disabled={!data.has_more} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </div>}
      <p className="small muted mt-4">{tr("Summaries are written by AI from the notice text and can miss details. Always read the official notice before deciding to bid: deadlines and documents can change. A tender is only listed while the official source says it is open.")}</p>
    </>}
  </div>;
}
