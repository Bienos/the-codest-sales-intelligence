import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post } from "../api";
import { counted, getLanguage, number, tr, useLanguage } from "../locale";
import { Err, Skeleton, safeHref } from "../components/ui";
import { countryName } from "./targetMarket";
import "../styles/today.css";

/** Today: who to contact today, and why. One ranked list of things to do, what is new since the last visit, and four numbers. */

type Org = { id: string; name: string; country: string | null; region: string; priority: string | null; relationship: string | null;
  people: number | null; people_with_email: number | null };
type Kind = "reply" | "tender" | "signal";
type Action = {
  key: string; kind: Kind; organization: Org | null; buyer?: string | null; title?: string | null; text?: string | null;
  happened?: string | null; deadline?: string | null; days_left?: number | null; strength: number;
  source?: { name: string | null; url: string | null } | null; who?: string | null; opener?: string | null; opener_kind?: "line" | "step";
  rule_id?: string | null; more?: number; person?: { id: string; name: string; title: string | null } | null;
};
type NewItem = {
  key: string; kind: "signal" | "tender" | "reply" | "campaign"; at: string; organization?: Org | null; buyer?: string | null; text?: string | null;
  url?: string | null; deadline?: string | null; person?: { id: string; name: string; title: string | null } | null;
  campaign?: { id: string; name: string; people: number } | null;
};
type Brief = {
  generated_at: string; name: string | null; since: string; last_visit: string | null; actions: Action[]; queue: Action[]; more_actions: number;
  hidden: number; new: NewItem[]; new_total: number; next_up: { organization: Org; next_step: string | null; signal: string | null }[];
  glance: { targets: number; with_news: number; people_with_email: number | null; accounts_with_email: number | null; campaigns_live: number; open_tenders: number };
};
type Mark = "done" | "later";

const NEW_SHOWN = 6;
const RELATIONSHIP: Record<string, string> = { client: "Client", past_client: "Past client", in_talks: "In talks" };

const Svg = ({ children }: { children: React.ReactNode }) => (
  <svg className="td-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="square" aria-hidden="true">{children}</svg>
);
const KIND_ICON: Record<string, React.ReactNode> = {
  reply: <Svg><path d="M4 5h16v11H9l-5 4z" /></Svg>,
  tender: <Svg><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 13h7M9 17h4" /></Svg>,
  signal: <Svg><path d="M2 12h4l3-7 5 14 3-7h5" /></Svg>,
  campaign: <Svg><path d="M3 10v4h3l6 4V6L6 10z" /><path d="M16 9a4 4 0 010 6" /></Svg>,
};

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
function shortDate(v?: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(getLanguage(), { day: "numeric", month: "short" });
}
function longDate(v?: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(getLanguage(), { weekday: "short", day: "numeric", month: "short" });
}
function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? tr("Good morning") : h < 18 ? tr("Good afternoon") : tr("Good evening");
}
const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name;

/** The short "why today" line on each card. */
function whyNow(a: Action): string {
  if (a.kind === "reply") return tr("Replied {}", ago(a.happened));
  if (a.kind === "tender" && a.days_left != null) {
    return a.days_left <= 0 ? tr("Tender closes today") : a.days_left === 1 ? tr("Tender closes tomorrow") : tr("Tender closes in {} days", a.days_left);
  }
  const when = ago(a.happened);
  return a.strength >= 3 ? tr("Strong signal · {}", when) : tr("New signal · {}", when);
}

/** The one next step for this item: answer the reply, read the tender, or go to the people at the company. */
function primary(a: Action): { label: string; to?: string; href?: string } {
  const org = a.organization;
  if (a.kind === "reply" && a.person) return { label: tr("Answer {}", firstName(a.person.name)), to: `/people/${a.person.id}` };
  if (a.kind === "tender") {
    const href = safeHref(a.source?.url);
    if (href) return { label: tr("Look at the tender"), href };
    return org ? { label: tr("Open company"), to: `/accounts/${org.id}` } : { label: tr("See open tenders"), to: "/tenders" };
  }
  if (!org) return { label: tr("See open tenders"), to: "/tenders" };
  if (org.people_with_email == null) return { label: tr("Open company"), to: `/accounts/${org.id}` };
  return org.people_with_email > 0
    ? { label: tr("See who to write to"), to: `/accounts/${org.id}?tab=people` }
    : { label: tr("Find people to contact"), to: `/accounts/${org.id}?tab=people` };
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1800); }).catch(() => undefined);
  };
  return <button type="button" className="td-copy" onClick={copy} aria-live="polite">{copied ? tr("Copied") : tr("Copy")}</button>;
}

