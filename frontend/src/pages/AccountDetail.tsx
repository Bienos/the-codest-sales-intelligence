import { getLanguage, number, tr } from "../locale";
import { Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { AccountFindings } from "../components/Findings";
import { FAMILY, familyOf, SHORT } from "../components/Signals";
import { countryName, staffCount } from "./targetMarket";
import { Badge, Err, humanize, Skeleton, reloadPage, safeHref, Tabs, TIER } from "../components/ui";
import "../styles/account.css";

/* ------------------------------------------------------------------ shared helpers (also used by the person page) */
export const host = (u?: string | null) => { try { return new URL(String(u)).hostname.replace(/^www\./, ""); } catch { return ""; } };
export const initials = (name: string) => {
  const words = name.split(/[\s-]+/).filter((w) => /^\p{L}/u.test(w));
  return (words.length === 1 ? words[0].slice(0, 2) : words.slice(0, 2).map((w) => w[0]).join("")).toUpperCase() || "?";
};
/** A research date: a full day, a month (YYYY-MM) or a year. */
export function fmtDay(d?: string | null): string {
  if (!d) return tr("Date unknown");
  if (/^\d{4}$/.test(d)) return d;
  if (/^\d{4}-\d{2}$/.test(d)) { const x = new Date(`${d}-01T00:00:00Z`); return isNaN(x.getTime()) ? d : x.toLocaleDateString(undefined, { year: "numeric", month: "short", timeZone: "UTC" }); }
  return fmtDate(d);
}
/** Where a statement came from: the page's site, or our own records. */
export function Cite({ c }: { c: any }) {
  if (!c) return null;
  if (c.source === "R" || !c.source_url) return <span className="ac-cite rec" title={tr("From our own records")}>{tr("Our records")}</span>;
  const href = safeHref(c.source_url);
  return href ? <a className="ac-cite" href={href} target="_blank" rel="noreferrer noopener" title={c.source_title ?? undefined}>{host(c.source_url)} ↗</a> : null;
}
export function usePoll(active: boolean, fn: () => void, ms = 5000) {
  useEffect(() => { if (!active) return; const t = setInterval(() => { if (!document.hidden) fn(); }, ms); return () => clearInterval(t); }, [active]);
}
/** Research costs money, so the button explains itself when it cannot run. */
export function ResearchSetup({ setup }: { setup?: any }) {
  if (!setup || setup.ready) return null;
  return <p className="ac-setup m-0" title={(setup.missing ?? []).join(", ") || undefined}>{tr("Research is not switched on yet: an administrator can turn it on.")}</p>;
}

/* ------------------------------------------------------------------ identity: monograms, avatars, icons */
/** One stable colour per name, each holding white initials at 5:1 or better. */
const HUES = ["#CC085D", "#6A3FC9", "#1F5C8F", "#B8402F", "#8A1C6B", "#0E7490"];
export const hueOf = (s: string) => { let h = 7; for (const ch of s.toLowerCase()) h = (h * 31 + (ch.codePointAt(0) ?? 0)) >>> 0; return HUES[h % HUES.length]; };

/** A company's mark: its initials on a colour of its own. */
export function Monogram({ name, size = "md", framed = false }: { name: string; size?: "xs" | "sm" | "md" | "lg"; framed?: boolean }) {
  return <span className={`mono mono-${size}${framed ? " framed" : ""}`} style={{ background: hueOf(name) }} aria-hidden="true">{initials(name)}</span>;
}

/** Buying committee groups. The API works the group out from the lead list's committee role and persona, the seniority and the job title. */
export const COMMITTEE: Record<string, { label: string; one: string; hint: string; color: string }> = {
  decision: { label: "Decision makers", one: "Decision maker", hint: "Sign off the budget and the supplier", color: "#CC085D" },
  technical: { label: "Technical", one: "Technical buyer", hint: "Own the engineering problem and judge the team", color: "#6A3FC9" },
  procurement: { label: "Procurement", one: "Procurement", hint: "Run the supplier route, vendor checks and contracts", color: "#1F5C8F" },
  influencer: { label: "Influencers", one: "Influencer", hint: "Business owners and advisers who shape the choice", color: "#B8402F" },
};
export const COMMITTEE_ORDER = ["decision", "technical", "procurement", "influencer"];
const group = (g?: string | null) => (g && COMMITTEE[g] ? g : "influencer");

/** A person's mark: their LinkedIn photo when Exa research found one, otherwise initials on the colour of their part of the buying committee.
 *  LinkedIn picture addresses expire after a while, so a picture that no longer loads drops back to the initials. The committee colour stays as a thin bar along the bottom of a photo. */
export function Avatar({ name, committee, size = "md", photo }: { name: string; committee?: string | null; size?: "xs" | "sm" | "md" | "lg"; photo?: string | null }) {
  const [failed, setFailed] = useState<string | null>(null);
  const color = COMMITTEE[group(committee)].color;
  const shown = Boolean(photo) && failed !== photo;
  return <span className={`mono avatar mono-${size}${shown ? " has-photo" : ""}`} style={{ background: color, "--tone": color } as React.CSSProperties} aria-hidden="true">
    {shown ? <img src={photo!} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(photo!)} /> : initials(name)}
  </span>;
}

