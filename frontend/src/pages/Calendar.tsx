import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { counted, getLanguage, number, tr, useLanguage } from "../locale";
import { Err, Skeleton, reloadPage, safeHref } from "../components/ui";
import { countryName } from "./targetMarket";
import "../styles/calendar.css";

/** The deadline calendar: a month view (regulatory deadlines and the closing dates of open tenders from ICP buyers, day by day, with the
 *  month's agenda beside the grid) and a list of the regulatory deadlines grouped by year. The regulatory calendar is data in the repository
 *  (backend/rules/regulatory_calendar.yaml); GET /api/regulatory-calendar adds our accounts, GET /api/calendar/events gives the month view
 *  its days. */

type Certainty = "adopted" | "provisional" | "possible";
type Account = { id: string; name: string; country: string; kind: string; logo_url?: string | null };
export type Deadline = {
  id: string; title: string; title_pl: string; short?: string; short_pl?: string; regulation: string; date: string; year: number; approximate: boolean;
  certainty: Certainty; past: boolean; days_left: number; pinned: boolean; countries: string[]; kinds: string[];
  applies_to: string; applies_to_pl: string; must: string; must_pl: string; say: string; say_pl: string;
  ask_for: string | null; ask_for_pl: string | null; note: string | null; note_pl: string | null; sources: string[];
  accounts?: { count: number; by_kind: Record<string, number>; sample: Account[] };
  why?: string; why_pl?: string; on_account?: boolean;
};
type Kind = { label: string; label_pl: string; plural: string; plural_pl: string };
type Cal = { today: string; pin_months: number; items: Deadline[]; kinds: Record<string, Kind>;
  counts: { deadlines: number; upcoming: number; pinned: number; accounts: number; left_out: number } };

export const IconCalendar = (p: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>
    <rect x="3" y="4" width="18" height="17" /><path d="M3 9h18M8 2v4M16 2v4M12 12v4l2.5 1.5" />
  </svg>
);

const isPl = () => getLanguage() === "pl";
/** The Polish text when the page is in Polish and the calendar has one. */
const pick = (en: string, pl?: string | null) => (isPl() && pl ? pl : en);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
const loc = () => (isPl() ? "pl-PL" : "en-GB");
const fmt = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(loc(), opts).format(new Date(`${iso}T12:00:00`));

/** The date as a person says it: "9 January 2027", or "about 2028" when the law does not fix the day yet. */
export const whenText = (d: Deadline) => (d.approximate ? tr("about {}", d.year) : fmt(d.date, { day: "numeric", month: "long", year: "numeric" }));
/** How far away it is, in plain words. */
export function leftText(d: Deadline): string {
  if (d.past) return tr("Already applies");
  if (d.approximate) return tr("Date not fixed yet");
  if (d.days_left <= 0) return tr("Today");
  if (d.days_left === 1) return tr("Tomorrow");
  if (d.days_left < 60) return tr("In {} days", d.days_left);
  return tr("In {} months", Math.round(d.days_left / 30.4));
}
const CERTAINTY: Record<Certainty, [string, string]> = {
  adopted: ["Adopted", "Law with a fixed date."],
  provisional: ["Provisional: check", "Agreed, but not formally adopted yet. Check whether it was adopted before you use it."],
  possible: ["Approximate date", "Expected. The exact date is not fixed yet."],
};
export function CertaintyBadge({ d }: { d: Deadline }) {
  const [label, hint] = CERTAINTY[d.certainty];
  return <span className={`rc-badge rc-${d.past ? "past" : d.certainty}`} title={tr(hint)}>{d.past ? tr("In force") : tr(label)}</span>;
}
/** The month grid's label: "IPR: send + VoP". */
const shortOf = (d: Deadline) => pick(d.short || d.title, d.short_pl || d.title_pl);

function Sources({ urls }: { urls: string[] }) {
  const ok = urls.map((u) => [u, safeHref(u)] as const).filter(([, h]) => h);
  if (!ok.length) return null;
  return <p className="rc-src"><span>{tr(ok.length > 1 ? "Sources" : "Source")}</span>
    {ok.map(([u, h]) => <a key={u} href={h} target="_blank" rel="noreferrer noopener">{host(u)} ↗</a>)}</p>;
}

const ACC_WORDS: [[string, string], [string, string, string]] = [["of our target accounts", "of our target accounts"], ["naszej firmy docelowej", "naszych firm docelowych", "naszych firm docelowych"]];
const accountsLine = (count: number) => (count > 0
  ? <>{tr("Applies to")} <b>{counted(count, ...ACC_WORDS)}</b></>
  : tr("None of our target accounts"));

/** What a deadline asks for, who to ask, our accounts it applies to and its sources: a card's details. */
function DeadlineBody({ d, kind, kinds, countShown = false }: { d: Deadline; kind: string; kinds: Record<string, Kind>; countShown?: boolean }) {
  const acc = d.accounts ?? { count: 0, by_kind: {}, sample: [] };
  const count = kind ? acc.by_kind[kind] ?? 0 : acc.count;
  const sample = kind ? acc.sample.filter((a) => a.kind === kind) : acc.sample;
  const kindWord = (k: string) => (kinds[k] ? pick(kinds[k].label, kinds[k].label_pl) : k);
  return <>
    <p className="rc-say" title={tr("Who it applies to, and what they must do")}>{pick(d.say, d.say_pl)}</p>
    <dl className="rc-facts">
      {d.ask_for && <div><dt>{tr("Who to ask for")}</dt><dd>{pick(d.ask_for, d.ask_for_pl)}</dd></div>}
      {d.note && <div><dt>{tr("Good to know")}</dt><dd>{pick(d.note, d.note_pl)}</dd></div>}
    </dl>
    <div className="rc-acc">
      {!countShown && <p className="rc-acc-n">{accountsLine(count)}</p>}
      {sample.length > 0 && <ul className="rc-names" aria-label={tr("Accounts it applies to")}>
        {sample.map((a) => <li key={a.id}><Link to={`/accounts/${a.id}`}>{a.name}</Link><small>{[kindWord(a.kind), a.country].join(" · ")}</small></li>)}
        {count > sample.length && <li className="rc-more">{tr("and {} more", number(count - sample.length))}</li>}
      </ul>}
    </div>
    <Sources urls={d.sources} />
  </>;
}