function track(a: Action, outcome: "opened" | Mark | "undo") {
  return post("/api/today/brief/items", { key: a.key, outcome, organization_id: a.organization?.id ?? null });
}

function ActionCard({ a, n, canCampaign, mark, onMark }: { a: Action; n: number; canCampaign: boolean; mark?: Mark; onMark: (a: Action, m: Mark | null) => void }) {
  const org = a.organization;
  const name = org?.name ?? a.buyer ?? tr("Unknown buyer");
  const id = `td-item-${n}`;
  if (mark) {
    return <li className="td-item td-marked">
      <span className="td-n" aria-hidden="true">✓</span>
      <p className="td-marked-text" role="status">
        <b>{name}</b>{" · "}{mark === "done" ? tr("Marked done. It stays off this list for 30 days.") : tr("Put aside. It comes back in 3 days.")}
        {" "}<button type="button" className="td-link" onClick={() => onMark(a, null)}>{tr("Undo")}</button>
      </p>
    </li>;
  }
  const go = primary(a);
  const facts = [org?.country ? countryName(org.country) : null, org?.priority === "P1" ? tr("Top priority on your list") : null,
    org?.relationship && RELATIONSHIP[org.relationship] ? tr(RELATIONSHIP[org.relationship]) : null].filter(Boolean);
  const src = a.source?.url ? safeHref(a.source.url) : undefined;
  const text = a.kind === "reply" && a.person
    ? tr("{} answered our email from the campaign “{}”. Reply while the conversation is warm.", a.person.name + (a.person.title ? ` (${a.person.title})` : ""), a.title ?? "")
    : a.text;
  const showCompany = org && go.to !== `/accounts/${org.id}`;
  return <li className={`td-item td-${a.kind}`}>
    <span className="td-n" aria-hidden="true">{String(n).padStart(2, "0")}</span>
    <article className="td-card" aria-labelledby={id}>
      <div className="td-card-top">
        <span className="td-n-in" aria-hidden="true">{String(n).padStart(2, "0")}</span>
        <span className={`td-why td-why-${a.kind}${a.kind === "tender" && (a.days_left ?? 99) <= 7 ? " soon" : ""}`}>{KIND_ICON[a.kind]}{whyNow(a)}</span>
        {a.kind === "tender" && a.deadline
          ? <span className="td-date">{tr("Deadline")} <b>{longDate(a.deadline)}</b></span>
          : a.happened ? <span className="td-date">{longDate(a.happened)}</span> : null}
      </div>
      <h3 id={id} className="td-co">{org ? <Link to={`/accounts/${org.id}`}>{name}</Link> : name}</h3>
      {facts.length > 0 && <div className="td-facts">{facts.join(" · ")}</div>}
      {(a.title || text) && <p className="td-text">{a.title && a.kind !== "reply" && <b>{a.title.replace(/[.\s]+$/, "")}. </b>}{text}</p>}
      {(src || a.who) && <p className="td-meta">
        {src && <span>{tr("Source:")} <a href={src} target="_blank" rel="noopener noreferrer" onClick={() => { track(a, "opened").catch(() => undefined); }}>{a.source?.name || tr("original page")} ↗</a></span>}
        {a.who && <span>{tr("Ask for:")} <b>{a.who}</b></span>}
      </p>}
      {a.opener && <div className={`td-open${a.opener_kind === "step" ? " step" : ""}`}>
        <span className="td-open-k">{a.opener_kind === "step" ? tr("Suggested next step") : tr("Opening line you could use")}</span>
        <p>{a.opener}</p>
        {a.opener_kind !== "step" && <CopyLine text={a.opener} />}
      </div>}
      <div className="td-acts">
        {go.href
          ? <a className="btn primary" href={go.href} target="_blank" rel="noopener noreferrer" onClick={() => { track(a, "opened").catch(() => undefined); }}>{go.label} ↗</a>
          : <Link className="btn primary" to={go.to!} onClick={() => { track(a, "opened").catch(() => undefined); }}>{go.label}</Link>}
        {showCompany && <Link className="td-link" to={`/accounts/${org!.id}`}>{tr("Open company")}</Link>}
        {canCampaign && a.rule_id && <Link className="td-link" to={`/campaigns/new?rules=${a.rule_id}`}>{tr("Start a campaign")}</Link>}
        <span className="td-markers">
          <button type="button" className="td-mark" onClick={() => onMark(a, "done")} aria-label={tr("Mark {} as done", name)}>{tr("Mark done")}</button>
          <button type="button" className="td-mark" onClick={() => onMark(a, "later")} aria-label={tr("Put {} aside for 3 days", name)}>{tr("Not now")}</button>
        </span>
      </div>
    </article>
  </li>;
}