const I = (d: ReactNode) => ({ className = "" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={`ico ${className}`}>{d}</svg>
);
export const Ic = {
  mail: I(<><rect x="3" y="5" width="18" height="14" /><path d="M3 6l9 7 9-7" /></>),
  check: I(<path d="M5 12.5l4.5 4.5L19 7.5" />),
  cross: I(<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />),
  dash: I(<path d="M6 12h12" />),
  linkedin: I(<><rect x="3" y="3" width="18" height="18" /><path d="M8 10.5V17M8 7.3v.4M11.5 17v-6.5M11.5 13.4c0-1.8 1.1-2.9 2.5-2.9s2.2 1 2.2 2.7V17" /></>),
  building: I(<path d="M4 21V5h10v16M14 10h6v11M4 21h16M8 9h2M8 13h2M8 17h2" />),
  globe: I(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18" /></>),
  people: I(<><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5" /><path d="M15.5 4.8a3.3 3.3 0 010 6.4M18 14.8c2 .7 3.2 2.4 3.5 5.2" /></>),
  key: I(<><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M16 7l3 3" /></>),
  layers: I(<path d="M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5" />),
  code: I(<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" />),
  cycle: I(<path d="M20 8a8 8 0 00-14.5-2M4 4v4h4M4 16a8 8 0 0014.5 2M20 20v-4h-4" />),
  outsource: I(<path d="M14 5h7v14h-7M3 12h11M10 8l4 4-4 4" />),
  list: I(<path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1" />),
  spark: I(<path d="M12 3v5M12 16v5M3 12h5M16 12h5M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3" />),
  search: I(<><circle cx="11" cy="11" r="7" /><path d="M16 16l5 5" /></>),
  news: I(<><path d="M4 5h13v14H6a2 2 0 01-2-2z" /><path d="M17 9h3v8a2 2 0 01-2 2M8 9h5M8 13h5M8 16h3" /></>),
  web: I(<><rect x="3" y="4" width="18" height="16" /><path d="M3 9h18M6 6.5h1M9 6.5h1" /></>),
  partner: I(<><rect x="3" y="7" width="10" height="10" /><rect x="11" y="7" width="10" height="10" /></>),
  hiring: I(<><circle cx="10" cy="8" r="4" /><path d="M3 20c.7-4 3.4-6 7-6 1.6 0 3 .4 4.1 1.1M18 14v6M15 17h6" /></>),
  target: I(<><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="M12 11.5v1" /></>),
  mic: I(<><rect x="9" y="3" width="6" height="11" /><path d="M5 11a7 7 0 0014 0M12 18v3M8 21h8" /></>),
  doc: I(<><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 12h7M9 16h5" /></>),
  chat: I(<path d="M4 5h16v11H9l-5 4z" />),
  headphones: I(<path d="M4 18v-5a8 8 0 0116 0v5M4 15h4v6H4zM16 15h4v6h-4z" />),
  star: I(<path d="M12 3l2.7 5.8 6.3.7-4.7 4.3 1.3 6.2L12 16.9 6.4 20l1.3-6.2L3 9.5l6.3-.7z" />),
  badge: I(<><rect x="4" y="3" width="16" height="18" /><circle cx="12" cy="10" r="3" /><path d="M8 17c.8-1.6 2.2-2.4 4-2.4s3.2.8 4 2.4" /></>),
  stamp: I(<path d="M9 3h6l-1 8h4v4H6v-4h4zM5 21h14" />),
  teamExt: I(<><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5M19 7v6M16 10h6" /></>),
  delivery: I(<><rect x="3" y="13" width="5" height="7" /><rect x="9.5" y="8" width="5" height="12" /><rect x="16" y="4" width="5" height="16" /></>),
  data: I(<><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M7.5 8.3l3.4 7.4M16.5 8.3l-3.4 7.4M8.5 6h7" /></>),
  qa: I(<><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" /><path d="M8.5 12l2.5 2.5 4.5-5" /></>),
  alert: I(<><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 17.5v.5" /></>),
  clock: I(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  quote: I(<path d="M4 18v-6c0-3 1.5-5 4.5-6M4 12h5v6H4zM14 18v-6c0-3 1.5-5 4.5-6M14 12h5v6h-5z" />),
};
export const COMMITTEE_ICON: Record<string, typeof Ic.mail> = { decision: Ic.stamp, technical: Ic.code, procurement: Ic.doc, influencer: Ic.chat };

/** Every address we hold is usable: we show only whether we have one. */
export function EmailMark({ has, onNavy = false }: { has: boolean; onNavy?: boolean }) {
  return <span className={`ac-mail${has ? " on" : ""}${onNavy ? " on-navy" : ""}`}><Ic.mail />{has ? tr("Email ✓") : tr("No email")}</span>;
}

/* ------------------------------------------------------------------ dates as calendar tiles, on a timeline */
export function DateTile({ d }: { d?: string | null }) {
  const m = d ? /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(d) : null;
  if (!m) return <span className="dtile none" title={tr("Date unknown")}><span className="dtile-m">{tr("Date")}</span><b className="dtile-d">?</b></span>;
  const month = m[2] ? new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1)).toLocaleDateString(getLanguage() === "pl" ? "pl-PL" : "en-GB", { month: "short", timeZone: "UTC" }).replace(".", "") : null;
  return <time className={`dtile${m[3] ? "" : month ? " month" : " year"}`} dateTime={d ?? undefined}>
    {month && <span className="dtile-m">{month}</span>}
    <b className="dtile-d">{m[3] ? Number(m[3]) : m[1]}</b>
    {m[3] && <span className="dtile-y">{m[1]}</span>}
  </time>;
}
export function Timeline({ items }: { items: { date?: string | null; title: ReactNode; detail?: string | null; extra?: ReactNode; tag?: ReactNode }[] }) {
  return <ol className="tl2">{items.map((x, i) => <li key={i}>
    <DateTile d={x.date} />
    <div className="tl2-body">{x.tag}<b className="tl2-t">{x.title}</b>{x.detail && <p>{x.detail}</p>}{x.extra}</div>
  </li>)}</ol>;
}

/* ------------------------------------------------------------------ research states: invite, running, failed */
const COMPANY_STEPS: [keyof typeof Ic, string][] = [["web", "Their website"], ["news", "News from the last 12 months"], ["partner", "Partners and vendors"], ["hiring", "Engineering hiring"], ["spark", "A profile with a link behind every line"]];
const PERSON_STEPS: [keyof typeof Ic, string][] = [["linkedin", "LinkedIn profile"], ["mic", "Talks, articles and interviews"], ["news", "News that names them"], ["spark", "A profile with a link behind every line"]];

/** Turns the stored error into what a seller can do about it; the raw text stays in a details element for administrators. */
export function failReason(e?: string | null): string {
  const s = (e ?? "").toLowerCase();
  if (/budget|monthly cap|spend limit/.test(s)) return "The monthly Exa budget is used up. An administrator can raise it under Administration.";
  if (/credit|billing/.test(s)) return "The Exa account is out of credit. An administrator needs to top it up.";
  if (/refused the key|api key|unauthori[sz]ed|\b401\b|\b403\b/.test(s)) return "Exa did not accept our key. An administrator needs to check the Exa settings.";
  if (/rate limit/.test(s)) return "Exa is busy right now. Try again in a few minutes.";
  if (/json|model/.test(s)) return "The AI answer came back in a shape we could not read. Trying again usually works.";
  if (/not set up|not configured/.test(s)) return "Research is not switched on in this environment yet.";
  return "Something went wrong while researching. Trying again usually works.";
}
const ago = (iso?: string | null) => {
  const s = iso ? Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000)) : 0;
  return s < 60 ? tr("{} seconds ago", s) : tr("{} min ago", Math.round(s / 60));
};

/** The research call to action when there is no profile, a progress card while it runs, and a plain failure with "Try again". */
export function ResearchState({ r, kind, name, canRun, start, busy, err }: { r: any; kind: "company" | "person"; name: string; canRun: boolean; start: () => void; busy: boolean; err: unknown }) {
  if (!r) return null;
  const p = r.profile, last = r.latest;
  const steps = kind === "company" ? COMPANY_STEPS : PERSON_STEPS;
  const ready = Boolean(r.setup?.ready);
  if (r.running) return <section className="rs rs-run" aria-live="polite">
    <div className="rs-main">
      <h2><span className="ac-pulse" />{tr("Researching {} with Exa", name)}</h2>
      <p>{tr("Started {}. It takes about a minute; this page fills in by itself.", ago(last?.created_at))}</p>
      <div className="rs-bar" aria-hidden="true"><i /></div>
    </div>
    <ol className="rs-steps run">{steps.map(([icon, label], i) => { const Icon = Ic[icon]; return <li key={label} style={{ animationDelay: `${i * 0.6}s` }}><Icon />{tr(label)}</li>; })}</ol>
  </section>;
  if (last?.status === "failed") return <section className={`rs rs-fail${p ? " compact" : ""}`} role="alert">
    <div className="rs-main">
      <h2><Ic.alert />{p ? tr("The latest research did not finish") : tr("Research did not finish")}</h2>
      <p>{tr(failReason(last.error))}{p ? " " + tr("The profile below is from {}.", fmtDate(p.finished_at)) : ""}</p>
      {last.error && <details className="rs-why"><summary>{tr("Error details for administrators")}</summary><code>{last.error}</code></details>}
    </div>
    {canRun && <div className="rs-act"><button type="button" className="primary" disabled={busy || !ready} onClick={start}>{tr("Try again")}</button>
      {!ready && <ResearchSetup setup={r.setup} />}<Err e={err} /></div>}
  </section>;
  if (p) return null;
  return <section className="rs rs-cta" aria-labelledby={`rs-${kind}`}>
    <div className="rs-main">
      <h2 id={`rs-${kind}`}><Ic.spark />{tr("Research {} with Exa", name)}</h2>
      <p>{kind === "company"
        ? tr("Exa reads the company's website, its news from the last year, partner and vendor announcements and hiring pages. The AI then writes what they do, how they fit us, what they are doing now and how we could help, with a link to the page behind every line.")
        : tr("Exa reads this person's LinkedIn profile, their public talks, articles and interviews, and news that names them. The AI then writes their role, career path, focus areas, recent activity and how to open the conversation, with a link to the page behind every line.")}</p>
      <p className="rs-cost">{kind === "company" ? tr("About four searches, a few US cents from the monthly Exa budget.") : tr("Professional information only. Three searches, a few US cents from the monthly Exa budget.")}</p>
      {canRun && <div className="rs-act"><button type="button" className="primary" disabled={busy || !ready} onClick={start}>{tr("Research with Exa")}</button></div>}
      <ResearchSetup setup={r.setup} />
      <Err e={err} />
    </div>
    <ol className="rs-steps">{steps.map(([icon, label]) => { const Icon = Ic[icon]; return <li key={label}><Icon />{tr(label)}</li>; })}</ol>
  </section>;
}

const REL_LABEL: Record<string, string> = { none: "No relationship", prospect: "Prospect", in_talks: "In talks", past_client: "Past client", client: "Client" };
const FIT_WORD: Record<string, string> = { good: "In our favour", neutral: "Neutral", bad: "Against us" };
const UKDACH = ["GB", "UK", "IE", "DE", "AT", "CH", "SE", "NO", "DK", "FI", "IS", "PL"];
const EUROPE = ["FR", "ES", "IT", "PT", "NL", "BE", "LU", "CZ", "SK", "HU", "RO", "BG", "GR", "HR", "SI", "EE", "LV", "LT", "CY", "MT", "LI", "MC"];
const FIN = /bank|insur|payment|fintech|lend|wealth|financ|credit|asset|invest|pension|brok|card|capital|leasing/i;
const isUrl = (u?: string | null) => /^https?:\/\//i.test(String(u ?? "").trim());

/** Fit points we can judge from our own records, shown until (and alongside) Exa research. */
function recordFit(a: any, lp: Record<string, string>): any[] {
  const out: any[] = [];
  const sector = a.segment || lp["Industry"];
  if (sector) out.push({ label: "Sector", value: sector, fit: FIN.test(sector) ? "good" : "neutral", source: "R" });
  const cc = String(a.country_code ?? "").toUpperCase();
  if (cc) out.push({ label: "Region", value: countryName(cc), fit: UKDACH.includes(cc) ? "good" : EUROPE.includes(cc) ? "neutral" : "bad", source: "R" });
  if (a.employees_estimate != null) {
    const min = UKDACH.includes(cc) || EUROPE.includes(cc) ? 500 : 2000;
    out.push({ label: "Size", value: staffCount(a.employees_estimate, a.employees_uncertain), fit: a.employees_estimate > min ? "good" : a.employees_estimate < 150 ? "bad" : "neutral", source: "R" });
  }
  const own = a.company_ownership;
  if (own && own !== "unknown") out.push({ label: "Ownership", value: tr(OWNERSHIP[own] ?? own), fit: own === "state_owned" ? "bad" : "good", source: "R" });
  if (a.list_eligibility) out.push({ label: "Lead list", value: a.list_eligibility, fit: a.list_eligibility === "Eligible" ? "good" : a.list_eligibility === "Review" ? "neutral" : "bad", source: "R" });
  return out;
}

const FIT_ORDER = ["good", "neutral", "bad"];
const FIT_GLYPH: Record<string, typeof Ic.mail> = { good: Ic.check, neutral: Ic.dash, bad: Ic.cross };
const sortFit = (points: any[]) => [...points].sort((x, y) => FIT_ORDER.indexOf(x.fit) - FIT_ORDER.indexOf(y.fit));
const fitCount = (points: any[], k: string) => points.filter((p) => p.fit === k).length;

/** An icon for each point of the ideal customer profile, read from its label. */
function fitIcon(label: string): typeof Ic.mail {
  const l = label.toLowerCase();
  if (/sector|industry|segment/.test(l)) return Ic.building;
  if (/region|geograph|countr|location|market|hq/.test(l)) return Ic.globe;
  if (/size|staff|employee|headcount/.test(l)) return Ic.people;
  if (/owner/.test(l)) return Ic.key;
  if (/stack|tech/.test(l)) return Ic.layers;
  if (/engineer|team|in-house/.test(l)) return Ic.code;
  if (/change|programme|program|transform|moderni/.test(l)) return Ic.cycle;
  if (/outsourc|integrator|vendor|supplier/.test(l)) return Ic.outsource;
  if (/lead list|list/.test(l)) return Ic.list;
  return Ic.target;
}

/** One square per fit point, in our favour first: the account's fit at a glance (used in the masthead). */
export function FitCells({ points }: { points: any[] }) {
  return <span className="fit-cells" aria-hidden="true">{sortFit(points).map((p, i) => <i key={i} className={`fit-cell ${p.fit}`} />)}</span>;
}

/** The split as one segmented bar (in our favour, neutral, against) with its legend. */
function FitBar({ points }: { points: any[] }) {
  return <div className="fit-figure">
    <div className="fit-bar" role="img" aria-label={tr("{} in our favour, {} neutral, {} against", fitCount(points, "good"), fitCount(points, "neutral"), fitCount(points, "bad"))}>
      {FIT_ORDER.filter((k) => fitCount(points, k)).map((k) => <span key={k} className={`fit-seg ${k}`} style={{ flexGrow: fitCount(points, k) }} title={`${tr(FIT_WORD[k])}: ${fitCount(points, k)}`}><b>{fitCount(points, k)}</b></span>)}
    </div>
    <ul className="fit-legend">{FIT_ORDER.map((k) => <li key={k}><i className={`fit-cell ${k}`} aria-hidden="true" />{tr(FIT_WORD[k])}</li>)}</ul>
  </div>;
}

/** One calm row per point: what it is, what we know, and whether it helps us. "Our records" is said once, below, not on every row. */
function FitList({ points, fromRecords }: { points: any[]; fromRecords: boolean }) {
  return <>
    <FitBar points={points} />
    <ul className="fit-rows">{sortFit(points).map((p, i) => {
      const Icon = fitIcon(p.label), Glyph = FIT_GLYPH[p.fit] ?? Ic.dash;
      return <li key={i} className={`fit-row ${p.fit}`}>
        <span className="fit-ic" aria-hidden="true"><Icon /></span>
        <b className="fit-label">{tr(p.label)}</b>
        <span className={`fit-verdict ${p.fit}`}><Glyph /><span className="fit-vw">{tr(FIT_WORD[p.fit] ?? "Neutral")}</span></span>
        <span className="fit-val">{p.value}{p.source !== "R" && <> <Cite c={p} /></>}</span>
      </li>;
    })}</ul>
    {fromRecords && <p className="ac-note mt-2 mb-0">{tr("Judged from our own records. Research with Exa adds the tech stack, the engineering team and current change programmes.")}</p>}
  </>;
}

const OWNERSHIP: Record<string, string> = { private: "Privately held", listed: "Publicly listed", state_owned: "State-owned", mutual_or_cooperative: "Mutual or cooperative", unknown: "Ownership unconfirmed" };

function CompanyProfile({ account, onSave }: { account: any; onSave: (profile: any) => void }) {
  const [form, setForm] = useState({
    ownership: account.company_ownership ?? "unknown", source_url: account.company_profile_source_url ?? "",
    country_code: account.country_code ?? "", segment: account.segment ?? "", employees: account.employees_estimate ?? "",
    employees_uncertain: account.employees_uncertain ?? false,
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setSaved(false); setErr(null);
    try {
      onSave(await put(`/api/accounts/${account.id}/company-profile`, { ...form, country_code: form.country_code || null, employees: form.employees ? Number(form.employees) : null }));
      setSaved(true);
    } catch (error) { setErr(error); } finally { setBusy(false); }
  };
  return <form className="card company-profile mt-3" onSubmit={save}>
    <h2>{tr("Company details")}</h2>
    <p className="small muted">{tr("Correct what we hold about the company. For ownership, the company register, annual report or website is a good source.")}</p>
    <div className="directory-filters">
      <label>{tr("Ownership")}<select value={form.ownership} onChange={(e) => setForm({ ...form, ownership: e.target.value })}>
        {Object.entries(OWNERSHIP).map(([key, title]) => <option key={key} value={key}>{tr(title)}</option>)}
      </select></label>
      <label>{tr("Ownership source")}<input type="url" maxLength={1000} value={form.source_url} required={form.ownership !== "unknown"} placeholder="https://…" onChange={(e) => setForm({ ...form, source_url: e.target.value })} /></label>
      <label>{tr("Headquarters country code")}<input maxLength={2} pattern="[A-Z]{2}" value={form.country_code} placeholder={tr("e.g. GB")} onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })} /></label>
      <label>{tr("Sector")}<input maxLength={80} value={form.segment} placeholder={tr("e.g. Banking or Payments")} onChange={(e) => setForm({ ...form, segment: e.target.value })} /></label>
      <label>{tr("Employees")}<input type="number" min={1} max={2147483647} value={form.employees} onChange={(e) => setForm({ ...form, employees: e.target.value })} /></label>
      <label className="profile-estimate"><input type="checkbox" checked={form.employees_uncertain} onChange={(e) => setForm({ ...form, employees_uncertain: e.target.checked })} />{" " + tr("Headcount is an estimate")}</label>
    </div>
    <button className="primary" disabled={busy}>{busy ? tr("Saving…") : tr("Save company details")}</button>
    {saved && <span className="small ml-2" role="status">{tr("Saved.")}</span>}
    <Err e={err} />
  </form>;
}

/* ------------------------------------------------------------------ owner and relationship */
function OwnerControl({ rel, orgId, onChange }: { rel: any; orgId: string; onChange: (owner: any) => void }) {
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!rel) return <span className="small">{tr("Loading")}</span>;
  const owner = rel.owner;
  const set = async (userId: string | null) => {
    setErr(null); setBusy(true);
    try { const r = await put(`/api/accounts/${orgId}/owner`, { user_id: userId }); onChange(r.owner); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  let control: ReactNode = null;
  if (rel.can_assign_others) {
    control = <select aria-label={tr("Account owner")} value={owner?.user_id ?? ""} disabled={busy} onChange={(e) => set(e.target.value || null)}>
      <option value="">{tr("Nobody yet")}</option>
      {rel.team.map((u: any) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
    </select>;
  } else if (rel.can_edit && !owner) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(rel.me)}>{tr("Assign to me")}</button>;
  } else if (rel.can_edit && owner?.user_id === rel.me) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(null)}>{tr("Release")}</button>;
  }
  return <>
    {rel.can_assign_others ? null : <b>{owner ? owner.name : tr("Nobody yet")} </b>}
    {control}
    {owner?.assigned_at && <span className="ac-note"> {tr("since {}", fmtDate(owner.assigned_at))}</span>}
    <Err e={err} />
  </>;
}

/** Our relationship as one clear badge: recorded, or what our data suggests. */
export function RelBadge({ rel, onNavy = false }: { rel: any; onNavy?: boolean }) {
  if (!rel) return null;
  const s = rel.recorded?.status;
  return <span className={`rel-badge ${s ?? "unset"}${onNavy ? " on-navy" : ""}`} title={s ? undefined : tr("Our data suggests: {}.", tr(REL_LABEL[rel.suggested]))}>
    <i aria-hidden="true" />{s ? tr(REL_LABEL[s]) : tr("Relationship not recorded")}</span>;
}

function RelationshipPanel({ rel, orgId, onSaved, onOwner }: { rel: any; orgId: string; onSaved: () => void; onOwner: (owner: any) => void }) {
  const rec = rel?.recorded;
  const [status, setStatus] = useState<string | null>(rec?.status ?? null);
  const [note, setNote] = useState<string>(rec?.note ?? "");
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setStatus(rec?.status ?? null); setNote(rec?.note ?? ""); }, [rec?.status, rec?.note]);
  if (!rel) return <section className="ac-panel"><h2>{tr("Relationship with us")}</h2><Skeleton rows={2} /></section>;
  const save = async (s: string | null = status) => {
    setErr(null);
    try { await put(`/api/accounts/${orgId}/relationship`, { status: s, note: note.trim() || null }); setEditing(false); onSaved(); } catch (e) { setErr(e); }
  };
  const o = rel.outreach;
  const ll = rel.lead_list;
  return <section className="ac-panel ac-rel" id="relationship" aria-labelledby="rel-h">
    <h2 id="rel-h">{tr("Relationship with us")}</h2>
    <p className="ac-purpose">{tr("Whether we already know them, and who on our team looks after the account.")}</p>
    <div className="rel-owner"><span className="ac-note">{tr("Account owner")}</span><span><OwnerControl rel={rel} orgId={orgId} onChange={onOwner} /></span></div>
    <div className="rel-now"><RelBadge rel={rel} />
      {rec?.status ? <span className="ac-note">{tr("Recorded by {} on {}", rec.set_by, fmtDate(rec.set_at))}</span>
        : <span className="small">{tr("Our data suggests: {}.", tr(REL_LABEL[rel.suggested]))} {tr(rel.suggested_why)}</span>}</div>
    {rec?.note && <p className="small mt-2">{rec.note}</p>}
    {rel.can_edit && !editing && <div className="row gap-2 mt-2">
      {!rec?.status && <button type="button" onClick={() => save(rel.suggested)}>{tr("Record as {}", tr(REL_LABEL[rel.suggested]).toLowerCase())}</button>}
      <button type="button" className="ghost" onClick={() => setEditing(true)}>{rec?.status ? tr("Change") : tr("Choose another")}</button>
    </div>}
    {editing && <div className="mt-2">
      <div className="ac-rel-options" role="group" aria-label={tr("Relationship")}>
        {Object.keys(REL_LABEL).map((k) => <button key={k} type="button" className="chip" aria-pressed={status === k} onClick={() => setStatus(k)}>{tr(REL_LABEL[k])}</button>)}
      </div>
      <label className="small">{tr("Note (what we did with them, who knows them)")}<textarea maxLength={600} value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="row gap-2 mt-2"><button type="button" className="primary" onClick={() => save()}>{tr("Save relationship")}</button><button type="button" className="ghost" onClick={() => setEditing(false)}>{tr("Cancel")}</button></div>
    </div>}
    <Err e={err} />
    <ul className="ac-evidence">
      <li>{ll.on_list ? tr("On your lead list ({}{}{}).", ll.account_id ?? "", ll.eligibility ? `, ${ll.eligibility}` : "", ll.priority ? `, ${ll.priority}` : "") : tr("Not on your lead list.")}</li>
      <li>{tr("{} people known, {} with an email.", number(rel.people.n), number(rel.people.with_email))}</li>
      <li>{o.contacted ? tr("Outreach: {}. Last activity {}.", tr(o.engagement_label), fmtDate(o.last_at)) : tr("Never contacted from this platform.")}
        {o.bounces > 0 && <> {tr("{} bounced.", o.bounces)}</>}{o.unsubscribes > 0 && <> {tr("{} unsubscribed.", o.unsubscribes)}</>}</li>
      {rel.campaigns.length > 0 && <li>{tr("In signal campaigns:")} {rel.campaigns.map((c: any, i: number) => <span key={c.id}>{i > 0 && ", "}<Link to={`/campaigns/${c.id}`}>{c.name}</Link></span>)}</li>}
      {rel.drafts.n > 0 && <li>{tr("{} outreach drafts written, {} approved.", rel.drafts.n, rel.drafts.approved)}</li>}
      {rel.deals.linked && <li>{tr("Deals: {} open, {} won, {} lost.", rel.deals.open, rel.deals.won, rel.deals.lost)}</li>}
    </ul>
  </section>;
}

/* ------------------------------------------------------------------ research: what the profile is built on (the states are in ResearchState) */
export function ResearchPanel({ r, canRun, start, busy, err }: { r: any; canRun: boolean; start: () => void; busy: boolean; err: unknown }) {
  const p = r?.profile;
  if (!p) return null;
  return <section className="ac-panel" aria-labelledby="research-h">
    <h2 id="research-h">{tr("Research with Exa")}</h2>
    <div className="ac-research">
      <p className="small m-0">{tr("Last researched {}. {} searches, {} pages read.", fmtDateTime(p.finished_at), p.searches, (p.sources ?? []).length)}
        {p.dropped > 0 && <> {tr("Left out because their page did not support them: {}.", p.dropped)}</>}</p>
      {p.content?.partial && <p className="small m-0">{(p.content.notes ?? []).join(" ")}</p>}
      {canRun && !r.running && <div><button type="button" disabled={busy || !r.setup?.ready} onClick={start}>{tr("Research again")}</button></div>}
      {canRun && <ResearchSetup setup={r.setup} />}
      <Err e={err} />
      {p.sources?.length > 0 && <details><summary className="small">{tr("Pages read ({})", p.sources.length)}</summary>
        <ul className="tight small mt-2">{p.sources.map((s: any) => <li key={s.id}><a href={safeHref(s.url)} target="_blank" rel="noreferrer noopener">{s.title || host(s.url)}</a> <span className="muted">{host(s.url)}{s.published ? `, ${fmtDay(s.published)}` : ""}</span></li>)}</ul></details>}
    </div>
  </section>;
}

export function Sec({ title, hint, children, id }: { title: string; hint?: string; children: ReactNode; id?: string }) {
  return <section className="ac-sec" aria-labelledby={id}>
    <h2 id={id}>{tr(title)}{hint && <small>{tr(hint)}</small>}</h2>
    <div>{children}</div>
  </section>;
}

/** The first few items, and a button for the rest. */
function useFew<T>(items: T[], n: number): [T[], ReactNode] {
  const [all, setAll] = useState(false);
  if (items.length <= n) return [items, null];
  return [all ? items : items.slice(0, n), <button type="button" className="linkish ac-more" aria-expanded={all} onClick={() => setAll(!all)}>
    {all ? tr("Show less") : tr("Show {} more", items.length - n)}</button>];
}

/** A section of the company page: its name, one line on what it is for, then the content. */
function Part({ title, purpose, id, children }: { title: string; purpose: string; id: string; children: ReactNode }) {
  return <section className="ac-part" aria-labelledby={id}>
    <h2 id={id}>{tr(title)}</h2>
    <p className="ac-purpose">{tr(purpose)}</p>
    {children}
  </section>;
}

/* ------------------------------------------------------------------ why talk to them now */
const ORIGIN: Record<string, string> = { tender: "Open tender", signal: "Finding we checked", lead_list: "Your lead list", news: "In the news" };
const STRENGTH: Record<number, string> = { 3: "Strong reason", 2: "Good reason", 1: "Worth a look" };
/** The opportunity engine's next actions in a seller's words. Internal review steps never reach this list (the API leaves them out). */
const STEP: Record<string, string> = {
  route_to_owner: "Pass this to the account owner before anyone contacts them.",
  bid_no_bid_review: "Decide whether to bid: check eligibility, scope and contract terms.",
  investigate_partner_route: "Ask {} whether they bring in specialist engineers for this work.",
  investigate_vendor_route: "Ask {} whether they need outside help to roll this out.",
  qualify_external_demand: "Ask whether they plan to bring in an outside team for this work, and how they choose suppliers.",
  identify_delivery_owner: "Find the person who runs delivery for this programme.",
  advance_discovery: "Book a discovery call: who sponsors it, how they buy, and the scope.",
};

function StrengthMark({ n }: { n: number }) {
  const word = tr(STRENGTH[n] ?? STRENGTH[1]);
  return <span className="wn-str" title={word}><span className="ac-sr">{word}</span>{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} aria-hidden="true" />)}</span>;
}
const origin = (r: any) => (r.kind === "signal" && r.rule_id && SHORT[r.rule_id] ? tr(SHORT[r.rule_id]) : tr(ORIGIN[r.kind] ?? "Finding we checked"));

/** Who to talk to first: a decision maker we can email, then a technical buyer, then anyone with an email. */
function firstContact(people: any[] | null) {
  const rank = (p: any) => (p.committee === "decision" ? 0 : p.committee === "technical" ? 1 : 2) + (p.has_email ? 0 : 3);
  return [...(people ?? []).filter((p) => !p.suppressed)].sort((x, y) => rank(x) - rank(y))[0] ?? null;
}

function stepText(ns: any, contact: any, hasReasons: boolean): string {
  if (ns?.kind === "tender") return ns.date ? tr("Decide by {} whether to bid, or which bidder to partner with.", fmtDate(ns.date)) : tr("Decide whether to bid, or which bidder to partner with.");
  if (ns?.kind === "lead_list" && ns.text) return ns.text;
  if (ns?.kind === "ask" && ns.text) return tr("Ask them: {}", ns.text);
  if (ns?.kind === "opportunity" && STEP[ns.action_type]) return tr(STEP[ns.action_type], ns.counterparty ?? "");
  if (contact) return hasReasons ? tr("Write to {} and open with the first reason.", contact.name) : tr("Write to {} with a short introduction. There is no fresh news to open with yet.", contact.name);
  return hasReasons ? tr("Find the people who choose engineering suppliers here, then open with the first reason.") : tr("Find the people who choose engineering suppliers here, and check for news.");
}

function WhyNow({ d, people, canPeople, canDraft, canScan, scanBusy, runScan, goto }: { d: any; people: any[] | null; canPeople: boolean; canDraft: boolean; canScan: boolean; scanBusy: boolean; runScan: () => void; goto: (t: Tab, s?: string) => void }) {
  const [open, setOpen] = useState(false);
  const reasons: any[] = d.why_now?.reasons ?? [];
  const ns = d.why_now?.next_step ?? null;
  const contact = canPeople ? firstContact(people) : null;
  const noPeople = canPeople && people !== null && !(people ?? []).some((p) => !p.suppressed);
  const details = reasons.some((r) => r.detail);
  return <section className="wn" aria-labelledby="wn-h">
    <div className="wn-main">
      <h2 id="wn-h"><Ic.target />{tr("Why talk to them now")}</h2>
      <p className="ac-purpose">{tr("The best reasons to get in touch now, strongest first. Each one links to where we found it.")}</p>
      {reasons.length > 0 ? <ol className="wn-list">{reasons.map((r, i) => {
        const href = isUrl(r.url) ? safeHref(r.url) : undefined;
        return <li key={i}>
          <span className="wn-i" aria-hidden="true">{i + 1}</span>
          <div className="wn-body">
            <p className="wn-line" title={r.text}>{r.text}</p>
            <p className="wn-meta"><StrengthMark n={r.strength} />
              <span>{r.kind === "tender" && r.deadline ? tr("Closes {}", fmtDate(r.deadline)) : fmtDay(r.date)}</span>
              {href && <a href={href} target="_blank" rel="noreferrer noopener">{host(r.url)} ↗</a>}
              <span className="wn-origin">{origin(r)}</span></p>
            {open && r.detail && <p className="wn-detail">{r.detail}</p>}
          </div>
        </li>;
      })}</ol> : <div className="wn-empty">
        <p className="m-0"><b>{tr("No fresh reason yet.")}</b> {tr("We look for open tenders, findings we have checked, your lead list's signals and news from the last 12 months.")}</p>
        {canScan && <button type="button" disabled={scanBusy} onClick={runScan}>{tr("Check for news")}</button>}
      </div>}
      <p className="wn-foot">
        {details && <button type="button" className="linkish" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? tr("Show less") : tr("Show more about each reason")}</button>}
        <button type="button" className="linkish" onClick={() => goto("activity", "timeline")}>{tr("All findings and tenders")}</button>
      </p>
    </div>
    <aside className="wn-step" aria-labelledby="wn-step-h">
      <h3 id="wn-step-h">{tr("Suggested next step")}</h3>
      <p className="wn-step-text">{stepText(ns, contact, reasons.length > 0)}</p>
      {ns?.kind === "lead_list" && <p className="wn-step-src">{tr("From your lead list")}</p>}
      {contact && <div className="wn-who">
        <Avatar name={contact.name} committee={contact.committee} size="sm" photo={contact.photo_url} />
        <span><small>{tr("Start with")}</small><Link to={`/people/${contact.id}`}>{contact.name}</Link><span>{contact.title || tr("Role not recorded")}</span></span>
        <EmailMark has={Boolean(contact.has_email)} onNavy />
      </div>}
      <div className="wn-step-act">
        {noPeople ? <button type="button" className="primary" onClick={() => goto("people")}>{tr("Find decision makers")}</button>
          : canDraft && <button type="button" className="primary" onClick={() => goto("outreach")}>{tr("Write outreach")}</button>}
        {ns?.kind === "tender" && isUrl(ns.url) && <a href={safeHref(ns.url)} target="_blank" rel="noreferrer noopener">{tr("Open the tender")} ↗</a>}
        {ns?.kind === "opportunity" && ns.hypothesis_id && <Link to={`/opportunities/${ns.hypothesis_id}`}>{tr("Open the opportunity")}</Link>}
      </div>
    </aside>
  </section>;
}

