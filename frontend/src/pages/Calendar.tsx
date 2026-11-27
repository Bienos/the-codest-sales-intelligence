import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { counted, getLanguage, number, tr, useLanguage } from "../locale";
import { Err, Skeleton, reloadPage, safeHref } from "../components/ui";
import "../styles/calendar.css";

/** Regulatory deadlines: hard EU dates that force banks, payment firms and insurers to spend on IT, with the accounts each one applies to.
 *  The calendar is data in the repository (backend/rules/regulatory_calendar.yaml); GET /api/regulatory-calendar adds our accounts. */

type Certainty = "adopted" | "provisional" | "possible";
type Account = { id: string; name: string; country: string; kind: string; logo_url?: string | null };
export type Deadline = {
  id: string; title: string; title_pl: string; regulation: string; date: string; year: number; approximate: boolean; certainty: Certainty;
  past: boolean; days_left: number; pinned: boolean; countries: string[]; kinds: string[];
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
const fmt = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(isPl() ? "pl-PL" : "en-GB", opts).format(new Date(`${iso}T12:00:00`));

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

function Sources({ urls }: { urls: string[] }) {
  const ok = urls.map((u) => [u, safeHref(u)] as const).filter(([, h]) => h);
  if (!ok.length) return null;
  return <p className="rc-src"><span>{tr(ok.length > 1 ? "Sources" : "Source")}</span>
    {ok.map(([u, h]) => <a key={u} href={h} target="_blank" rel="noreferrer noopener">{host(u)} ↗</a>)}</p>;
}

function Item({ d, kind, kinds }: { d: Deadline; kind: string; kinds: Record<string, Kind> }) {
  const acc = d.accounts ?? { count: 0, by_kind: {}, sample: [] };
  const count = kind ? acc.by_kind[kind] ?? 0 : acc.count;
  const sample = kind ? acc.sample.filter((a) => a.kind === kind) : acc.sample;
  const kindWord = (k: string) => (kinds[k] ? pick(kinds[k].label, kinds[k].label_pl) : k);
  return (
    <li className={`rc-item rc-c-${d.past ? "past" : d.certainty}`}>
      <div className="rc-when" aria-hidden="true">
        {d.approximate ? <><b>≈</b><span>{d.year}</span></> : <><b>{fmt(d.date, { day: "numeric" })}</b><span>{fmt(d.date, { month: "short" })}</span></>}
      </div>
      <article className="rc-card" aria-labelledby={`rc-${d.id}`}>
        <div className="rc-top"><CertaintyBadge d={d} /><span className="rc-left">{leftText(d)}</span></div>
        <h3 id={`rc-${d.id}`}>{pick(d.title, d.title_pl)}</h3>
        <p className="rc-reg">{d.regulation} · <time dateTime={d.date}>{whenText(d)}</time></p>
        <p className="rc-say" title={tr("Who it applies to, and what they must do")}>{pick(d.say, d.say_pl)}</p>
        <dl className="rc-facts">
          {d.ask_for && <div><dt>{tr("Who to ask for")}</dt><dd>{pick(d.ask_for, d.ask_for_pl)}</dd></div>}
          {d.note && <div><dt>{tr("Good to know")}</dt><dd>{pick(d.note, d.note_pl)}</dd></div>}
        </dl>
        <div className="rc-acc">
          <p className="rc-acc-n">{count > 0
            ? <>{tr("Applies to")} <b>{counted(count, ["of our target accounts", "of our target accounts"], ["naszej firmy docelowej", "naszych firm docelowych", "naszych firm docelowych"])}</b></>
            : tr("None of our target accounts")}</p>
          {sample.length > 0 && <ul className="rc-names" aria-label={tr("Accounts it applies to")}>
            {sample.map((a) => <li key={a.id}><Link to={`/accounts/${a.id}`}>{a.name}</Link><small>{[kindWord(a.kind), a.country].join(" · ")}</small></li>)}
            {count > sample.length && <li className="rc-more">{tr("and {} more", number(count - sample.length))}</li>}
          </ul>}
        </div>
        <Sources urls={d.sources} />
      </article>
    </li>
  );
}

export default function Calendar() {
  useLanguage();
  const [d, setD] = useState<Cal | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [kind, setKind] = useState("");
  useEffect(() => { api<Cal>("/api/regulatory-calendar").then(setD).catch(setErr); }, []);
  const groups = useMemo(() => {
    if (!d) return [];
    const shown = d.items.filter((x) => !kind || x.kinds.includes(kind));
    const ahead = shown.filter((x) => !x.past);
    const years = Array.from(new Set(ahead.map((x) => x.year))).sort();
    const out: [string, Deadline[]][] = years.map((y) => [String(y), ahead.filter((x) => x.year === y)]);
    const past = shown.filter((x) => x.past);
    if (past.length) out.push([tr("Already in force"), past]);
    return out;
  }, [d, kind]);
  const head = <div className="page-head"><div className="eyebrow">{tr("Regulation")}</div><h1>{tr("Regulatory deadlines")}</h1>
    <p className="sub">{tr("EU laws that give banks, payment firms and insurers a fixed date for IT work, and which of our accounts each one applies to. Use a date to open the conversation: they must ship this by then.")}</p></div>;
  if (err) return <>{head}<Err e={err} onRetry={reloadPage} /></>;
  if (!d) return <>{head}<Skeleton label={tr("Loading the deadlines")} /></>;
  const next = d.items.find((x) => !x.past && x.certainty === "adopted");
  const kinds = Object.entries(d.kinds);
  return (
    <div className="rc">
      {head}
      <ul className="rc-stats" aria-label={tr("At a glance")}>
        {next && <li><span>{tr("Next fixed date")}</span><b>{whenText(next)}</b><small>{leftText(next)}</small></li>}
        <li><span>{tr("Fixed dates in the next {} months", d.pin_months)}</span><b>{number(d.counts.pinned)}</b><small>{tr("pinned to our accounts")}</small></li>
        <li><span>{tr("Our accounts affected")}</span><b>{number(d.counts.accounts)}</b><small>{tr("target accounts with at least one date ahead")}</small></li>
      </ul>
      <p className="rc-how">{tr("Adopted dates in the next {} months show on each account's page, and up to two a day reach Today. Provisional and approximate dates are shown here only: check them before you use them.", d.pin_months)}</p>
      <div className="chips rc-filter" role="group" aria-label={tr("Kind of firm")}>
        <button type="button" className="chip" aria-pressed={!kind} onClick={() => setKind("")}>{tr("All firms")}</button>
        {kinds.map(([k, v]) => <button key={k} type="button" className="chip" aria-pressed={kind === k} onClick={() => setKind(kind === k ? "" : k)}>{cap(pick(v.plural, v.plural_pl))}</button>)}
      </div>
      {groups.length === 0 && <p className="rc-empty">{tr("No deadlines for this kind of firm.")}</p>}
      {groups.map(([label, list]) => (
        <section key={label} className="rc-year" aria-labelledby={`rc-y-${label}`}>
          <h2 id={`rc-y-${label}`}>{label}<small>{counted(list.length, ["deadline", "deadlines"], ["termin", "terminy", "terminów"])}</small></h2>
          <ol className="rc-line">{list.map((x) => <Item key={x.id} d={x} kind={kind} kinds={d.kinds} />)}</ol>
        </section>
      ))}
      <p className="rc-foot">{tr("Who a deadline applies to comes from the company's country and its sector on the lead list (banking, payments, insurance). Fintech and software companies are not matched: the list does not say which of them hold a licence.")}
        {d.counts.left_out > 0 && <> {tr("{} central banks, statutory insurers and public bodies on the list are left out: these rules are about commercial banks, payment firms and life insurers.", number(d.counts.left_out))}</>}</p>
    </div>
  );
}

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
        <Link to="/calendar">{tr("See every deadline")}</Link>
      </p>
    </section>
  );
}
