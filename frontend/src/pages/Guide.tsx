import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { number, tr } from "../locale";
import { IconAccounts, IconCampaign, IconFeed, IconPeople, IconTender, IconToday } from "../components/ui";
import "../styles/guide.css";

/* "How it works": a plain-English explainer the sales lead can show a colleague or a manager.
   Hero diagram (the market in, a call list out), six illustrated steps, a day with the app, costs and limits, words, questions. */

type Can = (permission: string) => boolean;
type Ico = (p: { className?: string }) => JSX.Element;

/** Same line style as the shared icons in components/ui.tsx. */
const ico = (d: ReactNode): Ico => (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>{d}</svg>
);
const IconJobs = ico(<><rect x="3" y="7" width="18" height="13" /><path d="M9 7V4h6v3M3 12h18" /></>);
const IconWeb = ico(<><rect x="3" y="4" width="18" height="16" /><path d="M3 9h18M6.5 6.5h.01M9.5 6.5h.01" /></>);
const IconSignal = ico(<><path d="M3 12h4l3-7 4 14 3-7h4" /></>);

/** A link when the viewer may open the page, otherwise the page name as plain text. */
function To({ to, perm, can, children }: { to: string; perm: string; can: Can; children: ReactNode }) {
  return !perm || can(perm) ? <Link to={to}>{children}</Link> : <span className="hw-nolink">{children}</span>;
}

/* ------------------------------------------------------------------ hero diagram */

