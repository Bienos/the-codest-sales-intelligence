import { useState } from "react";
import { Link } from "react-router-dom";
import { tr } from "../locale";
import { IconAccounts, IconCampaign, IconFeed, IconKnowledge, IconLibrary, IconPeople, IconTender, IconToday } from "../components/ui";

/** The path a fact takes from a web page to an email in someone's inbox. Each station says what happens there and where to see it. */
const STATIONS = [
  { k: "Read", title: "We read the sources", what: "Tenders, regulators, company newsrooms, trade media, job boards and your workbooks are read on a schedule. Pages that block automated reading are skipped, never worked around.",
    see: "Library shows every source and whether it is working.", to: "/library?tab=sources", cta: "Open the source list", perm: "" },
  { k: "Quote", title: "We keep only what is quoted", what: "A model pulls facts out of each page. If the exact words are not on the page, the fact is dropped. Nothing is taken on trust, and a search result is only ever a lead.",
    see: "Every finding has an original source link and its quote under View evidence.", to: "/feed", cta: "Open the findings", perm: "explorer.read" },
  { k: "Signal", title: "We match a buying signal", what: "A checked fact can switch on a signal: a funding round, a payments scheme change, a new technology leader. Each signal says in plain words why it tends to mean engineering work.",
    see: "Today opens with the signals that are live right now.", to: "/today", cta: "Open Today", perm: "today.read" },
  { k: "Company", title: "We tie it to the right company", what: "Coverage of the same news counts once. Ownership, size and sector are checked, so Today only shows privately owned financial companies. Tenders from any buyer have their own page.",
    see: "An account shows its score, its signals and who owns it in Pipedrive.", to: "/accounts", cta: "Open accounts", perm: "accounts.read" },
  { k: "People", title: "We find who to write to", what: "Contacts from your workbooks, Apollo and Pipedrive sit in one list. Only people with a verified email are offered, and never anyone who opted out or belongs to another seller.",
    see: "People shows where each contact came from and what happened in outreach.", to: "/people", cta: "Open people", perm: "contacts.read" },
  { k: "Campaign", title: "A person approves, then it goes to Lemlist", what: "Pick a signal and the platform builds the audience and writes each opening line from that company's own news. A manager approves the exact list and copy. It lands in Lemlist unlaunched.",
    see: "Campaigns shows every step, and replies come back here.", to: "/campaigns", cta: "Open campaigns", perm: "draft.create" },
] as const;

const WHERE: { to: string; icon: (p: { className?: string }) => JSX.Element; name: string; what: string; perm: string }[] = [
  { to: "/today", icon: IconToday, name: "Today", what: "Live signals and the newest verified findings.", perm: "today.read" },
  { to: "/tenders", icon: IconTender, name: "Open tenders", what: "Software tenders from every buyer, soonest deadline first.", perm: "today.read" },
  { to: "/feed", icon: IconFeed, name: "All findings", what: "Search the evidence by date, region or signal.", perm: "explorer.read" },
  { to: "/accounts", icon: IconAccounts, name: "Accounts & leads", what: "Companies, their scores and what we know about them.", perm: "accounts.read" },
  { to: "/people", icon: IconPeople, name: "People", what: "Every contact, from every system, in one list.", perm: "contacts.read" },
  { to: "/campaigns", icon: IconCampaign, name: "Campaigns", what: "Turn one signal into a reviewed, approved campaign.", perm: "draft.create" },
  { to: "/library", icon: IconLibrary, name: "Library", what: "The signal rules, sources and web addresses.", perm: "" },
  { to: "/knowledge", icon: IconKnowledge, name: "Knowledge base", what: "Who we target and what we may say.", perm: "today.read" },
];

const CHECKS: [string, string, string][] = [
  ["1", "Account fit", "Is this a company we should pursue?"], ["2", "Real programme", "Is there a checked programme, and what stage is it at?"],
  ["3", "Engineering fit", "Does the work match what The Codest delivers?"], ["4", "Outside demand", "Will the buyer use outside help? Often not known until you ask."],
  ["5", "Supplier access", "Can a supplier like us get in: directly, through the vendor or through an integrator?"], ["6", "Buyer path", "Do we know who decides, and have they said so?"],
  ["7", "Delivery", "Can we staff it in the time they need?"],
];

const WORDS: [string, string][] = [
  ["Account", "A company you can research and track."],
  ["Lead", "A buying signal from a workbook. Its source shows whether the claim has been checked."],
  ["Finding", "One dated fact we checked against the original page, for example “Bank X chose vendor Y on 10 November”."],
  ["Signal", "A pattern that suggests a company may need outside engineering help, such as a vendor being chosen or an engineering team being hired."],
  ["Programme", "A real project at a company, such as a core platform replacement. Many findings can belong to one."],
  ["Opportunity", "A specific piece of work we think we could win at an account, through one route in. Not the same as a Pipedrive deal."],
  ["Check", "One of seven questions about an opportunity. Each is confirmed, not known yet, or blocked."],
  ["Checked", "We read the original page and found the quote. Anything from your workbooks alone is marked not checked."],
  ["Signal score", "How strong an account’s recent signals are. Different kinds count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability."],
  ["Opportunity score", "Out of 100: company fit, engineering relevance, outside demand, way in, timing, and relationship. It ranks what to look at first."],
  ["Workbook score", "The score column from your own workbooks. We do not calculate it."],
  ["Source health", "Whether the sources we watch are working. A quiet screen only means something when this is healthy."],
  ["Signal campaign", "One campaign written for every company that shows the same signal, with each email opening on that company’s own news."],
  ["Unlaunched", "In Lemlist but not started. Nothing is sent until someone presses Launch in Lemlist."],
];

