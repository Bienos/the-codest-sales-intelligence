import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, fmtDateTime, post } from "../api";
import { number, tr } from "../locale";
import { Err, Skeleton, Strength, reloadPage, safeHref } from "../components/ui";
import { SHORT } from "../components/Signals";
import { Avatar, COMMITTEE, COMMITTEE_ICON, COMMITTEE_ORDER, Cite, DateTile, EmailMark, Ic, Monogram, ResearchPanel, ResearchState, Sec, fmtDay, host, initials, usePoll } from "./AccountDetail";
import { countryName, staffCount } from "./targetMarket";
import { Conversations } from "./Connections";
import { EmailThread } from "../components/Replies";
import "../styles/account.css";
import "../styles/person.css";

const KIND: Record<string, [string, typeof Ic.mail]> = { talk: ["Talk", Ic.mic], post: ["Post", Ic.chat], article: ["Article", Ic.doc], interview: ["Interview", Ic.chat],
  podcast: ["Podcast", Ic.headphones], news: ["News", Ic.news], appointment: ["Appointment", Ic.badge], award: ["Award", Ic.star], other: ["Other", Ic.spark] };
const ACTIVITY: Record<string, string> = { emailsSent: "Email sent", emailsOpened: "Opened an email", emailsClicked: "Clicked a link", emailsReplied: "Replied", emailsBounced: "Email bounced",
  emailsUnsubscribed: "Unsubscribed", emailsInterested: "Marked interested", emailsNotInterested: "Marked not interested", interested: "Marked interested", notInterested: "Marked not interested" };
const REL: Record<string, string> = { none: "No relationship", prospect: "Prospect", in_talks: "In talks", past_client: "Past client", client: "Client" };
/** Our services, by the key the fit engine sends. Anything else is read from its wording. */
const SVC: Record<string, typeof Ic.mail> = { team_extension: Ic.teamExt, staff_augmentation: Ic.teamExt, delivery_team: Ic.delivery, ai_data: Ic.data, ai_services: Ic.data, qa: Ic.qa };
const svcIcon = (s?: string | null): typeof Ic.mail => SVC[s ?? ""] ?? (/ai|data/i.test(s ?? "") ? Ic.data : /qa|test/i.test(s ?? "") ? Ic.qa : /deliver/i.test(s ?? "") ? Ic.delivery : Ic.teamExt);
const SVC_LABEL: Record<string, string> = { delivery_team: "Delivery team", team_extension: "Team extension", ai_data: "AI and data", qa: "QA and testing" };
const isUrl = (u?: string | null) => /^https?:\/\//i.test(String(u ?? "").trim());

/** A few line icons this page needs that the shared set does not have (same drawing rules). */
const L = (d: ReactNode) => ({ className = "" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={`ico ${className}`}>{d}</svg>
);
const Lc = {
  copy: L(<><rect x="8" y="8" width="12" height="12" /><path d="M16 8V4H4v12h4" /></>),
  ask: L(<><path d="M4 5h16v11H9l-5 4z" /><path d="M10 9.2a2 2 0 013.9.6c0 1.2-1.9 1.5-1.9 2.6M12 14v.4" /></>),
  eye: L(<><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>),
  go: L(<path d="M5 12h13M13 7l5 5-5 5" />),
  out: L(<path d="M10 5H5v14h14v-5M13 4h7v7M20 4l-9 9" />),
  cal: L(<><rect x="3" y="5" width="18" height="16" /><path d="M3 10h18M8 3v4M16 3v4" /></>),
};

/** What GET /api/people/{id}/brief returns: a short, sourced brief for one person. */
type Opener = { text: string; because: string; source_url?: string | null; date?: string | null };
type Fact = { label: string; value: string; source?: string | null; source_url?: string | null };
type Brief = { lens: { role?: string | null; cares_about: string[]; committee?: string | null; committee_key?: string | null };
  best_fit?: { service?: string | null; label: string; why?: string | null; level?: string | null; score?: number | null } | null;
  openers: Opener[]; questions: string[]; facts: Fact[]; gaps: string[]; avoid: string[]; company?: { id: string; name: string; best?: string | null; why_now?: string | null } | null };
/** Facts the masthead already shows; the brief's ledger leaves them out so nothing is said twice. */
const HERO_FACTS = new Set(["Role", "Seniority", "Priority", "Email", "Our contact so far"]);
const LEVEL: Record<string, [string, number]> = { strong: ["Strong fit", 3], possible: ["Possible fit", 2], weak: ["Weak fit", 1] };
/** A lead-list note offered as an opener often repeats the dated company evidence already in an opener above it: show that line once. */
const plain = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, "");
const dedupe = (xs: Opener[]) => xs.filter((o, i) => {
  if (o.source_url) return true;
  const note = plain(o.text.slice(o.text.indexOf(": ") + 2)).slice(0, 80);
  return note.length < 40 || !xs.slice(0, i).some((p) => plain(p.text).includes(note));
});

