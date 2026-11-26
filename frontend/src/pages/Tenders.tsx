import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { counted, number, tr, useLanguage } from "../locale";
import { Empty, Err, PageHeader, Skeleton, safeHref } from "../components/ui";
import { countryName } from "./targetMarket";
import "../styles/tenders.css";

type FitLevel = "strong" | "possible" | "weak" | "none";
type Sector = "banking" | "financial_services" | "other_icp" | "not_icp";
type Group = "banks" | "insurers" | "payments" | "lending" | "markets" | "other";
type Who = "icp" | "all" | Group;
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
  group: Group; is_new: boolean; cpv?: string[];
  account: { id: string; name: string } | null; country: string | null; region: string; deadline: string; deadline_tz: string | null;
  days_left: number; status: string; url: string; source: string; scope: string | null;
  value: number | null; currency?: string | null; value_eur?: number | null; value_basis?: string | null;
  checked_at: string | null; check_is_old: boolean; published?: string | null;
  summary?: Summary | null; summary_state?: "written" | "waiting" | "skipped" | "not_needed"; worth?: Worth;
};
type Facets = {
  groups: Record<Who, number>; region: Record<string, number>; country: Record<string, number>; value: Record<string, number>;
  within: Record<string, number>; status: Record<string, number>; fit: Record<string, number>; work_type: Record<string, number>;
};
type Result = {
  items: Tender[]; has_more: boolean; page: number; facets: Facets; search?: { words: string; cpv: string[] };
  counts: { total: number; closing_this_week: number; closing_too_soon: number; other_regions: number; new: number; waiting_summary: number; icp: number };
  sources: { source_id: string; display_name: string; status: string; last_success_at: string | null }[]; last_updated?: string | null; refreshing?: boolean;
  summaries?: { waiting: number; running: boolean }; eur_rates?: { date: string; source: string };
};
type Refresh = { queued: string[]; already_waiting: string[]; not_switched_on: string[] };
type Summarise = { queued: boolean; already_running: boolean; waiting: number; cap: number };

/* ---------- who is buying: The Codest's ICP in its own words, banks first ---------- */
const FINANCE: Group[] = ["banks", "insurers", "payments", "lending", "markets"];
const WHO: Who[] = ["icp", ...FINANCE, "other", "all"];
/** chip label, and one line on who is in it and why it matters to us */
const GROUP: Record<Group, [string, string]> = {
  banks: ["Banks", "Commercial, savings and cooperative banks, building societies and state development banks. Our first target."],
  insurers: ["Insurers", "Life, property and health insurers and reinsurers. State social insurance is public sector, so it is not here."],
  payments: ["Payments and fintech", "Payment companies, card and transfer schemes, and makers of banking software."],
  lending: ["Lenders and investors", "Lenders, leasing companies, pension funds and asset managers."],
  markets: ["Central banks and markets", "Central banks, financial supervisors, exchanges and clearing houses. Often state-owned, but they buy banking-grade software."],
  other: ["Other sectors", "Public bodies, utilities, hospitals, universities and companies outside finance. Outside our ICP: worth it only as a reference project."],
};
const WHO_LINE = {
  icp: "Our ICP: banks first, then insurers, payments and fintech, lenders and investors, central banks and markets.",
  all: "Every open tender for software and IT work, from every buyer. Our ICP comes first, then everyone else.",
};
const LEGACY_SECTOR: Record<string, Who> = { icp: "icp", finance: "icp", banking: "banks", financial_services: "icp", other_icp: "other", not_icp: "other" };
const GLYPH: Record<Who, ReactNode> = {
  icp: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><path d="M12 11v2" /></>,
  banks: <path d="M3 9l9-5 9 5M5.5 10.5v7M10 10.5v7M14 10.5v7M18.5 10.5v7M3 20h18" />,
  insurers: <path d="M12 3l8 3v6c0 4.4-3.4 7.5-8 9-4.6-1.5-8-4.6-8-9V6z" />,
  payments: <><rect x="3" y="6" width="18" height="12" /><path d="M3 10h18M7 14.5h4" /></>,
  lending: <><circle cx="12" cy="12" r="8.5" /><path d="M15 9a4 4 0 100 6M7.5 11h6M7.5 13h6" /></>,
  markets: <path d="M3 20h18M4.5 16l4.5-5.5 4 3L19.5 6M15.5 6h4v4" />,
  other: <path d="M4 20V9l6-3v14M10 20v-9h10v9M2.5 20h19M13 14h4M13 17h4M6.5 11h1M6.5 14h1" />,
  all: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />,
};
const Glyph = ({ k }: { k: Who }) => <svg className="tn-glyph" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">{GLYPH[k]}</svg>;