/* ------------------------------------------------------------------ what fits them best */
const SERVICE: Record<string, [string, typeof Ic.mail]> = { team_extension: ["Team extension", Ic.teamExt], delivery_team: ["Delivery team", Ic.delivery], ai_data: ["AI and data", Ic.data], qa: ["QA and testing", Ic.qa] };
const ROUTE_WORDS: Record<string, string> = { direct: "Directly with {}", vendor: "Through {}, their platform vendor", prime_subcontract: "Through {}, their integrator",
  procurement: "Public tender run by {}", relationship: "Through {}" };
const urlKey = (u?: string | null) => String(u ?? "").trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, "").split("#")[0].replace(/\/+$/, "");
const plainTitle = (t?: string | null) => String(t ?? "").replace(/\s*\((analyst )?working title\)\s*$/i, "").trim();
const LEVEL: Record<string, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit" };
/** Where an evidence line comes from, in a seller's words. */
const KIND: Record<string, [string, typeof Ic.mail]> = { signal: ["Buying signal", Ic.target], tender: ["Tender", Ic.stamp], job: ["Job post", Ic.hiring], news: ["News", Ic.news],
  profile: ["Exa research", Ic.spark], lead_list: ["Lead list", Ic.list], records: ["Our records", Ic.badge] };

/** One card per programme. The API already folds routes and same-kind repeats together; this keeps the page safe if it ever does not. */
function programmes(opps: any[]): any[] {
  const m = new Map<string, any>();
  for (const o of opps) {
    const title = o.initiative?.plain_title || plainTitle(o.initiative?.title);
    const key = title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim() || o.hypothesis_id;
    const routes = o.routes ?? [{ hypothesis_id: o.hypothesis_id, type: o.route?.type, counterparty: o.route?.counterparty }];
    const g = m.get(key);
    if (!g) { m.set(key, { ...o, title, routes: [...routes], sources: o.sources ?? 1 }); continue; }
    g.sources += o.sources ?? 1;
    for (const r of routes) if (!g.routes.some((x: any) => x.type === r.type && x.counterparty === r.counterparty)) g.routes.push(r);
  }
  return [...m.values()];
}

/** How strong a fit is: five squares, filled by the score (0 to 100). */
function FitMeter({ score, level }: { score: number; level: string }) {
  const on = Math.max(1, Math.min(5, Math.ceil(score / 20)));
  return <span className={`wf-meter lv-${level}`} role="img" aria-label={tr("{}: {} out of 100", tr(LEVEL[level] ?? level), score)} title={tr("{} out of 100", score)}>
    {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= on ? "on" : ""} />)}</span>;
}

/** One evidence line: when, what, where it comes from. */
function WhyLine({ w }: { w: any }) {
  const [label, Icon] = KIND[w.kind] ?? KIND.records;
  const href = w.source_url ? safeHref(w.source_url) : undefined;
  return <li className="wf-line">
    <span className="wf-when">{w.date ? fmtDay(w.date) : tr("Undated")}</span>
    <span className="wf-text">{w.text}</span>
    <span className="wf-src"><span className={`wf-kind k-${w.kind}`}><Icon />{tr(label)}</span>
      {href ? <a href={href} target="_blank" rel="noreferrer noopener">{host(w.source_url)} ↗</a> : null}</span>
  </li>;
}