/** True when one line only says again what another already says (the fit reason often quotes the news the opener is built on). */
const core = (t: string) => t.toLowerCase().replace(/\s*\([^)]*\)\s*$/, "").replace(/[^\p{L}\p{N} ]/gu, "").replace(/\s+/g, " ").trim().slice(0, 48);
const repeats = (a: string, others: string[]) => { const x = core(a); return x.length > 12 && others.some((o) => core(o).includes(x) || x.includes(core(o)) && core(o).length > 12); };

type Role = { title: string; company?: string | null; logo?: string | null; from?: number | null; to?: number | null; current?: boolean; cite?: any };
const yearOf = (v: any): number | null => { const m = /\b(19|20)\d{2}\b/.exec(String(v ?? "")); return m ? Number(m[0]) : null; };

/** The career as bars on a shared year axis (a real sequence, so a timeline). Roles without years are listed underneath. */
function CareerPath({ roles }: { roles: Role[] }) {
  const now = new Date().getFullYear();
  const dated = roles.filter((r) => r.from);
  const undated = roles.filter((r) => !r.from);
  const min = dated.length ? Math.min(...dated.map((r) => r.from!)) : now;
  const max = now + 1, span = Math.max(1, max - min);
  const step = [1, 2, 5, 10].find((x) => span / x <= 5) ?? 10;           // at most five year labels
  const ticks: number[] = [];
  for (let y = Math.ceil(min / step) * step; y <= now; y += step) ticks.push(y);
  const pos = (y: number) => `${((y - min) / span) * 100}%`;
  const years = (r: Role) => (r.from ? `${r.from}–${r.current ? tr("now") : r.to ?? "?"}` : "");
  return <div className="career">
    {dated.length > 0 && <div className="cp" role="list" aria-label={tr("Career path")}>
      {dated.map((r, i) => { const end = r.current ? max : (r.to ?? r.from! + 1); return <div key={i} className={`cp-row${r.current ? " now" : ""}`} role="listitem">
        <div className="cp-label"><Monogram name={r.company || r.title} size="sm" logo={r.logo} /><div><b>{r.title}</b><span>{[r.company, years(r)].filter(Boolean).join(", ")}</span>{r.cite && <Cite c={r.cite} />}</div></div>
        <div className="cp-track" aria-hidden="true"><span className={`cp-bar${r.to || r.current ? "" : " open"}`} style={{ left: pos(r.from!), width: `max(6px, calc(${pos(Math.max(end, r.from! + 0.5))} - ${pos(r.from!)}))` }} title={`${r.title}${r.company ? `, ${r.company}` : ""}: ${years(r)}`} /></div>
      </div>; })}
      <div className="cp-axis" aria-hidden="true"><div className="cp-ticks">{ticks.map((y) => <span key={y} style={{ left: pos(y) }}>{y}</span>)}</div></div>
    </div>}
    {undated.length > 0 && <ul className="cp-undated">{undated.map((r, i) => <li key={i}><Monogram name={r.company || r.title} size="xs" logo={r.logo} /><span><b>{r.title}</b>{r.company ? `, ${r.company}` : ""}{r.current ? ` (${tr("now")})` : ""}</span>{r.cite && <Cite c={r.cite} />}</li>)}</ul>}
  </div>;
}

/** Copies a line to the clipboard and says so (also to screen readers). */
function CopyButton({ text, what }: { text: string; what: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); } catch {
      const t = document.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } finally { t.remove(); }
    }
    setDone(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setDone(false), 2200);
  };
  return <>
    <button type="button" className={`sb-copy${done ? " done" : ""}`} onClick={copy} aria-label={tr("Copy {}", what)}>
      {done ? <Ic.check /> : <Lc.copy />}<span aria-hidden="true">{done ? tr("Copied") : tr("Copy")}</span>
    </button>
    <span className="ac-sr" role="status">{done ? tr("Copied") : ""}</span>
  </>;
}

/** Where a fact came from: a link when it is a page, otherwise the name of our own record. */
function Source({ s, url }: { s?: string | null; url?: string | null }) {
  const u = url ?? (isUrl(s) ? s : null);
  const href = u ? safeHref(u) : undefined;
  if (href) return <a className="ac-cite" href={href} target="_blank" rel="noreferrer noopener" title={s && !isUrl(s) ? tr(s) : undefined}>{host(u)} ↗</a>;
  return s ? <span className="ac-cite rec">{tr(s)}</span> : null;
}