/* ---------- the filters ---------- */
const OUR_MARKETS = "core";
const REGIONS: [string, string, string?][] = [
  ["core", "Our markets", "UK, DACH, Nordics, Poland"], ["uk", "United Kingdom"], ["dach", "DACH", "Germany, Austria, Switzerland"],
  ["nordics", "Nordics", "Sweden, Norway, Denmark, Finland, Iceland"], ["poland", "Poland"], ["europe", "Rest of Europe"], ["international", "Outside Europe"],
  ["all", "Everywhere"],
];
const VALUES: [string, number, string, string][] = [["100k", 100000, "€100k or more", "€100k+"], ["500k", 500000, "€500k or more", "€500k+"], ["1m", 1000000, "€1M or more", "€1M+"]];
const WITHIN = ["7", "14", "30", "60"];
const STATUS: [string, string][] = [["new", "New this week"], ["changed", "Changed by the buyer"]];
const SORTS: [string, string][] = [["fit", "Best fit first"], ["deadline", "Closing soonest"], ["value", "Biggest value"], ["newest", "Newest first"]];
const WORK_TYPES: [string, string][] = [
  ["software_development", "Software development"], ["maintenance_support", "Maintenance and support"], ["data_ai", "Data and AI"],
  ["testing_qa", "Testing and QA"], ["consulting", "Consulting"], ["licences_products", "Licences and products"],
  ["infrastructure_hardware", "Infrastructure and hardware"], ["other", "Other work"],
];
const SUGGESTIONS = ["core banking", "data platform", "Sparkasse", "test automation", "72262000"];
const FILTER_KEYS = ["q", "region", "country", "value", "within", "soon", "status", "fit", "work_type"];
const VIEW_KEY = "ri_tenders_layout";

const KIND: Record<string, string> = {
  bank: "Bank", central_bank: "Central bank", development_bank: "State development bank", savings_bank: "Savings bank", cooperative_bank: "Cooperative bank",
  building_society: "Building society", insurance: "Insurer", payments: "Payments", market_infrastructure: "Exchange or clearing", asset_management: "Asset manager",
  pension: "Pension fund", lending: "Lender", financial_software: "Financial software", financial_authority: "Financial authority",
  digital_commerce: "Digital commerce", enterprise_software: "Enterprise software", adtech_martech: "AdTech or MarTech",
  public_body: "Public body", social_insurance: "Social insurance", public_finance: "Public finance office", company: "Company",
};
const PUBLIC_BY_NAME = new Set(["central_bank", "development_bank", "financial_authority"]);     // "state-owned" would only repeat the label
const FIT_LABEL: Record<FitLevel, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit", none: "Not a fit" };
const FIT_STEPS: Record<FitLevel, number> = { strong: 3, possible: 2, weak: 1, none: 0 };
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
const regionLabel = (k: string) => tr(REGIONS.find(([r]) => r === k)?.[1] ?? "Our markets");
/** Our four services, by the kind of work a tender asks for (only where one clearly fits). */
const SERVICE_FOR_WORK: Record<string, string> = { software_development: "Delivery team", maintenance_support: "Team extension", data_ai: "AI and data", testing_qa: "QA and testing" };
const PHONE = "(max-width: 640px)";
/** True on a phone-sized screen, and follows the screen when it turns. */
function usePhone() {
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(PHONE).matches);
  useEffect(() => {
    const query = window.matchMedia?.(PHONE);
    if (!query) return;
    const on = () => setPhone(query.matches);
    query.addEventListener("change", on);
    return () => query.removeEventListener("change", on);
  }, []);
  return phone;
}
const readLayout = () => { try { return localStorage.getItem(VIEW_KEY); } catch { return null; } };
const saveLayout = (v: string) => { try { localStorage.setItem(VIEW_KEY, v); } catch { /* the address still carries it */ } };

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
type Local = ReturnType<typeof localised>;

/** What kind of buyer, in words: "Savings bank", "Pension fund, state-owned", "Insurer, to be confirmed". */
function kindText(t: Tender) {
  const label = tr(KIND[t.sector_kind ?? ""] ?? "Company");
  return `${label}${t.sector_public && t.sector !== "not_icp" && !PUBLIC_BY_NAME.has(t.sector_kind ?? "") ? `, ${tr("state-owned")}` : ""}${t.sector_sure ? "" : `, ${tr("to be confirmed")}`}`;
}

/** Why it matters to us, in one line: the summary's fit reason, or what the buyer's sector says until there is one. */
function whyLine(t: Tender, s: Local) {
  if (s?.reason) return s.reason;
  const kind = tr(KIND[t.sector_kind ?? ""] ?? "Company");
  if (t.group !== "other") return t.summary_state === "skipped" ? tr("{} in our ICP. No summary for this one: read the official notice.", kind) : tr("{} in our ICP: the summary is on its way.", kind);
  if (t.sector === "other_icp") return tr("{}: close to our ICP, worth a look.", kind);
  if (!t.sector_sure) return tr("A company we cannot place yet: check who the buyer is.");
  return tr("{}: outside our ICP, only worth it as a reference project.", kind);
}