function NewLine({ n }: { n: NewItem }) {
  const label = { signal: tr("Buying signal"), tender: tr("New tender"), reply: tr("Reply"), campaign: tr("Campaign started") }[n.kind];
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
    if (n.deadline) text = `${text} · ${tr("closes {}", shortDate(n.deadline))}`;
  }
  return <li className={`td-f td-f-${n.kind}`}>
    <time dateTime={n.at}>{shortDate(n.at)}</time>
    <div>
      <div className="td-f-head"><span className="td-f-k">{KIND_ICON[n.kind]}{label}</span>{who}</div>
      {text && <p>{text}</p>}
    </div>
  </li>;
}

function Glance({ g }: { g: Brief["glance"] }) {
  const tiles: { to: string; n: React.ReactNode; label: string }[] = [
    { to: "/accounts", n: <>{number(g.with_news)}<small> / {number(g.targets)}</small></>, label: tr("target accounts with news this month") },
    ...(g.people_with_email != null ? [{ to: "/people", n: number(g.people_with_email),
      label: tr("people you can email, at {}", counted(g.accounts_with_email ?? 0, ["account", "accounts"], ["firmie", "firmach", "firmach"])) }] : []),
    { to: "/tenders", n: number(g.open_tenders), label: tr("open tenders from our sectors") },
    { to: "/campaigns", n: number(g.campaigns_live), label: tr("campaigns sending now") },
  ];
  return <section className="td-glance" aria-labelledby="td-glance-h">
    <h2 id="td-glance-h">{tr("Your pipeline at a glance")}</h2>
    <p className="td-note">{tr("Four numbers to keep an eye on. Each opens the full list.")}</p>
    <ul>{tiles.map((t) => <li key={t.to}><Link to={t.to}><b>{t.n}</b><span>{t.label}</span></Link></li>)}</ul>
  </section>;
}

function NothingUrgent({ d }: { d: Brief }) {
  return <div className="td-empty">
    <p className="td-empty-t">{tr("Nothing urgent today.")}</p>
    <p>{d.hidden > 0
      ? tr("You have dealt with everything on today's list. Here is what to look at next.")
      : tr("No tender closes soon, nobody is waiting for an answer and no strong new signal came in. Here is what to look at next.")}</p>
    {d.next_up.length > 0 && <ol className="td-next">
      {d.next_up.map((x) => <li key={x.organization.id}>
        <Link to={`/accounts/${x.organization.id}`}><b>{x.organization.name}</b></Link>
        <span className="td-facts">{[countryName(x.organization.country), x.organization.priority === "P1" ? tr("Top priority on your list") : null].filter(Boolean).join(" · ")}</span>
        {x.next_step && <p><span className="td-open-k">{tr("Next step from your list")}</span>{x.next_step}</p>}
      </li>)}
    </ol>}
    <p className="td-empty-links"><Link to="/signals">{tr("Look through buying signals")} →</Link> <Link to="/tenders">{tr("See open tenders")} →</Link></p>
  </div>;
}