/** The sales brief: what this person cares about, what to offer, how to open, what to ask, and what we know against what we still need. */
function SalesBrief({ b, first, err, retry, extraGaps, title, researched, seen }: { b: Brief | null; first: string; err: unknown; retry: () => void; extraGaps: string[];
  title?: string | null; researched: boolean; seen: string[] }) {
  const head = <header className="sb-head">
    <span className="sb-ic" aria-hidden="true"><Ic.target /></span>
    <div><h2 id="sb-h">{tr("Sales brief")}</h2>
      <p className="ac-purpose">{tr("What matters to {}, which of our services to lead with, and how to start. Built from your lead list, our signals and research.", first)}</p></div>
  </header>;
  if (!b) return <section className="sb" aria-labelledby="sb-h" aria-busy={!err}>{head}
    <div className="sb-body">{err ? <Err e={err} onRetry={retry} /> : <Skeleton rows={4} label={tr("Writing the brief")} />}</div></section>;
  // "not researched yet" is said once, by the public profile section below; it is not repeated as an open question
  const gaps = [...b.gaps, ...extraGaps.filter((x) => !b.gaps.some((y) => y.toLowerCase() === x.toLowerCase()))].filter((x) => researched || !/not researched this person/i.test(x));
  // the masthead already shows the role and the part in the buying committee: only a different reading of the role is worth a line here
  const lensRole = b.lens.role && b.lens.role.trim().toLowerCase() !== (title ?? "").trim().toLowerCase() ? b.lens.role : null;
  const facts = b.facts.filter((f) => !HERO_FACTS.has(f.label));
  const openers = dedupe(b.openers);
  const fit = b.best_fit;
  const Fit = svcIcon(fit?.service ?? fit?.label);
  const level = fit?.level ? LEVEL[fit.level] : undefined;
  return <section className="sb" aria-labelledby="sb-h">{head}
    <div className="sb-top">
      <div className="sb-lens">
        <h3>{tr("What matters to {}", first)}</h3>
        {lensRole && <p className="sb-role"><span>{lensRole}</span></p>}
        {b.lens.cares_about.length > 0 ? <ul className="sb-cares">{b.lens.cares_about.map((x, i) => <li key={i}>{tr(x)}</li>)}</ul>
          : <p className="small m-0">{tr("Not enough known about the role yet.")}</p>}
      </div>
      <div className="sb-fit">
        <h3>{tr("Best fit from The Codest")}</h3>
        {fit ? <>
          <p className="sb-svc"><span className="sb-svc-ic" aria-hidden="true"><Fit /></span>{tr(fit.label)}</p>
          {level && <p className="sb-level"><Strength n={level[1]} />{tr(level[0])}</p>}
          {fit.why && !repeats(fit.why, seen) && <p className="sb-why">{tr(fit.why)}</p>}
        </> : <p className="sb-why">{tr("No clear fit yet. Ask about their engineering plans first.")}</p>}
      </div>
    </div>

    <div className="sb-part">
      <h3><Ic.quote />{tr("Ways to open")}</h3>
      <p className="ac-purpose">{tr("Each one rests on something you can point to. Copy one and make it your own.")}</p>
      {openers.length ? <ol className="sb-open">{openers.map((o, i) => <li key={i}>
        <span className="sb-n" aria-hidden="true">{i + 1}</span>
        <div className="sb-o">
          <p className="sb-line">{o.text}</p>
          <div className="sb-ev">
            {o.date && <span className="sb-date"><Lc.cal />{fmtDay(o.date)}</span>}
            {o.source_url && safeHref(o.source_url) ? <a className="ac-cite" href={safeHref(o.source_url)} target="_blank" rel="noreferrer noopener">{host(o.source_url)} ↗</a>
              : <span className="ac-cite rec">{/lead list/i.test(o.because) ? tr("Your lead list") : tr("Our records")}</span>}
            {o.because && <span className="sb-because">{tr(o.because)}</span>}
            <CopyButton text={o.text} what={tr("opener {}", i + 1)} />
          </div>
        </div>
      </li>)}</ol> : <p className="small m-0">{tr("No dated reason to get in touch yet. Research with Exa or a new signal will add one.")}</p>}
    </div>

    <div className="sb-cols">
      <div className="sb-part">
        <h3><Lc.ask />{tr("Questions to ask")}</h3>
        <p className="ac-purpose">{tr("To find the need, the budget and who decides.")}</p>
        {b.questions.length ? <ol className="sb-q">{b.questions.map((q, i) => <li key={i}>{tr(q)}</li>)}</ol> : <p className="small m-0">{tr("None yet.")}</p>}
      </div>
      <div className="sb-part sb-avoid-part">
        <h3><Ic.alert />{tr("Avoid")}</h3>
        <p className="ac-purpose">{tr("What is likely to put {} off.", first)}</p>
        {b.avoid.length ? <ul className="sb-avoid">{b.avoid.map((a, i) => <li key={i}><Ic.cross />{tr(a)}</li>)}</ul> : <p className="small m-0">{tr("Nothing in particular.")}</p>}
      </div>
    </div>

    <div className="sb-cols sb-ledger">
      <div className="sb-part">
        <h3><Ic.check />{tr("What we know")}</h3>
        <p className="ac-purpose">{tr("Facts we hold, each with where it came from.")}</p>
        {facts.length ? <dl className="sb-facts">{facts.map((f, i) => <div key={i}><dt>{tr(f.label)}</dt><dd><span>{f.value}</span><Source s={f.source} url={f.source_url} /></dd></div>)}</dl>
          : <p className="small m-0">{tr("Nothing beyond what the top of the page shows.")}</p>}
      </div>
      <div className="sb-part">
        <h3><Lc.eye />{tr("Still to find out")}</h3>
        <p className="ac-purpose">{tr("Ask or check these before you propose anything.")}</p>
        {gaps.length ? <ul className="sb-gaps">{gaps.map((x, i) => <li key={i}>{tr(x)}</li>)}</ul> : <p className="small m-0">{tr("No open questions.")}</p>}
      </div>
    </div>
  </section>;
}

