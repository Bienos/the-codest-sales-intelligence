import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, post } from "../api";
import { counted, getLanguage, number, tr, useLanguage } from "../locale";
import { Err, Skeleton, safeHref, useLogo } from "../components/ui";
import { countryName } from "./targetMarket";
import "../styles/today.css";

/** Today: a work cockpit. A short ranked list of who to contact today (left) and the brief for the one you picked (right): what
 *  happened, who to write to, the opening line and one next step. Above it, tender deadlines for the next two weeks and what is new. */

type Org = { id: string; name: string; country: string | null; region: string; priority: string | null; relationship: string | null;
  people: number | null; people_with_email: number | null; logo_url?: string | null };
type Kind = "reply" | "tender" | "signal" | "follow_up";
type Person = { id: string; name: string; title: string | null };
type Contact = Person & { photo_url: string | null; has_email: boolean; matches: boolean; committee: string | null };
type Fit = { service: string | null; name: string; level: string | null; why: string | null };
type Action = {
  key: string; kind: Kind; organization: Org | null; buyer?: string | null; title?: string | null; text?: string | null;
  happened?: string | null; deadline?: string | null; days_left?: number | null; strength: number;
  source?: { name: string | null; url: string | null } | null; who?: string | null; opener?: string | null; opener_kind?: "line" | "step";
  rule_id?: string | null; more?: number; person?: Person | null; engagement?: { opens: number; clicks: number } | null;
  contacts: Contact[]; fit: Fit | null;
};
type Done = { key: string; outcome: Mark; kind: Kind; slot: Slot; organization_id: string | null; label: string | null; at: string };
type NewKind = "signal" | "tender" | "reply" | "campaign";
type NewItem = {
  key: string; kind: NewKind; at: string; organization?: Org | null; buyer?: string | null; text?: string | null; url?: string | null;
  deadline?: string | null; person?: Person | null; campaign?: { id: string; name: string; people: number } | null; in_list?: Slot | null;
};
type Deadline = { key: string; name: string | null; organization_id: string | null; date: string; days_left: number; title: string | null;
  url: string | null; in_list: Slot | null; logo_url?: string | null };
type Brief = {
  generated_at: string; name: string | null; since: string; last_visit: string | null; actions: Action[]; done_today: Done[]; queue: Action[];
  more_actions: number; hidden: number; new: NewItem[]; new_total: number;
  week: { start: string; days: number; deadlines: Deadline[]; new: Record<NewKind, number> };
  next_up: { organization: Org; next_step: string | null; signal: string | null }[];
};
type Mark = "done" | "later";
type Slot = "today" | "next";
type Why = "reply" | "deadline" | "strong" | "signal" | "follow_up";

const PHONE = "(max-width: 860px)";
const NEW_SHOWN = 8;
const DEADLINES_SHOWN = 6;
const RELATIONSHIP: Record<string, string> = { client: "Client", past_client: "Past client", in_talks: "In talks" };

/* ------------------------------------------------------------------ small helpers (local copies, so this page owns its look) */
const HUES = ["#CC085D", "#6A3FC9", "#1F5C8F", "#B8402F", "#8A1C6B", "#0E7490"];
const hueOf = (s: string) => { let h = 7; for (const ch of s.toLowerCase()) h = (h * 31 + (ch.codePointAt(0) ?? 0)) >>> 0; return HUES[h % HUES.length]; };
/** Initials for a mark: the capitalised words first, so "Banque de France" is BF and "Banca d’Italia" is BI. */
const initials = (name: string) => {
  const words = name.replace(/\(.*?\)/g, "").split(/[\s-]+/).map((w) => w.replace(/^[dl][’']/i, "")).filter((w) => /^[\p{L}\p{N}]/u.test(w));
  const caps = words.filter((w) => /^\p{Lu}/u.test(w));
  const use = caps.length >= 2 ? caps : words;
  return (use.length === 1 ? use[0].slice(0, 2) : use.slice(0, 2).map((w) => w[0]).join("")).toUpperCase() || "?";
};
const COMMITTEE_HUE: Record<string, string> = { decision: "#CC085D", technical: "#6A3FC9", procurement: "#1F5C8F", influencer: "#B8402F" };
const COMMITTEE_LABEL: Record<string, string> = { decision: "Decision maker", technical: "Technical buyer", procurement: "Procurement", influencer: "Influencer" };
const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name;
const nameOf = (a: Action) => a.organization?.name ?? a.buyer ?? tr("Unknown buyer");

/** British date order for English (26 November), Polish for Polish. */
const loc = () => (getLanguage() === "pl" ? "pl-PL" : "en-GB");
function daysSince(v?: string | null): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
}
function ago(v?: string | null): string {
  const d = daysSince(v);
  if (d == null) return "";
  if (d === 0) return tr("today");
  if (d === 1) return tr("yesterday");
  if (d < 14) return tr("{} days ago", d);
  return tr("{} weeks ago", Math.round(d / 7));
}
function inDays(n?: number | null): string {
  if (n == null) return "";
  return n <= 0 ? tr("today") : n === 1 ? tr("tomorrow") : tr("in {} days", n);
}
function day(v?: string | null, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  if (!v) return "";
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(v) ? `${v}T12:00:00` : v);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(loc(), opts);
}
function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? tr("Good morning") : h < 18 ? tr("Good afternoon") : tr("Good evening");
}

