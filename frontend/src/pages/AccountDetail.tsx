import { getLanguage, number, tr } from "../locale";
import { Fragment, ReactNode, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post, put } from "../api";
import { AccountFindings } from "../components/Findings";
import { SignalSummary } from "../components/BuyingSignal";
import { SHORT } from "../components/Signals";
import { countryName, staffCount } from "./targetMarket";
import { Badge, Err, Evidence, groupName, humanize, lab, priorityKind, Skeleton, reloadPage, safeHref, whyShort, Tabs } from "../components/ui";
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
  return <p className="ac-setup m-0">{tr("Research with Exa is not set up in this environment. An administrator adds {} to the API and the worker.", (setup.missing ?? []).join(", "))}</p>;
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

/** A person's mark: initials on the colour of their part of the buying committee. */
export function Avatar({ name, committee, size = "md" }: { name: string; committee?: string | null; size?: "xs" | "sm" | "md" | "lg" }) {
  return <span className={`mono avatar mono-${size}`} style={{ background: COMMITTEE[group(committee)].color }} aria-hidden="true">{initials(name)}</span>;
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
  if (own && own !== "unknown") out.push({ label: "Ownership", value: tr(OWNERSHIP[own] ?? own), fit: own === "state_owned" ? "bad" : own === "private" ? "good" : "neutral", source: "R" });
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

/** The split as one tall segmented bar (in our favour, neutral, against) with its legend. */
function FitBar({ points }: { points: any[] }) {
  const words: Record<string, string> = { good: "In our favour", neutral: "Neutral", bad: "Against us" };
  return <div className="fit-figure">
    <div className="fit-bar" role="img" aria-label={tr("{} in our favour, {} neutral, {} against", fitCount(points, "good"), fitCount(points, "neutral"), fitCount(points, "bad"))}>
      {FIT_ORDER.filter((k) => fitCount(points, k)).map((k) => <span key={k} className={`fit-seg ${k}`} style={{ flexGrow: fitCount(points, k) }} title={`${tr(words[k])}: ${fitCount(points, k)}`}><b>{fitCount(points, k)}</b></span>)}
    </div>
    <ul className="fit-legend">{FIT_ORDER.map((k) => <li key={k}><i className={`fit-cell ${k}`} aria-hidden="true" />{tr(words[k])}</li>)}</ul>
  </div>;
}

function FitBoard({ points, fromRecords }: { points: any[]; fromRecords: boolean }) {
  return <>
    <FitBar points={points} />
    <ul className="fit-tiles">{sortFit(points).map((p, i) => {
      const Icon = fitIcon(p.label), Glyph = FIT_GLYPH[p.fit] ?? Ic.dash;
      return <li key={i} className={`fit-tile ${p.fit}`}>
        <div className="fit-tile-top"><span className="fit-ic"><Icon /></span>
          <span className="fit-name"><b>{tr(p.label)}</b><span className={`fit-verdict ${p.fit}`}><Glyph />{tr(FIT_WORD[p.fit] ?? "Neutral")}</span></span></div>
        <p>{p.value}</p>
        <Cite c={p} />
      </li>;
    })}</ul>
    {fromRecords && <p className="ac-note mt-2">{tr("Judged from our own records. Research with Exa adds the tech stack, the engineering team and current change programmes.")}</p>}
  </>;
}

const OWNERSHIP: Record<string, string> = { private: "Privately held", listed: "Publicly listed", state_owned: "State-owned", unknown: "Ownership unconfirmed" };

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
    <p className="small muted">{tr("Use the company register, annual report or company website to confirm ownership. For a subsidiary, check who owns the parent company.")}</p>
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
    control = <select aria-label={tr("Owner")} value={owner?.user_id ?? ""} disabled={busy} onChange={(e) => set(e.target.value || null)}>
      <option value="">{tr("Unassigned")}</option>
      {rel.team.map((u: any) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
    </select>;
  } else if (rel.can_edit && !owner) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(rel.me)}>{tr("Assign to me")}</button>;
  } else if (rel.can_edit && owner?.user_id === rel.me) {
    control = <button type="button" className="linkish" disabled={busy} onClick={() => set(null)}>{tr("Release")}</button>;
  }
  return <>
    {rel.can_assign_others ? null : <>{owner ? owner.name : tr("Unassigned")} </>}
    {control}
    {owner?.assigned_at && <div className="small">{tr("since {}", fmtDate(owner.assigned_at))}</div>}
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