/** The company's strongest current reason to call: from the company page's reasons first, else the leading checked signal. */
function strongest(acc: any, company: any): { title: string; tag?: string; detail?: string | null; date?: string | null; url?: string | null; strength?: number; deadline?: string | null } | null {
  const r = acc?.why_now?.reasons?.[0];
  if (r) {
    if (r.kind === "lead_list") return { title: r.detail || r.text, tag: r.detail ? r.text : tr("From your lead list"), date: r.date, url: r.url, strength: r.strength };
    if (r.kind === "tender") return { title: r.text, tag: tr("Open tender"), date: r.date, url: r.url, strength: r.strength, deadline: r.deadline };
    if (r.kind === "news") return { title: r.text, tag: tr("In the news"), detail: r.detail, date: r.date, url: r.url, strength: r.strength };
    return { title: r.text, tag: r.rule_id && SHORT[r.rule_id] ? tr(SHORT[r.rule_id]) : tr("Buying signal"), detail: r.detail, date: r.date, url: r.url, strength: r.strength };
  }
  const s = company?.signal;
  if (s) return { title: s.summary || s.label, tag: s.rule_id && SHORT[s.rule_id] ? tr(SHORT[s.rule_id]) : s.label, date: s.date, url: s.source_url };
  return null;
}

type LinkRow = { to?: string; href?: string; icon: typeof Ic.mail; label: string; detail?: string };
function LinkList({ rows, label }: { rows: LinkRow[]; label: string }) {
  if (!rows.length) return null;
  return <>
    <h3 className="ps-links-h">{label}</h3>
    <ul className="ps-links">{rows.map((l, i) => {
      const inner = <><span className="ps-link-ic" aria-hidden="true"><l.icon /></span>
        <span className="ps-link-t"><b>{l.label}</b>{l.detail && <small>{l.detail}</small>}</span>
        <span className="ps-link-go" aria-hidden="true">{l.to ? <Lc.go /> : <Lc.out />}</span></>;
      return <li key={i}>{l.to ? <Link className="ps-link" to={l.to}>{inner}</Link>
        : <a className="ps-link" href={l.href} target="_blank" rel="noreferrer noopener">{inner}<span className="ac-sr"> {tr("(opens in a new tab)")}</span></a>}</li>;
    })}</ul>
  </>;
}