function ServiceFit({ s, rank, best }: { s: any; rank: number; best: boolean }) {
  const [open, setOpen] = useState(false);
  const [label, Icon] = SERVICE[s.service] ?? [s.label, Ic.spark];
  const lines: any[] = s.why ?? [];
  const base = best ? 3 : 1;                       // the best fit shows its three strongest reasons, the others their strongest one
  const shown = open ? lines : lines.slice(0, base);
  const hidden = lines.length - shown.length;
  const proof = (best || open) && s.proof?.length > 0;
  return <li className={`wf-svc lv-${s.level}${best ? " best" : ""}`}>
    <div className="wf-head">
      <span className="wf-rank" aria-hidden="true">{rank}</span>
      <span className="wf-ic" aria-hidden="true"><Icon /></span>
      <div className="wf-title">
        <h3>{tr(label)}{best && <span className="wf-best">{tr("Best fit")}</span>}</h3>
        {s.pitch && <p>{tr(s.pitch)}</p>}
      </div>
      <span className="wf-lv"><FitMeter score={s.score} level={s.level} /><b>{tr(LEVEL[s.level] ?? s.level)}</b></span>
    </div>
    <ul className="wf-why" aria-label={tr("Why {} fits", tr(label))}>{shown.map((w, i) => <WhyLine key={i} w={w} />)}</ul>
    {proof && <div className="wf-proof">
      <h4>{tr("Proof we can use")}</h4>
      <ul>{s.proof.map((p: any, i: number) => <li key={i}><b>{p.href ? <Link to={p.href}>{p.title}</Link> : p.title}</b>{p.detail && <span>{p.detail}</span>}</li>)}</ul>
    </div>}
    {best && !s.proof?.length && <p className="wf-noproof">{tr("No approved case study for this service yet. The sales field guide in the Knowledge base says what we may claim.")}</p>}
    {(hidden > 0 || (open && lines.length > base)) && <p className="wf-foot">
      <button type="button" className="linkish" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? tr("Show less") : hidden === 1 ? tr("Show 1 more reason") : tr("Show {} more reasons", hidden)}</button>
    </p>}
  </li>;
}

/** The fit's "why now", unless "Why talk to them now" above already gives the same story (said once is enough). */
function freshWhyNow(fit: any, d: any): string | null {
  const w = String(fit?.why_now ?? "").trim();
  if (!w) return null;
  const head = w.slice(0, 40).toLowerCase();
  const said = (d.why_now?.reasons ?? []).some((r: any) => [r.text, r.detail].some((t: any) => t && (String(t).toLowerCase().includes(head) || (String(t).length >= 15 && head.includes(String(t).slice(0, 40).toLowerCase())))));
  return said ? null : w;
}

function HelpSection({ d, fit, fitErr, c }: { d: any; fit: any; fitErr: unknown; c: any }) {
  const progs = programmeCards(d);
  const whyNow = freshWhyNow(fit, d);
  const services: any[] = fit?.services ?? [];
  const asked = new Set((c?.open_questions ?? []).map((q: string) => q.trim()));
  const unknowns: string[] = (fit?.unknowns ?? []).filter((u: string) => !asked.has(u.trim()));      // the research's own questions are listed below
  const watch: string[] = fit?.watch_outs ?? [];
  return <Part title="What fits them best" purpose="Which of our four services fits them best, ranked by what we hold about them. Every reason shows its date and where it comes from." id="sec-help">
    {!fit && !fitErr && <Skeleton rows={3} label={tr("Working out what fits them")} />}
    <Err e={fitErr} />
    {whyNow && <p className="wf-now"><span className="wf-now-ic" aria-hidden="true"><Ic.clock /></span><span><b>{tr("Why now")}</b>{whyNow}</span></p>}
    {fit && services.length > 0 && <ol className="wf-list">{services.map((s, i) => <ServiceFit key={s.service} s={s} rank={i + 1} best={s.service === fit.best} />)}</ol>}
    {fit && !services.length && <p className="wf-empty">{tr("Nothing we hold points to one of our services yet. Research them with Exa, check for news, or add what you know to the lead list.")}</p>}
    {fit && (watch.length > 0 || unknowns.length > 0) && <div className="wf-notes">
      {watch.length > 0 && <section className="wf-watch" aria-labelledby="wf-watch-h">
        <h3 id="wf-watch-h"><Ic.alert />{tr("Watch out")}</h3>
        <ul>{watch.map((w, i) => <li key={i}>{tr(w)}</li>)}</ul>
      </section>}
      {unknowns.length > 0 && <section className="wf-unknown" aria-labelledby="wf-unknown-h">
        <h3 id="wf-unknown-h"><Ic.search />{tr("We do not know yet")}</h3>
        <ul>{unknowns.map((u, i) => <li key={i}>{tr(u)}</li>)}</ul>
      </section>}
    </div>}
    {progs.length > 0 && <>
      <h3 className="ac-sub-h">{tr("Programmes we track")}</h3>
      <ul className="prg-list">{progs.map((p) => <ProgrammeCard key={p.id ?? p.title} p={p} full={false} />)}</ul>
    </>}
  </Part>;
}

/* ------------------------------------------------------------------ the rest of the overview */
/** Shown when the company has not been researched: what research adds, in one calm block. */
function ResearchInvite({ r, canRun, start, busy, err }: { r: any; canRun: boolean; start: () => void; busy: boolean; err: unknown }) {
  const ready = Boolean(r?.setup?.ready);
  return <div className="ri-invite">
    <span className="ri-ic" aria-hidden="true"><Ic.spark /></span>
    <div>
      <p className="m-0"><b>{tr("We have not researched this company yet.")}</b> {tr("Exa reads their website, last year's news, partner announcements and hiring pages, then writes a short profile with a link behind every line.")}</p>
      <p className="ac-note mt-1 mb-0">{tr("About four searches, a few US cents from the monthly Exa budget.")}{r && !ready && <> {tr("Research is not switched on yet: an administrator can turn it on.")}</>}</p>
      <Err e={err} />
    </div>
    {canRun && r && <button type="button" disabled={busy || r.running || !ready} onClick={start}>{tr("Research with Exa")}</button>}
  </div>;
}

function AboutSection({ c, research, canRun, startResearch, researchBusy, researchErr }: any) {
  if (c?.empty) return <Part title="What they do" purpose="The company in a few lines, with the pages behind them." id="sec-about"><p className="m-0">{tr(c.empty)}</p></Part>;
  return <Part title="What they do" purpose="The company in a few lines, with the pages behind them." id="sec-about">
    {c ? <>
      {c.about && <p className="ac-lead">{c.about.text} {c.about.sources.map((s: any, i: number) => <Cite key={i} c={s} />)}</p>}
      {c.products?.length > 0 && <ul className="ac-tags mt-2" aria-label={tr("Products and services")}>{c.products.map((p: any, i: number) => <li key={i} className="ac-tag prod" title={p.detail ?? undefined}>{p.name}</li>)}</ul>}
      {c.footprint?.markets && <p className="small mt-2"><b>{tr("Markets")}:</b> {c.footprint.markets.value} <Cite c={c.footprint.markets} /></p>}
    </> : <ResearchInvite r={research} canRun={canRun} start={startResearch} busy={researchBusy} err={researchErr} />}
  </Part>;
}

/** Their news, minus what "Why talk to them now" already shows (said once is enough). */
function NowSection({ c, above }: { c: any; above: Set<string> }) {
  const all: any[] = c.recent ?? [];
  const rest = all.filter((x) => !above.has(urlKey(x.source_url)));
  const [shown, more] = useFew<any>(rest, 3);
  const moved = all.length - rest.length;
  return <Part title="What they're doing now" purpose="Their news from the last 12 months: good openers for a first message." id="sec-now">
    {shown.length > 0 && <Timeline items={shown.map((x: any) => ({ date: x.date, title: x.headline, detail: x.detail, extra: <Cite c={x} /> }))} />}
    {!all.length && <p className="small m-0">{tr("Exa found no dated news about this company in the last 12 months.")}</p>}
    {moved > 0 && <p className={`ac-note mb-0${shown.length ? " mt-2" : " mt-0"}`}>{moved === 1 ? tr("One more news item is above, under “Why talk to them now”.") : tr("{} more news items are above, under “Why talk to them now”.", moved)}</p>}
    {more}
  </Part>;
}

function VendorSection({ c, d }: { c: any; d: any }) {
  const vendors = [...(c?.vendors ?? []).map((v: any) => ({ name: v.name, role: v.role, cite: <Cite c={v} /> })),
    ...(d.suppliers ?? []).map((x: any) => ({ name: x.supplier_name, role: [humanize(x.supplier_role), x.scope_text].filter(Boolean).join(": "), cite: <span className="ac-cite rec">{tr("Our records")}</span> }))];
  const [shown, more] = useFew<any>(vendors, 4);
  if (!vendors.length && !c) return null;
  return <Part title="Vendors & partners" purpose="Who already supplies their technology. Ask what these partners do not cover." id="sec-vendors">
    {vendors.length ? <>
      <ul className="vchips">{shown.map((v: any, i: number) => <li key={i} className="vchip"><Monogram name={v.name} size="sm" /><div><b>{v.name}</b>{v.role && <span>{v.role}</span>}{v.cite}</div></li>)}</ul>
      {more}
      <p className="ac-note mt-2 mb-0">{tr("A named vendor does not mean an exclusive contract or that they cover everything.")}</p></>
      : <p className="small m-0">{tr("No vendors or integrators are named in the pages Exa found. Ask about current suppliers in the first call.")}</p>}
  </Part>;
}

