import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { counted, number, tr, useLanguage } from "../locale";
import { Empty, Err, PageHeader, Skeleton, safeHref } from "../components/ui";
import { REGIONS, countryName } from "./targetMarket";
import "../styles/tenders.css";

type FitLevel = "strong" | "possible" | "weak" | "none";
type Sector = "banking" | "financial_services" | "other_icp" | "not_icp";
type Summary = {
  title_en: string; summary_en?: string; wants?: string[]; work_type?: string; technologies?: string[]; requirements?: string[];
  fit?: { level: FitLevel; reason?: string; reason_pl?: string }; title_pl?: string; summary_pl?: string; wants_pl?: string[]; requirements_pl?: string[];
  buyer_note?: string; buyer_note_pl?: string;
};
/** The facts that decide a bid: the notice's official fields first, the summary where the notice has none. */
type Worth = {
  lots?: number; lot_titles?: string[]; duration?: string; months?: number; start?: string; end?: string; renewals?: number; procedure?: string;
  framework?: boolean; languages?: string[]; place?: { city?: string; country?: string }; award?: Record<string, number | null>; recurring?: boolean;
  contact?: { name?: string; department?: string; email?: string; web?: string }; on_site?: "required" | "partly" | "remote"; renewal?: boolean; incumbent?: string;
};
type Tender = {
  id: string; notice_id: string; title: string; category: string | null; buyer: string; buyer_kind: "finance" | "public";
  sector: Sector; sector_kind: string | null; sector_public: boolean; sector_sure: boolean; sector_source?: string | null;
  account: { id: string; name: string } | null; country: string | null; region: string; deadline: string; deadline_tz: string | null;
  days_left: number; status: string; url: string; source: string; scope: string | null;
  value: number | null; currency?: string | null; value_eur?: number | null; value_basis?: string | null;
  checked_at: string | null; check_is_old: boolean; published?: string | null;
  summary?: Summary | null; summary_state?: "written" | "waiting" | "skipped" | "not_needed"; worth?: Worth;
};
type Result = {
  items: Tender[]; has_more: boolean; page: number;
  counts: { total: number; finance: number; closing_this_week: number; closing_too_soon: number; other_regions?: number; summarised?: number; waiting_summary?: number;
    by_sector?: Record<Sector, number>; icp?: number; not_icp?: number };
  facets?: { fit: Record<string, number>; work_type: Record<string, number> };
  sources: { source_id: string; display_name: string; status: string; last_success_at: string | null }[]; last_updated?: string | null; refreshing?: boolean;
  summaries?: { waiting: number; running: boolean }; eur_rates?: { date: string; source: string };
};
type Refresh = { queued: string[]; already_waiting: string[]; not_switched_on: string[] };
type Summarise = { queued: boolean; already_running: boolean; waiting: number; cap: number };