/* ======================================================================================= shared pieces */

type View = "month" | "list";
const VIEW_KEY = "ri_calendar_view";
const readView = (): View => { try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "month"; } catch { return "month"; } };
const saveView = (v: View) => { try { localStorage.setItem(VIEW_KEY, v); } catch { /* remembered for this visit only */ } };

const GridGlyph = () => <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="12" /><path d="M1.5 6.5h13M6 6.5v8M10.5 6.5v8M1.5 10.5h13" /></svg>;
const ListGlyph = () => <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M5.5 3.5h9M5.5 8h9M5.5 12.5h9M1.5 3.5h2M1.5 8h2M1.5 12.5h2" /></svg>;
const Chevron = ({ dir }: { dir: "left" | "right" | "down" }) => (
  <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
    <path d={dir === "left" ? "M10 3 5 8l5 5" : dir === "right" ? "m6 3 5 5-5 5" : "m3.5 6 4.5 4.5L12.5 6"} /></svg>
);
const Alert = () => <svg className="ag-alert" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" focusable="false"><rect x=".5" y=".5" width="13" height="13" /><path d="M7 3v5M7 9.6v1.6" /></svg>;

function ViewToggle({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div className="cal-view" role="group" aria-label={tr("View")}>
      <button type="button" aria-pressed={view === "month"} onClick={() => onChange("month")}><GridGlyph /><span>{tr("Month")}</span></button>
      <button type="button" aria-pressed={view === "list"} onClick={() => onChange("list")}><ListGlyph /><span>{tr("List")}</span></button>
    </div>
  );
}

/** A number in bold and its noun: "<b>4</b> tender deadlines". */
function Count({ n, words }: { n: number; words: [[string, string], [string, string, string]] }) {
  const all = counted(n, ...words);
  const num = number(n);
  return <span className="cal-n"><b>{num}</b>{all.slice(num.length)}</span>;
}

/* ======================================================================================= the page */

export default function Calendar() {
  useLanguage();
  const [d, setD] = useState<Cal | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [params, setParams] = useSearchParams();
  // a link may ask for a view (the company page's "See every deadline" opens the list); otherwise the viewer's last choice
  const [view, setView] = useState<View>(() => (params.get("view") === "list" || params.get("view") === "month" ? params.get("view") as View : readView()));
  useEffect(() => { api<Cal>("/api/regulatory-calendar").then(setD).catch(setErr); }, []);
  const choose = (v: View) => {
    setView(v); saveView(v);
    if (params.has("view")) { const next = new URLSearchParams(params); next.delete("view"); setParams(next, { replace: true }); }
  };
  const toggle = <ViewToggle view={view} onChange={choose} />;
  const head = (
    <div className="page-head rc-head">
      <div className="eyebrow">{tr("Calendar")}</div><h1>{tr("Deadline calendar")}</h1>
      <p className="sub">{tr("Dates to sell on: EU laws that give banks, payment firms and insurers a fixed date for IT work, and the closing dates of open tenders from buyers in our ICP.")}</p>
    </div>
  );
  if (err) return <>{head}<Err e={err} onRetry={reloadPage} /></>;
  if (!d) return <div className="rc">{head}<Skeleton label={tr("Loading the deadlines")} /></div>;
  return <div className={`rc rc-is-${view}`}>{head}{view === "month" ? <MonthView cal={d} toggle={toggle} /> : <ListView d={d} toggle={toggle} />}</div>;
}

/* ======================================================================================= cards (the agenda and the list) */

/** A day as a block: "30 / Mon" (or "9 / Jan" in the list). Today is filled. */
function DateBlock({ top, bottom, today = false, approx = false }: { top: ReactNode; bottom: ReactNode; today?: boolean; approx?: boolean }) {
  return <span className={`ag-date-in${today ? " is-today" : ""}${approx ? " is-approx" : ""}`} aria-hidden="true"><b>{top}</b><span>{bottom}</span></span>;
}

/** A regulatory deadline as a card: what, when, how many of our accounts, what they must build; the sources and the accounts open in place. */
function RegCard({ d, kinds, open, onToggle, idKey, kind = "", when }: {
  d: Deadline; kinds: Record<string, Kind>; open: boolean; onToggle: () => void; idKey: string; kind?: string; when?: string;
}) {
  const id = `ag-d-${idKey}`;
  const acc = d.accounts ?? { count: 0, by_kind: {}, sample: [] };
  const count = kind ? acc.by_kind[kind] ?? 0 : acc.count;
  const hollow = !d.past && d.certainty !== "adopted";
  return (
    <li className={`ag-card ag-reg${d.past ? " is-past" : ""}${hollow ? " is-unsure" : ""}`}>
      <p className="ag-top">
        <span className="ag-type"><Mark layer="regulatory" hollow={hollow} />{tr("Regulatory")}</span>
        <CertaintyBadge d={d} />
        <span className="ag-left">{when ?? leftText(d)}</span>
      </p>
      <h4>{pick(d.title, d.title_pl)}</h4>
      <p className="ag-meta">{d.regulation}</p>
      <p className="ag-must">{tr("They must {}.", pick(d.must, d.must_pl))}</p>
      <p className="ag-acc">{accountsLine(count)}</p>
      <button type="button" className="linkish ag-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        {open ? tr("Hide the details") : tr("Details and sources")}<Chevron dir="down" /></button>
      {open && <div className="ag-detail" id={id}><DeadlineBody d={d} kind={kind} kinds={kinds} countShown /></div>}
    </li>
  );
}