/* ------------------------------------------------------------------ the type system: why an item is on the list */
const Svg = ({ children }: { children: React.ReactNode }) => (
  <svg className="td-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" aria-hidden="true">{children}</svg>
);
const ICON = {
  reply: <Svg><path d="M4 5h16v11H9l-5 4z" /></Svg>,
  deadline: <Svg><circle cx="12" cy="13" r="8" /><path d="M12 9v4l3 2M9 2h6" /></Svg>,
  strong: <Svg><path d="M13 2L5 13h6l-1 9 8-12h-6z" /></Svg>,
  signal: <Svg><path d="M2 12h4l3-7 5 14 3-7h5" /></Svg>,
  follow_up: <Svg><path d="M9 15l-5-5 5-5" /><path d="M4 10h10a6 6 0 010 12h-3" /></Svg>,
  campaign: <Svg><path d="M3 10v4h3l6 4V6L6 10z" /><path d="M16 9a4 4 0 010 6" /></Svg>,
  tender: <Svg><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 13h7M9 17h4" /></Svg>,
  check: <Svg><path d="M5 12l5 5 9-10" /></Svg>,
  chevron: <Svg><path d="M6 9l6 6 6-6" /></Svg>,
  back: <Svg><path d="M15 5l-7 7 7 7" /></Svg>,
};
const whyOf = (a: Pick<Action, "kind" | "strength">): Why =>
  a.kind === "reply" ? "reply" : a.kind === "tender" ? "deadline" : a.kind === "follow_up" ? "follow_up" : a.strength >= 3 ? "strong" : "signal";
const WHY_SHORT: Record<Why, string> = { reply: "Replied", deadline: "Tender deadline", strong: "Strong signal", signal: "New signal", follow_up: "Follow-up" };

/** The chip's long form, for the brief. */
function whyLong(a: Action): string {
  const w = whyOf(a);
  if (w === "reply") return tr("Replied {}", ago(a.happened));
  if (w === "deadline") return a.days_left == null ? tr("Open tender") : a.days_left <= 0 ? tr("Tender closes today")
    : a.days_left === 1 ? tr("Tender closes tomorrow") : tr("Tender closes in {} days", a.days_left);
  if (w === "follow_up") return a.engagement?.clicks ? tr("Clicked your email {}", ago(a.happened)) : tr("Opened your email {} times", a.engagement?.opens ?? 2);
  return w === "strong" ? tr("Strong signal, {}", ago(a.happened)) : tr("New signal, {}", ago(a.happened));
}
/** One plain sentence: why this item is on today's list. */
function whyReason(a: Action): string {
  switch (whyOf(a)) {
    case "reply": return tr("They answered our email. Replies go cold fast, so they come first.");
    case "deadline": return tr("The deadline is close and a bid takes time to prepare.");
    case "follow_up": return a.engagement?.clicks ? tr("They clicked a link in our email but have not answered yet. A short personal note often gets a reply.")
      : tr("They keep opening our email but have not answered yet. A short personal note often gets a reply.");
    case "strong": return tr("Strong, fresh news at a company on your list. The best time to write is within a week.");
    default: return tr("Fresh news at a company on your list.");
  }
}
/** True when the fit engine's reason only repeats the news shown above it. */
function repeats(why: string, shown: string): boolean {
  const core = why.replace(/^[^:]{1,60}:\s*/, "").replace(/\s*\([^)]*\)\s*$/, "").replace(/[.…\s]+$/, "").trim().toLowerCase().slice(0, 40);
  return core.length > 10 && shown.toLowerCase().includes(core);
}
/** The right-hand cue on a row: the deadline, or how fresh the news is. */
const cue = (a: Action) => (a.kind === "tender" && a.days_left != null ? inDays(a.days_left) : ago(a.happened));
/** One line on a list row: what happened, in plain words (the full text is in the brief). */
function rowLine(a: Action): string {
  if (a.kind === "reply" && a.person) return tr("{} answered our email.", a.person.name);
  if (a.kind === "follow_up" && a.person) return tr("{} opened our email but has not answered.", a.person.name);
  return (a.kind === "tender" ? a.title : a.text ?? a.title ?? "")?.replace(/[.\s]+$/, "") ?? "";
}
/** Who to talk to, on a list row: the person who wrote back, else the best match we hold, else the honest "nobody yet". */
function rowWho(a: Action): string {
  const c = a.person ?? a.contacts[0];
  if (c) return a.kind === "reply" || a.kind === "follow_up" ? c.name : tr("Ask for {}", c.name);
  return a.organization ? tr("No contacts yet") : tr("The tender page names the contact");
}

function Chip({ w, children }: { w: Why; children: React.ReactNode }) {
  return <span className={`td-chip td-w-${w}`}>{ICON[w]}<span>{children}</span></span>;
}
/** A company's mark: its logo on a white tile when the API sends one (`logo`), otherwise its initials. A logo that does not load drops back to the initials. */
function Mono({ name, size = "md", logo }: { name: string; size?: "xs" | "sm" | "md" | "lg"; logo?: string | null }) {
  const pic = useLogo(logo);
  return <span className={`td-mono td-mono-${size}${pic.ok ? " has-logo" : ""}`} style={pic.ok ? undefined : { background: hueOf(name) }} aria-hidden="true">{!pic.ok && initials(name)}{pic.img}</span>;
}
/** A person's mark: the LinkedIn photo when research found one (initials again if it no longer loads), else initials. */
function Face({ name, photo, committee }: { name: string; photo?: string | null; committee?: string | null }) {
  const [failed, setFailed] = useState<string | null>(null);
  const shown = Boolean(photo) && failed !== photo;
  return <span className={`td-face${shown ? " has-photo" : ""}`} style={{ background: COMMITTEE_HUE[committee ?? ""] ?? hueOf(name) }} aria-hidden="true">
    {shown ? <img src={photo!} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(photo!)} /> : initials(name)}
  </span>;
}

/** The one next step for an item. */
function primary(a: Action): { label: string; to?: string; href?: string } {
  const org = a.organization;
  if (a.kind === "reply" && a.person) return { label: tr("Answer {}", firstName(a.person.name)), to: `/people/${a.person.id}` };
  if (a.kind === "follow_up" && a.person) return { label: tr("Follow up with {}", firstName(a.person.name)), to: `/people/${a.person.id}` };
  if (a.kind === "tender") {
    const href = safeHref(a.source?.url);
    if (href) return { label: tr("Open the tender"), href };
  }
  const c = a.contacts[0];
  if (c) return { label: c.has_email ? tr("Write to {}", firstName(c.name)) : tr("Open {}’s page", firstName(c.name)), to: `/people/${c.id}` };
  if (org) return { label: tr("Find people to contact"), to: `/accounts/${org.id}?tab=people` };
  return { label: tr("See open tenders"), to: "/tenders" };
}