/** What the lead list says, without repeating what the page already shows above (the signal, the next step, the service ideas). */
function ListSection({ lp, skip }: { lp: Record<string, string>; skip: Set<string> }) {
  const [open, setOpen] = useState(false);
  const facts: [string, string | undefined][] = [["Buying stage", lp["Buying Stage"]], ["Signal strength", lp["Signal Strength"]], ["Segment", lp["ICP Segment"]], ["Last researched", lp["Last Researched"]]];
  const route = lp["Procurement Route"];
  const text = ([["Strongest signal", lp["Strongest Buying Signal"]], ["Possible work for us", lp["Potential Opportunity"]], ["How The Codest fits", lp["The Codest Service Fit"]],
    ["Next step", lp["Next Action"]], ["Still missing", lp["Coverage Gap"]]] as [string, string | undefined][]).filter(([k, v]) => v && !skip.has(k));
  const src = lp["Primary Source URL"];
  if (!facts.some(([, v]) => v) && !route && !text.length) return null;
  return <Part title="From your lead list" purpose="What your own lead list says about them. We have not checked it." id="sec-list">
    <dl className="ac-kv">{facts.filter(([, v]) => v).map(([k, v]) => <Fragment key={k}><dt>{tr(k)}</dt><dd>{v}</dd></Fragment>)}
      {route && <><dt>{tr("How they buy")}</dt><dd>{isUrl(route) ? <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a> : route}</dd></>}
      {isUrl(src) && <><dt>{tr("Main source")}</dt><dd><a className="ac-cite" href={safeHref(src)} target="_blank" rel="noreferrer noopener">{host(src)} ↗</a></dd></>}</dl>
    {open && <div className="ac-list-text">{text.map(([k, v]) => <p key={k}><b>{tr(k)}:</b> {v}</p>)}</div>}
    {text.length > 0 && <button type="button" className="linkish ac-more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? tr("Show less") : tr("Show more from the lead list")}</button>}
  </Part>;
}

/** Who we know in each part of the buying committee: a filled square per group with people, a dashed one for a gap. */
function Coverage({ people }: { people: any[] }) {
  return <ul className="ac-cov" aria-label={tr("Buying committee coverage")}>{COMMITTEE_ORDER.map((k) => {
    const n = people.filter((p) => group(p.committee) === k && !p.suppressed).length;
    const Icon = COMMITTEE_ICON[k];
    return <li key={k} className={n ? "on" : "gap"} style={{ ["--g" as string]: COMMITTEE[k].color }} title={n ? tr("{}: {}", tr(COMMITTEE[k].label), n) : tr("No one in {} yet", tr(COMMITTEE[k].label))}>
      <Icon /><b>{n}</b><span>{tr(COMMITTEE[k].label)}</span></li>;
  })}</ul>;
}

function KeyPeople({ people, goto, canSee }: { people: any[] | null; goto: (t: Tab) => void; canSee: boolean }) {
  if (!canSee) return null;
  const live = (people ?? []).filter((p) => !p.suppressed);
  return <section className="ac-panel kp" aria-labelledby="kp-h">
    <h2 id="kp-h">{tr("Key people")}</h2>
    <p className="ac-purpose">{tr("Who we know there, by their part in the buying decision.")}</p>
    {!people ? <Skeleton rows={2} /> : people.length === 0 ? <>
      <p className="small">{tr("No people recorded at this company yet.")}</p>
      <button type="button" onClick={() => goto("people")}>{tr("Find decision makers")}</button></> : <>
      <Coverage people={people} />
      <ul className="kp-list">{live.slice(0, 5).map((p) => <li key={p.id}>
        <Avatar name={p.name} committee={p.committee} size="sm" photo={p.photo_url} />
        <div className="kp-who"><Link to={`/people/${p.id}`}>{p.name}</Link><span>{p.title || tr("Role not recorded")}</span></div>
        {p.has_email && <span className="kp-mail" title={tr("Email ✓")}><Ic.mail /><span className="ac-sr">{tr("Email ✓")}</span></span>}
      </li>)}</ul>
      <button type="button" className="linkish mt-2" onClick={() => goto("people")}>{tr("All {} people, by buying committee", people.length)}</button>
    </>}
  </section>;
}

/** Fit points: from Exa research when there is a profile, otherwise judged from our own records. */
function fitPoints(a: any, lp: Record<string, string>, c: any): { points: any[]; fromRecords: boolean } {
  return c?.icp_points?.length ? { points: c.icp_points, fromRecords: false } : { points: recordFit(a, lp), fromRecords: true };
}

function Overview({ d, research, rel, orgId, me, canRun, startResearch, researchBusy, researchErr, reloadRel, onOwner, goto, people, fit, scanBusy, runScan, solutions, solutionsErr }: any) {
  const lp: Record<string, string> = d.list_profile ?? {};
  const c = research?.profile?.content;
  const why = d.why_now ?? {};
  // what the lead list section leaves out because the page already says it
  const skip = new Set<string>();
  if ((why.reasons ?? []).some((r: any) => r.kind === "lead_list")) skip.add("Strongest signal");
  if (why.next_step?.kind === "lead_list") skip.add("Next step");
  // what "What fits them best" already shows from the lead list is not repeated in the lead list section
  const fitText = (solutions?.services ?? []).flatMap((s: any) => (s.why ?? []).map((w: any) => String(w.text ?? ""))).join("\n");
  if (lp["The Codest Service Fit"] && fitText.includes(lp["The Codest Service Fit"])) skip.add("How The Codest fits");
  if (lp["Potential Opportunity"] && fitText.includes(lp["Potential Opportunity"])) skip.add("Possible work for us");
  const showState = research && (research.running || research.latest?.status === "failed");
  return <>
    <WhyNow d={d} people={people} canPeople={me.permissions.includes("contacts.read")} canDraft={me.permissions.includes("draft.create")} canScan={canRun}
      scanBusy={scanBusy} runScan={runScan} goto={goto} />
    {showState && <ResearchState r={research} kind="company" name={d.account.display_name} canRun={canRun} start={startResearch} busy={researchBusy} err={researchErr} />}
    <div className="ac-grid">
      <div className="ac-sheet">
        <HelpSection d={d} fit={solutions} fitErr={solutionsErr} c={c} />
        <AboutSection c={c} research={research} canRun={canRun} startResearch={startResearch} researchBusy={researchBusy} researchErr={researchErr} />
        <Part title="How they fit us" purpose="How close they are to the companies we sell to best." id="sec-fit">
          {fit.points.length ? <FitList points={fit.points} fromRecords={fit.fromRecords} /> : <p className="small m-0">{tr("We hold too little about this company to judge its fit. Edit the company details or research it with Exa.")}</p>}
        </Part>
        {c && <NowSection c={c} above={new Set((why.reasons ?? []).map((r: any) => urlKey(r.url)).filter(Boolean))} />}
        <VendorSection c={c} d={d} />
        <ListSection lp={lp} skip={skip} />
        {c?.open_questions?.length > 0 && <Part title="Still to find out" purpose="Questions to answer in the first call." id="sec-q"><ul className="tight m-0">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></Part>}
      </div>
      <aside className="ac-rail" aria-label={tr("People, relationship and research")}>
        <KeyPeople people={people} goto={goto} canSee={me.permissions.includes("contacts.read")} />
        <RelationshipPanel rel={rel} orgId={orgId} onSaved={reloadRel} onOwner={onOwner} />
        <ResearchPanel r={research} canRun={false} start={startResearch} busy={researchBusy} err={researchErr} />
      </aside>
    </div>
  </>;
}

/* ------------------------------------------------------------------ people */
const PFILTERS: Record<string, [string, (p: any) => boolean]> = {
  all: ["Everyone", () => true], email: ["With email", (p) => Boolean(p.has_email)], linkedin: ["On LinkedIn", (p) => Boolean(p.linkedin_url)],
  not_contacted: ["Not contacted", (p) => !p.engagement && !p.suppressed], researched: ["Researched", (p) => Boolean(p.researched_at)],
};
const LinkedIn = ({ url, name }: { url?: string | null; name: string }) => (url && safeHref(url)
  ? <a className="li-link" href={safeHref(url)} target="_blank" rel="noreferrer noopener" aria-label={tr("LinkedIn profile of {}", name)} title={tr("LinkedIn profile")}><Ic.linkedin /></a> : null);

function FindPeople({ orgId, company, onAdded }: { orgId: string; company: string; onAdded: () => void }) {
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(false);
  const load = () => api(`/api/accounts/${orgId}/people-search`).then(setS).catch(setErr);
  useEffect(() => { load(); }, [orgId]);
  usePoll(Boolean(s?.running), load, 4000);
  if (!s) return err ? <Err e={err} /> : null;
  const run = async () => { setErr(null); setBusy("run"); try { await post(`/api/accounts/${orgId}/people-search`); setOpen(true); await load(); } catch (e) { setErr(e); } finally { setBusy(""); } };
  const act = async (id: string, what: "add" | "dismiss") => {
    setErr(null); setBusy(id);
    try { await post(`/api/people-candidates/${id}/${what}`); await load(); if (what === "add") onAdded(); } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const found = s.items.filter((c: any) => c.status === "found");
  const known = s.items.filter((c: any) => c.status !== "found");
  const last = s.search;
  return <section className="fp" aria-labelledby="find-h">
    <div className="fp-head">
      <span className="fp-ic" aria-hidden="true"><Ic.search /></span>
      <div className="fp-intro">
        <h2 id="find-h">{tr("Find decision makers with Exa")}</h2>
        <p>{tr("Searches LinkedIn for technology, engineering, delivery, data, payments and procurement leaders at {}. You choose who to add.", company)}</p>
      </div>
      {s.can_run && <button type="button" className={found.length ? "" : "primary"} disabled={Boolean(busy) || s.running || !s.setup.ready} onClick={run}>{last ? tr("Search again") : tr("Find decision makers")}</button>}
    </div>
    {!s.setup.ready && <p className="ac-setup mt-2 mb-0">{tr("Finding people with Exa is not switched on yet: an administrator can turn it on.")}</p>}
    {s.running && <div className="fp-run" role="status"><span className="ac-pulse" />{tr("Searching five parts of the buying group. About half a minute.")}</div>}
    {last?.status === "failed" && !s.running && <div className="rs-fail-inline" role="alert"><Ic.alert /><div><b>{tr("The search did not finish.")}</b> {tr(failReason(last.error))}
      {last.error && <details className="rs-why"><summary>{tr("Error details for administrators")}</summary><code>{last.error}</code></details>}</div></div>}
    {last?.status === "done" && <p className="fp-last">{tr("Last search {}: {} found, {} new, {} already in your people list.", fmtDateTime(last.finished_at), last.found, last.new_candidates, last.already_known)}</p>}
    <Err e={err} />
    {found.length > 0 && <ul className="fp-grid" aria-label={tr("People found")}>{found.map((c: any) => <li key={c.id} className="fp-card">
      <div className="pp-top">
        <Avatar name={c.name} committee={c.committee} photo={c.photo_url} />
        <div className="pp-who"><b className="pp-name">{c.name}</b><span className="pp-title">{c.title}</span></div>
        <LinkedIn url={c.linkedin_url} name={c.name} />
      </div>
      <div className="ac-tags">
        <span className="ac-tag grp"><i style={{ background: COMMITTEE[group(c.committee)].color }} aria-hidden="true" />{tr(COMMITTEE[group(c.committee)].one)}</span>
        {c.seniority && <span className="ac-tag">{tr(c.seniority)}</span>}
        {c.persona && <span className="ac-tag">{tr(c.persona)}</span>}
      </div>
      {c.snippet && <p className="fp-snip">{c.snippet}</p>}
      <p className={`fp-match ${c.company_match}`}>{c.company_match === "headline" ? <><Ic.check />{tr("Their LinkedIn headline names {}.", company)}</>
        : <><Ic.alert />{tr("{} appears in the profile, not the headline: check they still work there.", company)}</>}</p>
      <div className="fp-acts">
        <button type="button" className="primary" disabled={Boolean(busy)} onClick={() => act(c.id, "add")}>{tr("Add to people")}</button>
        <button type="button" className="ghost" disabled={Boolean(busy)} onClick={() => act(c.id, "dismiss")}>{tr("Dismiss")}</button>
      </div>
    </li>)}</ul>}
    {known.length > 0 && <details className="fp-known" open={open && !found.length}><summary>{tr("{} already in your people list", known.length)}</summary>
      <ul className="tight small mt-2">{known.map((c: any) => <li key={c.id}>{c.person_id ? <Link to={`/people/${c.person_id}`}>{c.name}</Link> : c.name}, {c.title}</li>)}</ul></details>}
  </section>;
}

/** What to do with this person next, in one short sentence. */
function nextForPerson(p: any): string | null {
  if (p.suppressed) return null;
  if (p.engagement === "replied" || p.engagement === "interested") return tr("They replied. Answer them.");
  if (p.engagement) return tr("Follow up, or wait for a reply.");
  if (p.has_email) return tr("Not contacted yet. Write to them.");
  if (p.linkedin_url && safeHref(p.linkedin_url)) return tr("No email yet. Message them on LinkedIn.");
  return tr("No email yet. Find one on their profile.");
}

function PersonCard({ p }: { p: any }) {
  const ll = p.lead_list ?? {};
  const prio = ll.priority;
  const useful = (v?: string | null) => (v && !/^other\s*\/\s*review$/i.test(v) ? v : null);
  const seniority = useful(p.seniority);
  const next = nextForPerson(p);
  return <li className={`pp-card${prio === "P1" ? " p1" : ""}${p.suppressed ? " off" : ""}`}>
    <div className="pp-top">
      <Avatar name={p.name} committee={p.committee} photo={p.photo_url} />
      <div className="pp-who"><Link className="pp-name" to={`/people/${p.id}`}>{p.name}</Link><span className="pp-title">{p.title || tr("Role not recorded")}</span></div>
      {prio && <span className={`ac-tag ${prio.toLowerCase()}`} title={tr("Contact priority on your lead list")}>{prio}</span>}
    </div>
    {(seniority || ll.committee_role) && <div className="ac-tags">
      {seniority && <span className="ac-tag">{tr(seniority)}</span>}
      {ll.committee_role && <span className="ac-tag">{tr(ll.committee_role)}</span>}
    </div>}
    {ll.angle && <p className="pp-angle">{ll.angle}</p>}
    <div className="pp-foot">
      <EmailMark has={Boolean(p.has_email)} />
      {p.linkedin_url && safeHref(p.linkedin_url) && <a className="pp-li" href={safeHref(p.linkedin_url)} target="_blank" rel="noreferrer noopener" aria-label={tr("LinkedIn profile of {}", p.name)}><Ic.linkedin />LinkedIn</a>}
      {p.engagement && <span className={`pp-eng ${["replied", "interested"].includes(p.engagement) ? "hot" : ""}`}>{tr(p.engagement_label)}</span>}
      {p.researched_at && <span className="pp-res"><Ic.spark />{tr("Researched")}</span>}
      {p.suppressed && <span className="pp-dnc">{tr("Do not contact")}</span>}
    </div>
    {next && <Link className="pp-next" to={`/people/${p.id}`}><span>{next}</span><b aria-hidden="true">→</b></Link>}
  </li>;
}

function PeopleTab({ orgId, company, me, people, err, reload }: { orgId: string; company: string; me: { permissions: string[] }; people: any[] | null; err: unknown; reload: () => void }) {
  const [filter, setFilter] = useState("all");
  if (!me.permissions.includes("contacts.read")) return <div className="empty">{tr("Your role cannot see contact details.")}</div>;
  if (err) return <Err e={err} onRetry={reload} />;
  if (!people) return <Skeleton rows={4} label={tr("Loading people")} />;
  const shown = people.filter(PFILTERS[filter][1]);
  const gaps = COMMITTEE_ORDER.filter((k) => !people.some((p) => group(p.committee) === k && !p.suppressed));
  return <>
    <FindPeople orgId={orgId} company={company} onAdded={reload} />
    {people.length === 0 ? <div className="empty"><b>{tr("No people recorded yet")}</b>{tr("Use “Find decision makers” above, or import the lead list.")}</div> : <>
      <div className="pp-bar">
        <Coverage people={people} />
        <ScrollStrip className="chips pp-chips" label={tr("Filter people")} active={filter}>{Object.keys(PFILTERS).map((k) => <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{tr(PFILTERS[k][0])}<b>{people.filter(PFILTERS[k][1]).length}</b></button>)}</ScrollStrip>
      </div>
      {gaps.length > 0 && <p className="pp-gaps">{tr("No one yet in: {}. “Find decision makers” searches these parts of the buying group too.", gaps.map((k) => tr(COMMITTEE[k].label)).join(", "))}</p>}
      {shown.length === 0 ? <p className="muted">{tr("Nobody matches this filter.")}</p>
        : <div className="pp-board">{COMMITTEE_ORDER.map((k) => {
          const list = shown.filter((p) => group(p.committee) === k);
          if (!list.length) return null;
          const Icon = COMMITTEE_ICON[k];
          return <section key={k} className="pp-lane" style={{ ["--g" as string]: COMMITTEE[k].color }} aria-labelledby={`lane-${k}`}>
            <header className="pp-lane-head"><span className="pp-lane-ic" aria-hidden="true"><Icon /></span>
              <div><h3 id={`lane-${k}`}>{tr(COMMITTEE[k].label)} <span className="pp-n">{list.length}</span></h3><p>{tr(COMMITTEE[k].hint)}</p></div></header>
            <ul className="pp-grid">{list.map((p) => <PersonCard key={p.id} p={p} />)}</ul>
          </section>;
        })}</div>}
    </>}
  </>;
}

/* ------------------------------------------------------------------ outreach (drafts and the Lemlist panel) */
function SendPanel({ draft, orgId, can }: { draft: any; orgId: string; can: boolean }) {
  const [plan, setPlan] = useState<any>(null); const [sel, setSel] = useState<string[]>([]); const [en, setEn] = useState<any>({ items: [] });
  const [msg, setMsg] = useState(""); const [err, setErr] = useState<unknown>(null);
  const load = () => { api(`/api/accounts/${orgId}/contacts-plan`).then(setPlan).catch(() => setPlan(null)); api(`/api/campaigns/${draft.id}/enrolments`).then(setEn).catch(() => undefined); };
  useEffect(load, [draft.id]);
  if (!plan) return null;
  const ready = en.outbound_enabled === true && en.lemlist_configured === true;
  const unavailable = [
    [plan.manual_review, tr("need manual review")], [plan.no_email, tr("without an email")], [plan.suppressed, tr("suppressed or opted out")],
    [plan.already_enrolled ?? 0, tr("already enrolled")],
  ].filter(([count]) => Number(count) > 0).map(([count, reason]) => `${count} ${reason}`);
  const toggle = (id: string) => setSel((x) => x.includes(id) ? x.filter((i) => i !== id) : [...x, id]);
  const request = async () => { setErr(null); setMsg(""); try { const r = await post(`/api/campaigns/${draft.id}/enrol`, { person_ids: sel }); setMsg(tr("Approval requested for {} {}. A colleague approves it under Administration, Approvals.", r.contacts, r.contacts === 1 ? tr("contact") : tr("contacts"))); setSel([]); load(); } catch (e) { setErr(e); } };
  return (
    <div className="card mt-3">
      <div className="row spread"><b>{tr("Prepare a Lemlist campaign")}</b>{typeof en.lemlist_configured === "boolean" && <Badge kind={ready ? "ok" : "warn"}>{ready ? tr("Ready for approval") : tr("Lemlist setup required")}</Badge>}</div>
      <p className="small muted mt-1 mb-2">{tr("Everyone with an email address can be added. We build the campaign in Lemlist but do not launch it: a person presses Launch there.") + " "}
        {en.outbound_enabled === false && tr("Sending is switched off for now.") + " "}{en.lemlist_configured === false && tr("Lemlist is not connected yet.")}</p>
      {plan.eligible.length === 0 && <p className="small">{tr("No contacts are eligible for this campaign.")}</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1 ac-pick"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can || !ready} /><span>{c.name}{c.title ? `, ${c.title}` : ""}</span>
        <EmailMark has /></label>)}
      {unavailable.length > 0 && <p className="small muted">{tr("Unavailable for outreach:") + " "}{unavailable.join(", ")}.</p>}
      {can && <button className="primary" disabled={!ready || sel.length === 0} onClick={request}>{tr("Ask for approval to add") + " "}{sel.length || ""}</button>}
      {msg && <div className="okmsg mt-2" role="status">{msg}</div>}<Err e={err} />
      {en.items.length > 0 && <div className="table-wrap mt-3" tabIndex={0} role="region" aria-label={tr("Contact, status and last activity")}><table><thead><tr><th>{tr("Contact")}</th><th>{tr("Status")}</th><th>{tr("Last activity")}</th></tr></thead><tbody>{en.items.map((x: any) => <tr key={x.id}><td>{x.full_name ?? tr("Contact")}</td><td><Badge kind={x.status === "interested" ? "ok" : x.status === "unsubscribed" || x.status === "bounced" ? "bad" : "info"}>{humanize(x.status)}</Badge></td><td>{x.last_event_at ? fmtDateTime(x.last_event_at) : tr("No reply yet")}</td></tr>)}</tbody></table></div>}
    </div>
  );
}

function CampaignTab({ camps, canDraft, orgId, make, decide }: { camps: any; canDraft: boolean; orgId: string; make: (role: string) => void; decide: (id: string, d: string) => void }) {
  const [role, setRole] = useState("");
  const [copied, setCopied] = useState("");
  const b = camps.items[0];
  const busy = b && (b.status === "queued" || b.status === "running");
  const copy = async (k: string, t: string) => { try { await navigator.clipboard.writeText(t); setCopied(k); setTimeout(() => setCopied(""), 1500); } catch { /* clipboard unavailable */ } };
  return (
    <div className="card">
      <p className="small muted mt-0">{tr("Three emails written from facts we have checked about this company, and tested against The Codest's brand rules. These are drafts: nothing is sent from here.")}</p>
      {canDraft && <div className="row mb-3 ac-camp-form">
        <label className="small">{tr("Who is it for?")}<select id="camp-role" value={role} onChange={(e) => setRole(e.target.value)} className="ml-2"><option value="">{tr("Technology or delivery leader")}</option><option value="CTO">CTO</option><option value="Head of Engineering">{tr("Head of Engineering")}</option><option value="Head of Payments">{tr("Head of Payments")}</option><option value="Head of Delivery or PMO">{tr("Head of Delivery or PMO")}</option><option value="Procurement lead">{tr("Procurement lead")}</option></select></label>
        <button className="primary" disabled={busy} onClick={() => make(role)}>{tr("Write outreach sequence")}</button></div>}
      {!b && <p className="muted">{tr("No draft yet. Research the company with Exa first for the best result: the emails are built from the facts we hold.")}</p>}
      {busy && <p>{tr("Writing and checking the draft. This takes about a minute.")}</p>}
      {b?.status === "failed" && <Err e={new Error(b.error || "The draft could not be written.")} />}
      {b && (b.status === "draft" || b.status === "needs_work" || b.status === "approved" || b.status === "rejected") && <>
        <div className="row mt-2 mb-3">
          <Badge kind={b.status === "approved" ? "ok" : b.status === "draft" ? "info" : b.status === "rejected" ? "" : "warn"}>{b.status === "draft" ? tr("Passed the brand review") : b.status === "needs_work" ? tr("Needs work") : humanize(b.status)}</Badge>
          <span className="small muted">{b.rounds > 1 ? tr("Rewritten {} times to fix rule breaks.", b.rounds - 1) + " " : ""}{tr("Written") + " "}{fmtDateTime(b.finished_at)}{b.decided_by ? `, ${humanize(b.status).toLowerCase()} ${tr("by")} ${b.decided_by}` : ""}</span></div>
        {b.review?.breaks?.length > 0 && <div className="err" role="alert"><b>{tr("Fix these before approving:")}</b><ul className="tight">{b.review.breaks.map((x: any, i: number) => <li key={i}>{x.step ? tr("Email {}: ", x.step) : ""}{x.detail}</li>)}</ul></div>}
        {(b.steps ?? []).map((st: any, i: number) => (
          <article key={i} className="fcard">
            <div className="row spread"><b>{tr("Email") + " "}{i + 1}</b><span className="row small muted">{tr("Day {}", st.day)}<button className="chip" onClick={() => copy(`${b.id}-${i}`, `Subject: ${st.subject}\n\n${st.body}`)}>{copied === `${b.id}-${i}` ? tr("Copied") : tr("Copy")}</button></span></div>
            <p className="mt-2 mb-1"><b>{tr("Subject:")}</b> {st.subject}</p>
            <div className="pre-wrap">{st.body}</div>
          </article>))}
        {canDraft && b.status === "draft" && <div className="row"><button className="primary" onClick={() => decide(b.id, "approve")}>{tr("Approve draft")}</button><button onClick={() => decide(b.id, "reject")}>{tr("Reject")}</button></div>}
        {b.status === "approved" && <SendPanel draft={b} orgId={orgId} can={canDraft} />}
        {b.facts && Object.keys(b.facts).length > 0 && <details className="mt-3"><summary>{tr("Facts these emails are built on")}</summary><ul className="tight small">{Object.entries(b.facts).map(([k, v]: any) => <li key={k}><b>{k}</b> {v}</li>)}</ul></details>}
      </>}
    </div>
  );
}

/* ------------------------------------------------------------------ findings & tenders */
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "A programme we checked; outside help not yet clear", D4: "Background; outside help not yet clear" };
const SUBS = ["timeline", "tenders", "list", "signals", "sources"] as const;
const SUB_LABEL: Record<string, string> = { timeline: "Findings", tenders: "Tenders", list: "Lead list", signals: "Programmes", sources: "Sources" };
const SUB_PURPOSE: Record<string, string> = {
  timeline: "Everything we found and checked about this company in the last year, newest first.",
  tenders: "Public tenders where they are the buyer, and contracts they have awarded.",
  list: "Buying signals from your own lead list, with the page each one came from. We have not checked them.",
  signals: "The programmes they are running, and each finding we checked with how clearly it shows a need for outside help.",
  sources: "Links you added, and the pages and feeds we read for this company.",
};
/** The old chip names still work in links. */
const SUB_ALIAS: Record<string, string> = { leads: "sources" };
const asSub = (v?: string | null) => { const s = SUB_ALIAS[v ?? ""] ?? v ?? ""; return (SUBS as readonly string[]).includes(s) ? s : null; };