function Machine() {
  const sources: [string, Ico][] = [["News sites", IconFeed], ["Tenders", IconTender], ["Job ads", IconJobs], ["Company websites", IconWeb]];
  const calls: [string, string][] = [
    ["A bank in London", "Hiring 12 payments engineers"],
    ["An insurer in Munich", "Open tender for a testing partner"],
    ["A fintech in Warsaw", "Raised money to build a new app"],
  ];
  return (
    <figure className="hw-machine" aria-label={tr("Diagram: news, tenders, job ads and company websites go into the app. The app reads, matches and checks them. Out comes a short list of companies to call, each with a reason.")}>
      <ul className="hw-feed" aria-label={tr("What the app reads")}>
        {sources.map(([label, Icon]) => <li key={label}><span className="hw-src"><Icon />{tr(label)}</span></li>)}
      </ul>
      <div className="hw-wires" aria-hidden="true">{sources.map((_, i) => <i key={i} className="hw-wire" style={{ ["--d" as string]: `${i * 0.7}s` }} />)}</div>
      <i className="hw-down" aria-hidden="true" />
      <div className="hw-core">
        <span className="hw-core-t">{tr("The app")}</span>
        <ol>{["Reads", "Matches", "Checks"].map((w, i) => <li key={w} style={{ ["--i" as string]: i }}>{tr(w)}</li>)}</ol>
      </div>
      <div className="hw-wires hw-wires-out" aria-hidden="true">{calls.map((_, i) => <i key={i} className="hw-wire" style={{ ["--d" as string]: `${i * 2 + 0.4}s` }} />)}</div>
      <i className="hw-down" aria-hidden="true" />
      <ol className="hw-calls" aria-label={tr("Who to call, and why (example)")}>
        {calls.map(([who, why], i) => <li key={who} style={{ ["--i" as string]: i }}>
          <span className="hw-call"><span className="hw-call-k">{tr("Call them")}</span><b>{tr(who)}</b><span>{tr(why)}</span></span>
        </li>)}
      </ol>
      <figcaption>{tr("An example morning. In the app you see real company names and the page behind each reason.")}</figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------ step illustrations (240 x 140, purple line work, one pink highlight) */

const Art = ({ label, children }: { label: string; children: ReactNode }) => (
  <svg className="hw-ill" viewBox="0 0 240 140" role="img" aria-label={tr(label)} focusable="false">{children}</svg>
);

const ArtWatch = () => <Art label="Four kinds of page, news, a tender, a job ad and a company website, with a line scanning across them.">
  <path className="ln" d="M100 22 Q120 6 140 22 Q120 38 100 22Z" /><rect className="nv" x="116" y="18" width="8" height="8" />
  <g>{/* news */}<rect className="ln wt" x="14" y="50" width="44" height="66" /><rect className="nv" x="20" y="57" width="32" height="7" /><path className="ln" d="M20 74h32M20 82h32M20 90h24M20 98h32M20 106h18" /></g>
  <g>{/* tender */}<path className="ln wt" d="M70 50h32l12 12v54H70z" /><path className="ln" d="M102 50v12h12M76 76h30M76 84h30M76 92h22" /><rect className="nv" x="98" y="100" width="10" height="10" /></g>
  <g>{/* job ad */}<rect className="ln wt" x="126" y="50" width="44" height="66" /><rect className="ln" x="141" y="58" width="14" height="14" /><path className="ln" d="M136 86q12-12 24 0M134 96h28M134 104h20" /></g>
  <g>{/* website */}<rect className="ln wt" x="182" y="50" width="44" height="66" /><path className="ln" d="M182 60h44" /><rect className="nv" x="186" y="53" width="4" height="4" /><rect className="nv" x="193" y="53" width="4" height="4" /><path className="ln" d="M188 70h32M188 78h32M188 86h20" /><rect className="pu" x="188" y="94" width="32" height="14" /></g>
  <g className="a-scan"><rect className="pk" x="117" y="44" width="3" height="78" /><rect className="pk soft" x="120" y="44" width="10" height="78" /></g>
</Art>;

const ArtMatch = () => <Art label="One news story connected by a line to one company in a list of five, which lights up.">
  <rect className="ln wt" x="14" y="32" width="64" height="78" /><rect className="nv" x="22" y="41" width="48" height="8" /><path className="ln" d="M22 60h48M22 68h48M22 76h36M22 84h48M22 92h28" />
  {[0, 1, 2, 3, 4].map((i) => { const y = 18 + i * 22; const on = i === 2;
    return <g key={i} className={on ? "a-pop" : undefined}><rect className={on ? "nv" : "ln wt"} x="150" y={y} width="12" height="15" /><path className={on ? "ln-pk" : "ln"} d={`M170 ${y + 8}h${on ? 56 : 40 + (i % 2) * 14}`} /></g>; })}
  <path className="ln a-draw" pathLength={1} d="M78 64 C108 64 112 80 142 80" /><path className="ln" d="M136 75l6 5-6 5" />
</Art>;

const ArtCheck = () => <Art label="Three items drop into a funnel with three filters. Two fall out to the sides. One comes out at the bottom with a tick.">
  <rect className="nv" x="94" y="8" width="9" height="9" /><rect className="pk" x="116" y="8" width="9" height="9" /><rect className="nv" x="138" y="8" width="9" height="9" />
  <path className="ln wt" d="M58 28H182L140 84V108H100V84Z" /><path className="ln dash" d="M78 44H162M90 58H150M102 72H138" />
  <path className="ln a-draw" pathLength={1} d="M64 34 L36 92" /><path className="ln a-draw" pathLength={1} d="M176 34 L204 92" />
  <g className="a-pop"><rect className="nv" x="26" y="96" width="18" height="18" /><path className="ln-wt" d="M30 100l10 10M40 100l-10 10" /></g>
  <g className="a-pop"><rect className="nv" x="196" y="96" width="18" height="18" /><path className="ln-wt" d="M200 100l10 10M210 100l-10 10" /></g>
  <g className="a-pop"><rect className="pk" x="110" y="114" width="20" height="20" /><path className="ln-wt" d="M114 124l4 4 8-9" /></g>
</Art>;

const ArtResearch = () => <Art label="A company profile filling in line by line, with a magnifying glass over a person.">
  <rect className="ln wt" x="18" y="16" width="130" height="108" /><rect className="nv" x="30" y="28" width="14" height="18" /><rect className="nv" x="52" y="30" width="62" height="7" /><rect className="pu" x="52" y="41" width="38" height="4" />
  {[[62, 132], [78, 118], [94, 136], [110, 108]].map(([y, x2]) => <g key={y}><rect className="pu" x="30" y={y - 3} width="6" height="6" /><path className="ln a-grow" d={`M42 ${y}H${x2}`} /></g>)}
  <circle className="ln-thick wt-soft" cx="170" cy="76" r="27" /><path className="ln-nv" d="M190 96L214 120" />
  <g className="a-pop"><circle className="pk" cx="170" cy="70" r="7" /><path className="ln-pk" d="M158 90q12-14 24 0" /></g>
</Art>;

const ArtReach = () => <Art label="A draft email passes a manager's tick and lands in the Lemlist outbox, waiting to be launched.">
  <rect className="ln wt" x="12" y="38" width="46" height="62" /><path className="ln" d="M20 50h30M20 58h30M20 66h22M20 74h30M20 82h16" />
  <path className="ln dash" d="M62 70H100M140 70H182" /><circle className="ln wt" cx="120" cy="70" r="17" /><path className="ln-nv" d="M111 70l6 6 11-12" />
  <path className="ln" d="M186 48V98H234V48" /><path className="ln" d="M186 82H198L202 90H218L222 82H234" />
  <g className="a-env"><rect className="pk" x="194" y="54" width="32" height="22" /><path className="ln-wt" d="M194 54l16 12 16-12" /></g>
</Art>;

const ArtLearn = () => <Art label="A loop arrow around a small chart, with a reply coming back in a speech bubble.">
  <path className="ln a-draw" pathLength={1} d="M140.85 37.23 A48 48 0 1 1 93.58 28.89" /><path className="ln" d="M83.9 26.3 L93.6 28.9 L87.8 37.1" />
  <rect className="nv" x="90" y="80" width="9" height="14" /><rect className="nv" x="103" y="72" width="9" height="22" /><rect className="pk a-grow-up" x="116" y="62" width="9" height="32" /><path className="ln" d="M84 94h48" />
  <g className="a-pop"><rect className="pk" x="172" y="18" width="56" height="34" /><path className="pk" d="M182 52l-4 12 14-12z" /><path className="ln-wt" d="M180 30h40M180 40h28" /></g>
</Art>;

/* ------------------------------------------------------------------ the six steps */

type Step = { verb: string; line: string; chips: string[]; to: string; perm: string; page: string; note: string; art: () => JSX.Element };

const STEPS: Step[] = [
  { verb: "Watch the market", art: ArtWatch,
    line: "The app reads public news, tenders, job ads and company websites around the clock, so you don't have to.",
    chips: ["News and press pages", "EU and UK tenders", "Job ads", "Company websites", "Web search with Exa"],
    to: "/feed", perm: "explorer.read", page: "All findings", note: "every story it has read, with a link to the original page." },
  { verb: "Match it to your list", art: ArtMatch,
    line: "It works out which company each story is about, and whether it is a buying signal: a sign that the company may need engineers.",
    chips: ["Hiring engineers", "Money raised for a build", "New technology leader", "New payments or core banking project", "Open tender"],
    to: "/signals", perm: "accounts.read", page: "Buying signals", note: "the companies on your list with a reason to talk now." },
  { verb: "Check the facts", art: ArtCheck,
    line: "Before anything reaches you, it checks that the words are really on the page, that the company is our kind of customer, and that the work is something we do.",
    chips: ["Quoted from the original page", "Bank, insurer, payments or fintech", "Engineering, data, AI or testing work", "No reason to wait, such as a hiring freeze"],
    to: "/today", perm: "today.read", page: "Today", note: "only what passes these checks." },
  { verb: "Research the company", art: ArtResearch,
    line: "Pick a company and one click asks Exa to research it: what it does, its recent news, its suppliers, its engineering jobs and the people who decide.",
    chips: ["Company profile", "Recent news", "Who decides", "Person profiles", "Every line links to its page"],
    to: "/accounts", perm: "accounts.read", page: "Accounts & leads", note: "open any company to see its profile and people." },
  { verb: "Reach out", art: ArtReach,
    line: "Pick a buying signal and the app prepares a campaign: the right people at each company, with every email opening on that company's own news.",
    chips: ["Right people picked", "A personal first line", "A manager approves", "Waits in Lemlist until you launch"],
    to: "/campaigns", perm: "draft.create", page: "Campaigns", note: "each campaign, who is in it and what it says." },
  { verb: "Learn from replies", art: ArtLearn,
    line: "Replies, opens and opt-outs come back from Lemlist every hour. People who opt out are never offered again, and companies you skip stay quiet until something new happens.",
    chips: ["Replies", "Opt-outs respected", "Skipped companies stay quiet", "Report a story it missed"],
    to: "/people", perm: "contacts.read", page: "People", note: "who you contacted and who answered." },
];

function Steps({ can }: { can: Can }) {
  return (
    <ol className="hw-steps">
      {STEPS.map((s, i) => <li key={s.verb} className="hw-step">
        <span className="hw-num" aria-hidden="true">{i + 1}</span>
        <div className="hw-art"><s.art /></div>
        <h3 className="hw-step-h"><span className="sr">{tr("Step {}:", i + 1)} </span>{tr(s.verb)}</h3>
        <div className="hw-step-b">
          <p>{tr(s.line)}</p>
          <ul className="hw-chips" aria-label={tr("For example")}>{s.chips.map((c) => <li key={c}>{tr(c)}</li>)}</ul>
          <p className="hw-see">{tr("Where to see it:")} <To to={s.to} perm={s.perm} can={can}>{tr(s.page)}</To>, {tr(s.note)}</p>
        </div>
      </li>)}
      <li className="hw-again"><span className="hw-num hw-num-loop" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="square"><path d="M19 12a7 7 0 11-2.05-4.95M19 4v4h-4" /></svg></span>
        <p>{tr("Then it starts again the next morning, with what it has learned.")}</p></li>
    </ol>
  );
}

/* ------------------------------------------------------------------ a day with the app */

function Clock({ h, m }: { h: number; m: number }) {
  const pt = (deg: number, r: number) => { const a = (deg - 90) * Math.PI / 180; return `${(22 + r * Math.cos(a)).toFixed(2)} ${(22 + r * Math.sin(a)).toFixed(2)}`; };
  return <svg className="hw-clock" viewBox="0 0 44 44" aria-hidden="true" focusable="false">
    <circle cx="22" cy="22" r="20" />
    {[0, 90, 180, 270].map((d) => <path key={d} className="tick" d={`M${pt(d, 15)}L${pt(d, 19)}`} />)}
    <path className="hand" d={`M22 22L${pt((h % 12) * 30 + m / 2, 9)}`} /><path className="hand min" d={`M22 22L${pt(m * 6, 14)}`} />
  </svg>;
}

type Moment = { h: number; m: number; title: string; line: string; links: [string, string, string, Ico][] };
const DAY: Moment[] = [
  { h: 8, m: 30, title: "Open Today", line: "See the short list of companies worth your time and what changed since yesterday.",
    links: [["/today", "today.read", "Today", IconToday]] },
  { h: 9, m: 15, title: "Find a reason", line: "Pick one company with a clear reason to talk now: a buying signal or an open tender.",
    links: [["/signals", "accounts.read", "Buying signals", IconSignal], ["/tenders", "today.read", "Open tenders", IconTender]] },
  { h: 10, m: 0, title: "Prepare", line: "Open the company page. Read the research, see who decides, and research a person with Exa if you need more.",
    links: [["/accounts", "accounts.read", "Accounts & leads", IconAccounts], ["/people", "contacts.read", "People", IconPeople]] },
  { h: 11, m: 30, title: "Reach out", line: "Start a campaign from the signal. Once a manager approves it, you launch it in Lemlist.",
    links: [["/campaigns", "draft.create", "Campaigns", IconCampaign]] },
];

function Day({ can }: { can: Can }) {
  return (
    <ol className="hw-day">
      {DAY.map((d) => <li key={d.title} className="hw-moment">
        <Clock h={d.h} m={d.m} />
        <time className="hw-time" dateTime={`${String(d.h).padStart(2, "0")}:${String(d.m).padStart(2, "0")}`}>{`${d.h}:${String(d.m).padStart(2, "0")}`}</time>
        <h3>{tr(d.title)}</h3>
        <p>{tr(d.line)}</p>
        <ul className="hw-pages">{d.links.filter(([, perm]) => can(perm)).map(([to, , name, Icon]) => <li key={to}><Link to={to}><Icon />{tr(name)}</Link></li>)}</ul>
      </li>)}
    </ol>
  );
}

/* ------------------------------------------------------------------ costs and limits */

type Budget = { cap: number; spent: number };
type Live = { exa?: Budget; ai?: Budget; aiUsd?: number | null; apollo?: Budget };

// The limits set for production (render.yaml: RI_EXA_MONTHLY_CAP_USD=20, RI_ANTHROPIC_MONTHLY_TOKEN_CAP=10000000; budget.py enforces them).
// When the viewer may see costs, the live limits for this month replace these.
const EXA_CAP = 20, AI_TOKENS = 10_000_000, AI_USD = 1.6;

const tokens = (n: number) => (n >= 1_000_000 ? tr("{} million", number(Math.round(n / 100_000) / 10)) : number(Math.round(n)));
const usd = (n: number) => `$${n < 10 && n % 1 ? n.toFixed(2) : number(Math.round(n))}`;

/** A track that ends in a pink wall. With this month's numbers (people who may see costs), the track fills with what is used. */
function Wall({ b, label, idle }: { b?: Budget; label: string; idle: string }) {
  const pct = b && b.cap > 0 ? Math.min(b.spent / b.cap, 1) : 0;
  return <div className="hw-wall">
    <div className="hw-wall-k"><span>{b ? label : tr(idle)}</span><span aria-hidden="true">{tr("Stops here")}</span></div>
    <div className="hw-wall-bar" role={b ? "img" : undefined} aria-label={b ? label : undefined} aria-hidden={b ? undefined : true}><i style={{ width: `${pct * 100}%` }} /></div>
  </div>;
}

function Costs({ can }: { can: Can }) {
  const [live, setLive] = useState<Live>({});
  const mayCost = can("admin.costs");
  useEffect(() => {
    if (!mayCost) return;
    let gone = false;
    Promise.allSettled([api<any>("/api/admin/usage"), api<any>("/api/admin/integrations")]).then(([u, i]) => {
      if (gone) return;
      const out: Live = {};
      const b = (x: any): Budget | undefined => (x && Number(x.cap) > 0 ? { cap: Number(x.cap), spent: Number(x.spent) || 0 } : undefined);
      if (u.status === "fulfilled") { out.exa = b(u.value?.budgets?.exa); out.ai = b(u.value?.budgets?.anthropic); out.aiUsd = u.value?.anthropic_cap_usd_estimate || null; }
      if (i.status === "fulfilled") out.apollo = b(i.value?.items?.find((x: any) => x.key === "apollo"));
      setLive(out);
    });
    return () => { gone = true; };
  }, [mayCost]);
  const exaCap = live.exa?.cap ?? EXA_CAP;
  const aiCap = live.ai?.cap ?? AI_TOKENS;
  const aiUsd = live.aiUsd ?? (live.ai ? null : AI_USD);
  return <>
    <div className="hw-costs">
      <section className="hw-cost" aria-labelledby="cost-exa">
        <h3 id="cost-exa">{tr("Exa web research")}</h3>
        <p className="hw-big">{tr("{} a month", usd(exaCap))}</p>
        <p>{tr("Pays for company and people research, and for a few web searches a day. Researching one company costs a few cents.")}</p>
        <Wall b={live.exa} idle="Hard limit, checked before every search" label={tr("This month: {} of {} used", usd(live.exa?.spent ?? 0), usd(exaCap))} />
      </section>
      <section className="hw-cost" aria-labelledby="cost-ai">
        <h3 id="cost-ai">{tr("AI reading and writing")}</h3>
        <p className="hw-big">{tr("{} million tokens a month", number(Math.round(aiCap / 100_000) / 10))}</p>
        <p>{tr("A token is a small piece of a word. The AI reads pages, picks out facts and writes summaries and first lines.")}{aiUsd ? " " + tr("About {} a month at today's list price.", usd(aiUsd)) : ""}</p>
        <Wall b={live.ai} idle="Hard limit, checked before every call" label={tr("This month: {} of {} tokens used", tokens(live.ai?.spent ?? 0), tokens(aiCap))} />
      </section>
      <section className="hw-cost" aria-labelledby="cost-apollo">
        <h3 id="cost-apollo">{tr("Apollo work emails")}</h3>
        <p className="hw-big">{tr("Subscription credits only")}</p>
        <p>{tr("The app never buys extra credits. A person asks for each email address, and reveals stop at the monthly credit limit.")}</p>
        <Wall b={live.apollo} idle="Limit set by an administrator" label={tr("This month: {} of {} credits used", number(live.apollo?.spent ?? 0), number(live.apollo?.cap ?? 0))} />
      </section>
    </div>
    <div className="hw-free">
      <p><b>{tr("Free to read:")}</b> {tr("news pages, official tender sites, job boards and company websites cost nothing.")}</p>
      <p><b>{tr("Tracked:")}</b> {tr("every paid call is written down with what it was for.")} {mayCost && <Link to="/admin/usage">{tr("See usage and cost")}</Link>}</p>
    </div>
  </>;
}

/* ------------------------------------------------------------------ words and questions */

const WORDS: [string, string, string][] = [
  ["Account", "A company on your list that you can research and track.", "A UK bank from your lead list."],
  ["Buying signal", "Something that happened at a company that often means it needs engineers.", "It is hiring ten payments developers."],
  ["Finding", "One dated fact the app read on a public page, saved with the quote and a link.", "“Bank X chose a new core banking system on 10 November.”"],
  ["Tender", "An official request for suppliers, published by a buyer with a deadline.", "An insurer asking for a software testing partner."],
  ["Campaign", "Personal emails to the right people at every company with the same buying signal, sent from Lemlist.", "Ten fintechs that raised money this month."],
  ["ICP", "Our ideal customer: privately owned banks, insurers, payment companies and fintechs, mostly in the UK, DACH, the Nordics and Poland.", ""],
];
const SERVICES: [string, string][] = [
  ["Exa", "A web search service. The app uses it to research companies and people."],
  ["AI model", "Reads pages, picks out facts and writes summaries. A fact without a quote from the page is dropped."],
  ["Apollo", "A contact database that finds work email addresses, within our subscription."],
  ["Lemlist", "The email tool that sends campaigns. The app prepares them; you launch them there."],
];
const FAQ: [string, string][] = [
  ["Does the app send emails by itself?", "No. It prepares campaigns. A manager approves them, and nothing goes out until someone presses Launch in Lemlist."],
  ["Where does the information come from?", "Public pages: news sites, official tender sites, job boards and company websites, plus web search with Exa. Every finding links to its original page."],
  ["Can it make things up?", "It is built not to. A fact is kept only when its exact words are on the original page, and the research shows the page behind every line."],
  ["Why is a company I know missing?", "Either nothing new has happened there, or it is outside our ICP. Search for it in the menu, or paste a story the app missed on All findings."],
  ["What if a website blocks the app?", "It skips that page. It never logs in or gets round a block. Administration shows which sources are working."],
  ["What does it cost?", "Very little, and every paid service has a hard monthly limit. When a limit is reached, paid calls stop until next month."],
];

/* ------------------------------------------------------------------ the page */

const SECTIONS: [string, string][] = [["steps", "The six steps"], ["day", "A day with the app"], ["costs", "Costs and limits"], ["words", "Words"], ["faq", "Questions"]];

export default function Guide({ can = () => true }: { can?: Can }) {
  return <>
    <header className="hw-hero">
      <div className="page hw-hero-in">
        <div className="hw-hero-copy">
          <h1>{tr("How it works")}</h1>
          <p className="hw-lede">{tr("Every morning, the app reads the market for you and tells you which banks and fintechs to call, and why.")}</p>
          <p className="hw-sub">{tr("It reads public news, tenders, job ads and company websites, matches each story to a company on your list, and checks every fact against the page it came from. You decide who to contact.")}</p>
          <div className="hw-cta">
            {can("today.read") && <Link className="btn primary" to="/today">{tr("Open Today")}</Link>}
            <a href="#steps">{tr("See the six steps")}</a>
          </div>
        </div>
        <Machine />
      </div>
    </header>

    <div className="page hw-page">
      <nav className="hw-jump" aria-label={tr("On this page")}>
        {SECTIONS.map(([id, name]) => <a key={id} href={`#${id}`}>{tr(name)}</a>)}
      </nav>

      <section id="steps" className="hw-sec" aria-labelledby="steps-h">
        <h2 id="steps-h">{tr("The six steps")}</h2>
        <p className="hw-intro">{tr("What the app does for you, in order, from a story on a web page to a reply in your inbox. Each step says where to see it.")}</p>
        <Steps can={can} />
      </section>

      <section id="day" className="hw-sec" aria-labelledby="day-h">
        <h2 id="day-h">{tr("A day with the app")}</h2>
        <p className="hw-intro">{tr("A typical morning in four moves. Follow it the first time you use the app.")}</p>
        <Day can={can} />
      </section>

      <section id="costs" className="hw-sec" aria-labelledby="costs-h">
        <h2 id="costs-h">{tr("What it costs and the limits we set")}</h2>
        <p className="hw-intro">{tr("Every paid service has a hard monthly limit. When a limit is reached, the app stops those calls until next month.")}</p>
        <Costs can={can} />
      </section>

      <section id="words" className="hw-sec" aria-labelledby="words-h">
        <h2 id="words-h">{tr("What the words mean")}</h2>
        <p className="hw-intro">{tr("The few words the app uses, in plain English.")}</p>
        <dl className="hw-words">
          {WORDS.map(([w, d, eg]) => <div key={w} className="hw-word"><dt>{tr(w)}</dt><dd>{tr(d)}{eg && <span className="hw-eg"><b>{tr("For example:")}</b> {tr(eg)}</span>}</dd></div>)}
        </dl>
        <h3 className="hw-sub-h">{tr("The services behind it")}</h3>
        <dl className="hw-words hw-services">
          {SERVICES.map(([w, d]) => <div key={w} className="hw-word"><dt>{tr(w)}</dt><dd>{tr(d)}</dd></div>)}
        </dl>
      </section>

      <section id="faq" className="hw-sec" aria-labelledby="faq-h">
        <h2 id="faq-h">{tr("Questions people ask")}</h2>
        <p className="hw-intro">{tr("Short answers for you, your colleagues and your manager.")}</p>
        <div className="hw-faq">
          {FAQ.map(([q, a]) => <details key={q}><summary>{tr(q)}</summary><p>{tr(a)}</p></details>)}
        </div>
      </section>

      <aside className="hw-end" aria-label={tr("Get started")}>
        <p><b>{tr("Ready?")}</b> {tr("Open Today and follow the day above. The Knowledge base explains who we target and what we may say.")}</p>
        <div className="hw-cta">
          {can("today.read") && <Link className="btn primary" to="/today">{tr("Open Today")}</Link>}
          <Link to="/knowledge">{tr("Open the Knowledge base")}</Link>
        </div>
      </aside>
    </div>
  </>;
}