export default function Guide({ can = () => true }: { can?: (permission: string) => boolean }) {
  const [step, setStep] = useState(0);
  const [find, setFind] = useState("");
  const s = STATIONS[step];
  const words = WORDS.filter(([w, d]) => !find.trim() || `${tr(w)} ${tr(d)}`.toLowerCase().includes(find.trim().toLowerCase()));
  return <>
    <header className="today-heading">
      <div className="page today-heading-inner">
        <div>
          <h1>{tr("How it works")}</h1>
          <p className="today-intro guide-lede">{tr("Every signal here comes from a page we read and words we can point to. This is the path from that page to an approved email.")}</p>
        </div>
      </div>
    </header>

    <div className="page guide-page">
      <section aria-labelledby="path-h" className="gpath">
        <h2 id="path-h" className="sr">{tr("The path from a web page to an approved email")}</h2>
        <ol className="gpath-line" aria-label={tr("Stations")}>
          <svg className="gpath-wave" viewBox="0 0 1000 80" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path d="M0 52 C30 52 52 40 83.3 40 C145.3 14 188 14 250 40 C312 66 354.7 66 416.7 40 C478.7 14 521.3 14 583.3 40 C645.3 66 688 66 750 40 C812 14 854.7 14 916.7 40 C950 56 975 58 1000 52" /></svg>
          {STATIONS.map((st, i) => <li key={st.k} className={i === step ? "on" : i < step ? "past" : ""}>
            <button type="button" aria-pressed={i === step} onClick={() => setStep(i)}>
              <span className="gpath-node" aria-hidden="true" /><span className="gpath-name">{tr(st.k)}</span>
            </button>
          </li>)}
        </ol>
        <div className="gpath-panel" aria-live="polite">
          <h3>{tr(s.title)}</h3>
          <p>{tr(s.what)}</p>
          <p className="gpath-see"><b>{tr("Where you see it")}:</b> {tr(s.see)} {(!s.perm || can(s.perm)) && <Link to={s.to}>{tr(s.cta)}</Link>}</p>
          <div className="gpath-nav">
            <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)}>{tr("Previous")}</button>
            <button type="button" className="primary" disabled={step === STATIONS.length - 1} onClick={() => setStep(step + 1)}>{tr("Next")}</button>
          </div>
        </div>
      </section>

      <section aria-labelledby="where-h">
        <h2 id="where-h" className="gh">{tr("Where to click")}</h2>
        <div className="gwhere">
          {WHERE.filter((w) => !w.perm || can(w.perm)).map((w) => <Link key={w.to} to={w.to} className="gwhere-i"><w.icon /><span><b>{tr(w.name)}</b>{tr(w.what)}</span></Link>)}
        </div>
      </section>

      <section aria-labelledby="checks-h">
        <h2 id="checks-h" className="gh">{tr("Seven checks before anything is called an opportunity")}</h2>
        <div className="gate-legend">
          {CHECKS.map(([g, n, q]) => <div key={g} className="g"><div className="gi">{g}</div><div className="gn">{tr(n)}</div><div className="gr">{tr(q)}</div></div>)}
        </div>
        <p className="small muted mt-2">{tr("Green means confirmed. Amber means not known yet. Red means blocked. An opportunity can be worth acting on while checks 4 to 7 are amber. It is not qualified until they are answered.")}</p>
      </section>

      <section aria-labelledby="rules-h" className="grules">
        <h2 id="rules-h" className="gh">{tr("What it will not do")}</h2>
        <div className="grules-row">
          {[["It will not guess", "A source that fails to load shows as degraded coverage. A quiet screen does not mean nothing happened."],
            ["It will not send", "Drafts only. A second person approves any campaign or Pipedrive change, and the exact change is shown first."],
            ["It will not hide the limits", "Scores are provisional weights, not win probabilities. Missing facts are listed, not filled in."]].map(([t, d]) =>
            <div key={t} className="grules-i"><h3>{tr(t)}</h3><p>{tr(d)}</p></div>)}
        </div>
      </section>

      <section aria-labelledby="words-h">
        <div className="row spread"><h2 id="words-h" className="gh">{tr("Words used in the tool")}</h2>
          <input type="search" className="gfind" aria-label={tr("Find a word")} placeholder={tr("Find a word")} value={find} onChange={(e) => setFind(e.target.value)} /></div>
        {words.length === 0 ? <p className="muted">{tr("No word matches. Clear the search.")}</p> :
          <dl className="gloss">{words.map(([w, d]) => <div key={w}><dt>{tr(w)}</dt><dd>{tr(d)}</dd></div>)}</dl>}
      </section>
    </div>
  </>;
}