/** A row that scrolls sideways when it must: a fade shows there is more, and the chosen item is brought into view. */
function ScrollStrip({ children, active, label, className }: { children: ReactNode; active: string; label: string; className: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edge, setEdge] = useState({ start: false, end: false });
  const measure = () => {
    const el = ref.current;
    if (!el) return;
    const start = el.scrollLeft > 4, end = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setEdge((p) => (p.start === start && p.end === end ? p : { start, end }));
  };
  useEffect(() => {
    const el = ref.current;
    const on = el?.querySelector<HTMLElement>("[aria-pressed='true']");
    if (el && on) {
      const left = on.offsetLeft - 24, right = on.offsetLeft + on.offsetWidth + 24;
      if (left < el.scrollLeft) el.scrollLeft = Math.max(0, left); else if (right > el.scrollLeft + el.clientWidth) el.scrollLeft = right - el.clientWidth;
    }
  }, [active]);
  useEffect(measure);
  useEffect(() => { const el = ref.current; if (!el || typeof ResizeObserver === "undefined") return; const ro = new ResizeObserver(measure); ro.observe(el); return () => ro.disconnect(); }, []);
  return <div className={`ac-strip${edge.start ? " fade-start" : ""}${edge.end ? " fade-end" : ""}`}>
    <div ref={ref} className={className} role="group" aria-label={label} onScroll={measure}>{children}</div>
  </div>;
}

/* ---- programmes: what it is, why it matters, what is still unknown ---- */
const STAGE: Record<string, string> = { candidate: "Early signal", announced: "Announced", planning: "Being planned", sourcing: "Choosing suppliers", implementation: "Being built",
  transition_or_launch: "Going live", completed: "Finished", paused: "On hold", cancelled: "Cancelled", scope_unknown: "Scope unclear" };
/** What the stage means for us, said plainly. Used only when the research gives no better reason. */
const STAGE_NOTE: Record<string, string> = {
  candidate: "Too early to say. Keep an eye on it.",
  announced: "It has just been announced, so budget and scope are still being shaped. A good time to introduce The Codest.",
  planning: "They are planning the work. A good time to ask how they will staff it.",
  sourcing: "They are choosing suppliers now. This is the moment to be on the list.",
  implementation: "The work is under way. Extra engineers are most often needed at this stage.",
  transition_or_launch: "They are close to going live. Testing and support needs peak around now.",
  completed: "The work has finished. Ask what comes next.",
  paused: "It is on hold. Check back later.", cancelled: "It was cancelled. No action needed.",
};
const DOMAIN_WHAT: Record<string, string> = { payments_rail: "A change to the payment schemes they use.", core_banking: "A move to, or rebuild of, their core banking system.",
  cloud_data: "A move to the cloud or a new data platform.", procurement: "A formal purchase of engineering services." };
const daysAgo = (iso?: string | null) => { const t = iso ? Date.parse(iso) : NaN; return Number.isNaN(t) ? null : Math.round((Date.now() - t) / 86400000); };

/** One card per programme. Same-kind repeats are folded into one, and each programme is joined to its opportunity for the plain reasons. */
function programmeCards(d: any): any[] {
  const opps: any[] = d.opportunities ?? [];
  const byInit = new Map<string, any>();
  for (const o of opps) if (o.initiative?.id && !byInit.has(o.initiative.id)) byInit.set(o.initiative.id, o);
  const merged = new Map<string, any>();
  const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  for (const i of d.initiatives ?? []) {
    const title = plainTitle(i.title), key = norm(title) || i.id, o = byInit.get(i.id) ?? null;
    const cur = merged.get(key);
    if (!cur) { merged.set(key, { ...i, title, opp: o, gaps: [...(i.research_gaps ?? [])], tags: [...(i.technology_tags ?? [])] }); continue; }
    cur.opp = cur.opp ?? o;
    cur.gaps = [...new Set([...cur.gaps, ...(i.research_gaps ?? [])])];
    cur.tags = [...new Set([...cur.tags, ...(i.technology_tags ?? [])])];
    cur.budget_verified = cur.budget_verified || i.budget_verified;
    if ((Date.parse(i.last_material_event_at ?? "") || 0) > (Date.parse(cur.last_material_event_at ?? "") || 0)) Object.assign(cur, { stage: i.stage, last_material_event_at: i.last_material_event_at, next_milestone_at: i.next_milestone_at });
  }
  // an opportunity whose programme is not in the list above still gets its card
  for (const o of programmes(opps)) {
    if ([...merged.values()].some((m) => m.opp?.hypothesis_id === o.hypothesis_id || norm(m.title) === norm(o.title))) continue;
    merged.set(norm(o.title) || o.hypothesis_id, { id: o.initiative?.id, title: o.title, stage: o.initiative?.stage, opp: o, gaps: [], tags: [], budget_verified: true, last_material_event_at: o.what_happened?.event_date });
  }
  return [...merged.values()].sort((x, y) => (Date.parse(y.last_material_event_at ?? "") || 0) - (Date.parse(x.last_material_event_at ?? "") || 0));
}

/** The research's open questions, cleaned: no codes, no budget line (the card says that once), at most the first few. */
const cleanGap = (g: string) => String(g).replace(/\s*\([a-z_]+=[^)]*\)/gi, "").trim();
const isBudgetGap = (g: string) => /budget/i.test(g);

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="prg-row"><dt>{tr(label)}</dt><dd>{children}</dd></div>;
}

function ProgrammeCard({ p, full, said }: { p: any; full: boolean; said?: Set<string> }) {
  const [more, setMore] = useState(false);
  const o = p.opp;
  const what = o?.workstream?.scope_statement || o?.plain_reason || DOMAIN_WHAT[p.programme_domain] || null;
  const news = o?.what_happened?.summary && o.what_happened.summary !== what && !said?.has(normText(o.what_happened.summary)) ? o.what_happened : null;
  const why = o?.buying_signal?.meaning || STAGE_NOTE[p.stage] || null;
  const budgetUnknown = p.budget_verified === false;
  const questions: string[] = [...(budgetUnknown ? [tr("Is the budget approved?")] : []), ...p.gaps.filter((g: string) => !isBudgetGap(g)).map(cleanGap).filter(Boolean).map((g: string) => tr(g))];
  const shownQ = more ? questions : questions.slice(0, 2);
  const last = p.last_material_event_at, next = p.next_milestone_at;
  const ways = (o?.routes ?? []).map((r: any) => tr(ROUTE_WORDS[r.type] ?? "Through {}", r.counterparty ?? "")).join(" · ");
  return <li className={`prg${full ? " full" : ""}`}>
    <div className="prg-top"><h4>{p.title}</h4>{p.stage && STAGE[p.stage] && <span className={`prg-stage st-${p.stage}`}>{tr(STAGE[p.stage])}</span>}</div>
    <dl className="prg-rows">
      {what && <Row label="What it is">{what}</Row>}
      {full && news && <Row label="Latest news">{news.summary} <span className="prg-date">{fmtDay(news.event_date)}</span></Row>}
      {why && <Row label="Why it matters">{why}</Row>}
      {!full && ways && <Row label="Ways in">{ways}</Row>}
      {full && p.tags.length > 0 && <Row label="Technology"><ul className="ac-tags">{p.tags.map((t: string) => <li key={t} className="ac-tag">{t}</li>)}</ul></Row>}
      {full && (last || next) && <Row label="Dates">{[last ? tr("Last news {}", fmtDate(last)) : null, next ? tr("Next milestone {}", fmtDate(next)) : null].filter(Boolean).join(" · ")}</Row>}
      {full && questions.length > 0 && <Row label="Still to find out"><ul className="prg-q">{shownQ.map((g, i) => <li key={i}>{g}</li>)}</ul>
        {questions.length > 2 && <button type="button" className="linkish ac-more" aria-expanded={more} onClick={() => setMore(!more)}>{more ? tr("Show less") : tr("Show {} more", questions.length - 2)}</button>}</Row>}
    </dl>
    {o?.hypothesis_id && <p className="prg-act"><Link to={`/opportunities/${o.hypothesis_id}`}>{full ? tr("Open the opportunity") : tr("Open")} →</Link></p>}
  </li>;
}