/** The company card in the rail: who they are, how they sit with us, the strongest reason to call now, and every useful link. */
function CompanyCard({ co, company, acc, ctx, cres, best, others }: { co: any; company: any; acc: any; ctx: any[]; cres: any; best?: string | null; others: any[] }) {
  const a = acc?.account;
  const lp: Record<string, string> = acc?.list_profile ?? {};
  const sig = strongest(acc, company);
  const size = company?.employees != null ? staffCount(company.employees, company.employees_uncertain) : null;
  const prio = company?.list_priority ?? a?.list_priority;
  const score = a?.list_score;
  const rel = company?.relationship;
  const domain = co.domain || a?.canonical_domain;
  const linkedIn = [...ctx.filter((x) => x.kind === "link").map((x) => x.value), ...((cres?.profile?.sources ?? []).map((s: any) => s.url))].find((u: string) => /linkedin\.com\/company\//i.test(String(u)));
  const supplier = isUrl(lp["Procurement Route"]) ? lp["Procurement Route"] : null;
  const sources: LinkRow[] = [];
  const addSource = (u: string | null | undefined, label: string, icon: typeof Ic.mail) => {
    const h = isUrl(u) ? safeHref(u) : undefined;
    if (!h || sources.length >= 3 || (supplier && h === safeHref(supplier)) || sources.some((x) => x.href === h)) return;
    sources.push({ href: h, icon, label, detail: host(u) });
  };
  addSource(sig?.url, tr("Source of the signal"), Ic.news);
  for (const s of acc?.list_signals ?? []) addSource(s.source_url, s.category || tr("Lead list source"), Ic.doc);
  const here: LinkRow[] = [
    { to: `/accounts/${co.id}`, icon: Ic.building, label: tr("Company page"), detail: tr("Fit, signals, research and outreach") },
    { to: `/accounts/${co.id}?tab=people`, icon: Ic.people, label: tr("People at {}", co.name), detail: tr("Everyone we know there, by buying committee") },
  ];
  const web: LinkRow[] = [
    ...(domain ? [{ href: `https://${domain}`, icon: Ic.globe, label: tr("Website"), detail: domain }] : []),
    linkedIn && safeHref(linkedIn) ? { href: safeHref(linkedIn), icon: Ic.linkedin, label: tr("LinkedIn"), detail: tr("Company page on LinkedIn") }
      : { href: `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(co.name)}`, icon: Ic.linkedin, label: tr("Find on LinkedIn"), detail: tr("Search for the company page") },
    ...(supplier && safeHref(supplier) ? [{ href: safeHref(supplier), icon: Ic.key, label: tr("Supplier registration"), detail: host(supplier) }] : []),
    ...sources,
  ];
  const Fit = svcIcon(best);
  return <section className="ps-co" aria-labelledby="ps-co-h">
    <div className="ps-co-top" data-mark={initials(co.name)}>
      <span className="ps-co-eyebrow">{tr("Works at")}</span>
      <div className="ps-co-id">
        <Monogram name={co.name} size="md" framed logo={co.logo_url ?? company?.logo_url} />
        <div className="ps-co-name">
          <h2 id="ps-co-h"><Link to={`/accounts/${co.id}`}>{co.name}</Link></h2>
          <p>{[co.segment, countryName(co.country), size].filter(Boolean).map((x, i) => <span key={i}>{x}</span>)}</p>
        </div>
      </div>
      <div className="ps-co-chips">
        <span className={`ps-chip prio${prio ? ` ${String(prio).toLowerCase()}` : " none"}`}>{prio ? <><b>{prio}</b>{tr("Lead list")}{score != null ? ` · ${tr("score {}", score)}` : ""}</> : tr("Not on your lead list")}</span>
        <span className={`rel-badge on-navy ${rel ?? "unset"}`}><i aria-hidden="true" />{rel ? tr(REL[rel] ?? rel) : tr("Relationship not recorded")}</span>
        {best && <span className="ps-chip ps-svc" title={tr("Best fit for the company")}><Fit /><span className="ac-sr">{tr("Best fit for the company")}: </span>{tr(SVC_LABEL[best] ?? best)}</span>}
      </div>
    </div>
    <div className="ps-co-body">
      <div className="ps-co-sig">
        <h3>{tr("Strongest buying signal")}</h3>
        {sig ? <div className="ps-sig">
          <DateTile d={sig.date} />
          <div className="ps-sig-b">
            <span className="ps-sig-tag">{sig.tag}{sig.strength ? <Strength n={sig.strength} /> : null}</span>
            <b>{sig.title}</b>
            {sig.detail && <span>{sig.detail}</span>}
            {sig.deadline && <span className="ps-sig-dl">{tr("Deadline {}", fmtDate(sig.deadline))}</span>}
            {sig.url && safeHref(sig.url) && <a className="ac-cite" href={safeHref(sig.url)} target="_blank" rel="noreferrer noopener">{host(sig.url)} ↗</a>}
          </div>
        </div> : <p className="small m-0">{tr("No current signal for {}. The company page shows when one arrives.", co.name)}</p>}
      </div>
      {company?.about && <p className="ps-co-about">{company.about}</p>}
      <LinkList rows={here} label={tr("In this workspace")} />
      <LinkList rows={web} label={tr("On the web")} />
      {others.length > 0 && <>
        <h3 className="ps-links-h">{tr("Other roles we hold")}</h3>
        <ul className="ps-roles">{others.map((j: any, i: number) => <li key={i}><Monogram name={j.company.name} size="xs" logo={j.company.logo_url} /><span>{j.title ?? tr("Role not recorded")}, <Link to={`/accounts/${j.company.id}`}>{j.company.name}</Link>{j.current ? "" : ` (${tr("past")})`}</span></li>)}</ul>
      </>}
    </div>
  </section>;
}

/** Colleagues at the same company, grouped by their part in the buying decision, so gaps in the committee show at a glance. */
function Colleagues({ co, people, me }: { co: any; people: any[]; me: { name: string; committee: string } }) {
  const grp = (x: any) => (COMMITTEE[x.committee] ? x.committee : "influencer");
  const groups = COMMITTEE_ORDER.map((g) => [g, people.filter((x) => grp(x) === g)] as [string, any[]]);
  return <section className="ac-panel ps-team" aria-labelledby="ps-team-h">
    <h2 id="ps-team-h">{tr("Who else to know at {}", co.name)}</h2>
    <p className="ac-purpose">{tr("Colleagues on your list, by their part in the buying decision. An empty square is a gap to fill.")}</p>
    <ul className="ps-cov" aria-label={tr("Buying committee coverage")}>
      {groups.map(([g, xs]) => { const Icon = COMMITTEE_ICON[g]; const n = xs.length, self = me.committee === g;
        return <li key={g} className={n || self ? "" : "gap"} style={{ "--g": COMMITTEE[g].color } as React.CSSProperties} title={tr(COMMITTEE[g].hint)}>
          <Icon /><b>{number(n)}</b><span>{tr(COMMITTEE[g].label)}{self && <span className="ac-sr"> {tr("(not counting {})", me.name)}</span>}</span></li>; })}
    </ul>
    {groups.filter(([, xs]) => xs.length).map(([g, xs]) => <div key={g} className="ps-grp" style={{ "--g": COMMITTEE[g].color } as React.CSSProperties}>
      <h3>{tr(COMMITTEE[g].label)}<span className="ps-grp-n">{xs.length}</span></h3>
      <ul className="ps-ppl">{xs.slice(0, 4).map((x: any) => <li key={x.id} className={x.suppressed ? "off" : ""}>
        <Avatar name={x.name} committee={x.committee} size="sm" photo={x.photo_url} />
        <span className="ps-ppl-who"><Link to={`/people/${x.id}`}>{x.name}</Link><span>{x.title || tr("Role not recorded")}</span></span>
        {x.suppressed ? <span className="ps-dnc">{tr("Do not contact")}</span>
          : x.has_email ? <span className="ps-mail" title={tr("Email ✓")}><Ic.mail /><span className="ac-sr">{tr("Email ✓")}</span></span> : null}
      </li>)}</ul>
      {xs.length > 4 && <p className="ps-more">{tr("and {} more", xs.length - 4)}</p>}
    </div>)}
    <Link className="ps-all" to={`/accounts/${co.id}?tab=people`}>{tr("Everyone at {}, by buying committee", co.name)}<Lc.go /></Link>
  </section>;
}

/** Before any research: one calm invitation that says what it would add. */
function ResearchInvite({ r, first, canRun, start, busy, err }: { r: any; first: string; canRun: boolean; start: () => void; busy: boolean; err: unknown }) {
  return <section className="ac-sheet ps-pub" aria-labelledby="ps-pub-h">
    <div className="ps-pub-head"><h2 id="ps-pub-h">{tr("Public profile")}</h2>
      <p className="ac-purpose m-0">{tr("What public pages say about {}: career, focus areas and recent talks or posts, each with its source.", first)}</p></div>
    <div className="ri-invite ps-invite">
      <span className="ri-ic" aria-hidden="true"><Ic.spark /></span>
      <div><p className="m-0"><b>{tr("Not researched yet.")}</b> {tr("Exa reads the LinkedIn profile, public talks, articles and news that name them. Professional information only; a few US cents from the monthly Exa budget.")}</p>
        {!r.setup?.ready && <p className="small muted m-0 mt-1">{tr("Research is not switched on yet: an administrator can turn it on.")}</p>}
        <Err e={err} /></div>
      {canRun && <button type="button" disabled={busy || !r.setup?.ready} onClick={start}>{tr("Research with Exa")}</button>}
    </div>
  </section>;
}

/** One person, everything we hold about them: a sales brief first, then the public profile, with their company and colleagues in the rail. */
export default function Person() {
  const { id } = useParams();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [rErr, setRErr] = useState<unknown>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [bErr, setBErr] = useState<unknown>(null);
  const [acc, setAcc] = useState<any>(null);
  const [ctx, setCtx] = useState<any[]>([]);
  const [cres, setCres] = useState<any>(null);
  const [team, setTeam] = useState<any[] | null>(null);
  const load = () => api(`/api/people/${id}/profile`).then(setD).catch(setErr);
  const loadBrief = () => { setBErr(null); return api<Brief>(`/api/people/${id}/brief`).then(setBrief).catch(setBErr); };
  useEffect(() => { setD(null); setErr(null); setBrief(null); load(); loadBrief(); }, [id]);
  usePoll(Boolean(d?.research?.running), load, 5000);
  const orgId: string | undefined = d?.employment?.[0]?.company?.id;
  useEffect(() => {
    setAcc(null); setCtx([]); setCres(null); setTeam(null);
    if (!orgId) return;
    const q = new AbortController();
    api(`/api/accounts/${orgId}?target=true`, { signal: q.signal }).then((x) => { if (!q.signal.aborted) setAcc(x); }).catch(() => undefined);
    api(`/api/accounts/${orgId}/context`, { signal: q.signal }).then((x) => { if (!q.signal.aborted) setCtx(x.items ?? []); }).catch(() => undefined);
    api(`/api/accounts/${orgId}/research`, { signal: q.signal }).then((x) => { if (!q.signal.aborted) setCres(x); }).catch(() => undefined);
    // everyone at the company (the profile carries only the first twelve), so the committee counts are exact
    api(`/api/accounts/${orgId}/people`, { signal: q.signal }).then((x) => { if (!q.signal.aborted) setTeam(x.items ?? null); }).catch(() => undefined);
    return () => q.abort();
  }, [orgId]);
  // a new profile from research changes the brief: read it again (quietly, the old one stays meanwhile)
  const profileId: string | null | undefined = d ? d.research?.profile?.id ?? null : undefined;
  const seen = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (profileId === undefined) return;
    if (seen.current !== undefined && seen.current !== profileId) void loadBrief();
    seen.current = profileId;
  }, [profileId]);
  useEffect(() => { seen.current = undefined; }, [id]);

  if (!d) return <>
    <p className="small mt-0"><Link to="/people">{tr("People")}</Link></p><h1 className="title-sm">{tr("Person")}</h1>
    {err ? <Err e={err} onRetry={reloadPage} /> : <Skeleton rows={4} label={tr("Loading the person")} />}
  </>;
  const p = d.person;
  const first = String(p.name).split(/\s+/)[0] || p.name;
  const job = d.employment[0];
  const co = job?.company;
  const others = d.employment.slice(1);
  const ll = d.lead_list ?? {};
  const r = d.research;
  const c = r?.profile?.content;
  const g = COMMITTEE[p.committee] ? p.committee : "influencer";
  const GIcon = COMMITTEE_ICON[g];
  const start = async () => { setRErr(null); setBusy(true); try { await post(`/api/people/${id}/research`); await load(); } catch (e) { setRErr(e); } finally { setBusy(false); } };
  const out = d.outreach;
  const touches = [...out.enrolments.map((e: any) => ({ when: e.last_event_at ?? e.created_at, what: tr("{}: {}", e.campaign, tr(e.status === "pushed" ? "added to the campaign" : e.status.replace(/_/g, " "))) })),
    ...out.activity.map((a: any) => ({ when: a.happened_at, what: `${tr(ACTIVITY[a.type] ?? a.type)}${a.campaign_name ? ` (${a.campaign_name})` : ""}` }))]
    .sort((x, y) => String(y.when ?? "").localeCompare(String(x.when ?? "")));
  const title = c?.current_role?.title ?? job?.title;
  /** A past employer that is also a company on our list shows its logo. */
  const logoOf = (name?: string | null): string | null => { const k = String(name ?? "").trim().toLowerCase(); return k ? d.employment.find((j: any) => String(j.company?.name ?? "").trim().toLowerCase() === k)?.company?.logo_url ?? null : null; };
  const roles: Role[] = c ? [
    ...(c.current_role ? [{ title: c.current_role.title, company: co?.name, logo: co?.logo_url ?? d.company?.logo_url, from: c.current_role.since_year ?? yearOf(c.current_role.since), current: true, cite: c.current_role }] : []),
    ...(c.previous_roles ?? []).map((x: any) => ({ title: x.title, company: x.company, logo: logoOf(x.company), from: x.from ?? yearOf(x.period), to: x.to ?? yearOf(String(x.period ?? "").split(/[-–]/)[1]), cite: x })),
  ] : [];
  const colleagues: any[] = team ? team.filter((x) => x.id !== p.id) : d.colleagues ?? [];
  const company = d.company;
  const prio = ll.priority as string | undefined;
  const lastTouch = touches[0]?.when;
  const researchBusy = Boolean(r?.running) || r?.latest?.status === "failed";
  return <>
    <header className="ac-hero ps-hero" data-mark={initials(p.name)}>
      <nav className="ac-crumb ps-crumb" aria-label={tr("Breadcrumb")}>
        {co ? <><Link className="ps-crumb-far" to="/accounts">{tr("Accounts")}</Link><span className="ps-crumb-far" aria-hidden="true">/</span><Link to={`/accounts/${co.id}`}>{co.name}</Link><span className="ps-crumb-far" aria-hidden="true">/</span>
          <Link className="ps-crumb-far" to={`/accounts/${co.id}?tab=people`}>{tr("People")}</Link></> : <Link to="/people">{tr("People")}</Link>}
        <span aria-hidden="true">/</span><span aria-current="page">{p.name}</span>
      </nav>
      <div className="ps-id">
        <span className="ps-avatar"><Avatar name={p.name} committee={g} size="lg" photo={p.photo_url} /></span>
        <div className="ps-name">
          <h1>{p.name}</h1>
          <p className="ps-role">{title ?? tr("Role not recorded")}{co && <> {tr("at")} <Link className="ps-co-link" to={`/accounts/${co.id}`}><Monogram name={co.name} size="xs" logo={co.logo_url ?? company?.logo_url} />{co.name}</Link></>}</p>
          {c?.summary?.text && <p className="ps-summary">{c.summary.text}</p>}
          {p.suppressed && <p className="ps-warn">{tr("Do not contact.")} {p.suppression_reason ?? ""}</p>}
        </div>
        <p className="ps-tags"><EmailMark has={Boolean(p.has_email)} onNavy /></p>
        <div className="ps-acts">
          {p.linkedin_url && safeHref(p.linkedin_url) ? <a className="btn ps-li" href={safeHref(p.linkedin_url)} target="_blank" rel="noreferrer noopener"><Ic.linkedin />{tr("LinkedIn profile")}</a>
            : <a className="btn ps-li" href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${p.name} ${co?.name ?? ""}`)}`} target="_blank" rel="noreferrer noopener"><Ic.linkedin />{tr("Find on LinkedIn")}</a>}
          {d.can_research && <button type="button" className={c ? "" : "primary"} disabled={busy || r.running || !r.setup?.ready} title={r.setup?.ready ? undefined : tr("Research with Exa is not set up in this environment.")} onClick={start}>
            {r.running ? tr("Researching…") : c ? tr("Research again") : tr("Research with Exa")}</button>}
        </div>
      </div>
      <ul className="ps-glance" aria-label={tr("At a glance")}>
        <li><span className="ps-gi tone" style={{ "--g": COMMITTEE[g].color } as React.CSSProperties} aria-hidden="true"><GIcon /></span>
          <span><small>{tr("Buying committee")}</small><b>{tr(COMMITTEE[g].one)}</b>{ll.committee_role && <em>{tr(ll.committee_role)}</em>}</span></li>
        <li><span className="ps-gi" aria-hidden="true"><Ic.badge /></span>
          <span><small>{tr("Seniority")}</small><b>{p.seniority ? tr(p.seniority) : tr("Not known")}</b>{ll.persona && <em>{tr(ll.persona)}</em>}</span></li>
        <li><span className={`ps-gi${prio ? ` prio ${prio.toLowerCase()}` : ""}`} aria-hidden="true">{prio ? <b>{prio}</b> : <Ic.list />}</span>
          <span><small>{tr("Your lead list")}</small><b>{prio ? tr("Priority {}", prio.replace(/^P/i, "")) : tr("Not on the list")}</b>{ll.eligibility && <em>{tr(ll.eligibility)}</em>}</span></li>
        <li><span className="ps-gi" aria-hidden="true"><Ic.mail /></span>
          <span><small>{tr("Outreach")}</small><b>{tr(out.engagement_label)}</b><em>{lastTouch ? tr("Last activity {}", fmtDate(lastTouch)) : tr("Never contacted from this platform.")}</em></span></li>
      </ul>
    </header>
    <div className="ac-grid mt-4">
      <div className="ps-main">
        {c?.identity && !c.identity.confident && !c.empty && <p className="ps-check"><Ic.alert />{tr("Check first: the pages may be about someone else with the same name.")} {c.identity.note ?? ""}</p>}
        <SalesBrief b={brief} first={first} err={bErr} retry={() => void loadBrief()} extraGaps={c?.open_questions ?? []} title={title} researched={Boolean(r.profile)}
          seen={[strongest(acc, company)?.title, company?.signal?.summary, ...(brief?.openers ?? []).map((o) => o.text)].filter(Boolean) as string[]} />
        <EmailThread personId={p.id} name={p.name} />
        <Conversations scope="person" id={p.id} name={p.name} />
        {researchBusy && <ResearchState r={r} kind="person" name={p.name} canRun={d.can_research} start={start} busy={busy} err={rErr} />}
        {!r.profile && !researchBusy && <ResearchInvite r={r} first={first} canRun={d.can_research} start={start} busy={busy} err={rErr} />}
        {c && <section className="ac-sheet ps-pub" aria-labelledby="ps-pub-h">
          <div className="ps-pub-head"><h2 id="ps-pub-h">{tr("Public profile")}</h2>
            <p className="ac-purpose m-0">{tr("What public pages say about {}, each line with its source. Researched {}.", first, fmtDate(r.profile.finished_at))}</p></div>
          {c.empty ? <Sec title="Research" id="ps-empty"><p className="m-0">{tr(c.empty)}</p></Sec> : <>
            <Sec title="Role & remit" id="ps-role">
              {c.current_role ? <>
                <p className="ac-lead">{c.current_role.title}{c.current_role.since ? <span className="small"> {tr("since {}", c.current_role.since)}</span> : null}</p>
                {c.current_role.remit && <p>{c.current_role.remit} <Cite c={c.current_role} /></p>}
              </> : <p className="m-0">{job ? tr("On record: {} at {}.", job.title ?? tr("no title"), co.name) : tr("Not linked to a company.")} {tr("The pages did not describe the current role.")}</p>}
            </Sec>
            {roles.length > 0 && <Sec title="Career path" id="ps-career"><CareerPath roles={roles} /></Sec>}
            {c.focus_areas?.length > 0 && <Sec title="Focus areas" id="ps-focus"><ul className="ac-tags">{c.focus_areas.map((f: any, i: number) => <li key={i} className="ac-tag focus" title={f.source_title ?? undefined}>{f.label}</li>)}</ul></Sec>}
            <Sec title="Recent public activity" hint="Talks, articles, posts and news" id="ps-act">
              {c.recent_activity?.length ? <ul className="act-grid">{c.recent_activity.map((x: any, i: number) => { const [label, KIcon] = KIND[x.kind] ?? KIND.other; return <li key={i} className="act-card">
                <DateTile d={x.date} />
                <div><span className="act-kind"><KIcon />{tr(label)}</span><b>{x.title}</b>{x.detail && <p>{x.detail}</p>}<Cite c={x} /></div></li>; })}</ul>
                : <p className="small m-0">{tr("Exa found no public talks, articles or news about this person in the last two years.")}</p>}
            </Sec>
          </>}
        </section>}
      </div>
      <aside className="ac-rail" aria-label={tr("Company, colleagues and outreach")}>
        {co && <CompanyCard co={co} company={company} acc={acc} ctx={ctx} cres={cres} best={brief?.company?.id === co.id ? brief?.company?.best : null} others={others} />}
        {co && colleagues.length > 0 && <Colleagues co={co} people={colleagues} me={{ name: p.name, committee: g }} />}
        <section className="ac-panel" aria-labelledby="ps-out"><h2 id="ps-out">{tr("Outreach")}</h2>
          <p className="ac-purpose">{tr("What we have sent {} from this platform, and how they responded.", first)}</p>
          {touches.length === 0 ? <p className="small m-0">{tr("Never contacted from this platform.")}</p>
            : <ul className="ps-log">{touches.slice(0, 15).map((t, i) => <li key={i}><span>{t.what}</span><span className="when">{t.when ? fmtDate(t.when) : ""}</span></li>)}</ul>}
          {out.campaigns.length > 0 && <><h3 className="ps-links-h">{tr("Signal campaigns")}</h3>
            <ul className="tight small mb-0">{out.campaigns.map((m: any) => <li key={m.id}><Link to={`/campaigns/${m.id}`}>{m.name}</Link>{!m.included && m.exclusion ? `: ${tr("left out")}, ${m.exclusion}` : ""}</li>)}</ul></>}
        </section>
        <ResearchPanel r={r} canRun={false} start={start} busy={busy} err={rErr} />
        <p className="ac-note m-0">{tr("Added {}. Every look at this page is logged.", fmtDateTime(p.created_at))}</p>
      </aside>
    </div>
  </>;
}