/** A tender closing on the day: who is buying, what, where, roughly for how much, how long is left, and where to read it. */
function TenderCard({ t, today }: { t: TenderItem; today: string }) {
  const n = daysBetween(today, t.date);
  const urgent = n < 7;
  const closes = n <= 0 ? tr("Closes today") : n === 1 ? tr("Closes tomorrow") : tr("Closes in {} days", n);
  const href = safeHref(tedPage(t.url));
  const kind = t.kind && BUYER_KIND[t.kind] ? tr(BUYER_KIND[t.kind]) : null;
  const where = [countryName(t.country), t.value_eur != null ? tr("about {}", euros(t.value_eur)) : null].filter(Boolean).join(" · ");
  return (
    <li className={`ag-card ag-ten${urgent ? " is-urgent" : ""}`}>
      <p className="ag-top">
        <span className="ag-type"><Mark layer="tenders" />{tr("Tender")}</span>
        {kind && <span className="ag-kind">{kind}</span>}
        {t.fit && FIT_LABEL[t.fit] && <span className={`cm-fit cm-fit-${t.fit}`}>{tr(FIT_LABEL[t.fit])}</span>}
        <span className={`ag-left${urgent ? " is-urgent" : ""}`}>{urgent && <Alert />}{urgent ? tr("Urgent: {}", closes.charAt(0).toLowerCase() + closes.slice(1)) : closes}</span>
      </p>
      <p className="ag-buyer">{t.buyer}</p>
      <h4>{pick(t.title, t.title_pl)}</h4>
      {where && <p className="ag-meta">{where}</p>}
      <p className="ag-links">
        {href && <a href={href} target="_blank" rel="noreferrer noopener">{tr("Open the tender")} ↗</a>}
        {t.account && <Link to={`/accounts/${t.account.id}`}>{tr("Company page")}</Link>}
        <Link to={`/tenders?who=all&region=all&soon=1&q=${encodeURIComponent(buyerSearch(t.buyer))}`}>{tr("This buyer's open tenders")}</Link>
      </p>
    </li>
  );
}

/* ======================================================================================= the list view */

/** The regulatory deadlines as cards grouped by year, in the agenda's style. */
function ListView({ d, toggle }: { d: Cal; toggle: ReactNode }) {
  const [kind, setKind] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const groups = useMemo(() => {
    const shown = d.items.filter((x) => !kind || x.kinds.includes(kind));
    const ahead = shown.filter((x) => !x.past);
    const years = Array.from(new Set(ahead.map((x) => x.year))).sort();
    const out: [string, Deadline[]][] = years.map((y) => [String(y), ahead.filter((x) => x.year === y)]);
    const past = shown.filter((x) => x.past);
    if (past.length) out.push([tr("Already in force"), past]);
    return out;
  }, [d, kind]);
  const next = d.items.find((x) => !x.past && x.certainty === "adopted");
  const kinds = Object.entries(d.kinds);
  const ahead = d.items.filter((x) => !x.past).length;
  return <div className="cl">
    <div className="cm-bar">
      <h2 className="cl-title">{tr("Regulatory deadlines")}</h2>
      <div className="cm-tools">{toggle}</div>
    </div>
    <p className="cm-sum">
      <Count n={ahead} words={[["deadline ahead", "deadlines ahead"], ["termin przed nami", "terminy przed nami", "terminów przed nami"]]} />
      <span className="cm-sep" aria-hidden="true" />
      <span>{tr("Our accounts affected")}: <b>{number(d.counts.accounts)}</b></span>
      {next && <><span className="cm-sep" aria-hidden="true" /><span>{tr("Next fixed date")}: <b>{whenText(next)}</b> ({leftText(next).toLowerCase()})</span></>}
    </p>
    <div className="cl-filter">
      <div className="chips" role="group" aria-label={tr("Kind of firm")}>
        <button type="button" className="chip" aria-pressed={!kind} onClick={() => setKind("")}>{tr("All firms")}</button>
        {kinds.map(([k, v]) => <button key={k} type="button" className="chip" aria-pressed={kind === k} onClick={() => setKind(kind === k ? "" : k)}>{cap(pick(v.plural, v.plural_pl))}</button>)}
      </div>
      <p className="rc-how">{tr("Adopted dates in the next {} months show on each account's page, and up to two a day reach Today. Provisional and approximate dates are shown here only: check them before you use them.", d.pin_months)}</p>
    </div>
    {groups.length === 0 && <p className="rc-empty">{tr("No deadlines for this kind of firm.")}</p>}
    {groups.map(([label, list]) => (
      <section key={label} className="cl-year" aria-labelledby={`cl-y-${label}`}>
        <h3 id={`cl-y-${label}`}>{label}<small>{counted(list.length, ["deadline", "deadlines"], ["termin", "terminy", "terminów"])}</small></h3>
        <ol className="ag-days">
          {list.map((x) => (
            <li key={x.id} className={`ag-day${x.past ? " is-past" : ""}`}>
              <span className="ag-date">{x.approximate
                ? <DateBlock top="≈" bottom={x.year} approx />
                : <DateBlock top={fmt(x.date, { day: "numeric" })} bottom={fmt(x.date, { month: "short" }).replace(/\.$/, "")} />}</span>
              <ul className="ag-cards">
                <RegCard d={x} kinds={d.kinds} kind={kind} idKey={`l-${x.id}`} open={open === x.id} onToggle={() => setOpen(open === x.id ? null : x.id)}
                  when={`${whenText(x)} · ${leftText(x)}`} />
              </ul>
            </li>
          ))}
        </ol>
      </section>
    ))}
    <p className="rc-foot">{tr("Who a deadline applies to comes from the company's country and its sector on the lead list (banking, payments, insurance). Fintech and software companies are not matched: the list does not say which of them hold a licence.")}
      {d.counts.left_out > 0 && <> {tr("{} central banks, statutory insurers and public bodies on the list are left out: these rules are about commercial banks, payment firms and life insurers.", number(d.counts.left_out))}</>}</p>
  </div>;
}

/* ======================================================================================= the month view */

type Layer = "regulatory" | "tenders";
const LAYERS: Layer[] = ["regulatory", "tenders"];
type RegItem = Deadline & { layer: "regulatory" };
type TenderItem = {
  layer: "tenders"; id: string; date: string; deadline: string; deadline_tz: string | null; days_left: number; title: string; title_pl?: string | null;
  title_original: string; buyer: string; account: { id: string; name: string } | null; country: string | null; url: string; source: string;
  value_eur: number | null; sector: string; kind: string | null; group: string; fit: string | null;
};
type DayItem = RegItem | TenderItem;
type Counts = Partial<Record<Layer, number>>;
type Day = { date: string; counts: Counts; total: number; items: DayItem[]; more: number };
type Events = { from: string; to: string; today: string; layers: Layer[]; unavailable: Layer[]; per_day: number; days: Day[];
  months: Record<string, Counts>; totals: Counts; undated: RegItem[] };