/* ---- checked signals ---- */
const normText = (t?: string | null) => String(t ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
/** Where a finding was read, and the quoted line only when it says something the title does not. */
function SourceLines({ items, title }: { items: any[]; title: string }) {
  if (!items.length) return <p className="sg-nosrc">{tr("No source attached yet.")}</p>;
  const said = normText(title);
  return <ul className="sg-src">{items.map((e) => {
    const href = e.url ? safeHref(e.url) : undefined;
    const c = normText(e.claim);
    const quote = e.claim && c && !said.includes(c) && !c.includes(said) ? e.claim : null;
    const meta = [e.authority ? tr(TIER[e.authority] ?? e.authority) : null, e.published_at ? tr("published {}", fmtDate(e.published_at)) : null].filter(Boolean).join(" · ");
    return <li key={e.claim_id}>
      {href ? <a className="ac-cite" href={href} target="_blank" rel="noreferrer noopener" title={tr("Open the original source")}>{host(e.url)} ↗</a> : <span className="ac-cite rec">{tr("Our records")}</span>}
      {meta && <span className="sg-meta">{meta}</span>}
      {quote && <q className="sg-quote">{quote}</q>}
    </li>;
  })}</ul>;
}

function SignalStory({ group, sigNames }: { group: any[]; sigNames: Record<string, string> }) {
  const first = group[0];
  const rules = [...new Map(group.map((x: any) => [`${x.rule_id}:${x.status}:${x.demand_tier}`, x])).values()];
  const evidence = [...new Map(group.flatMap((x: any) => x.evidence).map((e: any) => [e.claim_id, e])).values()];
  const date = first.event_at ?? first.pub_at;
  const meaning = FAMILY[familyOf(first.rule_id)]?.meaning;
  return <li className="sg">
    {[...new Set(group.map((x: any) => x.event_id))].map((eventId: any) => <span id={`event-${eventId}`} key={eventId} />)}
    <p className="sg-meta-top"><span>{date ? fmtDate(date) : tr("Date unknown")}</span>
      {first.status === "active" ? <span className="sg-state on">{tr("Current")}</span> : <span className="sg-state">{tr("Historical")}</span>}</p>
    <h4>{first.summary}</h4>
    <ul className="sg-types">{rules.map((x: any) => <li key={`${x.rule_id}:${x.status}:${x.demand_tier}`}>
      <b>{SHORT[x.rule_id] ? tr(SHORT[x.rule_id]) : sigNames[x.rule_id] ?? tr("Buying signal")}</b><span>{tr(DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier))}</span></li>)}</ul>
    {meaning && <p className="sg-why"><b>{tr("Why it matters")}</b> {tr(meaning)}</p>}
    <SourceLines items={evidence} title={first.summary ?? ""} />
  </li>;
}