const OUR_MARKETS = "core";      // the default: UK, DACH, Nordics and Poland. "all" in the address means every region.
const ICP = "icp";               // the default buyers: our ICP sectors, banks first. "all" in the address means every buyer.
const BUYERS: [string, string][] = [
  [ICP, "Our ICP: banks first"], ["finance", "Banks and financial services"], ["banking", "Banks only"], ["other_icp", "Other ICP sectors"],
  ["not_icp", "Public sector and other buyers"], ["all", "Every buyer"],
];
const SECTION: Record<Sector, [string, string]> = {
  banking: ["Banks", "Commercial, savings, cooperative, central and state development banks."],
  financial_services: ["Financial services", "Insurers, payments, exchanges and clearing, asset managers, pension funds, lenders and financial supervisors."],
  other_icp: ["Other ICP sectors", "Digital commerce, enterprise software, AdTech and MarTech companies."],
  not_icp: ["Public sector and other buyers", "Ministries, cities, hospitals, universities, utilities and other buyers outside our ICP."],
};
const KIND: Record<string, string> = {
  bank: "Bank", central_bank: "Central bank", development_bank: "State development bank", savings_bank: "Savings bank", cooperative_bank: "Cooperative bank",
  building_society: "Building society", insurance: "Insurer", payments: "Payments", market_infrastructure: "Exchange or clearing", asset_management: "Asset manager",
  pension: "Pension fund", lending: "Lender", financial_software: "Financial software", financial_authority: "Financial authority",
  digital_commerce: "Digital commerce", enterprise_software: "Enterprise software", adtech_martech: "AdTech or MarTech",
  public_body: "Public body", social_insurance: "Social insurance", public_finance: "Public finance office", company: "Company",
};
const PUBLIC_BY_NAME = new Set(["central_bank", "development_bank", "financial_authority"]);     // "state-owned" would only repeat the label
const VALUES: [string, number, string][] = [["100k", 100000, "€100k or more"], ["500k", 500000, "€500k or more"], ["1m", 1000000, "€1M or more"]];
const WORK_TYPES: [string, string][] = [
  ["software_development", "Software development"], ["maintenance_support", "Maintenance and support"], ["data_ai", "Data and AI"],
  ["testing_qa", "Testing and QA"], ["consulting", "Consulting"], ["licences_products", "Licences and products"],
  ["infrastructure_hardware", "Infrastructure and hardware"], ["other", "Other work"],
];
const FIT_LABEL: Record<FitLevel, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit", none: "Not a fit" };
const FIT_STEPS: Record<FitLevel, number> = { strong: 3, possible: 2, weak: 1, none: 0 };
const WITHIN = ["7", "14", "30", "60"];
const SORTS: [string, string][] = [["fit", "Best fit, then soonest deadline"], ["deadline", "Soonest deadline"], ["value", "Highest value (in euros)"], ["newest", "Newest first"]];
const PROCEDURE: Record<string, string> = {
  open: "Open procedure", restricted: "Restricted procedure", "neg-w-call": "Negotiated procedure", "neg-wo-call": "Negotiated without a call for bids",
  "comp-dial": "Competitive dialogue", innovation: "Innovation partnership", "oth-single": "Single-stage procedure", "oth-mult": "Multi-stage procedure",
  selective: "Selective procedure", limited: "Limited procedure", direct: "Direct award",
};
const VALUE_BASIS: Record<string, string> = { estimated: "estimated value", lots_total: "all lots added up", framework_estimate: "framework estimate",
  framework_max: "framework maximum", reported: "value given in the notice" };
const ON_SITE: Record<string, string> = { required: "work at the buyer's site", partly: "partly on site", remote: "remote work allowed" };
const AWARD: Record<string, string> = { price: "Price", quality: "Quality", cost: "Cost" };