/** The list's second line: why it matters, then (until there is a summary) the notice's category in plain English. */
function listLine(t: Tender, s: Local) {
  if (s?.reason) return s.reason;
  const why = t.group !== "other" ? whyLine(t, s) : t.sector === "other_icp" ? tr("Close to our ICP") : !t.sector_sure ? tr("Buyer not placed yet") : tr("Outside our ICP");
  return [why, t.category].filter(Boolean).join(" · ");
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

const NewMark = () => <span className="tn-new">{tr("New")}</span>;

function Due({ t }: { t: Tender }) {
  return <div className="tn-due" role="img" aria-label={tr("Closes in {}", days(t.days_left))}>
    <b>{t.days_left}</b>
    <span>{t.days_left === 1 ? tr("day left") : tr("days left")}</span>
  </div>;
}

/** Days left, with a bar that shortens as the deadline nears (pink in the last week). */
function DueBar({ t }: { t: Tender }) {
  const share = Math.max(6, Math.min(100, (t.days_left / 60) * 100));
  return <span className="tn-duebar" title={tr("Closes {}", fmtDateTime(t.deadline))}>
    <b>{days(t.days_left)}</b>
    <i aria-hidden="true"><i style={{ width: `${share}%` }} /></i>
  </span>;
}

/** What kind of buyer, as a tag on the cards. */
function SectorTag({ t }: { t: Tender }) {
  if (t.sector === "not_icp" && t.sector_sure) return null;
  return <span className={`tn-tag tn-tag-${t.sector}`} title={t.sector_sure ? undefined : tr("The buyer's sector is confirmed when the summary is written.")}>{kindText(t)}</span>;
}

function BuyerName({ t }: { t: Tender }) {
  return t.account ? <Link to={`/accounts/${t.account.id}`}>{t.buyer}</Link> : <>{t.buyer}</>;
}

function OfficialLink({ t, language, label = "Open official notice" }: { t: Tender; language: string; label?: string }) {
  const href = safeHref(tedPage(t.url, language));
  return href ? <a className="tn-official" href={href} target="_blank" rel="noreferrer noopener">{tr(label)} <span aria-hidden="true">↗</span></a> : null;
}

/** "≈ €1.25M" first, the notice's own amount under it. */
function Value({ t, language, compact = false }: { t: Tender; language: string; compact?: boolean }) {
  if (t.value == null) return compact ? <span className="tn-none">{tr("Not stated")}</span> : <span className="tn-v tn-muted">{tr("Not stated in the notice")}</span>;
  const cur = (t.currency ?? "").toUpperCase();
  const own = cur ? money(t.value, cur, language) : number(Math.round(t.value));
  const eur = t.value_eur != null ? money(t.value_eur, "EUR", language) : null;
  const approx = cur !== "EUR" && eur;
  if (compact) return <span title={cur ? money(t.value, cur, language, false) : undefined}><b>{eur ? `${approx ? "≈ " : ""}${eur}` : own}</b>{approx && <small>{own}</small>}</span>;
  return <span className="tn-value">
    <span className="tn-money">{eur ? `${approx ? "≈ " : ""}${eur}` : own}</span>
    <span className="tn-value-own">
      {approx || !eur ? <>{cur ? money(t.value, cur, language, false) : own}{!cur && `, ${tr("currency not given")}`}</> : null}
      {t.value_basis && VALUE_BASIS[t.value_basis] && <span>{tr(VALUE_BASIS[t.value_basis])}</span>}
    </span>
  </span>;
}

/** The facts someone needs to judge a bid without opening the notice. Rows the notice says nothing about are left out. */
function WorthIt({ t, s, language }: { t: Tender; s: Local; language: string }) {
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

function TenderCard({ t, language, phone }: { t: Tender; language: string; phone: boolean }) {
  const s = localised(t.summary, language);
  const worth = <WorthIt t={t} s={s} language={language} />;
  const chips = s && (s.work || s.technologies.length > 0) ? <ul className="tn-chips" aria-label={tr("Kind of work and technologies")}>
    {s.work && <li className="tn-chip tn-chip-work">{workLabel(s.work)}</li>}
    {s.technologies.map((x) => <li key={x} className="tn-chip">{x}</li>)}
  </ul> : null;
  const foot = <div className="tn-foot">
    {s && !sameTitle(t.title, s.title) && <p className="tn-original"><span>{tr("Original title")}</span> {t.title}</p>}
    <p className="tn-source">
      <OfficialLink t={t} language={language} />
      <span>{t.source}</span>
      <span>{tr("Notice {}", t.notice_id)}</span>
      <span className={t.check_is_old ? "warnline" : undefined}>{t.checked_at ? tr("Status checked {}", fmtDate(t.checked_at)) : tr("Status not rechecked")}</span>
    </p>
  </div>;
  const side = <aside className="tn-side">
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
  </aside>;
  return <li className={`tn-card${t.days_left <= 7 ? " is-soon" : ""}`}>
    <Due t={t} />
    <div className="tn-body">
      <h3 className="tn-title" lang={s ? s.lang : undefined}>{t.is_new && <NewMark />}{s ? s.title : t.title}</h3>
      <div className="tn-buyer">
        <p>
          <span className="tn-buyer-name"><BuyerName t={t} /></span>
          <span className="tn-country">{countryName(t.country) ?? tr("Country not given")}</span>
          <SectorTag t={t} />
        </p>
        {s?.note && <p className="tn-note">{s.note}</p>}
      </div>
      {s ? <>
        {s.text && <p className="tn-summary" lang={s.lang}>{s.text}</p>}
        {phone && side}
        {phone ? <>{chips}<details className="tn-fold"><summary>{tr("Is it worth it?")}</summary>{worth}{foot}</details></> : <>{worth}{chips}{foot}</>}
      </> : <>
        {t.category && <p className="tn-category">{t.category}</p>}
        <p className={`tn-why-line${t.summary_state === "waiting" ? " is-waiting" : ""}`}>{whyLine(t, s)}</p>
        {t.scope && <p className="tn-scope">{t.scope}</p>}
        {phone && side}
        {phone ? <details className="tn-fold"><summary>{tr("Is it worth it?")}</summary><WorthIt t={t} s={null} language={language} />{foot}</details> : <><WorthIt t={t} s={null} language={language} />{foot}</>}
      </>}
    </div>
    {!phone && side}
  </li>;
}

/** Arrow keys move between the rows of the compact list, Enter or Space opens one. */
function moveBetweenRows(e: KeyboardEvent<HTMLButtonElement>) {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const rows = Array.from(document.querySelectorAll<HTMLButtonElement>(".tn-li-what > button"));
  const next = rows[rows.indexOf(e.currentTarget) + (e.key === "ArrowDown" ? 1 : -1)];
  if (next) { e.preventDefault(); next.focus(); }
}

function TenderRow({ t, language, open, onToggle }: { t: Tender; language: string; open: boolean; onToggle: () => void }) {
  const s = localised(t.summary, language);
  const id = `tn-li-${t.id}`;
  const button = useRef<HTMLButtonElement>(null);
  const why = listLine(t, s);
  const service = s?.fit && s.fit !== "none" && s.work ? SERVICE_FOR_WORK[s.work] : undefined;
  const close = (e: KeyboardEvent<HTMLDivElement>) => { if (e.key === "Escape") { e.stopPropagation(); onToggle(); button.current?.focus(); } };
  return <li className={`tn-li${open ? " is-open" : ""}${t.days_left <= 7 ? " is-soon" : ""}`}
    onClick={(e) => { if (!(e.target as HTMLElement).closest("a, button, .tn-li-more")) onToggle(); }}>
    <div className="tn-li-buyer">
      <span className="tn-li-name"><BuyerName t={t} /></span>
      <span className="tn-li-kind">{kindText(t)}<span className="tn-li-where">, {countryName(t.country) ?? tr("Country not given")}</span></span>
    </div>
    <div className="tn-li-what">
      <button type="button" ref={button} aria-expanded={open} aria-controls={id} onClick={onToggle} onKeyDown={moveBetweenRows} lang={s ? s.lang : undefined}>
        {t.is_new && <NewMark />}<span>{s ? s.title : t.title}</span>
      </button>
      <p className="tn-why" lang={s?.reason ? s.lang : undefined} title={why}>{why}</p>
    </div>
    <div className="tn-li-value" data-k={tr("Value")}><Value t={t} language={language} compact /></div>
    <div className="tn-li-due" data-k={tr("Closes")}><DueBar t={t} /><small className="tn-duedate">{fmtDate(t.deadline)}</small></div>
    <div className="tn-li-fit" data-k={tr("Fit for us")}>
      <span className="tn-k"><FitMeter level={s?.fit} /> {s?.fit ? tr(FIT_LABEL[s.fit]) : tr("Not judged yet")}</span>
      {service && <small>{tr("Best fit: {}", tr(service))}</small>}
    </div>
    <div className="tn-li-country">{countryName(t.country) ?? "–"}</div>
    <span className="tn-li-chev" aria-hidden="true" />
    <div className="tn-li-acts">
      <button type="button" className="tn-li-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}>{open ? tr("Hide details") : tr("Details")}</button>
      <OfficialLink t={t} language={language} label="Official notice" />
    </div>
    {open && <div className="tn-li-more" id={id} onKeyDown={close}>
      <div className="tn-li-main">
        {s?.text ? <p className="tn-summary" lang={s.lang}>{s.text}</p> : t.scope ? <p className="tn-scope">{t.scope}</p> : null}
        {s?.note && <p className="tn-note">{s.note}</p>}
        <WorthIt t={t} s={s} language={language} />
        {!s?.wants.length && !t.worth && !t.scope && !s?.text && <p className="tn-why-line">{tr("The notice says little more: open it for the documents.")}</p>}
      </div>
      <dl className="tn-li-facts">
        <div><dt>{tr("Closes")}</dt><dd>{fmtDateTime(t.deadline)}{t.deadline_tz ? ` (${t.deadline_tz})` : ""}</dd></div>
        <div className="tn-dup"><dt>{tr("Value")}</dt><dd><Value t={t} language={language} /></dd></div>
        <div className="tn-dup"><dt>{tr("Fit for us")}</dt><dd><FitMeter level={s?.fit} /> {s?.fit ? tr(FIT_LABEL[s.fit]) : tr("Fit not judged yet")}</dd></div>
        {s?.work && <div><dt>{tr("Kind of work")}</dt><dd>{workLabel(s.work)}{s.technologies.length ? `: ${s.technologies.join(", ")}` : ""}</dd></div>}
        {t.category && <div><dt>{tr("Category")}</dt><dd>{t.category}</dd></div>}
        {t.cpv && t.cpv.length > 0 && <div><dt>{tr("CPV codes")}</dt><dd>{t.cpv.join(", ")}</dd></div>}
        {s && !sameTitle(t.title, s.title) && <div><dt>{tr("Original title")}</dt><dd lang="und">{t.title}</dd></div>}
        <div><dt>{tr("Source")}</dt><dd>{t.source}, {tr("Notice {}", t.notice_id)}</dd></div>
        <div className="tn-li-link tn-dup"><OfficialLink t={t} language={language} /></div>
      </dl>
    </div>}
  </li>;
}

/** The tenders of one page, in runs of the same buyer chip (the list comes banks first). */
function byGroup(items: Tender[]) {
  const runs: { group: Group; items: Tender[] }[] = [];
  for (const t of items) {
    const last = runs[runs.length - 1];
    if (last && last.group === t.group) last.items.push(t); else runs.push({ group: t.group, items: [t] });
  }
  return runs;
}

function Count({ n }: { n?: number }) {
  return <span className="tn-n">{n == null ? "…" : number(n)}</span>;
}

/** One choice in a filter panel, with how many tenders it would show. */
function Choice({ on, count, hint, onPick, children }: { on: boolean; count?: number; hint?: string; onPick: () => void; children: ReactNode }) {
  return <button type="button" className={`tn-choice${count === 0 && !on ? " is-zero" : ""}`} aria-pressed={on} onClick={onPick}>
    <span className="tn-choice-l">{children}{hint && <small>{hint}</small>}</span>
    {count !== undefined && <Count n={count} />}
  </button>;
}

/** A filter as a small button that opens a panel of choices: compact, and every choice says how many tenders it would show. */
function Pill({ id, label, value, on, open, onOpen, wide = false, children }: { id: string; label: string; value: string; on: boolean; open: boolean;
  onOpen: (id: string | null) => void; wide?: boolean; children: ReactNode }) {
  return <div className={`tn-pill${on ? " is-on" : ""}${open ? " is-open" : ""}`} data-pill={id}>
    <button type="button" id={`tn-pill-${id}`} aria-expanded={open} aria-controls={`tn-pop-${id}`} onClick={() => onOpen(open ? null : id)}>
      <span className="tn-pill-k">{label}</span><span className="tn-pill-v">{value}</span>
      <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2 4l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>
    </button>
    {open && <div className={`tn-pop${wide ? " is-wide" : ""}`} id={`tn-pop-${id}`} role="group" aria-labelledby={`tn-pill-${id}`}>{children}</div>}
  </div>;
}

/** Open tenders for software work from TED, UK Find a Tender, UK Contracts Finder and e-Zamówienia: our ICP by default, banks first. */
export default function Tenders({ canRefresh = false }: { canRefresh?: boolean }) {
  const language = useLanguage();
  const phone = usePhone();
  const [params, setParams] = useSearchParams();
  const qParam = params.get("q") ?? "";
  const [search, setSearch] = useState(qParam);
  const [data, setData] = useState<Result | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState<{ busy: boolean; done: Refresh | null; err: unknown }>({ busy: false, done: null, err: null });
  const [writing, setWriting] = useState<{ busy: boolean; done: Summarise | null; err: unknown }>({ busy: false, done: null, err: null });
  const [pill, setPill] = useState<string | null>(null);
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const searchBox = useRef<HTMLInputElement>(null);

  const legacy = LEGACY_SECTOR[params.get("sector") ?? ""] ?? (params.get("sector") === "all" ? "all" : null);
  const who: Who = WHO.includes(params.get("who") as Who) ? params.get("who") as Who : legacy ?? "icp";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const region = REGIONS.some(([k]) => k === params.get("region")) ? params.get("region")! : OUR_MARKETS;
  const country = /^[A-Z]{2}$/.test(params.get("country") ?? "") ? params.get("country")! : "";
  const sort = SORTS.some(([k]) => k === params.get("sort")) ? params.get("sort")! : "fit";
  const value = VALUES.find(([k]) => k === params.get("value"));
  const within = WITHIN.includes(params.get("within") ?? "") ? params.get("within")! : "";
  const soon = params.get("soon") === "1";
  const status = STATUS.some(([k]) => k === params.get("status")) ? params.get("status")! : "";
  const fit = ["strong", "possible"].includes(params.get("fit") ?? "") ? params.get("fit")! : "";
  const work = WORK_TYPES.some(([k]) => k === params.get("work_type")) ? params.get("work_type")! : "";
  const saved = readLayout();       // a phone starts on the compact list (cards are long on a small screen) until the person picks a layout
  const view = params.get("view") === "list" || (params.get("view") !== "cards" && (saved ? saved === "list" : phone)) ? "list" : "cards";
  const limit = view === "list" ? 50 : 30;

  const query = new URLSearchParams({ group: who, page: String(page), limit: String(limit), min_days: soon ? "0" : "3", sort });
  if (region !== "all") query.set("region", region);
  if (country) query.set("country", country);
  if (value) query.set("min_value", String(value[1]));
  if (within) query.set("max_days", within);
  if (status) query.set("status", status);
  if (fit) query.set("fit", fit);
  if (work) query.set("work_type", work);
  if (qParam) query.set("q", qParam);
  const qs = query.toString();

  useEffect(() => { setSearch(qParam); }, [qParam]);
  useEffect(() => {
    const request = new AbortController();
    setErr(null); setLoading(true);
    api<Result>(`/api/tenders?${qs}`, { signal: request.signal }).then((d) => { setData(d); setOpened(new Set()); setLoading(false); })
      .catch((e) => { if (!request.signal.aborted) { setErr(e); setLoading(false); } });
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

  const update = useCallback((changes: Record<string, string>) => {
    setParams((prev) => {
      const n = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(changes)) { if (v) n.set(k, v); else n.delete(k); }
      if (!("page" in changes)) n.delete("page");
      n.delete("sector");
      return n;
    });
  }, [setParams]);
  const set = (k: string, v: string) => update({ [k]: v });
  const pick = (changes: Record<string, string>) => {
    update(changes);
    const from = pill;
    setPill(null);
    if (from) window.requestAnimationFrame(() => document.getElementById(`tn-pill-${from}`)?.focus());       // back to the filter's button
  };
  const choose = (next: Who) => update({ who: next === "icp" ? "" : next });
  const clearFilters = () => { update(Object.fromEntries(FILTER_KEYS.map((k) => [k, ""]))); setSearch(""); setPill(null); };

  // Search as you type (a short pause first), and at once on Enter.
  useEffect(() => {
    const next = search.trim();
    if (next === qParam) return;
    const timer = window.setTimeout(() => update({ q: next }), 450);
    return () => window.clearTimeout(timer);
  }, [search, qParam, update]);

  // A filter panel closes on Escape (focus goes back to its button) and on a click anywhere else; when it opens, focus moves to the current choice.
  useEffect(() => {
    if (!pill) return;
    const panel = document.getElementById(`tn-pop-${pill}`);
    if (panel && panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) panel.classList.add("is-right");     // near the right edge: open leftwards
    (panel?.querySelector<HTMLElement>('[aria-pressed="true"]') ?? panel?.querySelector<HTMLElement>("button, input"))?.focus();
    const away = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(`[data-pill="${pill}"]`)) setPill(null); };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { document.getElementById(`tn-pill-${pill}`)?.focus(); setPill(null); } };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [pill]);

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
  const toggleRow = (id: string) => setOpened((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const setLayout = (v: "cards" | "list") => { saveLayout(v); set("view", v); };

  const f = data?.facets;
  const g = f?.groups;
  const live = data?.sources.filter((s) => s.status === "active").length ?? 0;
  const done = refresh.done;
  const waitingSummaries = data?.summaries?.waiting ?? 0;
  const activeCount = [qParam, region !== OUR_MARKETS, country, value, within, soon, status, fit, work].filter(Boolean).length;
  const whoLine = who === "icp" || who === "all" ? WHO_LINE[who] : GROUP[who][1];
  const fitCount = (k: string) => f ? (f.fit.strong ?? 0) + (k === "possible" ? f.fit.possible ?? 0 : 0) : undefined;
  const countries = f ? Object.entries(f.country).filter(([c]) => c).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])) : [];
  const whereValue = country ? countryName(country) ?? country : regionLabel(region);
  const total = data?.counts.total ?? 0;
  const from = (page - 1) * limit + 1;

  return <div className="tn-page">
    <PageHeader title="Open tenders" sub="Public tenders for software and IT work, from the official procurement sites. Banks and the rest of our ICP come first. Each tender says what is bought, how big it is and whether it fits us."
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

    <section className="tn-finder" aria-label={tr("Find a tender")}>
      <form className="tn-search" role="search" onSubmit={(e) => { e.preventDefault(); update({ q: search.trim() }); }}>
        <svg className="tn-search-icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21" /></svg>
        <label className="sr" htmlFor="tn-q">{tr("Search tenders")}</label>
        <input id="tn-q" ref={searchBox} type="search" placeholder={tr("Buyer, what they buy, or a CPV code")} maxLength={120} value={search}
          onChange={(e) => setSearch(e.target.value)} autoComplete="off" />
        {search && <button type="button" className="tn-search-clear" aria-label={tr("Clear the search")} onClick={() => { setSearch(""); update({ q: "" }); searchBox.current?.focus(); }}>×</button>}
      </form>
      <p className="tn-try scroll-x" hidden={!!qParam && !data?.search?.cpv.length}>
        {data?.search?.cpv.length ? <span>{tr("CPV codes starting {}", data.search.cpv.join(", "))}{data.search.words ? ` + “${data.search.words}”` : ""}</span>
          : !qParam && <><span>{tr("Try")}</span>{SUGGESTIONS.map((x) => <button key={x} type="button" className="tn-try-b" onClick={() => { setSearch(x); update({ q: x }); }}>{x}</button>)}</>}
      </p>

      <div className="tn-who" role="group" aria-labelledby="tn-who-h">
        <h2 id="tn-who-h" className="tn-label">{tr("Who is buying")}</h2>
        <div className="tn-who-row">
          <div className={`tn-icp${who === "icp" ? " is-on" : FINANCE.includes(who as Group) ? " is-part" : ""}`}>
            <button type="button" className="tn-icp-all" aria-pressed={who === "icp"} onClick={() => choose("icp")}>
              <Glyph k="icp" /><span className="tn-icp-name">{tr("Our ICP")}</span><Count n={g?.icp} />
            </button>
            <div className="tn-icp-chips scroll-x">
              {FINANCE.map((k) => <button key={k} type="button" className={`tn-chipb${g && g[k] === 0 ? " is-zero" : ""}`} aria-pressed={who === k} data-in={who === "icp" || undefined}
                onClick={() => choose(k)}><Glyph k={k} /><span>{tr(GROUP[k][0])}</span><Count n={g?.[k]} /></button>)}
            </div>
          </div>
          <div className="tn-rest">
            <button type="button" className="tn-chipb tn-chip-out" aria-pressed={who === "other"} onClick={() => choose("other")}><Glyph k="other" /><span>{tr("Other sectors")}</span><Count n={g?.other} /></button>
            <button type="button" className="tn-chipb tn-chip-out" aria-pressed={who === "all"} onClick={() => choose("all")}><Glyph k="all" /><span>{tr("All tenders")}</span><Count n={g?.all} /></button>
          </div>
        </div>
        <p className="tn-who-line">{tr(whoLine)}
          {who === "icp" && g && g.icp < 25 && <span className="tn-who-extra"> {tr("Banks and insurers rarely have to publish public tenders, so most notices come from public bodies: they are under Other sectors.")}</span>}
        </p>
      </div>

      <div className="tn-pills">
        <Pill id="where" label={tr("Where")} value={whereValue} on={region !== OUR_MARKETS || !!country} open={pill === "where"} onOpen={setPill} wide>
          <p className="tn-pop-h">{tr("Region")}</p>
          <div className="tn-choices tn-choices-2">
            {REGIONS.map(([k, label, hint]) => <Choice key={k} on={region === k && !country} count={f?.region[k] ?? 0} hint={hint ? tr(hint) : undefined}
              onPick={() => pick({ region: k === OUR_MARKETS ? "" : k, country: "" })}>{tr(label)}</Choice>)}
          </div>
          {countries.length > 0 && <>
            <p className="tn-pop-h">{region === "all" ? tr("Country") : tr("Country, in {}", regionLabel(region))}</p>
            <div className="tn-countries">
              {countries.map(([c, n]) => <button key={c} type="button" className="tn-cty" aria-pressed={country === c} onClick={() => pick({ country: country === c ? "" : c })}>
                {countryName(c) ?? c} <Count n={n} /></button>)}
            </div>
          </>}
        </Pill>
        <Pill id="value" label={tr("Value")} value={value ? tr(value[3]) : tr("Any")} on={!!value} open={pill === "value"} onOpen={setPill}>
          <div className="tn-choices">
            <Choice on={!value} count={f?.value.any} onPick={() => pick({ value: "" })}>{tr("Any value")}</Choice>
            {VALUES.map(([k, , label]) => <Choice key={k} on={value?.[0] === k} count={f?.value[k]} onPick={() => pick({ value: k })}>{tr(label)}</Choice>)}
          </div>
          <p className="tn-pop-note">{tr("In euros, converted at fixed rates. Tenders that give no value are left out when a value is chosen.")}</p>
        </Pill>
        <Pill id="closes" label={tr("Closes")} value={within ? tr("within {}", days(Number(within))) : tr("Any time")} on={!!within || soon} open={pill === "closes"} onOpen={setPill}>
          <div className="tn-choices">
            <Choice on={!within} count={f?.within.any} onPick={() => pick({ within: "" })}>{tr("Any time")}</Choice>
            {WITHIN.map((d) => <Choice key={d} on={within === d} count={f?.within[d]} onPick={() => pick({ within: d })}>{tr("Within {}", days(Number(d)))}</Choice>)}
          </div>
          <label className="tn-check"><input type="checkbox" checked={soon} onChange={(e) => set("soon", e.target.checked ? "1" : "")} />
            <span>{tr("Also show tenders closing in the next 3 days")}{!soon && data && data.counts.closing_too_soon > 0 ? ` (${number(data.counts.closing_too_soon)})` : ""}
              <small>{tr("Hidden by default: usually too late to prepare a bid.")}</small></span></label>
        </Pill>
        <Pill id="status" label={tr("Status")} value={status ? tr(STATUS.find(([k]) => k === status)![1]) : tr("All open")} on={!!status} open={pill === "status"} onOpen={setPill}>
          <div className="tn-choices">
            <Choice on={!status} count={f?.status.any} onPick={() => pick({ status: "" })}>{tr("All open tenders")}</Choice>
            <Choice on={status === "new"} count={f?.status.new} hint={tr("Published in the last 7 days")} onPick={() => pick({ status: "new" })}>{tr("New this week")}</Choice>
            <Choice on={status === "changed"} count={f?.status.changed} hint={tr("The buyer changed the notice: check the deadline and documents")} onPick={() => pick({ status: "changed" })}>{tr("Changed by the buyer")}</Choice>
          </div>
        </Pill>
        <Pill id="fit" label={tr("Fit and work")} value={[fit ? tr(fit === "strong" ? "Strong fit" : "Possible or strong") : "", work ? workLabel(work) : ""].filter(Boolean).join(", ") || tr("Any")}
          on={!!fit || !!work} open={pill === "fit"} onOpen={setPill} wide>
          <p className="tn-pop-h">{tr("Fit for us")}</p>
          <div className="tn-choices">
            <Choice on={!fit} onPick={() => pick({ fit: "" })}>{tr("Any fit")}</Choice>
            <Choice on={fit === "strong"} count={fitCount("strong")} onPick={() => pick({ fit: "strong" })}>{tr("Strong fit only")}</Choice>
            <Choice on={fit === "possible"} count={fitCount("possible")} onPick={() => pick({ fit: "possible" })}>{tr("Possible or strong fit")}</Choice>
          </div>
          <p className="tn-pop-h">{tr("Kind of work")}</p>
          <div className="tn-choices tn-choices-2">
            <Choice on={!work} onPick={() => pick({ work_type: "" })}>{tr("Any kind of work")}</Choice>
            {WORK_TYPES.map(([k, label]) => <Choice key={k} on={work === k} count={f?.work_type[k] ?? 0} onPick={() => pick({ work_type: k })}>{tr(label)}</Choice>)}
          </div>
          <p className="tn-pop-note">{tr("Judged from the AI summary, so only summarised tenders match these.")}</p>
        </Pill>
        <p className="tn-active" aria-live="polite">
          {activeCount > 0 ? <><b>{counted(activeCount, ["active filter", "active filters"], ["aktywny filtr", "aktywne filtry", "aktywnych filtrów"])}</b>
            <button type="button" className="linkish" onClick={clearFilters}>{tr("Clear")}</button></> : <span>{tr("No filters: our markets, any value, any closing date.")}</span>}
        </p>
      </div>
    </section>

    <div className="tn-resultbar">
      {data && !err ? <p className="tn-counts" aria-live="polite">
        <b>{counted(total, ["open tender", "open tenders"], ["otwarty przetarg", "otwarte przetargi", "otwartych przetargów"])}</b>
        {data.counts.closing_this_week > 0 && <span className="tn-c-soon">{tr("{} close within a week", number(data.counts.closing_this_week))}</span>}
        {data.counts.new > 0 && <span>{tr("{} new this week", number(data.counts.new))}</span>}
        {data.counts.other_regions > 0 && <button type="button" className="linkish" onClick={() => update({ region: "all", country: "" })}>{region === OUR_MARKETS ? tr("{} more outside our markets", number(data.counts.other_regions)) : tr("{} more outside {}", number(data.counts.other_regions), regionLabel(region))}</button>}
        {(data.counts.waiting_summary ?? 0) > 0 && <span className="muted">{data.summaries?.running ? tr("{} summaries being written now", number(data.counts.waiting_summary)) : tr("{} waiting for a summary", number(data.counts.waiting_summary))}</span>}
      </p> : <span />}
      <div className="tn-arrange">
        <Pill id="sort" label={tr("Sort")} value={tr(SORTS.find(([k]) => k === sort)![1])} on={false} open={pill === "sort"} onOpen={setPill}>
          <div className="tn-choices">{SORTS.map(([k, label]) => <Choice key={k} on={sort === k} onPick={() => pick({ sort: k === "fit" ? "" : k })}>{tr(label)}</Choice>)}</div>
          <p className="tn-pop-note">{tr("Within each group of buyers: banks always come first.")}</p>
        </Pill>
        <div className="tn-view" role="group" aria-label={tr("Layout")}>
          <button type="button" aria-pressed={view === "cards"} onClick={() => setLayout("cards")}>
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 2h12v5H2zM2 9h12v5H2z" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>{tr("Cards")}</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => setLayout("list")}>
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 3h12M2 6.5h12M2 10h12M2 13.5h12" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>{tr("Compact list")}</button>
        </div>
      </div>
    </div>

    {err ? <Err e={err} /> : !data ? <Skeleton rows={6} label={tr("Loading tenders")} /> : <>
      {data.items.length === 0 ? <Empty title="No open tender matches">
        <p>{live === 0 ? tr("No tender source is switched on yet. An administrator switches them on in Administration, Sources.")
          : who !== "all" && (g?.all ?? 0) > 0 ? tr("Nothing from these buyers right now. {} from other buyers match the same search and filters.", number(g?.all ?? 0))
            : tr("Clear a filter, or include tenders closing within 3 days.")}</p>
        <p className="tn-empty-acts">
          {who !== "all" && (g?.all ?? 0) > 0 && <button type="button" onClick={() => choose("all")}>{tr("See all tenders ({})", number(g?.all ?? 0))}</button>}
          {activeCount > 0 && <button type="button" onClick={clearFilters}>{tr("Clear filters")}</button>}
        </p>
      </Empty> : <div className={`tn-results${view === "list" ? " tn-table" : ""}${loading ? " is-loading" : ""}`} aria-busy={loading}>
        {view === "list" && <div className="tn-cols">
          <span>{tr("Buyer")}</span>
          <span>{tr("What they want, and why it matters to us")}</span>
          <button type="button" className="tn-col-sort" aria-pressed={sort === "value"} onClick={() => set("sort", "value")}>{tr("Value")}{sort === "value" && <span aria-hidden="true"> ↓</span>}</button>
          <button type="button" className="tn-col-sort" aria-pressed={sort === "deadline"} onClick={() => set("sort", "deadline")}>{tr("Closes in")}{sort === "deadline" && <span aria-hidden="true"> ↑</span>}</button>
          <span>{tr("Country")}</span>
        </div>}
        {byGroup(data.items).map((run, i) => <section key={`${run.group}-${i}`} className={`tn-section tn-section-${run.group}`} aria-labelledby={`tn-sec-${run.group}-${i}`}>
          <header className="tn-section-head">
            <h2 id={`tn-sec-${run.group}-${i}`}><Glyph k={run.group} />{tr(GROUP[run.group][0])} <span className="tn-section-count">{number(g?.[run.group] ?? run.items.length)}</span></h2>
            <p>{tr(GROUP[run.group][1])}</p>
          </header>
          {view === "list"
            ? <ol className="tn-list">{run.items.map((t) => <TenderRow key={t.id} t={t} language={language} open={opened.has(t.id)} onToggle={() => toggleRow(t.id)} />)}</ol>
            : <ol className="tn-cards">{run.items.map((t) => <TenderCard key={t.id} t={t} language={language} phone={phone} />)}</ol>}
        </section>)}
        {view === "list" && <p className="tn-keys small muted">{tr("Keyboard: Tab to a tender, ↑ and ↓ to move, Enter to open or close, Esc to close.")}</p>}
      </div>}
      {(page > 1 || data.has_more) && <nav className="tn-pages" aria-label={tr("Pages")}>
        <button type="button" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>{tr("Previous")}</button>
        <span>{tr("{} to {} of {}", number(from), number(Math.min(total, from + data.items.length - 1)), number(total))}</span>
        <button type="button" disabled={!data.has_more} onClick={() => set("page", String(page + 1))}>{tr("Next")}</button>
      </nav>}
      <p className="small muted mt-4">{tr("Summaries are written by AI from the notice text and can miss details. Always read the official notice before deciding to bid: deadlines and documents can change. A tender is only listed while the official source says it is open.")}</p>
      {data.eur_rates && <p className="small muted">{tr("Euro amounts are approximate: converted at fixed {} of {}.", tr(data.eur_rates.source), fmtDate(data.eur_rates.date))}</p>}
    </>}
  </div>;
}