const LAYERS_KEY = "ri_calendar_layers";
const PER_DAY = 4;               // items per day the month asks for (the rest are counted)
const ALL_OF_A_DAY = 50;         // one day opened in full
const readLayers = (): Record<Layer, boolean> => {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYERS_KEY) ?? "null") as Partial<Record<Layer, boolean>> | null;
    if (raw && typeof raw === "object") return { regulatory: raw.regulatory !== false, tenders: raw.tenders !== false };
  } catch { /* storage can be disabled */ }
  return { regulatory: true, tenders: true };
};
const saveLayers = (v: Record<Layer, boolean>) => { try { localStorage.setItem(LAYERS_KEY, JSON.stringify(v)); } catch { /* remembered for this visit only */ } };

/* days as "YYYY-MM-DD" in the viewer's own calendar (no time zones involved), months as "YYYY-MM" */
const pad = (n: number) => String(n).padStart(2, "0");
const isoOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const dateOf = (s: string) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, (m || 1) - 1, d || 1); };
const addDays = (s: string, n: number) => { const d = dateOf(s); d.setDate(d.getDate() + n); return isoOf(d); };
const addMonths = (k: string, n: number) => { const [y, m] = k.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };
const daysBetween = (a: string, b: string) => Math.round((dateOf(b).getTime() - dateOf(a).getTime()) / 86_400_000);
const weekday = (s: string) => (dateOf(s).getDay() + 6) % 7;            // Monday 0 ... Sunday 6
/** The same day of the month in another month, or its last day. */
const sameDay = (k: string, s: string) => { const [y, m] = k.split("-").map(Number); return `${k}-${pad(Math.min(Number(s.slice(8, 10)), new Date(y, m, 0).getDate()))}`; };
/** Every day the month's grid shows: whole weeks, Monday first. */
function gridDays(k: string): string[] {
  const first = `${k}-01`;
  const [y, m] = k.split("-").map(Number);
  const lead = weekday(first);
  const weeks = Math.ceil((lead + new Date(y, m, 0).getDate()) / 7);
  return Array.from({ length: weeks * 7 }, (_, i) => addDays(first, i - lead));
}
const dayFmt = (s: string, o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(loc(), o).format(dateOf(s));
const shortWd = (s: string) => cap(dayFmt(s, { weekday: "short" })).replace(/\.$/, "");
const okDay = (s: string | null) => (s && /^\d{4}-\d{2}-\d{2}$/.test(s) && isoOf(dateOf(s)) === s ? s : null);
const okMonth = (s: string | null) => (s && /^\d{4}-(0[1-9]|1[0-2])$/.test(s) ? s : null);
const reducedMotion = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

const REG_WORDS: [[string, string], [string, string, string]] = [["regulatory deadline", "regulatory deadlines"], ["termin regulacyjny", "terminy regulacyjne", "terminów regulacyjnych"]];
const TENDER_WORDS: [[string, string], [string, string, string]] = [["tender deadline", "tender deadlines"], ["termin przetargu", "terminy przetargów", "terminów przetargów"]];
const WORDS: Record<Layer, [[string, string], [string, string, string]]> = { regulatory: REG_WORDS, tenders: TENDER_WORDS };
/** "1 regulatory deadline and 3 tender deadlines" for the layers switched on; "" when there is nothing. */
function countsText(c: Counts, on: (l: Layer) => boolean): string {
  const parts = LAYERS.filter((l) => on(l) && (c[l] ?? 0) > 0).map((l) => counted(c[l] ?? 0, ...WORDS[l]));
  return parts.length === 2 ? tr("{} and {}", parts[0], parts[1]) : parts[0] ?? "";
}

/** Who is buying, in the tender list's own words (same labels as the Open tenders page). */
const BUYER_KIND: Record<string, string> = {
  bank: "Bank", central_bank: "Central bank", development_bank: "State development bank", savings_bank: "Savings bank", cooperative_bank: "Cooperative bank",
  building_society: "Building society", insurance: "Insurer", payments: "Payments", market_infrastructure: "Exchange or clearing", asset_management: "Asset manager",
  pension: "Pension fund", lending: "Lender", financial_software: "Financial software", financial_authority: "Financial authority",
  digital_commerce: "Digital commerce", enterprise_software: "Enterprise software", adtech_martech: "AdTech or MarTech",
};
const FIT_LABEL: Record<string, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit", none: "Not a fit" };
/** TED shows each notice in every EU language: open it in the language the app is in. */
const tedPage = (url: string) => url.replace(/^https:\/\/ted\.europa\.eu\/[a-z]{2}\/notice\//, `https://ted.europa.eu/${getLanguage()}/notice/`);
const euros = (v: number) => {
  try { const t = new Intl.NumberFormat(loc(), { style: "currency", currency: "EUR", notation: "compact", maximumSignificantDigits: 3 }).format(v); return isPl() ? t : t.replace(/(\d)K\b/, "$1k"); }
  catch { return `€${number(Math.round(v))}`; }
};
/** The words of a buyer's name the Open tenders search can find it by. */
const buyerSearch = (buyer: string) => buyer.replace(/[^\p{L}\p{N}]+/gu, " ").split(" ").filter((w) => w.length >= 2).slice(0, 4).join(" ");
const LEGAL_FORM = /[\s,]+(s\.\s?a\.|sa|ag|plc|ltd\.?|limited|oyj|oy|ab|asa|a\/s|a\.\s?s\.|gmbh|e\.\s?g\.|eg|s\.p\.a\.|spa|n\.v\.|nv|b\.v\.|bv|se|kgaa|zrt\.?|nyrt\.?|sp\.\s?z\s?o\.\s?o\.)$/i;
/** A buyer's name short enough for a grid pill: the part before a comma, without the legal form, and in normal case when written in capitals. */
function buyerShort(name: string): string {
  let s = (name.split(/,|;|\(| [–—] /)[0] ?? "").trim();
  for (let i = 0; i < 2; i++) s = s.replace(LEGAL_FORM, "").trim();
  if (s.length > 4 && s === s.toUpperCase() && s !== s.toLowerCase()) {
    s = s.split(" ").map((w) => (w.length <= 3 ? w : w.charAt(0) + w.slice(1).toLowerCase())).join(" ");
  }
  return s || name;
}

/** A layer's marker: a diamond for a regulatory deadline (hollow while it still has to be confirmed), a triangle for a tender. Never colour alone. */
function Mark({ layer, hollow = false }: { layer: Layer; hollow?: boolean }) {
  return (
    <svg className={`cm-mark cm-mark-${layer}${hollow ? " is-hollow" : ""}`} viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
      {layer === "regulatory" ? <path d="M6 .9 11.1 6 6 11.1.9 6Z" /> : <path d="M6 1.3 11.2 10.7H.8Z" />}
    </svg>
  );
}
const hollowOf = (i: DayItem) => i.layer === "regulatory" && !i.past && i.certainty !== "adopted";
const pillText = (i: DayItem) => (i.layer === "regulatory" ? shortOf(i) : buyerShort(i.buyer));
const itemKey = (i: DayItem) => `${i.layer}-${i.id}`;

function MonthView({ cal, toggle }: { cal: Cal; toggle: ReactNode }) {
  useLanguage();
  const today = useMemo(() => isoOf(new Date()), []);
  const thisMonth = today.slice(0, 7);
  const [params, setParams] = useSearchParams();
  const [month, setMonth] = useState(() => okMonth(params.get("month")) ?? okDay(params.get("day"))?.slice(0, 7) ?? thisMonth);
  const [day, setDay] = useState(() => { const d = okDay(params.get("day")); return d && d.startsWith(month) ? d : month === thisMonth ? today : `${month}-01`; });
  const [focus, setFocus] = useState(day);
  const [layers, setLayers] = useState(readLayers);
  const [data, setData] = useState<Events | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [years, setYears] = useState<Record<number, Record<string, Counts>>>({});
  const [full, setFull] = useState<Record<string, Day>>({});
  const [busyDay, setBusyDay] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [picker, setPicker] = useState(false);
  const [pkYear, setPkYear] = useState(() => Number(month.slice(0, 4)));
  const cache = useRef(new Map<string, Events>());
  const pickFirst = useRef(!okDay(params.get("day")) && month !== thisMonth);
  const touched = useRef(false);
  const moveFocus = useRef(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const pickBtn = useRef<HTMLButtonElement>(null);
  const pickBox = useRef<HTMLDivElement>(null);
  const grid = useMemo(() => gridDays(month), [month]);

  // the visible weeks, once per month (kept while the page is open)
  useEffect(() => {
    const hit = cache.current.get(month);
    if (hit) { setData(hit); setErr(null); return; }
    const ctl = new AbortController();
    setData(null); setErr(null);
    api<Events>(`/api/calendar/events?from=${grid[0]}&to=${grid[grid.length - 1]}&per_day=${PER_DAY}`, { signal: ctl.signal })
      .then((x) => { cache.current.set(month, x); setData(x); })
      .catch((e) => { if (!ctl.signal.aborted) setErr(e); });
    return () => ctl.abort();
  }, [month, grid, attempt]);
  // how much each month of a year holds, for the month picker (fetched when the picker shows that year)
  useEffect(() => {
    if (!picker || years[pkYear]) return;
    const ctl = new AbortController();
    api<Events>(`/api/calendar/events?from=${pkYear}-01-01&to=${pkYear}-12-31&per_day=0`, { signal: ctl.signal })
      .then((x) => setYears((m) => ({ ...m, [pkYear]: x.months }))).catch(() => undefined);
    return () => ctl.abort();
  }, [picker, pkYear, years]);
  // the address keeps the month and day once the person moves (so Back and a shared link land on the same day)
  useEffect(() => { if (touched.current) setParams({ month, day }, { replace: true }); }, [month, day, setParams]);
  // arrow keys: focus follows the day (after the grid has been drawn for its month)
  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-day="${focus}"]`)?.focus();
  }, [focus, month]);
  // the picker closes on a click elsewhere; it opens on the month shown, with focus on it
  useEffect(() => {
    if (!picker) return;
    pickBox.current?.querySelector<HTMLButtonElement>("[aria-current='true'], .cm-pk-months button")?.focus();
    const away = (e: Event) => {
      const t = e.target as Node;
      if (!pickBox.current?.contains(t) && !pickBtn.current?.contains(t)) setPicker(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("touchstart", away); };
  }, [picker]);

  const allowed = data?.layers ?? LAYERS;
  const on = useCallback((l: Layer) => layers[l] && allowed.includes(l), [layers, allowed]);
  const days = useMemo(() => new Map((data?.days ?? []).map((x) => [x.date, full[x.date] ?? x])), [data, full]);
  const visible = useCallback((s: string) => {
    const x = days.get(s);
    if (!x) return { items: [] as DayItem[], more: 0, total: 0, counts: {} as Counts };
    const items = x.items.filter((i) => on(i.layer));
    const total = LAYERS.reduce((n, l) => n + (on(l) ? x.counts[l] ?? 0 : 0), 0);
    return { items, more: total - items.length, total, counts: x.counts };
  }, [days, on]);
  // a month reached by its buttons opens on its first day with something on it
  useEffect(() => {
    if (!data || data.from !== grid[0] || !pickFirst.current) return;          // wait for this month's own days
    pickFirst.current = false;
    if (visible(day).total) return;
    const first = data.days.find((x) => x.date.startsWith(month) && visible(x.date).total > 0);
    if (first) { setDay(first.date); setFocus(first.date); }
  }, [data, day, month, grid, visible]);

  const toTop = () => {                                       // the chosen day is pinned at the top of the agenda
    const p = panelRef.current;
    if (p && p.scrollHeight > p.clientHeight) p.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
  };
  const go = (k: string, target?: string) => {
    touched.current = true;
    const t = target ?? (k === thisMonth ? today : `${k}-01`);
    pickFirst.current = !target && k !== thisMonth;
    setMonth(k); setDay(t); setFocus(t); setOpen(null); setPicker(false);
    toTop();
  };
  const choose = (s: string, e?: MouseEvent) => {
    touched.current = true;
    setDay(s); setFocus(s); setOpen(null);
    if (!s.startsWith(month)) setMonth(s.slice(0, 7));          // a day of the month before or after
    const v = visible(s);
    setSaid(`${dayFmt(s, { day: "numeric", month: "long" })}: ${countsText(v.counts, on) || tr("nothing due")}`);
    toTop();
    if (e && e.detail > 0) {                                     // tapped or clicked: make sure the day's list can be seen (phones: it is under the grid)
      window.requestAnimationFrame(() => {
        const r = document.getElementById("ag-sel-h")?.getBoundingClientRect();
        if (r && r.top > window.innerHeight - 96) window.scrollBy({ top: r.top - window.innerHeight + 260, behavior: reducedMotion() ? "auto" : "smooth" });
      });
    }
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    let next: string | null = null;
    if (e.key in step) next = addDays(focus, step[e.key]);
    else if (e.key === "Home") next = addDays(focus, -weekday(focus));
    else if (e.key === "End") next = addDays(focus, 6 - weekday(focus));
    else if (e.key === "PageUp" || e.key === "PageDown") next = sameDay(addMonths(focus.slice(0, 7), e.key === "PageUp" ? -1 : 1), focus);
    if (!next) return;
    e.preventDefault();
    moveFocus.current = true;
    touched.current = true;
    setFocus(next);
    if (!grid.includes(next) || e.key.startsWith("Page")) {            // another month: the chosen day comes along, so the agenda matches the grid
      setMonth(next.slice(0, 7)); setDay(next); setOpen(null);
    }
  };
  const toggleLayer = (l: Layer) => { const v = { ...layers, [l]: !layers[l] }; setLayers(v); saveLayers(v); };
  const loadAll = (s: string) => {
    setBusyDay(s);
    api<Events>(`/api/calendar/events?from=${s}&to=${s}&per_day=${ALL_OF_A_DAY}`)
      .then((x) => { const one = x.days[0]; if (one) setFull((m) => ({ ...m, [s]: one })); })
      .catch(() => undefined).finally(() => setBusyDay(null));
  };
  const openPicker = () => { setPkYear(Number(month.slice(0, 4))); setPicker(!picker); };
  const pickerKey = (e: KeyboardEvent<HTMLDivElement>) => { if (e.key === "Escape") { e.stopPropagation(); setPicker(false); pickBtn.current?.focus(); } };

  const weekdays = Array.from({ length: 7 }, (_, i) => addDays("2024-01-01", i));      // 1 January 2024 was a Monday
  const monthTitle = cap(dayFmt(`${month}-01`, { month: "long", year: "numeric" }));
  const inMonth: Counts = {};
  for (const x of data?.days ?? []) if (x.date.startsWith(month)) for (const l of LAYERS) inMonth[l] = (inMonth[l] ?? 0) + (x.counts[l] ?? 0);
  const year = Number(month.slice(0, 4));
  const firstYear = Math.min(...cal.items.map((x) => x.year), year, Number(thisMonth.slice(0, 4)));
  const lastYear = Math.max(...cal.items.map((x) => x.year), year, Number(thisMonth.slice(0, 4)) + 1);
  const yearList = Array.from({ length: lastYear - firstYear + 1 }, (_, i) => firstYear + i);
  const undated = (data?.undated ?? []).filter((x) => on("regulatory") && x.year === year);
  const sel = visible(day);
  const ahead = (data?.days ?? []).filter((x) => visible(x.date).total > 0);
  const nextDay = sel.total ? null : ahead.find((x) => x.date > day)?.date ?? null;
  const tabStop = grid.includes(focus) ? focus : `${month}-01`;
  const nothingOn = !LAYERS.some(on);
  const monthDays = ahead.filter((x) => x.date.startsWith(month)).map((x) => x.date);
  const restDays = monthDays.filter((s) => s !== day);
  // "next": the first day from today (or from the month's start, for a month ahead) with something on it
  const from = month === thisMonth ? today : month > thisMonth ? `${month}-01` : null;
  const nextUp = from ? ahead.find((x) => x.date >= from)?.date ?? null : null;
  const nextItem = nextUp ? visible(nextUp).items[0] : null;
  const loading = !data && !err;

  const summary = nothingOn ? <span>{tr("Switch on at least one kind of date above.")}</span>
    : loading ? <span>{tr("Loading the month")}</span>
      : err ? null
        : <>
          {LAYERS.some((l) => on(l) && (inMonth[l] ?? 0) > 0)
            ? LAYERS.filter((l) => on(l) && (inMonth[l] ?? 0) > 0).map((l, i) => <span key={l} className="cm-sum-part">{i > 0 && <span className="cm-sep" aria-hidden="true" />}<Mark layer={l} /><Count n={inMonth[l] ?? 0} words={WORDS[l]} /></span>)
            : <span>{tr("Nothing due in {}.", monthTitle)}</span>}
          {nextUp && nextItem && <span className="cm-sum-part is-next">
            <span className="cm-sep" aria-hidden="true" />
            <span>{tr("Next up")}:</span>{" "}
            <button type="button" className="linkish cm-next" onClick={() => choose(nextUp)}>
              {dayFmt(nextUp, { day: "numeric", month: "short" }).replace(/\.$/, "")}, {nextItem.layer === "tenders" ? nextItem.buyer : shortOf(nextItem)}</button>{" "}
            {nextUp >= today && <span className="cm-in">({inText(daysBetween(today, nextUp))})</span>}
          </span>}
        </>;

  const cardsOf = (s: string, items: DayItem[]) => items.map((i) => i.layer === "regulatory"
    ? <RegCard key={itemKey(i)} d={i} kinds={cal.kinds} idKey={`${s}-${i.id}`} open={open === `${s}-${i.id}`} onToggle={() => setOpen(open === `${s}-${i.id}` ? null : `${s}-${i.id}`)} />
    : <TenderCard key={itemKey(i)} t={i} today={today} />);

  return (
    <div className="cm">
      <div className="cm-bar">
        <div className="cm-nav">
          <button type="button" className="cm-step" aria-label={tr("Previous month")} onClick={() => go(addMonths(month, -1))}><Chevron dir="left" /></button>
          <button type="button" className="cm-step" aria-label={tr("Next month")} onClick={() => go(addMonths(month, 1))}><Chevron dir="right" /></button>
          <h2 id="cm-title" className="cm-title">
            <button type="button" ref={pickBtn} className="cm-pick" aria-expanded={picker} aria-controls="cm-picker" onClick={openPicker}
              aria-label={`${monthTitle}. ${tr("Jump to a month")}`}>
              <span aria-live="polite">{monthTitle}</span><Chevron dir="down" /></button>
          </h2>
          <button type="button" className="cm-today" onClick={() => go(thisMonth, today)} disabled={month === thisMonth && day === today}>{tr("Today")}</button>
          {picker && (
            <div id="cm-picker" className="cm-picker" ref={pickBox} role="dialog" aria-label={tr("Jump to a month")} onKeyDown={pickerKey}>
              <div className="cm-pk-years" role="group" aria-label={tr("Year")}>
                {yearList.map((y) => <button key={y} type="button" aria-pressed={y === pkYear} onClick={() => setPkYear(y)}>{y}</button>)}
              </div>
              <ol className="cm-pk-months">
                {Array.from({ length: 12 }, (_, i) => `${pkYear}-${pad(i + 1)}`).map((k) => {
                  const c = years[pkYear]?.[k] ?? {};
                  const what = countsText(c, on);
                  return <li key={k}><button type="button" aria-current={k === month ? "true" : undefined} className={k === thisMonth ? "is-now" : undefined}
                    aria-label={`${cap(dayFmt(`${k}-01`, { month: "long", year: "numeric" }))}${what ? `: ${what}` : ""}`} onClick={() => { go(k); pickBtn.current?.focus(); }}>
                    <span>{cap(dayFmt(`${k}-01`, { month: "short" })).replace(/\.$/, "")}</span>
                    <span className="cm-mm">{on("regulatory") && (c.regulatory ?? 0) > 0 && <Mark layer="regulatory" />}{on("tenders") && (c.tenders ?? 0) > 0 && <Mark layer="tenders" />}</span>
                  </button></li>;
                })}
              </ol>
            </div>
          )}
        </div>
        <div className="cm-tools">
          <div className="cm-chips" role="group" aria-label={tr("Show on the calendar")}>
            {LAYERS.filter((l) => allowed.includes(l)).map((l) => (
              <button key={l} type="button" className={`cm-chip cm-chip-${l}`} aria-pressed={layers[l]} onClick={() => toggleLayer(l)}
                aria-label={`${l === "regulatory" ? tr("Regulatory deadlines") : tr("Tender deadlines")}${data ? `: ${tr("{} this month", inMonth[l] ?? 0)}` : ""}`}>
                <span className="cm-chip-box" aria-hidden="true" />
                <Mark layer={l} />
                <span className="cm-chip-t" aria-hidden="true">{l === "regulatory" ? tr("Regulatory") : tr("Tenders")}</span>
                {data && <b aria-hidden="true">{number(inMonth[l] ?? 0)}</b>}
              </button>
            ))}
          </div>
          {toggle}
        </div>
      </div>

      <p className="cm-sum" aria-live="polite">{summary}</p>
      {err != null && <Err e={err} onRetry={() => { cache.current.delete(month); setAttempt((n) => n + 1); }} />}

      <div className="cm-body">
        <div className="cm-grid" role="grid" aria-labelledby="cm-title" aria-busy={loading} ref={gridRef} onKeyDown={onKey}>
          <div className="cm-row cm-wd" role="row">
            {weekdays.map((w, i) => <div key={w} role="columnheader" className={i >= 5 ? "is-weekend" : undefined}>
              <abbr title={cap(dayFmt(w, { weekday: "long" }))}>{shortWd(w)}</abbr></div>)}
          </div>
          {Array.from({ length: grid.length / 7 }, (_, w) => (
            <div key={grid[w * 7]} className="cm-row" role="row">
              {grid.slice(w * 7, w * 7 + 7).map((s, col) => {
                const v = visible(s);
                const heat = v.total >= 4 ? 3 : v.total >= 2 ? 2 : v.total ? 1 : 0;
                const cls = ["cm-day", s.startsWith(month) ? "" : "is-other", weekday(s) >= 5 ? "is-weekend" : "", s === today ? "is-today" : "",
                  s === day ? "is-selected" : "", s < today ? "is-past" : "", heat ? `heat-${heat}` : "", col >= 4 ? "tip-left" : "", w >= grid.length / 7 - 2 ? "tip-up" : ""]
                  .filter(Boolean).join(" ");
                const what = countsText(v.counts, on);
                return (
                  <div key={s} role="gridcell" aria-selected={s === day} className={cls}>
                    <button type="button" data-day={s} tabIndex={s === tabStop ? 0 : -1} aria-current={s === today ? "date" : undefined} aria-controls="cm-panel"
                      aria-label={`${cap(dayFmt(s, { weekday: "long", day: "numeric", month: "long", year: "numeric" }))}${s === today ? `, ${tr("today")}` : ""}. ${what || tr("Nothing due.")}`}
                      onClick={(e) => choose(s, e)}>
                      <span className="cm-num" aria-hidden="true">{Number(s.slice(8))}</span>
                      {v.total > 0 && <span className="cm-pills" aria-hidden="true">
                        {v.items.slice(0, 3).map((i, n) => (
                          <span key={itemKey(i)} className={`cm-pill cm-pill-${i.layer}${hollowOf(i) ? " is-hollow" : ""}${n === 2 ? " is-3rd" : ""}`}>
                            <Mark layer={i.layer} hollow={hollowOf(i)} /><span className="cm-t">{pillText(i)}</span></span>
                        ))}
                        {v.total > 2 && <span className="cm-rest cm-rest-wide">+{v.total - 2}<span className="cm-t"> {tr("more")}</span></span>}
                        {v.total > 3 && <span className="cm-rest cm-rest-narrow">+{v.total - 3}</span>}
                      </span>}
                      {v.total > 0 && <span className="cm-tip" aria-hidden="true">
                        <span className="cm-tip-h">{cap(dayFmt(s, { weekday: "long", day: "numeric", month: "long" }))}<span>{cap(what)}</span></span>
                        {v.items.map((i) => (
                          <span key={itemKey(i)} className="cm-tip-i">
                            <Mark layer={i.layer} hollow={hollowOf(i)} />
                            <span><b>{i.layer === "regulatory" ? shortOf(i) : i.buyer}</b>
                              <span>{i.layer === "regulatory" ? `${pick(i.title, i.title_pl)} · ${accountsCount(i)}` : [pick(i.title, i.title_pl), i.value_eur != null ? tr("about {}", euros(i.value_eur)) : null].filter(Boolean).join(" · ")}</span></span>
                          </span>
                        ))}
                        {v.more > 0 && <span className="cm-tip-more">{tr("and {} more", v.more)}</span>}
                      </span>}
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <p className="sr" aria-live="polite">{said}</p>

        <section id="cm-panel" className="cm-agenda" ref={panelRef} aria-label={tr("Agenda")}>
          <div className="ag-sel">
            <h3 id="ag-sel-h" className="ag-sel-h">
              <DateBlock top={Number(day.slice(8))} bottom={shortWd(day)} today={day === today} />
              <span className="ag-sel-t">{cap(dayFmt(day, { weekday: "long", day: "numeric", month: "long", year: "numeric" }))}
                {day === today && <span className="cm-tag">{tr("Today")}</span>}</span>
            </h3>
            {loading ? <Skeleton rows={2} label={tr("Loading the month")} />
              : sel.total === 0 ? <p className="ag-none">{nothingOn ? tr("Switch on at least one kind of date above.") : tr("Nothing due on this day.")}
                {nextDay && <> <button type="button" className="linkish" onClick={() => choose(nextDay)}>{tr("Next date: {}", dayFmt(nextDay, { weekday: "long", day: "numeric", month: "long" }))}</button></>}</p>
                : <>
                  <ul className="ag-cards">{cardsOf(day, sel.items)}</ul>
                  {sel.more > 0 && (sel.items.length < ALL_OF_A_DAY
                    ? <p className="cm-more"><button type="button" onClick={() => loadAll(day)} disabled={busyDay === day}>{busyDay === day ? tr("Loading") : tr("Show {} more", sel.more)}</button></p>
                    : <p className="cm-more"><Link to="/tenders?who=all&region=all&within=7&soon=1">{tr("See the rest in open tenders")}</Link></p>)}
                </>}
          </div>

          {!loading && !nothingOn && (restDays.length > 0 || undated.length > 0) && <div className="ag-month">
            {restDays.length > 0 && <>
              <h3 className="ag-month-h">{monthDays.includes(day) ? tr("Rest of {}", monthTitle) : tr("All of {}", monthTitle)}</h3>
              <ol className="ag-days">
                {restDays.map((s) => {
                  const v = visible(s);
                  return (
                    <li key={s} className={`ag-day${s < today ? " is-past" : ""}`}>
                      <button type="button" className="ag-date" onClick={(e) => choose(s, e)}
                        aria-label={`${cap(dayFmt(s, { weekday: "long", day: "numeric", month: "long" }))}: ${tr("show this day")}`}>
                        <DateBlock top={Number(s.slice(8))} bottom={shortWd(s)} today={s === today} /></button>
                      <div className="ag-day-body">
                        <ul className="ag-cards">{cardsOf(s, v.items)}</ul>
                        {v.more > 0 && <p className="cm-more"><button type="button" className="linkish" onClick={() => { choose(s); loadAll(s); }}>{tr("{} more on this day", v.more)}</button></p>}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </>}
            {undated.length > 0 && <>
              <h3 className="ag-month-h">{tr("Expected in {}, day not fixed yet", year)}</h3>
              <ol className="ag-days">
                <li className="ag-day">
                  <span className="ag-date"><DateBlock top="≈" bottom={year} approx /></span>
                  <ul className="ag-cards">{undated.map((x) => <RegCard key={x.id} d={x} kinds={cal.kinds} idKey={`u-${x.id}`} when={whenText(x)}
                    open={open === `u-${x.id}`} onToggle={() => setOpen(open === `u-${x.id}` ? null : `u-${x.id}`)} />)}</ul>
                </li>
              </ol>
            </>}
          </div>}
        </section>
      </div>
    </div>
  );
}

/** "in 3 days", "in 7 months": how far away a day is, as the cards say it. */
const inText = (n: number) => (n <= 0 ? tr("today") : n === 1 ? tr("tomorrow") : n < 60 ? tr("in {} days", n) : tr("in {} months", Math.round(n / 30.4)));
const accountsCount = (d: Deadline) => ((d.accounts?.count ?? 0) > 0 ? tr("{} of our accounts", number(d.accounts?.count ?? 0)) : tr("none of our accounts"));

const MINI = 3;          // deadlines shown on the company page before "Show more"

/** The company page's small block: the deadlines ahead that apply to this company. Nothing is shown when there are none. */
export function AccountDeadlines({ orgId }: { orgId: string }) {
  useLanguage();
  const [d, setD] = useState<{ items: Deadline[] } | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let on = true;
    setD(null); setAll(false);
    api<{ items: Deadline[] }>(`/api/accounts/${orgId}/deadlines`).then((x) => { if (on) setD(x); }).catch(() => { if (on) setD({ items: [] }); });
    return () => { on = false; };
  }, [orgId]);
  // the fixed dates first (soonest first), then the ones still to be confirmed
  const items = (d?.items ?? []).filter((x) => !x.past).sort((a, b) => Number(a.certainty !== "adopted") - Number(b.certainty !== "adopted") || a.date.localeCompare(b.date));
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, MINI);
  return (
    <section className="ac-panel rc-panel" aria-labelledby="rc-acc-h">
      <h2 id="rc-acc-h">{tr("Regulatory deadlines")}</h2>
      <p className="ac-purpose">{tr("EU laws that give them a fixed date for IT work. A good reason to ask who is doing it.")}</p>
      <ul className="rc-mini" id="rc-mini-list">
        {shown.map((x) => <li key={x.id}>
          <span className="rc-mini-d"><b>{x.approximate ? tr("about {}", x.year) : fmt(x.date, { day: "numeric", month: "short", year: "numeric" })}</b><small>{leftText(x)}</small></span>
          <div>
            <p className="rc-mini-t">{pick(x.title, x.title_pl)}</p>
            <p className="rc-mini-s">{tr("{}: must {}.", cap(pick(x.why ?? "", x.why_pl)), pick(x.must, x.must_pl))}</p>
            {x.certainty !== "adopted" && <CertaintyBadge d={x} />}
          </div>
        </li>)}
      </ul>
      <p className="rc-mini-more">
        {items.length > MINI && <button type="button" className="linkish" aria-expanded={all} aria-controls="rc-mini-list" onClick={() => setAll(!all)}>
          {all ? tr("Show fewer") : tr("Show {} more", items.length - MINI)}</button>}
        <Link to="/calendar?view=list">{tr("See every deadline")}</Link>
      </p>
    </section>
  );
}