/** TED shows each notice in every EU language: open it in the language the app is in. Other sites' links are left as they are. */
const tedPage = (url: string, language: string) => url.replace(/^https:\/\/ted\.europa\.eu\/[a-z]{2}\/notice\//, `https://ted.europa.eu/${language}/notice/`);
const sameTitle = (a?: string, b?: string) => (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();
const workLabel = (key?: string) => tr(WORK_TYPES.find(([k]) => k === key)?.[1] ?? "Other work");
const locale = (language: string) => (language === "pl" ? "pl-PL" : "en-GB");
const days = (n: number) => counted(n, ["day", "days"], ["dzień", "dni", "dni"]);
const sentence = (parts: (string | null | undefined | false)[]) => { const t = parts.filter(Boolean).join(", "); return t.charAt(0).toUpperCase() + t.slice(1); };

/** The summary in the app's language: Polish when there is a Polish version, English otherwise. */
function localised(s: Summary | null | undefined, language: string) {
  if (!s) return null;
  const pl = language === "pl";
  const pick = <T,>(p: T | undefined, e: T | undefined) => (pl && p && (!Array.isArray(p) || p.length) ? p : e);
  return {
    title: pick(s.title_pl, s.title_en) ?? s.title_en, text: pick(s.summary_pl, s.summary_en), wants: pick(s.wants_pl, s.wants) ?? [],
    requirements: pick(s.requirements_pl, s.requirements) ?? [], reason: pick(s.fit?.reason_pl, s.fit?.reason), technologies: s.technologies ?? [],
    fit: s.fit?.level, work: s.work_type, note: pick(s.buyer_note_pl, s.buyer_note), lang: pl && s.title_pl ? "pl" : "en",
  };
}

/** An amount, short ("€1.25M", "1,25 mln €") or in full. */
function money(value: number, currency: string, language: string, short = true) {
  try {
    const text = new Intl.NumberFormat(locale(language), { style: "currency", currency, ...(short ? { notation: "compact", maximumSignificantDigits: 3 } : { maximumFractionDigits: 0 }) })
      .format(value);
    return language === "pl" ? text : text.replace(/(\d)K\b/, "$1k");
  } catch { return `${currency} ${number(Math.round(value))}`; }
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

function monthYear(v: string, language: string) {
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleDateString(locale(language), { month: "short", year: "numeric" });
}

function languageNames(codes: string[], language: string) {
  let names: Intl.DisplayNames | null = null;
  try { names = new Intl.DisplayNames([locale(language)], { type: "language" }); } catch { /* old browsers: the codes */ }
  return codes.map((c) => names?.of(c) ?? c.toUpperCase()).join(", ");
}

function FitMeter({ level }: { level?: FitLevel }) {
  const on = level ? FIT_STEPS[level] : 0;
  return <span className={`tn-meter tn-fit-${level ?? "unknown"}`} aria-hidden="true">{[1, 2, 3].map((i) => <i key={i} className={i <= on ? "on" : ""} />)}</span>;
}

function Due({ t }: { t: Tender }) {
  return <div className="tn-due" role="img" aria-label={tr("Closes in {}", days(t.days_left))}>
    <b>{t.days_left}</b>
    <span>{t.days_left === 1 ? tr("day left") : tr("days left")}</span>
  </div>;
}

/** What kind of buyer, as a tag: "Savings bank", "Pension fund, state-owned", "Insurer, to be confirmed". */
function SectorTag({ t }: { t: Tender }) {
  if (t.sector === "not_icp" && t.sector_sure) return null;
  const label = tr(KIND[t.sector_kind ?? ""] ?? "Company");
  return <span className={`tn-tag tn-tag-${t.sector}`} title={t.sector_sure ? undefined : tr("The buyer's sector is confirmed when the summary is written.")}>
    {label}{t.sector_public && t.sector !== "not_icp" && !PUBLIC_BY_NAME.has(t.sector_kind ?? "") ? `, ${tr("state-owned")}` : ""}{t.sector_sure ? "" : `, ${tr("to be confirmed")}`}
  </span>;
}

function Buyer({ t, note }: { t: Tender; note?: string }) {
  return <div className="tn-buyer">
    <p>
      <span className="tn-buyer-name">{t.account ? <Link to={`/accounts/${t.account.id}`}>{t.buyer}</Link> : t.buyer}</span>
      <span className="tn-country">{countryName(t.country) ?? tr("Country not given")}</span>
      <SectorTag t={t} />
    </p>
    {note && <p className="tn-note">{note}</p>}
  </div>;
}

function OfficialLink({ t, language }: { t: Tender; language: string }) {
  const href = safeHref(tedPage(t.url, language));
  return href ? <a className="tn-official" href={href} target="_blank" rel="noreferrer noopener">{tr("Open official notice")} <span aria-hidden="true">↗</span></a> : null;
}

/** "≈ €1.25M" first, the notice's own amount under it. */
function Value({ t, language, compact = false }: { t: Tender; language: string; compact?: boolean }) {
  if (t.value == null) return compact ? null : <span className="tn-v tn-muted">{tr("Not stated in the notice")}</span>;
  const cur = (t.currency ?? "").toUpperCase();
  const own = cur ? money(t.value, cur, language) : number(Math.round(t.value));
  const eur = t.value_eur != null ? money(t.value_eur, "EUR", language) : null;
  const approx = cur !== "EUR" && eur;
  if (compact) return <span title={cur ? money(t.value, cur, language, false) : undefined}>{eur ? `${approx ? "≈ " : ""}${eur}` : own}{approx && <small>{own}</small>}</span>;
  return <span className="tn-value">
    <span className="tn-money">{eur ? `${approx ? "≈ " : ""}${eur}` : own}</span>
    <span className="tn-value-own">
      {approx || !eur ? <>{cur ? money(t.value, cur, language, false) : own}{!cur && `, ${tr("currency not given")}`}</> : null}
      {t.value_basis && VALUE_BASIS[t.value_basis] && <span>{tr(VALUE_BASIS[t.value_basis])}</span>}
    </span>
  </span>;
}

/** The facts someone needs to judge a bid without opening the notice. Rows the notice says nothing about are left out. */
function WorthIt({ t, s, language }: { t: Tender; s: ReturnType<typeof localised>; language: string }) {
  const w = t.worth ?? {};
  const rows: [string, ReactNode][] = [];
  const duration = [w.duration ? durationText(w.duration) : w.months ? counted(w.months, ["month", "months"], ["miesiąc", "miesiące", "miesięcy"]) : null,
    w.start && w.end ? tr("{} to {}", monthYear(w.start, language), monthYear(w.end, language)) : w.start ? tr("from {}", monthYear(w.start, language)) : null,
    w.renewals ? tr("up to {}", counted(w.renewals, ["renewal", "renewals"], ["przedłużenie", "przedłużenia", "przedłużeń"])) : null].filter(Boolean);
  if (duration.length) rows.push(["Duration", sentence(duration)]);
  if (w.lots) rows.push(["Lots", <>{counted(w.lots, ["lot", "lots"], ["część", "części", "części"])}{w.lot_titles?.length
    ? <span className="tn-sub" lang="und">{w.lot_titles.slice(0, 4).join("; ")}{w.lot_titles.length > 4 ? "…" : ""}</span> : null}</>]);
  const proc = [w.procedure ? tr(PROCEDURE[w.procedure] ?? w.procedure) : null,
    w.framework === true ? tr("framework agreement") : w.framework === false ? tr("one contract, not a framework") : null].filter(Boolean);
  if (proc.length) rows.push(["Procedure", sentence(proc)]);
  if (w.languages?.length) rows.push(["Bid language", languageNames(w.languages, language)]);
  const place = [w.place?.city, w.place?.country ? countryName(w.place.country) : null].filter(Boolean).join(", ");
  const where = [place || null, w.on_site ? tr(ON_SITE[w.on_site]) : null].filter(Boolean);
  if (where.length) rows.push(["Where", sentence(where)]);
  if (w.award && Object.keys(w.award).length) {
    const parts = Object.entries(w.award).map(([k, v]) => (v == null ? tr(AWARD[k] ?? k) : `${tr(AWARD[k] ?? k)} ${v}%`));
    const noWeights = Object.values(w.award).every((v) => v == null);
    rows.push(["Bids judged on", noWeights ? tr("{}, no weights given", parts.join(", ")) : parts.join(", ")]);
  }
  if (w.renewal != null || w.recurring || w.incumbent) {
    rows.push(["Re-tender", sentence([w.renewal === true ? tr("Yes, it replaces or renews an existing contract") : w.renewal === false ? tr("No, new work") : null,
      w.incumbent ? tr("now: {}", w.incumbent) : null, w.recurring ? tr("bought again regularly") : null])]);
  }
  if (s && s.requirements.length) rows.push(["Bidders need", <span lang={s.lang}>{s.requirements.join("; ")}</span>]);
  if (w.contact && (w.contact.email || w.contact.name || w.contact.department)) {
    const email = w.contact.email;
    rows.push(["Contact", <>{[w.contact.department, w.contact.name].filter(Boolean).join(", ")}{email && <> {" "}<a href={`mailto:${email}`}>{email}</a></>}</>]);
  }
  if (!s?.wants.length && !rows.length) return null;
  return <section className="tn-worth" aria-label={tr("Is it worth it?")}>
    <h4>{tr("Is it worth it?")}</h4>
    {s && s.wants.length > 0 && <div className="tn-wants">
      <span className="tn-wk">{tr("What they want")}</span>
      <ul lang={s.lang}>{s.wants.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>}
    {rows.length > 0 && <dl className="tn-facts">{rows.map(([k, v]) => <div key={k}><dt>{tr(k)}</dt><dd>{v}</dd></div>)}</dl>}
  </section>;
}

function TenderCard({ t, language }: { t: Tender; language: string }) {
  const s = localised(t.summary, language);
  return <li className={`tn-card${t.days_left <= 7 ? " is-soon" : ""}`}>
    <Due t={t} />
    <div className="tn-body">
      <h3 className="tn-title" lang={s ? s.lang : undefined}>{s ? s.title : t.title}</h3>
      <Buyer t={t} note={s?.note} />
      {s ? <>
        {s.text && <p className="tn-summary" lang={s.lang}>{s.text}</p>}
        <WorthIt t={t} s={s} language={language} />
        {(s.work || s.technologies.length > 0) && <ul className="tn-chips" aria-label={tr("Kind of work and technologies")}>
          {s.work && <li className="tn-chip tn-chip-work">{workLabel(s.work)}</li>}
          {s.technologies.map((x) => <li key={x} className="tn-chip">{x}</li>)}
        </ul>}
      </> : <>
        {t.category && <p className="tn-category">{t.category}</p>}
        <p className="tn-pending">{t.summary_state === "skipped" ? tr("No summary for this one: read the official notice.")
          : t.summary_state === "not_needed" ? tr("Not summarised: the buyer is outside our ICP.") : tr("Summary being written")}</p>
        {t.scope && <p className="tn-scope">{t.scope}</p>}
        <WorthIt t={t} s={null} language={language} />
      </>}
      <div className="tn-foot">
        {s && !sameTitle(t.title, s.title) && <p className="tn-original"><span>{tr("Original title")}</span> {t.title}</p>}
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
      <div>
        <span className="tn-k">{tr("Value")}</span>
        <Value t={t} language={language} />
      </div>
      <div className={`tn-fit${s?.fit ? "" : " is-empty"}`}>
        <span className="tn-k"><FitMeter level={s?.fit} /> {s?.fit ? tr(FIT_LABEL[s.fit]) : tr("Fit not judged yet")}</span>
        {s?.reason && <span className="tn-reason" lang={s.lang}>{s.reason}</span>}
      </div>
    </aside>
  </li>;
}

function TenderRow({ t, language }: { t: Tender; language: string }) {
  const [open, setOpen] = useState(false);
  const s = localised(t.summary, language);
  const id = `tn-row-${t.id}`;
  return <li className={`tn-row${t.days_left <= 7 ? " is-soon" : ""}${open ? " is-open" : ""}`}>
    <span className="tn-row-due" aria-label={tr("Closes in {}", days(t.days_left))}><b>{t.days_left}</b><span aria-hidden="true">{tr("d")}</span></span>
    <div className="tn-row-main">
      <button type="button" className="tn-row-title" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)} lang={s ? s.lang : undefined}>{s ? s.title : t.title}</button>
      <span className="tn-row-sub">{t.buyer}, {countryName(t.country) ?? tr("Country not given")}<SectorTag t={t} /></span>
    </div>
    <span className="tn-row-fit"><FitMeter level={s?.fit} /> <span>{s?.fit ? tr(FIT_LABEL[s.fit]) : s || t.summary_state === "not_needed" ? "" : tr("Summary being written")}</span></span>
    <span className="tn-row-value"><Value t={t} language={language} compact /></span>
    <span className="tn-row-link"><OfficialLink t={t} language={language} /></span>
    {open && <div className="tn-row-more" id={id}>
      {s?.text ? <p lang={s.lang}>{s.text}</p> : <p className="tn-pending">{t.summary_state === "skipped" ? tr("No summary for this one: read the official notice.")
        : t.summary_state === "not_needed" ? tr("Not summarised: the buyer is outside our ICP.") : tr("Summary being written")}</p>}
      <WorthIt t={t} s={s} language={language} />
      {s?.reason && <p className="small"><b>{tr(FIT_LABEL[s.fit ?? "none"])}:</b> {s.reason}</p>}
      {s && !sameTitle(t.title, s.title) && <p className="tn-original small"><span>{tr("Original title")}</span> {t.title}</p>}
      <p className="small muted">{tr("Closes {}", fmtDateTime(t.deadline))}{t.deadline_tz ? ` (${t.deadline_tz})` : ""}</p>
    </div>}
  </li>;
}

/** The tenders of one page, in runs of the same sector (the list comes banks first, then financial services, other ICP sectors, everyone else). */
function bySector(items: Tender[]) {
  const runs: { sector: Sector; items: Tender[] }[] = [];
  for (const t of items) {
    const last = runs[runs.length - 1];
    if (last && last.sector === t.sector) last.items.push(t); else runs.push({ sector: t.sector, items: [t] });
  }
  return runs;
}

/** Open tenders for software work from TED, UK Find a Tender, UK Contracts Finder and e-Zamówienia: our ICP by default, banks first. */
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
  const buyers = BUYERS.some(([k]) => k === params.get("sector")) ? params.get("sector")! : ICP;
  const sort = params.get("sort") || "fit";
  const value = VALUES.find(([k]) => k === params.get("value"));
  const view = params.get("view") === "list" ? "list" : "cards";
  const query = new URLSearchParams({ page: String(page), limit: "30", min_days: params.get("soon") === "1" ? "0" : "3", sort });
  if (region !== "all") query.set("region", region);
  if (buyers !== "all") query.set("sector", buyers);
  if (value) query.set("min_value", String(value[1]));
  ["q", "buyer", "fit", "work_type"].forEach((k) => { const v = params.get(k); if (v) query.set(k, v); });
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
  const filtered = ["q", "buyer", "fit", "work_type", "within", "soon", "value", "sector"].some((k) => params.get(k)) || region !== OUR_MARKETS;
  const done = refresh.done;
  const waitingSummaries = data?.summaries?.waiting ?? 0;
  const sectors = data?.counts.by_sector;
  const finance = sectors ? sectors.banking + sectors.financial_services : 0;
  const otherIcp = sectors?.other_icp ?? 0;
  const publicCount = sectors?.not_icp ?? 0;
  const icpView = buyers === ICP || buyers === "finance" || buyers === "banking" || buyers === "other_icp";
  return <div className="tn-page">
    <PageHeader title="Open tenders" sub="Tenders for software and IT work from banks, insurers and the rest of our ICP, from the official procurement sites. Each one says what is bought, how big it is and whether it fits us."
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
        : tr("Every open tender from our ICP already has a summary.")}</div>}
    <Err e={refresh.err} />
    <Err e={writing.err} />
    {data && !err && sectors && <section className="tn-scope-bar" aria-live="polite">
      {icpView ? <>
        <p className="tn-scope-head">
          <b>{counted(finance, ["open tender from banks and financial services", "open tenders from banks and financial services"],
            ["otwarty przetarg od banków i firm finansowych", "otwarte przetargi od banków i firm finansowych", "otwartych przetargów od banków i firm finansowych"])}</b>
          <span>{counted(otherIcp, ["from another ICP sector", "from other ICP sectors"], ["z innego sektora ICP", "z innych sektorów ICP", "z innych sektorów ICP"])}</span>
        </p>
        {finance + otherIcp < 25 && <p className="tn-scope-why">{tr("That is all there is right now. Banks and insurers rarely have to publish public tenders, so almost every notice on TED, Find a Tender, Contracts Finder and e-Zamówienia comes from a public body. Those are hidden here, not lost.")}</p>}
        {publicCount > 0 && <button type="button" className="linkish" onClick={() => set("sector", "not_icp")}>{tr("Show all public-sector tenders ({})", number(publicCount))}</button>}
      </> : <>
        <p className="tn-scope-head"><b>{buyers === "not_icp" ? counted(publicCount, ["open tender from the public sector and other buyers", "open tenders from the public sector and other buyers"],
          ["otwarty przetarg od sektora publicznego i innych", "otwarte przetargi od sektora publicznego i innych", "otwartych przetargów od sektora publicznego i innych"])
          : counted(finance + otherIcp + publicCount, ["open tender from every buyer", "open tenders from every buyer"], ["otwarty przetarg od wszystkich kupujących", "otwarte przetargi od wszystkich kupujących", "otwartych przetargów od wszystkich kupujących"])}</b></p>
        <button type="button" className="linkish" onClick={() => set("sector", "")}>{tr("Back to our ICP ({})", number(finance + otherIcp))}</button>
      </>}
    </section>}
    <form className="tn-filters" role="search" onSubmit={(e) => { e.preventDefault(); set("q", search.trim()); }}>
      <div className="tn-search">
        <input type="search" aria-label={tr("Search tenders")} placeholder={tr("What they buy, buyer or technology")} maxLength={120} value={search} onChange={(e) => setSearch(e.target.value)} />
        <button type="submit" className="primary">{tr("Search")}</button>
      </div>
      <div className="tn-selects">
        <label><span>{tr("Buyers")}</span>
          <select value={buyers} onChange={(e) => set("sector", e.target.value === ICP ? "" : e.target.value)}>
            {BUYERS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select></label>
        <label><span>{tr("Region")}</span>
          <select value={region} onChange={(e) => set("region", e.target.value === OUR_MARKETS ? "" : e.target.value)}>
            <option value={OUR_MARKETS}>{tr("Our markets (UK, DACH, Nordics, Poland)")}</option>
            <option value="all">{tr("All regions")}</option>{REGIONS.map(([id, label]) => <option key={id} value={id}>{tr(label)}</option>)}
          </select></label>
        <label><span>{tr("Value")}</span>
          <select value={value?.[0] ?? ""} onChange={(e) => set("value", e.target.value)}>
            <option value="">{tr("Any value")}</option>
            {VALUES.map(([k, , label]) => <option key={k} value={k}>{tr(label)}</option>)}
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
            {WITHIN.map((d) => <option key={d} value={d}>{days(Number(d))}</option>)}
          </select></label>
      </div>
      <div className="tn-filter-foot">
        <label className="check"><input type="checkbox" checked={params.get("soon") === "1"} onChange={(e) => set("soon", e.target.checked ? "1" : "")} /> {tr("Include tenders closing within 3 days")}</label>
        {filtered && <button type="button" className="linkish" onClick={clearFilters}>{tr("Clear filters")}</button>}
      </div>
    </form>
    <div className="tn-resultbar">
      {data && !err ? <p className="tn-counts">
        <b>{tr("{} shown", number(data.counts.total))}</b>
        <span>{tr("{} closing within a week", number(data.counts.closing_this_week))}</span>
        {data.counts.closing_too_soon > 0 && params.get("soon") !== "1" && <span>{tr("{} more close within 3 days", number(data.counts.closing_too_soon))}</span>}
        {elsewhere > 0 && <button type="button" className="linkish" onClick={() => set("region", "all")}>{tr("{} more in other regions", number(elsewhere))}</button>}
        {(data.counts.waiting_summary ?? 0) > 0 && <span>{data.summaries?.running ? tr("{} summaries being written now", number(data.counts.waiting_summary ?? 0)) : tr("{} waiting for a summary", number(data.counts.waiting_summary ?? 0))}</span>}
        <span className="muted">{tr("Read from {} of {} tender sources", live, data.sources.length)}</span>
      </p> : <span />}
      <div className="tn-arrange">
        <label className="tn-sort"><span>{tr("Sort by")}</span>
          <select value={sort} onChange={(e) => set("sort", e.target.value === "fit" ? "" : e.target.value)}>
            {SORTS.map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select></label>
        <div className="tn-view" role="group" aria-label={tr("Layout")}>
          <button type="button" aria-pressed={view === "cards"} onClick={() => set("view", "")}>{tr("Cards")}</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => set("view", "list")}>{tr("Compact list")}</button>
        </div>
      </div>
    </div>
    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading tenders")} /> : <>
      {data.items.length === 0 ? <Empty title="No open tender matches">{live === 0 ? tr("No tender source is switched on yet. An administrator switches them on in Administration, Sources.")
        : icpView && publicCount > 0 && finance + otherIcp === 0 ? tr("No bank, insurer or other ICP company has an open software tender right now. The public-sector ones are one click away above.")
          : tr("Clear a filter, or include tenders closing within 3 days.")}</Empty>
        : bySector(data.items).map((run, i) => <section key={`${run.sector}-${i}`} className={`tn-section tn-section-${run.sector}`} aria-labelledby={`tn-sec-${run.sector}-${i}`}>
          <header className="tn-section-head">
            <h2 id={`tn-sec-${run.sector}-${i}`}>{tr(SECTION[run.sector][0])} <span className="tn-section-count">{number(sectors?.[run.sector] ?? run.items.length)}</span></h2>
            <p>{tr(SECTION[run.sector][1])}</p>
          </header>
          {view === "list"
            ? <ol className="tn-list">{run.items.map((t) => <TenderRow key={t.id} t={t} language={language} />)}</ol>
            : <ol className="tn-cards">{run.items.map((t) => <TenderCard key={t.id} t={t} language={language} />)}</ol>}
        </section>)}
      {(page > 1 || data.has_more) && <div className="row mt-2">
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("Page {}", page)}</span>
        <button type="button" disabled={!data.has_more} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </div>}
      <p className="small muted mt-4">{tr("Summaries are written by AI from the notice text and can miss details. Always read the official notice before deciding to bid: deadlines and documents can change. A tender is only listed while the official source says it is open.")}</p>
      {data.eur_rates && <p className="small muted">{tr("Euro amounts are approximate: converted at fixed {} of {}.", tr(data.eur_rates.source), fmtDate(data.eur_rates.date))}</p>}
    </>}
  </div>;
}