function RelationshipPanel({ rel, orgId, onSaved }: { rel: any; orgId: string; onSaved: () => void }) {
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

/** What the lead list says about the account: not checked by the platform. */
function ListProfile({ p }: { p: Record<string, string> }) {
  const text: [string, string | undefined][] = [["Strongest signal", p["Strongest Buying Signal"]], ["Possible work for us", p["Potential Opportunity"]], ["How The Codest fits", p["The Codest Service Fit"]],
    ["Next step", p["Next Action"]], ["Still missing", p["Coverage Gap"]]];
  const facts: [string, string | undefined][] = [["Segment", p["ICP Segment"]], ["Buying stage", p["Buying Stage"]], ["Signal strength", p["Signal Strength"]], ["Last researched", p["Last Researched"]]];
  const route = p["Procurement Route"];
  return <>
    {text.filter(([, v]) => v).map(([k, v]) => <p key={k} className={k === "Still missing" ? "small" : ""}><b>{tr(k)}:</b> {v}</p>)}
    {(facts.some(([, v]) => v) || route) && <dl className="ac-kv mt-2">{facts.filter(([, v]) => v).map(([k, v]) => <Fragment key={k}><dt>{tr(k)}</dt><dd>{v}</dd></Fragment>)}
      {route && <><dt>{tr("How they buy")}</dt><dd>{safeHref(route) ? <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a> : route}</dd></>}</dl>}
    {p["Primary Source URL"] && safeHref(p["Primary Source URL"]) && <p className="mt-2"><a className="ac-cite" href={safeHref(p["Primary Source URL"])} target="_blank" rel="noreferrer noopener">{host(p["Primary Source URL"])} ↗</a></p>}
  </>;
}

/* ------------------------------------------------------------------ overview */
const SERVICE: Record<string, [string, typeof Ic.mail]> = { team_extension: ["Team extension", Ic.teamExt], delivery_team: ["Delivery team", Ic.delivery], ai_data: ["AI and data", Ic.data], qa: ["QA and testing", Ic.qa] };
/** The service an angle is about: the research names it; older profiles are read from the wording. */
const serviceOf = (h: any): string => (h.service && SERVICE[h.service] ? h.service
  : /\bqa\b|test/i.test(h.angle ?? "") ? "qa" : /\bai\b|data|machine learning|\bml\b|analytics/i.test(h.angle ?? "") ? "ai_data"
  : /delivery team|dedicated team|squad|project team/i.test(h.angle ?? "") ? "delivery_team" : "team_extension");

/** Fit points: from Exa research when there is a profile, otherwise judged from our own records. */
function fitPoints(a: any, lp: Record<string, string>, c: any): { points: any[]; fromRecords: boolean } {
  return c?.icp_points?.length ? { points: c.icp_points, fromRecords: false } : { points: recordFit(a, lp), fromRecords: true };
}

/** The reason to call now: the strongest buying signal next to our best angle. */
function Opportunity({ d, help, me, goto }: { d: any; help?: any[]; me: { permissions: string[] }; goto: (t: Tab, s?: string) => void }) {
  const s = d.buying_signal, h = help?.[0];
  if (!s && !h) return <section className="why-now is-quiet" aria-label={tr("Why talk to them now")}>
    <p className="m-0"><Ic.target /> {tr("No live buying signal yet. “Check for news” reads their pages and searches the web; research with Exa suggests angles.")}</p>
  </section>;
  const [label, Icon] = SERVICE[h ? serviceOf(h) : "team_extension"];
  return <section className="why-now" aria-labelledby="opp-h">
    <h2 id="opp-h"><Ic.target />{tr("Why talk to them now")}</h2>
    <div className={`wn-cols${s && h ? " two" : ""}`}>
      {s && <div className="wn-sig">
        <h3>{tr("Strongest buying signal")}</h3>
        <SignalSummary signal={s} />
        {s.commercial_questions?.[0] && <p className="small mt-2 mb-0"><b>{tr("Qualify next:")}</b> {s.commercial_questions[0]}</p>}
        {d.score && !d.score.stopped && <p className="ac-note mt-2 mb-0">{tr("Signal score {} ({}) from {} signals: {}.", d.score.score, tr(BAND[d.score.band]?.[1] ?? d.score.band), d.score.signal_count, d.score.groups.map(groupName).join(", "))}</p>}
        {d.score?.stopped && <p className="small mt-2 mb-0">{tr("On hold: something we found says not to pursue this account now.")}</p>}
        <p className="wn-links"><button type="button" className="linkish" onClick={() => goto("activity", "signals")}>{tr("All signals, findings and tenders")}</button>
          {s.rule_id && me.permissions.includes("draft.create") && <Link to={`/campaigns/new?rules=${s.rule_id}`}>{tr("Start a campaign to every company with this signal")}</Link>}</p>
      </div>}
      {h && <div className="wn-help">
        <h3>{tr("Our best angle")}</h3>
        <span className="svc-tag"><Icon />{tr(label)}</span>
        <p className="wn-angle">{h.angle}</p>
        {h.why && <p className="small">{h.why}</p>}
        {me.permissions.includes("draft.create") && <button type="button" className="primary" onClick={() => goto("outreach")}>{tr("Write outreach")}</button>}
      </div>}
    </div>
  </section>;
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
  return <section className="ac-panel kp" aria-labelledby="kp-h">
    <h2 id="kp-h">{tr("Key people")}</h2>
    {!people ? <Skeleton rows={2} /> : people.length === 0 ? <>
      <p className="small">{tr("No people recorded at this company yet.")}</p>
      <button type="button" onClick={() => goto("people")}>{tr("Find decision makers")}</button></> : <>
      <Coverage people={people} />
      <ul className="kp-list">{people.filter((p) => !p.suppressed).slice(0, 5).map((p) => <li key={p.id}>
        <Avatar name={p.name} committee={p.committee} size="sm" />
        <div className="kp-who"><Link to={`/people/${p.id}`}>{p.name}</Link><span>{p.title || tr("Role not recorded")}</span></div>
        {p.has_email && <span className="kp-mail" title={tr("Email ✓")}><Ic.mail /><span className="ac-sr">{tr("Email ✓")}</span></span>}
      </li>)}</ul>
      <button type="button" className="linkish mt-2" onClick={() => goto("people")}>{tr("All {} people, by buying committee", people.length)}</button>
    </>}
  </section>;
}

function Overview({ d, research, rel, orgId, me, canRun, startResearch, researchBusy, researchErr, reloadRel, goto, people, fit }: any) {
  const a = d.account;
  const lp: Record<string, string> = d.list_profile ?? {};
  const c = research?.profile?.content;
  const vendors = [...(c?.vendors ?? []).map((v: any) => ({ name: v.name, role: v.role, cite: <Cite c={v} /> })),
    ...(d.suppliers ?? []).map((x: any) => ({ name: x.supplier_name, role: [humanize(x.supplier_role), x.scope_text].filter(Boolean).join(": "), cite: <span className="ac-cite rec">{tr("Our records")}</span> }))];
  return <>
    <ResearchState r={research} kind="company" name={a.display_name} canRun={canRun} start={startResearch} busy={researchBusy} err={researchErr} />
    <Opportunity d={d} help={c?.how_we_help} me={me} goto={goto} />
    <div className="ac-grid">
      <div className="ac-sheet">
        {c && (c.about || c.products?.length > 0) && <Sec title="What they do" id="sec-about">
          {c.about && <p className="ac-lead">{c.about.text} {c.about.sources.map((s: any, i: number) => <Cite key={i} c={s} />)}</p>}
          {c.products?.length > 0 && <ul className="ac-tags mt-2" aria-label={tr("Products and services")}>{c.products.map((p: any, i: number) => <li key={i} className="ac-tag prod" title={p.detail ?? undefined}>{p.name}</li>)}</ul>}
          {c.footprint?.markets && <p className="small mt-2"><b>{tr("Markets")}:</b> {c.footprint.markets.value} <Cite c={c.footprint.markets} /></p>}
        </Sec>}
        {c?.empty && <section className="ac-sec"><h2>{tr("What they do")}</h2><div><p className="m-0">{tr(c.empty)}</p></div></section>}
        <Sec title="Why they fit us" hint="Points of our ideal customer profile" id="sec-fit">
          {fit.points.length ? <FitBoard points={fit.points} fromRecords={fit.fromRecords} /> : <p className="small">{tr("We hold too little about this company to judge its fit. Edit the company details or research it with Exa.")}</p>}
        </Sec>
        {c && <Sec title="What they're doing now" hint="Last 12 months, from the sources" id="sec-now">
          {c.recent?.length ? <Timeline items={c.recent.map((x: any) => ({ date: x.date, title: x.headline, detail: x.detail, extra: <Cite c={x} /> }))} />
            : <p className="small">{tr("Exa found no dated news about this company in the last 12 months.")}</p>}
        </Sec>}
        {(vendors.length > 0 || c) && <Sec title="Vendors & partners" hint="Who they buy technology from" id="sec-vendors">
          {vendors.length ? <>
            <ul className="vchips">{vendors.map((v: any, i: number) => <li key={i} className="vchip"><Monogram name={v.name} size="sm" /><div><b>{v.name}</b>{v.role && <span>{v.role}</span>}{v.cite}</div></li>)}</ul>
            <p className="ac-note mt-2">{tr("A named vendor does not mean an exclusive contract or that they cover everything.")}</p></>
            : <p className="small">{tr("No vendors or integrators are named in the pages Exa found. Ask about current suppliers in the first call.")}</p>}
        </Sec>}
        {c?.how_we_help?.length > 0 && <Sec title="How we could help" hint="Suggested angles, check before you use them" id="sec-help">
          <ul className="svc-grid">{c.how_we_help.map((h: any, i: number) => { const [label, Icon] = SERVICE[serviceOf(h)]; return <li key={i} className="svc-card">
            <span className="svc-tag"><Icon />{tr(label)}</span><b>{h.angle}</b>{h.why && <p>{h.why}</p>}
            <div>{h.sources.map((s: any, k: number) => <Cite key={k} c={s} />)}</div></li>; })}</ul>
        </Sec>}
        {Object.keys(lp).length > 0 && <Sec title="From your lead list" hint="Not checked by the platform" id="sec-list"><ListProfile p={lp} /></Sec>}
        {d.opportunities.length > 0 && <Sec title="Opportunities" id="sec-opps">
          {d.opportunities.map((o: any) => <div key={o.hypothesis_id} className="mb-3"><div className="row spread"><span><Badge kind={priorityKind(o.priority)}>{lab("priority", o.priority)}</Badge> <b>{o.initiative.title}</b></span><Link to={`/opportunities/${o.hypothesis_id}`}>{tr("Open")}</Link></div>
            <p className="small">{o.plain_reason ?? whyShort(o.why_it_matters)}</p>{o.next_action && <p className="small"><b>{tr("Next:")}</b> {o.next_action.objective}</p>}</div>)}
        </Sec>}
        {c?.open_questions?.length > 0 && <Sec title="Still to find out" id="sec-q"><ul className="tight">{c.open_questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></Sec>}
      </div>
      <aside className="ac-rail" aria-label={tr("People, relationship and research")}>
        <KeyPeople people={people} goto={goto} canSee={me.permissions.includes("contacts.read")} />
        <RelationshipPanel rel={rel} orgId={orgId} onSaved={reloadRel} />
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
    {!s.setup.ready && <p className="ac-setup mt-2 mb-0">{tr("Finding people with Exa is not set up in this environment. An administrator adds EXA_API_KEY and RI_EXA_MONTHLY_CAP_USD to the API and the worker.")}</p>}
    {s.running && <div className="fp-run" role="status"><span className="ac-pulse" />{tr("Searching five parts of the buying group. About half a minute.")}</div>}
    {last?.status === "failed" && !s.running && <div className="rs-fail-inline" role="alert"><Ic.alert /><div><b>{tr("The search did not finish.")}</b> {tr(failReason(last.error))}
      {last.error && <details className="rs-why"><summary>{tr("Error details for administrators")}</summary><code>{last.error}</code></details>}</div></div>}
    {last?.status === "done" && <p className="fp-last">{tr("Last search {}: {} found, {} new, {} already in your people list.", fmtDateTime(last.finished_at), last.found, last.new_candidates, last.already_known)}</p>}
    <Err e={err} />
    {found.length > 0 && <ul className="fp-grid" aria-label={tr("People found")}>{found.map((c: any) => <li key={c.id} className="fp-card">
      <div className="pp-top">
        <Avatar name={c.name} committee={c.committee} />
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

function PersonCard({ p }: { p: any }) {
  const ll = p.lead_list ?? {};
  const prio = ll.priority;
  const useful = (v?: string | null) => (v && !/^other\s*\/\s*review$/i.test(v) ? v : null);
  const seniority = useful(p.seniority);
  return <li className={`pp-card${prio === "P1" ? " p1" : ""}${p.suppressed ? " off" : ""}`}>
    <div className="pp-top">
      <Avatar name={p.name} committee={p.committee} />
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
        <div className="chips" role="group" aria-label={tr("Filter people")}>{Object.keys(PFILTERS).map((k) => <button key={k} type="button" className="chip" aria-pressed={filter === k} onClick={() => setFilter(k)}>{tr(PFILTERS[k][0])}<b>{people.filter(PFILTERS[k][1]).length}</b></button>)}</div>
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
        {en.outbound_enabled === false && tr("Sending is switched off for now.")}{en.lemlist_configured === false && tr("Lemlist is not connected yet.")}</p>
      {plan.eligible.length === 0 && <p className="small">{tr("No contacts are eligible for this campaign.")}</p>}
      {plan.eligible.map((c: any) => <label key={c.id} className="row small gap-2 mb-1"><input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggle(c.id)} disabled={!can || !ready} /> {c.name}{c.title ? `, ${c.title}` : ""}
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
      {canDraft && <div className="row mb-3">
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

/* ------------------------------------------------------------------ activity */
const BAND: Record<string, ["ok" | "warn" | "info" | "bad" | "", string]> = { hot: ["bad", "Hot"], warm: ["warn", "Warm"], watch: ["info", "Watch"], none: ["", "No signal"] };
const DEMAND_TIER: Record<string, string> = { D1: "They asked for outside help", D2: "Likely to need outside help", D3: "Checked programme, outside demand unknown", D4: "Attention, outside demand unknown" };
const SUBS = ["timeline", "signals", "tenders", "list", "leads", "sources"] as const;
const SUB_LABEL: Record<string, string> = { timeline: "Findings timeline", signals: "Signals", tenders: "Tenders & RFPs", list: "Lead list signals", leads: "Assigned links", sources: "Sources we watch" };

function Tenders({ orgId, route }: { orgId: string; route?: string }) {
  const [t, setT] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/accounts/${orgId}/tenders`).then(setT).catch(setErr); }, [orgId]);
  if (err) return <Err e={err} />;
  if (!t) return <Skeleton rows={2} />;
  if (!t.notices.length && !t.awards.length) return <div className="card"><p className="m-0">{tr("No public tenders or contract awards name this company as the buyer.")}</p>
    <p className="small mt-2 mb-0">{tr("Most private banks, insurers and payment companies buy without a public tender.")}{route && safeHref(route) && <> {tr("Their supplier route from your lead list:")} <a href={safeHref(route)} target="_blank" rel="noreferrer noopener">{host(route)} ↗</a></>}</p></div>;
  return <>
    {t.notices.length > 0 && <div className="card"><h3 className="mt-0">{tr("Tenders where they are the buyer")}</h3>
      {t.notices.map((n: any) => <div key={n.id} className="ac-tender"><div><b>{n.title || tr("Untitled notice")}</b>
        <div className="small">{[humanize(n.status), n.country, n.published_at ? tr("published {}", fmtDate(n.published_at)) : null].filter(Boolean).join(", ")}</div></div>
        <div className="small">{n.deadline_at && <div><b>{tr("Deadline")}</b> {fmtDate(n.deadline_at)}</div>}{n.official_url && safeHref(n.official_url) && <a href={safeHref(n.official_url)} target="_blank" rel="noreferrer noopener">{tr("Official notice")} ↗</a>}</div></div>)}</div>}
    {t.awards.length > 0 && <div className="card"><h3 className="mt-0">{tr("Contracts they awarded")}</h3>
      {t.awards.map((a: any) => <div key={a.notice_id} className="ac-tender"><div><b>{a.title || tr("Untitled award")}</b><div className="small">{tr("Won by {}", (a.winners ?? []).join(", ") || tr("not stated"))}</div></div>
        <div className="small">{a.awarded_on && <div>{tr("Awarded {}", fmtDate(a.awarded_on))}</div>}{a.expected_end && <div>{tr("Ends {}", fmtDate(a.expected_end))}{a.end_basis === "assumed" ? ` (${tr("estimate")})` : ""}</div>}</div></div>)}</div>}
  </>;
}

function Activity({ d, eps, sub, setSub, params, sigNames }: any) {
  const [signalStatus, setSignalStatus] = useState("all");
  const [signalTier, setSignalTier] = useState("all");
  const [signalOrder, setSignalOrder] = useState("newest");
  const a = d.account;
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
  const counts: Record<string, number | undefined> = { signals: new Set(d.signals.map((s: any) => s.story_key ?? s.event_id)).size, list: (d.list_signals ?? []).length };
  return <>
    <div className="ac-subnav" role="group" aria-label={tr("Activity sections")}>
      {SUBS.map((k) => <button key={k} type="button" className="chip" aria-pressed={sub === k} onClick={() => setSub(k)}>{tr(SUB_LABEL[k])}{counts[k] != null && <b>{counts[k]}</b>}</button>)}
    </div>
    {sub === "timeline" && <AccountFindings key={"t" + (params.get("finding") ?? "")} accountId={a.id} selected={params.get("finding")} leadsOnly={false} />}
    {sub === "leads" && <AccountFindings key="leads" accountId={a.id} selected={null} leadsOnly />}
    {sub === "tenders" && <Tenders orgId={a.id} route={d.list_profile?.["Procurement Route"]} />}
    {sub === "list" && <div className="card">
      <p className="small muted mt-0">{tr("Buying signals recorded in your lead list, with the source the list gives. Not checked by the platform.")}</p>
      {(d.list_signals ?? []).length === 0 ? <p className="m-0">{tr("Your lead list records no signals for this company.")}</p>
        : <ul className="ac-tl">{d.list_signals.map((x: any, i: number) => <li key={i}><time>{fmtDay(x.date)}</time><div className="ac-tl-body">
          <b>{x.event}</b><p>{[x.category, x.confidence ? tr("{} confidence", x.confidence) : null].filter(Boolean).join(", ")}</p>
          {x.implication && <p>{x.implication}</p>}{x.source_url && <Cite c={{ source_url: x.source_url, source: "list" }} />}</div></li>)}</ul>}
    </div>}
    {sub === "signals" && <>
      {d.initiatives.length > 0 && <><h3>{tr("Programmes")}</h3>{d.initiatives.map((i: any) => <div key={i.id} className="card"><b>{i.title}</b> {!i.title_is_official && <Badge kind="warn">{tr("Our working title")}</Badge>} <Badge>{humanize(i.stage)}</Badge> {!i.budget_verified && <Badge kind="warn">{tr("Budget not confirmed")}</Badge>}
        <p className="small">{tr("Technology:") + " "}{i.technology_tags.join(", ") || tr("Not known")}. {tr("Last big news {}, next milestone {}.", fmtDate(i.last_material_event_at), fmtDate(i.next_milestone_at))}</p>
        {i.research_gaps.length > 0 && <><span className="small"><b>{tr("Still to find out")}</b></span><ul className="tight small">{i.research_gaps.map((g: string, k: number) => <li key={k}>{g}</li>)}</ul></>}</div>)}</>}
      <div className="row gap-2 mb-2">
        <label>{tr("Status") + " "}<select value={signalStatus} onChange={(e) => setSignalStatus(e.target.value)}><option value="all">{tr("All signals")}</option><option value="active">{tr("Current")}</option><option value="historical">{tr("Historical")}</option></select></label>
        <label>{tr("Order") + " "}<select value={signalOrder} onChange={(e) => setSignalOrder(e.target.value)}><option value="newest">{tr("Newest first")}</option><option value="oldest">{tr("Oldest first")}</option></select></label>
        <label>{tr("Evidence of demand") + " "}<select value={signalTier} onChange={(e) => setSignalTier(e.target.value)}><option value="all">{tr("All levels")}</option><option value="D1">{tr("Explicit")}</option><option value="D2">{tr("Inferred")}</option><option value="D3">{tr("Programme")}</option><option value="D4">{tr("Context")}</option></select></label>
      </div>
      {stories.map((group) => {
        const first = group[0];
        const rules = new Map(group.map((x: any) => [`${x.rule_id}:${x.status}:${x.demand_tier}`, x]));
        const evidence = new Map(group.flatMap((x: any) => x.evidence).map((e: any) => [e.claim_id, e]));
        return <div className="card" key={first.story_key ?? first.event_id}>
          {[...new Set(group.map((x: any) => x.event_id))].map((eventId: any) => <span id={`event-${eventId}`} key={eventId} />)}
          <b>{first.summary}</b><p className="small muted">{first.event_at || first.pub_at ? fmtDate(first.event_at ?? first.pub_at) : tr("Date unknown")}</p>
          {[...rules.entries()].map(([key, x]: any) => <p className="small" key={key}><Badge kind={x.status === "active" ? "ok" : ""}>{x.status === "active" ? tr("Current") : tr("Historical")}</Badge> {SHORT[x.rule_id] ? tr(SHORT[x.rule_id]) : sigNames[x.rule_id] ?? x.rule_id}. {tr(DEMAND_TIER[x.demand_tier] ?? humanize(x.demand_tier))}</p>)}
          <Evidence items={[...evidence.values()]} />
        </div>;
      })}
      {stories.length === 0 && <p className="muted">{d.signals.length ? tr("No signals match these filters.") : tr("No verified signals yet. Press “Check for news” to read the company's own pages and search the web.")}</p>}
    </>}
    {sub === "sources" && <>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Coverage table")}><p className="small mt-0">{tr(d.source_coverage_note)}</p><table><thead><tr><th>{tr("Kind of source")}</th><th>{tr("State")}</th><th>{tr("Last successful check")}</th></tr></thead><tbody>{d.coverage.map((c: any, i: number) => <tr key={i}><td>{humanize(c.source_family)}</td><td>{humanize(c.coverage_state)}</td><td>{fmtDate(c.last_success_at)}</td></tr>)}
        {d.coverage.length === 0 && <tr><td colSpan={3} className="muted">{tr("We have not checked this account's sources yet. That means we cannot see, not that nothing is happening.")}</td></tr>}</tbody></table></div>
      <div className="card table-wrap" tabIndex={0} role="region" aria-label={tr("Where this company publishes")}><h3 className="mt-0">{tr("Where this company publishes")}</h3>
        {eps.length === 0 ? <p className="small muted">{tr("Nothing found yet. Press “Check for news” to look for its news feed, press page, careers page and job board.")}</p>
          : <table><thead><tr><th>{tr("Kind")}</th><th>{tr("Address")}</th><th>{tr("Last check")}</th><th>{tr("Items read")}</th></tr></thead><tbody>{eps.map((e: any, i: number) => <tr key={i}><td>{humanize(e.kind)}{e.provider ? ` (${e.provider})` : ""}</td><td className="small break-all"><a href={safeHref(e.url)} target="_blank" rel="noreferrer noopener">{e.url} ↗</a></td><td className="small">{e.last_scanned_at ? <>{fmtDateTime(e.last_scanned_at)} <Badge kind={e.last_status === "ok" ? "ok" : "bad"}>{e.last_status === "ok" ? tr("Worked") : tr("Problem")}</Badge>{e.last_detail && <div className="muted">{e.last_detail}</div>}</> : tr("Not yet")}</td><td>{e.docs_seen}</td></tr>)}</tbody></table>}</div>
      {d.source_rows?.length > 0 && <div className="card"><h3 className="mt-0">{tr("Where it appears in your workbooks")}</h3><ul className="tight small mb-0">{d.source_rows.map((r: any, i: number) => <li key={i}>{tr("{}, row {}", r.sheet_title, r.row_number)}</li>)}</ul></div>}
      {d.opportunities.length > 0 && <div className="card"><h3 className="mt-0">{tr("Sources for its opportunities")}</h3>{d.opportunities.slice(0, 3).map((o: any) => <Evidence key={o.hypothesis_id} items={o.evidence} />)}</div>}
    </>}
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
  const [tab, setTab] = useState<Tab>(linked?.[0] ?? (asked && TABS.includes(asked) ? asked : "overview"));
  const [sub, setSub] = useState<string>(linked?.[1] ?? "timeline");
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
  useEffect(() => { loadResearch(); }, [id]);
  usePoll(Boolean(research?.running), loadResearch, 5000);
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
    <p className="small mt-0"><Link to="/accounts">{tr("Accounts")}</Link></p><h1 className="title-sm">{tr("Account details")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the account")} />}
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
    try { const r = await post(`/api/accounts/${id}/scan`); setScan({ busy: false, msg: r.website ? tr("Reading {} and searching the web. Verified findings appear under Activity.", r.website) : tr("Started. This account has no website on record, so we will only run the web search."), err: null }); }
    catch (e) { setScan({ busy: false, msg: "", err: e }); }
  };
  const goto = (t: Tab, s?: string) => {
    setTab(t); if (s) setSub(s);
    if (linked || params.has("tab")) { const q = new URLSearchParams(params); ["finding", "timeline", "event", "tab"].forEach((k) => q.delete(k)); setParams(q, { replace: true }); }
  };
  const jump = (anchor: string) => { goto("overview"); setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60); };
  const companyLinkedIn = [...context.filter((x) => x.kind === "link").map((x) => x.value), ...((research?.profile?.sources ?? []).map((s: any) => s.url))]
    .find((u: string) => /linkedin\.com\/company\//i.test(u));
  const hq = c?.footprint?.hq?.value ?? countryName(a.country_code);
  const sector = a.segment || lp["Industry"];
  const peopleCount = people?.length ?? d.buyers?.length ?? 0;
  const live = (people ?? []).filter((p) => !p.suppressed);
  const deciders = live.filter((p) => p.committee === "decision").length;
  const good = fit.points.filter((p: any) => p.fit === "good").length;
  const sig = d.buying_signal;
  return (
    <>
      <header className="ac-hero" data-mark={initials(a.display_name)}>
        <nav className="ac-crumb" aria-label={tr("Breadcrumb")}><Link to="/accounts">{tr("Accounts & leads")}</Link><span aria-hidden="true">/</span><span aria-current="page">{a.display_name}</span></nav>
        <div className="ac-id">
          <Monogram name={a.display_name} size="lg" framed />
          <div className="ac-name">
            <h1>{a.display_name}</h1>
            {(sector || hq) && <p className="ac-sub">{[sector, hq].filter(Boolean).join(", ")}</p>}
            <div className="ac-links">
              <RelBadge rel={rel} onNavy />
              {a.canonical_domain && <a href={`https://${a.canonical_domain}`} target="_blank" rel="noreferrer noopener"><Ic.web />{a.canonical_domain}</a>}
              {companyLinkedIn ? <a href={safeHref(companyLinkedIn)} target="_blank" rel="noreferrer noopener"><Ic.linkedin />LinkedIn</a>
                : <a href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(a.display_name)}`} target="_blank" rel="noreferrer noopener"><Ic.linkedin />{tr("Find on LinkedIn")}</a>}
            </div>
          </div>
          <div className="ac-actions">
            {canScan && research && <button type="button" className="primary" disabled={researchBusy || research.running || !research.setup?.ready}
              title={research.setup?.ready ? undefined : tr("Research with Exa is not set up in this environment.")} onClick={startResearch}>{research.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>}
            {canScan && <button type="button" disabled={scan.busy} onClick={runScan}>{tr("Check for news")}</button>}
            {canScan && a.entity_type !== "public_body" && <button type="button" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? tr("Close details") : tr("Edit details")}</button>}
          </div>
        </div>
        <div className="ac-glance">
          {fit.points.length > 0 && <button type="button" className="glance" onClick={() => jump("sec-fit")}>
            <FitCells points={fit.points} />
            <span><b>{tr("{} of {} fit points in our favour", good, fit.points.length)}</b><small>{fit.fromRecords ? tr("Judged from our records") : tr("From Exa research")}</small></span></button>}
          {canPeople && <button type="button" className="glance" onClick={() => goto("people")}>
            {live.length > 0 ? <span className="ac-stack" aria-hidden="true">{live.slice(0, 5).map((p) => <Avatar key={p.id} name={p.name} committee={p.committee} size="sm" />)}{live.length > 5 && <span className="mono mono-sm more">+{live.length - 5}</span>}</span>
              : <span className="glance-ic" aria-hidden="true"><Ic.people /></span>}
            <span><b>{peopleCount ? tr("{} people", number(peopleCount)) : tr("No people yet")}</b><small>{peopleCount ? tr("{} decision makers", deciders) : tr("Find decision makers with Exa")}</small></span></button>}
          {sig && <button type="button" className="glance" onClick={() => jump("opp-h")}>
            <span className="glance-ic is-sig" aria-hidden="true"><Ic.target /></span>
            <span><b>{sig.rule_id && SHORT[sig.rule_id] ? tr(SHORT[sig.rule_id]) : sig.label}</b><small>{tr("Live buying signal, {}", fmtDate(sig.happened_at ?? sig.published_at))}</small></span></button>}
        </div>
      </header>
      <dl className="ac-facts">
        <div><dt>{tr("Headquarters")}</dt><dd>{hq || tr("Not known")}</dd></div>
        <div><dt>{tr("Size")}</dt><dd>{c?.footprint?.size?.value ?? staffCount(a.employees_estimate, a.employees_uncertain)}</dd></div>
        <div><dt>{tr("Industry")}</dt><dd>{sector || tr("Not known")}{a.company_ownership && a.company_ownership !== "unknown" && <div className="small">{tr(OWNERSHIP[a.company_ownership])}</div>}</dd></div>
        <div><dt>{tr("Lead list")}</dt><dd>{a.list_priority || a.list_score != null ? <>{a.list_priority && <span className={`ac-tag ${String(a.list_priority).toLowerCase()}`}>{a.list_priority}</span>} {a.list_score != null && tr("score {}", a.list_score)}<div className="small">{a.list_eligibility}</div></> : tr("Not on the list")}</dd></div>
        <div><dt>{tr("Owner")}</dt><dd><OwnerControl rel={rel} orgId={a.id} onChange={(owner) => setRel({ ...rel, owner })} /></dd></div>
      </dl>
      {scan.msg && <div className="okmsg mt-2 mb-2" role="status">{scan.msg}</div>}<Err e={scan.err} />
      {editing && canScan && a.entity_type !== "public_body" && <CompanyProfile account={a} onSave={(profile) => setD({ ...d, account: { ...a, ...profile } })} />}
      <Tabs items={[{ key: "overview", label: tr("Overview") }, { key: "people", label: <>{tr("People")}{canPeople && <span className="tabn">{number(peopleCount)}</span>}</> },
        { key: "activity", label: tr("Activity") }, { key: "outreach", label: tr("Outreach") }]} value={tab} onChange={(k) => goto(k as Tab)} label={tr("Account sections")}>
        {tab === "overview" && <Overview d={d} research={research} rel={rel} orgId={a.id} me={me} canRun={canScan} startResearch={startResearch} researchBusy={researchBusy}
          researchErr={researchErr} reloadRel={loadRel} goto={goto} people={canPeople ? people : null} fit={fit} />}
        {tab === "people" && <PeopleTab orgId={a.id} company={a.display_name} me={me} people={people} err={peopleErr} reload={() => { loadPeople(); loadRel(); }} />}
        {tab === "activity" && <Activity d={d} eps={eps} sub={sub} setSub={setSub} params={params} sigNames={sigNames} />}
        {tab === "outreach" && <CampaignTab camps={camps} canDraft={me.permissions.includes("draft.create")} orgId={a.id} make={makeCampaign} decide={decideCampaign} />}
      </Tabs>
    </>
  );
}