function track(a: { key: string; kind: Kind; organization?: Org | null; buyer?: string | null }, outcome: "opened" | Mark | "undo", slot?: Slot) {
  return post("/api/today/brief/items", { key: a.key, outcome, organization_id: a.organization?.id ?? null,
    ...(outcome === "done" || outcome === "later" ? { label: (a.organization?.name ?? a.buyer ?? "").slice(0, 160) || null, kind: a.kind, slot } : {}) });
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1800); }).catch(() => undefined);
  };
  return <button type="button" className="td-btn td-btn-quiet td-copy" onClick={copy}>
    <span aria-live="polite">{copied ? tr("Copied") : tr("Copy")}</span>
  </button>;
}

/** "More" next to the main button: the other ways in, as a small menu. */
function MoreMenu({ items }: { items: { label: string; to?: string; href?: string; onPick?: () => void }[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    box.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  if (!items.length) return null;
  const onKey = (e: React.KeyboardEvent) => {
    const all = Array.from(box.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
    const i = all.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); btn.current?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
    else if (e.key === "Tab") setOpen(false);
  };
  return <div className="td-more" ref={box} onKeyDown={open ? onKey : undefined}>
    <button ref={btn} type="button" className="td-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
      {tr("More")}{ICON.chevron}
    </button>
    {open && <ul role="menu" className="td-menu" aria-label={tr("More actions")}>
      {items.map((it) => <li role="none" key={it.label}>
        {it.href
          ? <a role="menuitem" href={it.href} target="_blank" rel="noopener noreferrer" onClick={() => { it.onPick?.(); setOpen(false); }}>{it.label} ↗</a>
          : <Link role="menuitem" to={it.to!} onClick={() => { it.onPick?.(); setOpen(false); }}>{it.label}</Link>}
      </li>)}
    </ul>}
  </div>;
}

/* ------------------------------------------------------------------ the header: the day at a glance, and progress */
function Header({ name, segments, done, total, next }: { name: string | null; segments: { key: string; w: Why; done: boolean; label: string }[];
  done: number; total: number; next: string | null }) {
  const today = new Date().toLocaleDateString(loc(), { weekday: "long", day: "numeric", month: "long" });
  return <header className="td-top">
    <div className="page td-top-in">
      <div className="td-hello">
        <p className="td-today">{today}</p>
        <h1>{name ? `${greeting()}, ${name}` : greeting()}</h1>
        <p className="td-purpose">{tr("Who to contact today, and why. Pick one, act on it, mark it done.")}</p>
      </div>
      {total > 0 && <div className="td-progress">
        <div className="td-squares" aria-hidden="true">
          {segments.map((s) => <span key={s.key} className={`td-sq td-w-${s.w}${s.done ? " is-done" : ""}`} title={s.label} />)}
        </div>
        <p className="td-progress-t" aria-live="polite">
          <b>{done === total ? tr("All {} done", total) : tr("{} of {} done", done, total)}</b>
          <span>{next ? tr("Next: {}", next) : done === total ? tr("Nice work. The list fills again tomorrow.") : ""}</span>
        </p>
      </div>}
    </div>
  </header>;
}

/* ------------------------------------------------------------------ the week strip: deadlines on a timeline, and what is new */
/** Where a deadline leads: the item on today's list, else the tender itself, else the company (or all tenders). */
function Target({ m, className, label, onPick, children }: { m: Deadline; className: string; label: string; onPick: (key: string, slot: Slot) => void; children: React.ReactNode }) {
  const external = m.url ? safeHref(m.url) : undefined;
  if (m.in_list) return <button type="button" className={className} data-tip={label} aria-label={label} onClick={() => onPick(m.key, m.in_list!)}>{children}</button>;
  if (external) return <a className={className} data-tip={label} aria-label={label} href={external} target="_blank" rel="noopener noreferrer">{children}</a>;
  return <Link className={className} data-tip={label} aria-label={label} to={m.organization_id ? `/accounts/${m.organization_id}` : "/tenders"}>{children}</Link>;
}

function Week({ d, sinceLabel, openKind, phone, onKind, onPick }: { d: Brief; sinceLabel: string; openKind: NewKind | null; phone: boolean;
  onKind: (k: NewKind) => void; onPick: (key: string, slot: Slot) => void }) {
  const start = new Date(`${d.week.start}T12:00:00`);
  const days = Array.from({ length: d.week.days }, (_, i) => { const x = new Date(start); x.setDate(start.getDate() + i); return x; });
  const iso = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const byDay = new Map<string, Deadline[]>();
  for (const dl of d.week.deadlines) byDay.set(dl.date, [...(byDay.get(dl.date) ?? []), dl]);
  const tiles: { k: NewKind; label: string }[] = [
    { k: "signal", label: tr("buying signals") }, { k: "tender", label: tr("tenders") },
    { k: "reply", label: tr("replies") }, { k: "campaign", label: tr("campaigns started") },
  ];
  const anyNew = tiles.some((t) => (d.week.new[t.k] ?? 0) > 0);
  const labelOf = (m: Deadline) => tr("{}: tender closes {}", m.name ?? "", day(m.date));
  return <section className="td-week" aria-label={tr("This week")}>
    <div className="td-tl">
      <div className="td-sec">
        <h2>{tr("Tender deadlines, next two weeks")}</h2>
        <p>{d.week.deadlines.length
          ? tr("{} close soon. Pick one to open it.", counted(d.week.deadlines.length, ["tender", "tenders"], ["przetarg", "przetargi", "przetargów"]))
          : tr("No tender closes in the next two weeks.")}</p>
      </div>
      <ol className="td-days">
        {days.map((x, i) => {
          const marks = byDay.get(iso(x)) ?? [];
          const weekend = x.getDay() === 0 || x.getDay() === 6;
          const month = i === 0 || x.getDate() === 1;
          return <li key={i} className={`td-day${i === 0 ? " is-today" : ""}${weekend ? " is-weekend" : ""}${marks.length ? " has-marks" : ""}`}>
            <span className="td-dow" aria-hidden="true">{x.toLocaleDateString(loc(), { weekday: "narrow" })}</span>
            <span className="td-dom"><span className="td-sr">{day(iso(x), { weekday: "long", day: "numeric", month: "long" })}</span>
              <span aria-hidden="true">{x.getDate()}{month && <small>{x.toLocaleDateString(loc(), { month: "short" })}</small>}</span></span>
            {marks.length > 0 && <span className="td-pins" aria-hidden={phone || undefined}>
              {phone
                ? <>{marks.slice(0, 2).map((m) => <span key={m.key} className="td-pin"><Mono name={m.name ?? "?"} size="xs" /></span>)}{marks.length > 2 && <span className="td-pin td-pin-more">+{marks.length - 2}</span>}</>
                : <>{marks.slice(0, 3).map((m) => <Target key={m.key} m={m} className="td-pin" label={labelOf(m)} onPick={onPick}><Mono name={m.name ?? "?"} size="xs" logo={m.logo_url} /></Target>)}
                  {marks.length > 3 && <Link className="td-pin td-pin-more" to="/tenders" aria-label={tr("{} more tenders", marks.length - 3)}>+{marks.length - 3}</Link>}</>}
            </span>}
          </li>;
        })}
      </ol>
      {phone && d.week.deadlines.length > 0 && <ul className="td-dl">
        {d.week.deadlines.slice(0, DEADLINES_SHOWN).map((m) => <li key={m.key}>
          <Target m={m} className="td-dl-row" label={labelOf(m)} onPick={onPick}>
            <Mono name={m.name ?? "?"} size="sm" logo={m.logo_url} />
            <span className="td-dl-t"><b>{m.name ?? tr("Unknown buyer")}</b>{m.title && <span>{m.title}</span>}</span>
            <span className="td-dl-d"><b>{day(m.date, { weekday: "short", day: "numeric", month: "short" })}</b><small>{inDays(m.days_left)}</small></span>
          </Target>
        </li>)}
        {d.week.deadlines.length > DEADLINES_SHOWN && <li><Link className="td-dl-all" to="/tenders">{tr("See all {} open tenders", number(d.week.deadlines.length))}</Link></li>}
      </ul>}
    </div>
    <div className="td-counts">
      <div className="td-sec">
        <h2>{sinceLabel}</h2>
        <p>{anyNew ? tr("What came in. Pick a number to see the list.") : tr("Nothing new has come in yet.")}</p>
      </div>
      {anyNew && <ul>
        {tiles.map((t) => {
          const n = d.week.new[t.k] ?? 0;
          return <li key={t.k}>
            {n > 0
              ? <button type="button" className={`td-count td-c-${t.k}`} aria-expanded={openKind === t.k} aria-controls="td-newlist" onClick={() => onKind(t.k)}>
                  <b>{number(n)}</b><span>{t.label}</span></button>
              : <span className="td-count is-zero"><b>0</b><span>{t.label}</span></span>}
          </li>;
        })}
      </ul>}
    </div>
  </section>;
}

function NewList({ d, kind, onPick, onClose }: { d: Brief; kind: NewKind; onPick: (key: string, slot: Slot) => void; onClose: () => void }) {
  const [all, setAll] = useState(false);
  const items = d.new.filter((n) => n.kind === kind);
  const shown = all ? items : items.slice(0, NEW_SHOWN);
  const page = { signal: { to: "/signals", label: tr("Open buying signals") }, tender: { to: "/tenders", label: tr("Open tenders") },
    reply: { to: "/people", label: tr("Open people") }, campaign: { to: "/campaigns", label: tr("Open campaigns") } }[kind];
  const title = { signal: tr("New buying signals"), tender: tr("New tenders"), reply: tr("Replies"), campaign: tr("Campaigns started") }[kind];
  return <section className="td-newlist" id="td-newlist" aria-label={title}>
    <div className="td-newlist-h">
      <h2>{title} <span>{number(items.length)}</span></h2>
      <Link to={page.to}>{page.label}</Link>
      <button type="button" className="td-btn td-btn-quiet" onClick={onClose}>{tr("Close")}</button>
    </div>
    <ol>
      {shown.map((n) => {
        let who: React.ReactNode = n.organization ? <Link to={`/accounts/${n.organization.id}`}>{n.organization.name}</Link> : n.buyer ?? "";
        let text = n.text ?? "";
        if (n.kind === "reply" && n.person) {
          who = <Link to={`/people/${n.person.id}`}>{n.person.name}</Link>;
          text = n.organization ? tr("{} replied to “{}”.", n.organization.name, n.text ?? "") : tr("Replied to “{}”.", n.text ?? "");
        } else if (n.kind === "campaign" && n.campaign) {
          who = <Link to={`/campaigns/${n.campaign.id}`}>{n.campaign.name}</Link>;
          text = tr("Started sending to {}.", counted(n.campaign.people, ["person", "people"], ["osoby", "osób", "osób"]));
        } else if (n.kind === "tender" && !n.organization && safeHref(n.url)) {
          who = <a href={safeHref(n.url)} target="_blank" rel="noopener noreferrer">{n.buyer} ↗</a>;
        }
        return <li key={n.key}>
          <time dateTime={n.at}>{day(n.at, { day: "numeric", month: "short" })}</time>
          <span className="td-newlist-who">{who}</span>
          <span className="td-newlist-t">{text}</span>
          {n.in_list && <button type="button" className="td-onlist" onClick={() => onPick(n.key, n.in_list!)}>
            {n.in_list === "today" ? tr("On today’s list") : tr("In up next")}</button>}
        </li>;
      })}
    </ol>
    {items.length > NEW_SHOWN && <button type="button" className="td-btn td-btn-quiet" aria-expanded={all} onClick={() => setAll((v) => !v)}>
      {all ? tr("Show fewer") : tr("Show all {}", number(items.length))}</button>}
  </section>;
}

/* ------------------------------------------------------------------ the queue */
function Row({ a, rank, selected, onSelect }: { a: Action; rank: number; selected: boolean; onSelect: () => void }) {
  const w = whyOf(a);
  const soon = a.kind === "tender" && (a.days_left ?? 99) <= 7;
  const line = rowLine(a);
  const fit = a.fit?.name;
  return <li>
    <button type="button" id={`td-row-${a.key}`} className={`td-row td-w-${w}${selected ? " is-sel" : ""}`} aria-current={selected ? "true" : undefined}
      onClick={onSelect}>
      <span className="td-rank" aria-hidden="true">{rank}</span>
      <Mono name={nameOf(a)} size="sm" logo={a.organization?.logo_url} />
      <span className="td-row-main">
        <span className="td-row-name">{nameOf(a)}</span>
        <span className="td-row-why">
          <Chip w={w}>{tr(WHY_SHORT[w])}</Chip>
          <span className={`td-row-cue${soon ? " is-soon" : ""}`}>{cue(a)}</span>
        </span>
        {line && <span className="td-row-line">{line}</span>}
        <span className="td-row-who"><span>{rowWho(a)}</span>{fit && <span>{fit}</span>}</span>
      </span>
      <span className="td-row-go" aria-hidden="true">{ICON.chevron}</span>
    </button>
  </li>;
}

function DoneRow({ label, kind, outcome, onUndo }: { label: string; kind: Kind; outcome: Mark; onUndo: () => void }) {
  return <li className={`td-row is-done td-w-${whyOf({ kind, strength: 3 })}`}>
    <span className="td-rank td-rank-done" aria-hidden="true">{ICON.check}</span>
    <Mono name={label} size="sm" />
    <span className="td-row-main">
      <span className="td-row-name">{label}</span>
      <span className="td-row-state">{outcome === "done" ? tr("Done") : tr("Not now, back in 3 days")}</span>
    </span>
    <button type="button" className="td-btn td-btn-quiet td-undo" onClick={onUndo} aria-label={tr("Undo for {}", label)}>{tr("Undo")}</button>
  </li>;
}

/* ------------------------------------------------------------------ the brief for the selected item */
function BriefPanel({ a, rank, total, slot, canCampaign, onMark, onBack }: { a: Action; rank: number; total: number; slot: Slot; canCampaign: boolean;
  onMark: (a: Action, m: Mark) => void; onBack?: () => void }) {
  const org = a.organization;
  const name = nameOf(a);
  const w = whyOf(a);
  const go = primary(a);
  const src = safeHref(a.source?.url);
  const opened = () => { track(a, "opened").catch(() => undefined); };
  const facts = [org?.country ? countryName(org.country) : null, org?.priority === "P1" ? tr("Top priority on your list") : null,
    org?.relationship && RELATIONSHIP[org.relationship] ? tr(RELATIONSHIP[org.relationship]) : null].filter(Boolean) as string[];
  const person = a.person ? { ...a.person, ...(a.contacts.find((c) => c.id === a.person!.id) ?? {}) } as Contact : null;
  const people = person ? [] : a.contacts;
  const more: { label: string; to?: string; href?: string; onPick?: () => void }[] = [];
  if (org && go.to !== `/accounts/${org.id}`) more.push({ label: tr("Open the company page"), to: `/accounts/${org.id}` });
  if (src && go.href !== src) more.push({ label: tr("Read the source"), href: src, onPick: opened });
  if (org && (a.contacts.length > 0 || person)) more.push({ label: tr("Everyone we know at {}", org.name), to: `/accounts/${org.id}?tab=people` });
  if (canCampaign && a.rule_id) more.push({ label: tr("Start a campaign about this"), to: `/campaigns/new?rules=${a.rule_id}` });
  if (a.kind === "tender") more.push({ label: tr("All open tenders"), to: "/tenders" });
  else if (a.kind === "signal") more.push({ label: tr("All buying signals"), to: "/signals" });
  const text = a.kind === "reply" && a.person
    ? tr("{} answered our email from the campaign “{}”.", a.person.name, a.title ?? "")
    : a.kind === "follow_up" && a.person
      ? tr("{} opened our email from the campaign “{}” {} and clicked {}. No answer yet.", a.person.name, a.title ?? "",
        counted(a.engagement?.opens ?? 0, ["time", "times"], ["raz", "razy", "razy"]), counted(a.engagement?.clicks ?? 0, ["time", "times"], ["raz", "razy", "razy"]))
      : a.text;
  const headline = a.title && a.kind !== "reply" && a.kind !== "follow_up" ? a.title.replace(/[.\s]+$/, "") : null;

  const actionBar = <div className="td-b-acts">
    <span className="td-b-main">
      {go.href
        ? <a className="btn primary" href={go.href} target="_blank" rel="noopener noreferrer" onClick={opened}>{go.label} ↗</a>
        : <Link className="btn primary" to={go.to!} onClick={opened}>{go.label}</Link>}
      <MoreMenu items={more} />
    </span>
    <span className="td-b-marks">
      <button type="button" className="td-btn td-done" onClick={() => onMark(a, "done")} aria-keyshortcuts="D">{ICON.check}{tr("Mark done")}</button>
      <button type="button" className="td-btn td-btn-quiet" onClick={() => onMark(a, "later")} aria-keyshortcuts="N">{tr("Not now")}</button>
    </span>
  </div>;

  return <article className={`td-brief td-w-${w}`} aria-labelledby="td-brief-h">
    {onBack && <div className="td-sheet-bar">
      <button type="button" className="td-btn td-btn-quiet td-back" onClick={onBack} autoFocus>{ICON.back}{tr("Back to the list")}</button>
      <span>{tr("{} of {}", rank, total)}</span>
    </div>}
    <div className="td-b-scroll">
      <header className="td-b-head">
        <div className="td-b-top">
          <Chip w={w}>{whyLong(a)}</Chip>
          {!onBack && <span className="td-b-rank">{slot === "today" ? tr("{} of {} today", rank, total) : tr("Up next, number {}", rank)}</span>}
        </div>
        <div className="td-b-id">
          <Mono name={name} size="lg" logo={org?.logo_url} />
          <div>
            <h2 id="td-brief-h">{org ? <Link to={`/accounts/${org.id}`}>{name}</Link> : name}</h2>
            {facts.length > 0 && <p className="td-b-facts">{facts.map((f) => <span key={f}>{f}</span>)}</p>}
          </div>
        </div>
        <p className="td-b-why">{whyReason(a)}</p>
      </header>
      {actionBar}
      <div className="td-b-body">
        {a.kind === "tender" && a.days_left != null && <div className="td-b-clock" role="group" aria-label={tr("Deadline")}>
          <b>{a.days_left <= 0 ? "0" : a.days_left}</b>
          <span>{a.days_left === 1 ? tr("day left") : tr("days left")}<br /><strong>{tr("Deadline {}", day(a.deadline, { weekday: "long", day: "numeric", month: "long" }))}</strong></span>
        </div>}

        <section className="td-b-sec">
          <h3>{tr("What happened")}</h3>
          {(headline || text) && <p className="td-b-text">{headline && <b>{headline}. </b>}{text}</p>}
          {(a.happened || src) && <p className="td-b-src">
            {a.happened && <span>{day(a.happened, { weekday: "short", day: "numeric", month: "long" })}</span>}
            {src && <a href={src} target="_blank" rel="noopener noreferrer" onClick={opened}>{a.source?.name || tr("Original page")} ↗</a>}
            {(a.more ?? 0) > 0 && org && <Link to={`/accounts/${org.id}`}>{tr("{} more news items", a.more)}</Link>}
          </p>}
        </section>

        {a.fit && <section className="td-b-sec td-b-fit">
          <h3>{tr("What to offer")}</h3>
          <p><b>{a.fit.name}</b><span className={`td-fit-l${a.fit.level === "strong" ? " is-strong" : ""}`}>
            {a.fit.level === "strong" ? tr("Strong fit") : tr("Possible fit")}</span></p>
          {a.fit.why && !repeats(a.fit.why, `${a.title ?? ""} ${a.text ?? ""}`) && <p className="td-b-fit-why">{a.fit.why}</p>}
        </section>}

        <section className="td-b-sec">
          <h3>{tr("Who to contact")}</h3>
          {person && <ul className="td-people"><PersonLine c={person} /></ul>}
          {people.length > 0 && <ul className="td-people">{people.map((c, i) => <PersonLine key={c.id} c={c} tag={i === 0 && c.matches ? tr("Matches the role to ask for") : undefined} />)}</ul>}
          {a.who && <p className="td-ask">{tr("The research suggests asking for: {}", a.who)}</p>}
          {!person && people.length === 0 && org && org.people != null && <p className="td-nobody">
            {tr("Nobody at {} is on file yet.", org.name)} <Link to={`/accounts/${org.id}?tab=people`}>{tr("Find people at {}", org.name)}</Link></p>}
          {!person && people.length === 0 && !org && <p className="td-nobody">{tr("A buyer we do not track as an account yet. The tender page names the contact.")}</p>}
        </section>

        {a.opener && <section className={`td-b-sec td-b-open${a.opener_kind === "step" ? " is-step" : ""}`}>
          <div className="td-b-open-h">
            <h3>{a.opener_kind === "step" ? tr("Suggested next step") : tr("Opening line")}</h3>
            {a.opener_kind !== "step" && <CopyButton text={a.opener} />}
          </div>
          <p>{a.opener}</p>
        </section>}
      </div>
    </div>
  </article>;
}

function PersonLine({ c, tag }: { c: Contact; tag?: string }) {
  return <li className={tag ? "is-lead" : undefined}>
    <Face name={c.name} photo={c.photo_url} committee={c.committee} />
    <span className="td-person">
      <Link to={`/people/${c.id}`}>{c.name}</Link>
      {c.title && <span className="td-person-t">{c.title}</span>}
      {tag && <span className="td-person-tag">{tag}</span>}
      {!tag && c.committee && COMMITTEE_LABEL[c.committee] && <span className="td-person-grp"><i style={{ background: COMMITTEE_HUE[c.committee] }} aria-hidden="true" />{tr(COMMITTEE_LABEL[c.committee])}</span>}
    </span>
    {c.has_email !== undefined && <span className={`td-mail${c.has_email ? " on" : ""}`}>{c.has_email ? tr("Email ✓") : tr("No email")}</span>}
  </li>;
}

/* ------------------------------------------------------------------ empty and finished */
function AllDone({ done, queue, onMore }: { done: number; queue: number; onMore: () => void }) {
  return <div className="td-state td-state-done" role="status">
    <span className="td-state-mark" aria-hidden="true">{ICON.check}</span>
    <h2>{tr("All done for today")}</h2>
    <p>{tr("You dealt with {} today. The list fills again tomorrow morning.", counted(done, ["item", "items"], ["sprawę", "sprawy", "spraw"]))}</p>
    {queue > 0 && <button type="button" className="td-btn" onClick={onMore}>{tr("Take the next one from up next")}</button>}
    <p className="td-state-links"><Link to="/signals">{tr("Look through buying signals")}</Link><Link to="/tenders">{tr("See open tenders")}</Link></p>
  </div>;
}

function NothingUrgent({ d }: { d: Brief }) {
  return <div className="td-state">
    <h2>{tr("Nothing urgent today")}</h2>
    <p>{d.next_up.length > 0 ? tr("No tender closes soon, nobody is waiting for an answer and no strong new signal came in. These top-priority companies are a good place to start.")
      : tr("No tender closes soon, nobody is waiting for an answer and no strong new signal came in.")}</p>
    {d.next_up.length > 0 && <ol className="td-nextup">
      {d.next_up.map((x) => <li key={x.organization.id}>
        <Mono name={x.organization.name} size="sm" logo={x.organization.logo_url} />
        <div>
          <Link to={`/accounts/${x.organization.id}`}><b>{x.organization.name}</b></Link>
          <span className="td-person-t">{countryName(x.organization.country)}</span>
          {x.next_step && <p>{tr("Next step from your list: {}", x.next_step)}</p>}
        </div>
      </li>)}
    </ol>}
    <p className="td-state-links"><Link to="/signals">{tr("Look through buying signals")}</Link><Link to="/tenders">{tr("See open tenders")}</Link></p>
  </div>;
}

/* ------------------------------------------------------------------ the page */
export default function Today({ canCampaign = false }: { canReview?: boolean; canCampaign?: boolean }) {
  useLanguage();
  const navigate = useNavigate();
  const [d, setD] = useState<Brief | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const [marks, setMarks] = useState<Record<string, Mark>>({});
  const [undone, setUndone] = useState<Set<string>>(new Set());
  const [markErr, setMarkErr] = useState<unknown>(null);
  const [showNext, setShowNext] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const [newKind, setNewKind] = useState<NewKind | null>(null);
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia?.(PHONE).matches);

  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    api<Brief>("/api/today/brief", { signal: request.signal })
      .then((data) => { if (!request.signal.aborted) { setD(data); setMarks({}); setUndone(new Set()); } })
      .catch((error) => { if (!request.signal.aborted) setErr(error); });
    return () => request.abort();
  }, [revision]);
  useEffect(() => {
    const mq = window.matchMedia?.(PHONE);
    if (!mq) return;
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const actions = d?.actions ?? [];
  const queue = d?.queue ?? [];
  const openToday = actions.filter((a) => !marks[a.key]);
  const openNext = queue.filter((a) => !marks[a.key]);
  const order = [...openToday, ...(showNext ? openNext : [])];
  const serverDone = (d?.done_today ?? []).filter((x) => !undone.has(x.key));
  const doneTodayCount = serverDone.filter((x) => x.slot === "today").length + actions.filter((a) => marks[a.key]).length;
  const total = actions.length + serverDone.filter((x) => x.slot === "today").length;

  // keep a sensible selection: the first open item, and the next one after an item is marked
  const current = [...actions, ...queue].find((a) => a.key === selected && !marks[a.key]) ?? null;
  useEffect(() => {
    if (d && !current && !phone) setSelected(openToday[0]?.key ?? null);
  }, [d, current, phone, openToday]);

  const slotOf = (key: string): Slot => (queue.some((a) => a.key === key) ? "next" : "today");
  const select = useCallback((key: string, slot?: Slot, focus = false) => {
    if (slot === "next") setShowNext(true);
    setSelected(key);
    if (window.matchMedia?.(PHONE).matches) setSheet(true);
    if (focus) window.setTimeout(() => document.getElementById(`td-row-${key}`)?.focus(), 0);
  }, []);

  const onMark = (a: Action, m: Mark) => {
    setMarkErr(null);
    const slot = slotOf(a.key);
    const i = order.findIndex((x) => x.key === a.key);
    const next = order.filter((x) => x.key !== a.key)[Math.min(Math.max(i, 0), order.length - 2)] ?? null;
    setMarks((s) => ({ ...s, [a.key]: m }));
    setSelected(next && (slot === "next" || slotOf(next.key) === "today") ? next.key : null);
    if (!next || phone) setSheet(false);
    track(a, m, slot).catch((e) => {
      setMarkErr(e);
      setMarks((s) => { const x = { ...s }; delete x[a.key]; return x; });
    });
  };
  const onUndoLocal = (a: Action) => {
    setMarks((s) => { const x = { ...s }; delete x[a.key]; return x; });
    track(a, "undo").catch((e) => { setMarkErr(e); setMarks((s) => ({ ...s, [a.key]: marks[a.key] })); });
  };
  const onUndoServer = (x: Done) => {
    setUndone((s) => new Set(s).add(x.key));
    post("/api/today/brief/items", { key: x.key, outcome: "undo" }).then(() => setRevision((n) => n + 1)).catch((e) => {
      setMarkErr(e);
      setUndone((s) => { const n = new Set(s); n.delete(x.key); return n; });
    });
  };

  // keyboard: up and down move through the list, Enter opens, D marks done, N puts aside
  const onMarkRef = useRef(onMark);
  onMarkRef.current = onMark;
  const keyState = useRef({ order, current, phone, sheet });
  keyState.current = { order, current, phone, sheet };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const t = e.target as HTMLElement;
      if (t.closest?.("input, textarea, select, [contenteditable='true'], [role=menu], .nav")) return;
      const row = t.closest?.(".td-row")?.id.replace(/^td-row-/, "") ?? null;
      const free = t === document.body || Boolean(row);
      const { order: list, current: cur, phone: small, sheet: open } = keyState.current;
      const i = list.findIndex((x) => x.key === (row ?? cur?.key));
      if ((e.key === "ArrowDown" || e.key === "j" || e.key === "ArrowUp" || e.key === "k") && free && !open && list.length) {
        e.preventDefault();
        const down = e.key === "ArrowDown" || e.key === "j";
        const n = list[Math.min(list.length - 1, Math.max(0, i + (down ? 1 : -1)))];
        setSelected(n.key);
        if (t !== document.body) document.getElementById(`td-row-${n.key}`)?.focus();
      } else if (e.key === "Enter" && free && cur && (!row || row === cur.key)) {
        e.preventDefault();
        if (small) { setSheet(true); return; }
        const go = primary(cur);
        track(cur, "opened").catch(() => undefined);
        if (go.href) window.open(go.href, "_blank", "noopener,noreferrer"); else navigate(go.to!);
      } else if ((e.key === "d" || e.key === "D" || e.key === "n" || e.key === "N") && cur && (free || Boolean(t.closest?.(".td-brief")))
        && !t.closest?.("a, button:not(.td-row)")) {
        e.preventDefault();
        onMarkRef.current(cur, e.key.toLowerCase() === "d" ? "done" : "later");
      } else if (e.key === "Escape" && open) {
        setSheet(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  // the phone sheet: lock the page behind it, and bring focus back to the row on close
  useEffect(() => {
    if (!sheet || !phone) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const key = selected;
    return () => { document.body.style.overflow = prev; if (key) document.getElementById(`td-row-${key}`)?.focus(); };
  }, [sheet, phone, selected]);

  const sinceLabel = d ? (d.last_visit
    ? tr("New since {}", (daysSince(d.since) ?? 0) < 6 ? new Date(d.since).toLocaleDateString(loc(), { weekday: "long" }) : day(d.since, { day: "numeric", month: "short" }))
    : tr("New in the last 7 days")) : "";
  const segments = [
    ...serverDone.filter((x) => x.slot === "today").map((x) => ({ key: x.key, w: whyOf({ kind: x.kind, strength: 3 }), done: true, label: x.label ?? "" })),
    ...actions.map((a) => ({ key: a.key, w: whyOf(a), done: Boolean(marks[a.key]), label: nameOf(a) })),
  ];
  const rankOf = (a: Action) => {
    const i = actions.findIndex((x) => x.key === a.key);
    return i >= 0 ? i + 1 + serverDone.filter((x) => x.slot === "today").length : total + queue.findIndex((x) => x.key === a.key) + 1;
  };
  const extraDone = serverDone.filter((x) => x.slot === "next");

  // on a phone the list comes first (it is the job of the page); the deadlines and what is new follow it
  const weekEl = d ? <>
    <Week d={d} sinceLabel={sinceLabel} openKind={newKind} phone={phone} onKind={(k) => setNewKind((v) => (v === k ? null : k))} onPick={(k, s) => select(k, s, true)} />
    {newKind && <NewList d={d} kind={newKind} onPick={(k, s) => select(k, s, true)} onClose={() => setNewKind(null)} />}
  </> : null;

  return <div className="td">
    <Header name={d?.name ?? null} segments={segments} done={doneTodayCount} total={total}
      next={openToday[0] ? nameOf(openToday[0]) : null} />
    <div className="page td-body">
      {err ? <Err e={err} onRetry={() => setRevision((n) => n + 1)} /> : !d ? <Skeleton rows={8} label={tr("Loading today's list")} /> : <>
        {!phone && weekEl}
        <Err e={markErr} />
        <div className="td-work">
          {!(phone && actions.length === 0 && serverDone.length === 0 && queue.length === 0) && <section className="td-queue" aria-labelledby="td-queue-h">
            <div className="td-q-head">
              <h2 id="td-queue-h">{tr("Today’s list")}</h2>
              <span>{openToday.length ? tr("{} left", openToday.length) : total ? tr("All done") : ""}</span>
            </div>
            <p className="td-note">{tr("Most urgent first: replies, close deadlines, then the strongest fresh news.")}</p>
            {total === 0 && actions.length === 0
              ? <p className="td-q-empty">{tr("Nothing on the list today.")}</p>
              : <ol className="td-q">
                  {serverDone.filter((x) => x.slot === "today").map((x) => <DoneRow key={x.key} label={x.label ?? tr("Item")} kind={x.kind} outcome={x.outcome} onUndo={() => onUndoServer(x)} />)}
                  {actions.map((a) => marks[a.key]
                    ? <DoneRow key={a.key} label={nameOf(a)} kind={a.kind} outcome={marks[a.key]} onUndo={() => onUndoLocal(a)} />
                    : <Row key={a.key} a={a} rank={rankOf(a)} selected={current?.key === a.key} onSelect={() => select(a.key)} />)}
                </ol>}
            {(queue.length > 0 || extraDone.length > 0) && <div className="td-next">
              <button type="button" className="td-next-t" aria-expanded={showNext} aria-controls="td-next-list" onClick={() => setShowNext((v) => !v)}>
                {ICON.chevron}<span>{tr("Up next")}</span><small>{tr("{} more, if you have time", openNext.length)}</small>
              </button>
              {showNext && <ol className="td-q td-q-next" id="td-next-list">
                {extraDone.map((x) => <DoneRow key={x.key} label={x.label ?? tr("Item")} kind={x.kind} outcome={x.outcome} onUndo={() => onUndoServer(x)} />)}
                {queue.map((a) => marks[a.key]
                  ? <DoneRow key={a.key} label={nameOf(a)} kind={a.kind} outcome={marks[a.key]} onUndo={() => onUndoLocal(a)} />
                  : <Row key={a.key} a={a} rank={rankOf(a)} selected={current?.key === a.key} onSelect={() => select(a.key)} />)}
              </ol>}
              {showNext && d.more_actions > queue.length && <p className="td-note">
                {tr("{} more companies have news from the last three weeks.", number(d.more_actions - queue.length))} <Link to="/signals">{tr("See buying signals")}</Link></p>}
            </div>}
            {order.length > 1 && <p className="td-keys" aria-hidden="true">
              <kbd>↑</kbd><kbd>↓</kbd> {tr("move")} <kbd>Enter</kbd> {tr("open")} <kbd>D</kbd> {tr("done")} <kbd>N</kbd> {tr("not now")}</p>}
          </section>}

          <div className={`td-detail${current ? " has-item" : ""}${sheet && current ? " is-open" : ""}`}
            {...(phone && current && sheet ? { role: "dialog", "aria-modal": true, "aria-labelledby": "td-brief-h" } : {})}>
            {current
              ? <BriefPanel key={current.key} a={current} rank={rankOf(current)} total={total} slot={slotOf(current.key)} canCampaign={canCampaign}
                  onMark={onMark} onBack={phone ? () => setSheet(false) : undefined} />
              : actions.length === 0 && serverDone.length === 0
                ? <NothingUrgent d={d} />
                : openToday.length === 0
                  ? <AllDone done={doneTodayCount} queue={openNext.length} onMore={() => { if (openNext[0]) select(openNext[0].key, "next", true); }} />
                  : null}
          </div>
        </div>
        {phone && weekEl}
      </>}
    </div>
  </div>;
}