export default function Today({ canCampaign = false }: { canReview?: boolean; canCampaign?: boolean }) {
  useLanguage();
  const [d, setD] = useState<Brief | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const [marks, setMarks] = useState<Record<string, Mark>>({});
  const [markErr, setMarkErr] = useState<unknown>(null);
  const [showQueue, setShowQueue] = useState(false);
  const [allNew, setAllNew] = useState(false);
  useEffect(() => {
    const request = new AbortController();
    setErr(null);
    api<Brief>("/api/today/brief", { signal: request.signal })
      .then((data) => { if (!request.signal.aborted) setD(data); })
      .catch((error) => { if (!request.signal.aborted) setErr(error); });
    return () => request.abort();
  }, [revision]);

  const onMark = (a: Action, m: Mark | null) => {
    setMarkErr(null);
    setMarks((s) => { const next = { ...s }; if (m) next[a.key] = m; else delete next[a.key]; return next; });
    track(a, m ?? "undo").catch((e) => {
      setMarkErr(e);
      setMarks((s) => { const next = { ...s }; if (m) delete next[a.key]; else next[a.key] = marks[a.key]; return next; });
    });
  };

  const today = new Date().toLocaleDateString(getLanguage(), { weekday: "long", day: "numeric", month: "long" });
  const open = d ? d.actions.filter((a) => !marks[a.key]) : [];
  const first = open[0];
  const sinceLabel = d ? (d.last_visit
    ? tr("New since {}", (daysSince(d.since) ?? 0) < 6 ? new Date(d.since).toLocaleDateString(getLanguage(), { weekday: "long" }) : shortDate(d.since))
    : tr("New in the last 7 days")) : "";
  const newShown = d ? (allNew ? d.new : d.new.slice(0, NEW_SHOWN)) : [];

  return <div className="td">
    <header className="td-hero">
      <div className="page td-hero-in">
        <div>
          <div className="eyebrow">{today}</div>
          <h1>{d?.name ? `${greeting()}, ${d.name}` : greeting()}</h1>
          <p className="td-purpose">{tr("This page tells you who to contact today, and why. Start at the top.")}</p>
        </div>
        {d && <p className="td-headline" aria-live="polite">
          {open.length
            ? <>{counted(open.length, ["thing to do today", "things to do today"], ["sprawa na dziś", "sprawy na dziś", "spraw na dziś"])}.<br />
                <span>{tr("Start with {}.", first.organization?.name ?? first.buyer ?? "")}</span></>
            : <>{tr("A quiet day.")}<br /><span>{tr("Nothing urgent. See below for what to look at next.")}</span></>}
        </p>}
      </div>
    </header>
    <div className="page td-body">
      {err ? <Err e={err} onRetry={() => setRevision((n) => n + 1)} /> : !d ? <Skeleton rows={6} label={tr("Loading today's list")} /> :
        <div className="td-grid">
          <section className="td-do" aria-labelledby="td-do-h">
            <h2 id="td-do-h">{tr("Do today")}</h2>
            <p className="td-note">{tr("Who to contact, why now, and the first step. Most urgent first: replies, then tenders about to close, then the freshest strong signals. Mark each one done when you have acted on it.")}</p>
            <Err e={markErr} />
            {d.actions.length === 0 ? <NothingUrgent d={d} /> : <>
              <ol className="td-list">
                {d.actions.map((a, i) => <ActionCard key={a.key} a={a} n={i + 1} canCampaign={canCampaign} mark={marks[a.key]} onMark={onMark} />)}
                {showQueue && d.queue.map((a, i) => <ActionCard key={a.key} a={a} n={d.actions.length + i + 1} canCampaign={canCampaign} mark={marks[a.key]} onMark={onMark} />)}
              </ol>
              {open.length === 0 && <p className="td-alldone" role="status">{tr("All done for today. Well done.")}</p>}
              {d.queue.length > 0 && !showQueue && <button type="button" className="td-more" onClick={() => setShowQueue(true)} aria-expanded={false}>
                {tr("Show the next {}", d.queue.length)}</button>}
              {showQueue && d.more_actions > d.queue.length && <p className="td-note">
                {tr("{} more companies have news from the last three weeks.", number(d.more_actions - d.queue.length))}{" "}
                <Link to="/accounts">{tr("See all accounts")} →</Link></p>}
            </>}
          </section>
          <aside className="td-side">
            <section className="td-new" aria-labelledby="td-new-h">
              <h2 id="td-new-h">{sinceLabel}</h2>
              <p className="td-note">{tr("Other news that came in since then, newest first. Open a company to read more.")}</p>
              {d.new.length === 0
                ? <p className="td-quiet">{tr("Nothing else is new since then.")} <Link to="/signals">{tr("Look through buying signals")} →</Link></p>
                : <ol className="td-feed">{newShown.map((n) => <NewLine key={n.key} n={n} />)}</ol>}
              {d.new.length > NEW_SHOWN && <button type="button" className="td-more" onClick={() => setAllNew((v) => !v)} aria-expanded={allNew}>
                {allNew ? tr("Show fewer") : tr("Show all {}", number(d.new.length))}</button>}
              {allNew && d.new_total > d.new.length && <p className="td-note">{tr("{} older items are on the buying signals page.", number(d.new_total - d.new.length))}</p>}
            </section>
            <Glance g={d.glance} />
          </aside>
        </div>}
    </div>
  </div>;
}