/** Status, order and how clear the need is, folded into one button so they never wrap unevenly. */
function SignalFilters({ status, setStatus, tier, setTier, order, setOrder }: { status: string; setStatus: (v: string) => void; tier: string; setTier: (v: string) => void; order: string; setOrder: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const on = (status !== "all" ? 1 : 0) + (tier !== "all" ? 1 : 0) + (order !== "newest" ? 1 : 0);
  return <div className="ac-filter">
    <button type="button" className="chip ac-filter-btn" aria-expanded={open} onClick={() => setOpen(!open)}>{tr("Filter and sort")}{on > 0 && <b>{on}</b>}</button>
    {open && <div className="ac-filter-grid">
      <label>{tr("Status")}<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">{tr("All signals")}</option><option value="active">{tr("Current")}</option><option value="historical">{tr("Historical")}</option></select></label>
      <label>{tr("How clear the need is")}<select value={tier} onChange={(e) => setTier(e.target.value)}><option value="all">{tr("All levels")}</option><option value="D1">{tr("They asked for help")}</option><option value="D2">{tr("Likely to need help")}</option><option value="D3">{tr("A programme we checked")}</option><option value="D4">{tr("Background")}</option></select></label>
      <label>{tr("Order")}<select value={order} onChange={(e) => setOrder(e.target.value)}><option value="newest">{tr("Newest first")}</option><option value="oldest">{tr("Oldest first")}</option></select></label>
      {on > 0 && <button type="button" className="linkish" onClick={() => { setStatus("all"); setTier("all"); setOrder("newest"); }}>{tr("Clear filters")}</button>}
    </div>}
  </div>;
}

/* ---- tenders ---- */
const NOTICE_STATE: Record<string, string> = { open: "Open", amended: "Open, updated", closed: "Closed", awarded: "Awarded", cancelled: "Cancelled", planned: "Planned" };
function Tenders({ orgId, route }: { orgId: string; route?: string }) {
  const [t, setT] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/accounts/${orgId}/tenders`).then(setT).catch(setErr); }, [orgId]);
  if (err) return <Err e={err} />;
  if (!t) return <Skeleton rows={2} />;
  if (!t.notices.length && !t.awards.length) return <div className="ac-empty"><p><b>{tr("No public tenders name this company as the buyer.")}</b></p>
    <p>{tr("Most private banks, insurers and payment companies buy without a public tender.")}{route && <> {tr("How they buy, from your lead list:")} {isUrl(route) ? <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a> : route}</>}</p></div>;
  return <>
    {t.notices.length > 0 && <>
      <h3 className="ac-sub-h">{tr("Tenders where they are the buyer")}</h3>
      <ul className="tn-list">{t.notices.map((n: any) => {
        const left = n.deadline_at ? -(daysAgo(n.deadline_at) ?? 0) : null;
        const open = n.status === "open" || n.status === "amended";
        const href = n.official_url ? safeHref(n.official_url) : undefined;
        return <li key={n.id} className="tn">
          <div className="tn-top"><span className={`tn-state${open ? " open" : ""}`}>{tr(NOTICE_STATE[n.status] ?? humanize(n.status))}</span>
            {open && left != null && left >= 0 && <span className="tn-left">{left === 0 ? tr("Closes today") : left === 1 ? tr("1 day left") : tr("{} days left", left)}</span>}</div>
          <h4>{n.title || tr("Untitled notice")}</h4>
          <p className="tn-meta">{[n.deadline_at ? tr("Deadline {}", fmtDate(n.deadline_at)) : null, n.published_at ? tr("published {}", fmtDate(n.published_at)) : null, n.country].filter(Boolean).join(" · ")}</p>
          {open && <p className="tn-next">{n.deadline_at ? tr("Decide by {} whether to bid, or which bidder to partner with.", fmtDate(n.deadline_at)) : tr("Decide whether to bid, or which bidder to partner with.")}</p>}
          {href && <p className="prg-act"><a href={href} target="_blank" rel="noreferrer noopener">{tr("Open the notice")} ↗</a></p>}
        </li>;
      })}</ul></>}
    {t.awards.length > 0 && <>
      <h3 className="ac-sub-h">{tr("Contracts they awarded")}</h3>
      <ul className="tn-list">{t.awards.map((a: any) => {
        const href = a.official_url ? safeHref(a.official_url) : undefined;
        return <li key={a.notice_id} className="tn">
          <div className="tn-top"><span className="tn-state">{tr("Awarded")}</span>{a.awarded_on && <span className="tn-left">{fmtDate(a.awarded_on)}</span>}</div>
          <h4>{a.title || tr("Untitled award")}</h4>
          <p className="tn-meta">{[tr("Won by {}", (a.winners ?? []).join(", ") || tr("not stated")), a.expected_end ? tr("Ends {}", fmtDate(a.expected_end)) + (a.end_basis === "assumed" ? ` (${tr("estimate")})` : "") : null].filter(Boolean).join(" · ")}</p>
          {(a.expected_end || (a.winners ?? []).length > 0) && <p className="tn-next">{tr("Ask the winner whether they need extra engineers, or plan ahead for when the contract ends.")}</p>}
          {href && <p className="prg-act"><a href={href} target="_blank" rel="noreferrer noopener">{tr("Open the notice")} ↗</a></p>}
        </li>;
      })}</ul></>}
  </>;
}

/* ---- sources and links ---- */
function SourcesView({ d, eps, accountId, canScan, scanBusy, runScan }: { d: any; eps: any[]; accountId: string; canScan: boolean; scanBusy: boolean; runScan: () => void }) {
  const [links, setLinks] = useState(false);
  return <>
    <details className="ac-fold" onToggle={(e) => setLinks((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{tr("Links you added")}</summary>
      {links && <AccountFindings key="leads" accountId={accountId} selected={null} leadsOnly />}
    </details>
    {eps.length === 0 && d.coverage.length === 0 ? <div className="ac-empty">
      <p><b>{tr("We have not read this company's pages yet.")}</b> {tr("That means we cannot see, not that nothing is happening.")}</p>
      <p>{tr("Check for news looks for its news feed, press page, careers page and job board.")}</p>
      {canScan && <button type="button" disabled={scanBusy} onClick={runScan}>{tr("Check for news")}</button>}
    </div> : <>
    {eps.length > 0 && <><h3 className="ac-sub-h">{tr("Pages and feeds we read")}</h3>
      <ul className="ac-rows">{eps.map((e: any, i: number) => <li key={i}>
        <div className="ac-rows-main"><b>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</b>
          <a className="break-all" href={safeHref(e.url)} target="_blank" rel="noreferrer noopener">{e.url} ↗</a></div>
        <div className="ac-rows-side">{e.last_scanned_at ? <><Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? tr("Worked") : tr("Problem")}</Badge>
          <span>{tr("Checked {}", fmtDateTime(e.last_scanned_at))}</span>{e.docs_seen > 0 && <span>{tr("{} items read", e.docs_seen)}</span>}</> : <span>{tr("Not checked yet")}</span>}
          {e.last_detail && <span className="muted">{e.last_detail}</span>}</div>
      </li>)}</ul></>}
    {d.coverage.length > 0 && <><h3 className="ac-sub-h">{tr("What we can see")}</h3>
      <ul className="ac-rows">{d.coverage.map((c: any, i: number) => <li key={i}>
        <div className="ac-rows-main"><b>{humanize(c.source_family)}</b></div>
        <div className="ac-rows-side"><Badge kind={c.coverage_state === "ok" || c.coverage_state === "covered" ? "ok" : ""}>{humanize(c.coverage_state)}</Badge>{c.last_success_at && <span>{tr("Last checked {}", fmtDate(c.last_success_at))}</span>}</div>
      </li>)}</ul></>}
    </>}
    {d.source_rows?.length > 0 && <><h3 className="ac-sub-h">{tr("Where it appears in your workbooks")}</h3><ul className="tight small mb-0">{d.source_rows.map((r: any, i: number) => <li key={i}>{tr("{}, row {}", r.sheet_title, r.row_number)}</li>)}</ul></>}
  </>;
}

function Activity({ d, eps, sub, setSub, params, sigNames, canScan, scanBusy, runScan }: any) {
  const [signalStatus, setSignalStatus] = useState("all");
  const [signalTier, setSignalTier] = useState("all");
  const [signalOrder, setSignalOrder] = useState("newest");
  const a = d.account;
  const all = new Set<string>(d.signals.map((s: any) => s.story_key ?? s.event_id));
  const groups = new Map<string, any[]>();
  for (const s of d.signals) {
    if ((signalStatus !== "all" && s.status !== signalStatus) || (signalTier !== "all" && s.demand_tier !== signalTier)) continue;
    const key = s.story_key ?? s.event_id;
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const stories = [...groups.values()].sort(([x], [y]) => {
    const dx = x.event_at ?? x.pub_at, dy = y.event_at ?? y.pub_at;
    if (!dx) return dy ? 1 : 0; if (!dy) return -1;
    const o = Date.parse(dx) - Date.parse(dy); return signalOrder === "oldest" ? o : -o;
  });
  const progs = programmeCards(d);
  const nList = (d.list_signals ?? []).length;
  const counts: Record<string, number | undefined> = { signals: (progs.length || all.size) || undefined, list: nList || undefined };
  // choices that are empty are not shown, unless you are looking at one
  const shown = SUBS.filter((k) => k !== "list" && k !== "signals" ? true : k === sub || Boolean(counts[k]));
  return <>
    <ScrollStrip className="ac-subnav" label={tr("Findings and tenders")} active={sub}>
      {shown.map((k) => <button key={k} type="button" className="chip" aria-pressed={sub === k} onClick={() => setSub(k)}>{tr(k === "signals" && progs.length === 0 ? "Signals" : SUB_LABEL[k])}{counts[k] != null && <b>{counts[k]}</b>}</button>)}
    </ScrollStrip>
    <p className="ac-purpose ac-sub-purpose">{tr(SUB_PURPOSE[sub] ?? "")}</p>
    {sub === "timeline" && <AccountFindings key={"t" + (params.get("finding") ?? "")} accountId={a.id} selected={params.get("finding")} leadsOnly={false} />}
    {sub === "tenders" && <Tenders orgId={a.id} route={d.list_profile?.["Procurement Route"]} />}
    {sub === "list" && (nList === 0 ? <div className="ac-empty"><p><b>{tr("Your lead list records no signals for this company.")}</b></p></div>
      : <div className="card"><ul className="ac-tl">{d.list_signals.map((x: any, i: number) => <li key={i}><time>{fmtDay(x.date)}</time><div className="ac-tl-body">
          <b>{x.category || x.event}</b>{x.category && x.event && <p>{x.event}</p>}
          {x.implication && <p>{x.implication}</p>}
          <p className="ac-tl-meta">{x.source_url && <Cite c={{ source_url: x.source_url, source: "list" }} />}{x.confidence && <span>{tr("{} confidence", x.confidence)}</span>}
            {x.repeats > 1 && <span>{tr("Your list records this {} times; shown once.", x.repeats)}</span>}</p></div></li>)}</ul></div>)}
    {sub === "signals" && <>
      {progs.length > 0 && <>
        <h3 className="ac-sub-h">{tr("Programmes")}</h3>
        <ul className="prg-list">{progs.map((p) => <ProgrammeCard key={p.id ?? p.title} p={p} full said={new Set(d.signals.map((x: any) => normText(x.summary)))} />)}</ul>
      </>}
      {all.size > 0 && <>
        <div className="ac-sub-row"><h3 className="ac-sub-h">{tr("Checked findings")}</h3>
          {all.size > 1 && <SignalFilters status={signalStatus} setStatus={setSignalStatus} tier={signalTier} setTier={setSignalTier} order={signalOrder} setOrder={setSignalOrder} />}</div>
        {stories.length > 0 ? <ul className="sg-list">{stories.map((group) => <SignalStory key={group[0].story_key ?? group[0].event_id} group={group} sigNames={sigNames} />)}</ul>
          : <p className="ac-note">{tr("No findings match these filters.")} <button type="button" className="linkish" onClick={() => { setSignalStatus("all"); setSignalTier("all"); }}>{tr("Clear filters")}</button></p>}
      </>}
      {all.size === 0 && <div className="ac-empty">
        <p><b>{tr("Nothing checked yet.")}</b> {tr("Check for news reads their website and searches the web.")}</p>
        {canScan && <button type="button" disabled={scanBusy} onClick={runScan}>{tr("Check for news")}</button>}
      </div>}
    </>}
    {sub === "sources" && <SourcesView d={d} eps={eps} accountId={a.id} canScan={canScan} scanBusy={scanBusy} runScan={runScan} />}
  </>;
}

/* ------------------------------------------------------------------ the page */
const TABS = ["overview", "people", "activity", "outreach"] as const;
type Tab = typeof TABS[number];

export function Account({ me }: { me: { permissions: string[] } }) {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const [editing, setEditing] = useState(params.get("profile") === "1");
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const linked: [Tab, string] | null = params.has("finding") || params.has("timeline") ? ["activity", "timeline"] : params.has("event") ? ["activity", "signals"] : null;
  const asked = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(linked?.[0] ?? (asked && TABS.includes(asked) ? asked : params.has("sub") ? "activity" : "overview"));
  const [sub, setSub] = useState<string>(linked?.[1] ?? asSub(params.get("sub")) ?? "timeline");
  useEffect(() => { if (linked) { setTab(linked[0]); setSub(linked[1]); } }, [params]);
  const [sigNames, setSigNames] = useState<Record<string, string>>({});
  useEffect(() => { api("/api/library/signals").then((r) => setSigNames(Object.fromEntries(r.items.map((x: any) => [x.rule_id, x.signal])))).catch(() => undefined); }, []);
  const [eps, setEps] = useState<any[]>([]);
  const [context, setContext] = useState<any[]>([]);
  const [scan, setScan] = useState<{ busy: boolean; msg: string; err: unknown }>({ busy: false, msg: "", err: null });
  useEffect(() => {
    const request = new AbortController();
    setD(null); setErr(null); setEps([]);
    api(`/api/accounts/${id}?target=true`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setD(data); }).catch((error) => { if (!request.signal.aborted) setErr(error); });
    api(`/api/accounts/${id}/endpoints`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setEps(data.items); }).catch(() => undefined);
    api(`/api/accounts/${id}/context`, { signal: request.signal }).then((data) => { if (!request.signal.aborted) setContext(data.items); }).catch(() => undefined);
    return () => request.abort();
  }, [id]);
  // every hook runs on every render, before any early return (React rule of hooks)
  const [research, setResearch] = useState<any>(null);
  const [researchBusy, setResearchBusy] = useState(false);
  const [researchErr, setResearchErr] = useState<unknown>(null);
  const loadResearch = () => api(`/api/accounts/${id}/research`).then(setResearch).catch(() => undefined);
  useEffect(() => { setResearch(null); loadResearch(); }, [id]);
  usePoll(Boolean(research?.running), loadResearch, 5000);
  const [solutions, setSolutions] = useState<any>(null);
  const [solutionsErr, setSolutionsErr] = useState<unknown>(null);
  // when a new profile lands, the reasons to call may change: read the account again (quietly, the page stays as it is meanwhile)
  const profileSeen = useRef<string | null | undefined>(undefined);
  const profileId: string | null | undefined = research ? research.profile?.id ?? null : undefined;
  useEffect(() => {
    if (profileId === undefined) return;
    if (profileSeen.current !== undefined && profileSeen.current !== profileId) {
      api(`/api/accounts/${id}?target=true`).then((data) => setD((cur: any) => (cur?.account.id === data.account.id ? data : cur))).catch(() => undefined);
      api(`/api/accounts/${id}/solutions`).then(setSolutions).catch(() => undefined);       // new research can change what fits
    }
    profileSeen.current = profileId;
  }, [profileId]);
  useEffect(() => { profileSeen.current = undefined; }, [id]);
  useEffect(() => {
    const request = new AbortController();
    setSolutions(null); setSolutionsErr(null);
    api(`/api/accounts/${id}/solutions`, { signal: request.signal }).then((r) => { if (!request.signal.aborted) setSolutions(r); }).catch((e) => { if (!request.signal.aborted) setSolutionsErr(e); });
    return () => request.abort();
  }, [id]);
  const [rel, setRel] = useState<any>(null);
  const loadRel = () => api(`/api/accounts/${id}/relationship`).then(setRel).catch(() => undefined);
  useEffect(() => { loadRel(); }, [id]);
  const canPeople = me.permissions.includes("contacts.read");
  const [people, setPeople] = useState<any[] | null>(null);
  const [peopleErr, setPeopleErr] = useState<unknown>(null);
  const loadPeople = () => { if (canPeople) api(`/api/accounts/${id}/people`).then((r) => { setPeople(r.items); setPeopleErr(null); }).catch(setPeopleErr); };
  useEffect(() => { setPeople(null); loadPeople(); }, [id]);
  const [camps, setCamps] = useState<any>({ items: [] });
  const loadCamps = () => api(`/api/accounts/${id}/campaigns`).then(setCamps).catch(() => setCamps({ items: [] }));
  useEffect(() => { loadCamps(); }, [id]);
  usePoll(camps.items.some((b: any) => b.status === "queued" || b.status === "running"), loadCamps, 6000);
  useEffect(() => {
    const event = params.get("event");
    if (d && tab === "activity" && sub === "signals" && event) document.getElementById(`event-${event}`)?.scrollIntoView({ block: "center" });
  }, [d, tab, sub, params]);
  if (!d || d.account.id !== id) return <>
    <p className="small mt-0"><Link to="/accounts">{tr("Accounts")}</Link></p><h1 className="title-sm">{tr("Company")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the company")} />}
  </>;
  const a = d.account;
  const lp: Record<string, string> = d.list_profile ?? {};
  const canScan = me.permissions.includes("research.request");
  const c = research?.profile?.content;
  const fit = fitPoints(a, lp, c);
  const startResearch = async () => {
    setResearchErr(null); setResearchBusy(true);
    try { await post(`/api/accounts/${id}/research`); await loadResearch(); } catch (e) { setResearchErr(e); } finally { setResearchBusy(false); }
  };
  const makeCampaign = async (role: string) => { try { await post(`/api/accounts/${id}/campaign`, { role: role || null }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const decideCampaign = async (did: string, decision: string) => { try { await post(`/api/campaigns/${did}/decision`, { decision }); await loadCamps(); } catch (e) { setScan({ busy: false, msg: "", err: e }); } };
  const runScan = async () => {
    setScan({ busy: true, msg: "", err: null });
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? tr("Reading {} and searching the web. New findings appear under Findings & tenders.", r.website) : tr("Started. This company has no website on record, so we only search the web."), err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  /** After a tab changes, bring the start of its content into view (a phone shows only the cover otherwise). */
  const toTabs = () => setTimeout(() => {
    const top = document.querySelector(".ac-tabs")?.getBoundingClientRect().top;
    if (top != null && (top < 70 || top > window.innerHeight * 0.45)) window.scrollTo({ top: Math.max(0, window.scrollY + top - 64), behavior: "smooth" });
  }, 40);
  const goto = (t: Tab, s?: string, stay = false) => {
    setTab(t); if (s) setSub(s);
    if (!stay) toTabs();
    if (linked || params.has("tab") || params.has("sub")) { const q = new URLSearchParams(params); ["finding", "timeline", "event", "tab", "sub"].forEach((k) => q.delete(k)); setParams(q, { replace: true }); }
  };
  const jump = (anchor: string) => { goto("overview", undefined, true); setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60); };
  const companyLinkedIn = [...context.filter((x) => x.kind === "link").map((x) => x.value), ...((research?.profile?.sources ?? []).map((s: any) => s.url))]
    .find((u: string) => /linkedin\.com\/company\//i.test(u));
  const hq = c?.footprint?.hq?.value ?? countryName(a.country_code);
  const sector = a.segment || lp["Industry"];
  const size = c?.footprint?.size?.value ?? (a.employees_estimate != null ? staffCount(a.employees_estimate, a.employees_uncertain) : null);
  const ownership = a.company_ownership && a.company_ownership !== "unknown" ? tr(OWNERSHIP[a.company_ownership]) : null;
  const peopleCount = people?.length ?? d.buyers?.length ?? 0;
  const live = (people ?? []).filter((p) => !p.suppressed);
  const deciders = live.filter((p) => p.committee === "decision").length;
  const good = fit.points.filter((p: any) => p.fit === "good").length;
  const ready = Boolean(research?.setup?.ready);
  return (
    <>
      <header className="ac-hero" data-mark={initials(a.display_name)}>
        <nav className="ac-crumb" aria-label={tr("Breadcrumb")}><Link to="/accounts">{tr("Accounts")}</Link><span aria-hidden="true">/</span><span aria-current="page">{a.display_name}</span></nav>
        <div className="ac-id">
          <Monogram name={a.display_name} size="lg" framed />
          <div className="ac-name">
            <h1>{a.display_name}</h1>
            {(sector || hq || size) && <p className="ac-sub">{[sector, hq, size, ownership].filter(Boolean).map((x, i) => <span key={i}>{x}</span>)}</p>}
            <div className="ac-links">
              <RelBadge rel={rel} onNavy />
              {a.canonical_domain && <a href={`https://${a.canonical_domain}`} target="_blank" rel="noreferrer noopener" title={a.canonical_domain}><Ic.web /><span>{a.canonical_domain}</span></a>}
              {companyLinkedIn ? <a href={safeHref(companyLinkedIn)} target="_blank" rel="noreferrer noopener"><Ic.linkedin /><span>LinkedIn</span></a>
                : <a href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(a.display_name)}`} target="_blank" rel="noreferrer noopener" title={tr("Find on LinkedIn")} aria-label={tr("Find on LinkedIn")}><Ic.linkedin /><span>LinkedIn</span></a>}
            </div>
          </div>
          <div className="ac-actions">
            {canScan && research && <button type="button" className={c || !ready ? "" : "primary"} disabled={researchBusy || research.running || !ready}
              title={ready ? undefined : tr("Research is not switched on yet: an administrator can turn it on.")} onClick={startResearch}>{research.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>}
            {canScan && <button type="button" disabled={scan.busy} onClick={runScan}>{tr("Check for news")}</button>}
            {canScan && a.entity_type !== "public_body" && <button type="button" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? tr("Close details") : tr("Edit details")}</button>}
          </div>
        </div>
        <div className="ac-glance">
          {fit.points.length > 0 && <button type="button" className="glance" onClick={() => jump("sec-fit")}>
            <span className="glance-vis"><FitCells points={fit.points} /></span>
            <span className="glance-txt"><b>{tr("{} of {} points in our favour", good, fit.points.length)}</b><small>{tr("How they fit us")}</small></span></button>}
          {canPeople && <button type="button" className="glance" onClick={() => goto("people")}>
            <span className="glance-vis">{live.length > 0 ? <span className="ac-stack" aria-hidden="true">{live.slice(0, 4).map((p) => <Avatar key={p.id} name={p.name} committee={p.committee} size="sm" photo={p.photo_url} />)}{live.length > 4 && <span className="mono mono-sm more">+{live.length - 4}</span>}</span>
              : <span className="glance-ic" aria-hidden="true"><Ic.people /></span>}</span>
            <span className="glance-txt"><b>{peopleCount === 1 ? tr("1 person") : peopleCount ? tr("{} people", number(peopleCount)) : tr("No people yet")}</b>
              <small>{!peopleCount ? tr("Find decision makers with Exa") : deciders === 1 ? tr("1 decision maker") : tr("{} decision makers", deciders)}</small></span></button>}
          <button type="button" className="glance" onClick={() => jump(Object.keys(lp).length ? "sec-list" : "sec-fit")}>
            <span className="glance-vis"><span className="glance-ic" aria-hidden="true">{a.list_priority ? <b className={`glance-p ${String(a.list_priority).toLowerCase()}`}>{a.list_priority}</b> : <Ic.list />}</span></span>
            <span className="glance-txt"><b>{a.list_priority || a.list_score != null ? [a.list_score != null ? tr("Score {}", a.list_score) : null, a.list_eligibility].filter(Boolean).join(" · ") || tr("On your lead list") : tr("Not on your lead list")}</b>
              <small>{tr("Your lead list")}</small></span></button>
          <button type="button" className="glance" onClick={() => jump("relationship")}>
            <span className="glance-vis"><span className="glance-ic" aria-hidden="true"><Ic.badge /></span></span>
            <span className="glance-txt"><b>{rel?.owner?.name ?? tr("Nobody yet")}</b><small>{tr("Account owner")}</small></span></button>
        </div>
      </header>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      {editing && canScan && a.entity_type !== "public_body" && <CompanyProfile account={a} onSave={(profile) => setD({ ...d, account: { ...a, ...profile } })} />}
      <div className="ac-tabs">
      <Tabs items={[{ key: "overview", label: tr("Overview") }, { key: "people", label: <>{tr("People")}{canPeople && <span className="tabn">{number(peopleCount)}</span>}</> },
        { key: "activity", label: <><span className="t-long">{tr("Findings & tenders")}</span><span className="t-short">{tr("Findings")}</span></> }, { key: "outreach", label: tr("Outreach") }]} value={tab} onChange={(k) => goto(k as Tab)} label={tr("Company sections")}>
        {tab === "overview" && <Overview d={d} research={research} rel={rel} orgId={a.id} me={me} canRun={canScan} startResearch={startResearch} researchBusy={researchBusy}
          researchErr={researchErr} reloadRel={loadRel} onOwner={(owner: any) => setRel({ ...rel, owner })} goto={goto} people={canPeople ? people : null} fit={fit}
          scanBusy={scan.busy} runScan={runScan} solutions={solutions} solutionsErr={solutionsErr} />}
        {tab === "people" && <PeopleTab orgId={a.id} company={a.display_name} me={me} people={people} err={peopleErr} reload={() => { loadPeople(); loadRel(); }} />}
        {tab === "activity" && <Activity d={d} eps={eps} sub={sub} setSub={setSub} params={params} sigNames={sigNames} canScan={canScan} scanBusy={scan.busy} runScan={runScan} />}
        {tab === "outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={a.id} make={makeCampaign} decide={decideCampaign} />}
      </Tabs>
      </div>
    </>
  );
}
