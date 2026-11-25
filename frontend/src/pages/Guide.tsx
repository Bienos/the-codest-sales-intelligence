import { tr } from "../locale";
import { Link } from "react-router-dom";

const INPUTS = [
  ["Your two workbooks", "Accounts, contacts and the signals you already found. Loaded in Administration, Imports."],
  ["Official records", "Tenders and regulators. The most trusted kind of source."],
  ["Company newsrooms and trade media", "Press pages and RSS feeds. Listed in Library, URLs and feeds."],
  ["Web search (Exa)", "Finds announcements nobody told us about. A hit is only a link to check."],
];
const ENGINE = [
  ["1. Read the original", "The platform fetches the page and asks a model to pull out events with word-for-word quotes."],
  ["2. Keep only what is quoted", "If the quote is not on the page, the claim is dropped. Nothing is taken on trust."],
  ["3. Match to the 64 signals", "A checked finding can switch on a signal, such as a core banking replacement or a new CTO."],
  ["4. Group into a programme", "Ten articles about one announcement become one programme, counted once."],
  ["5. Run the seven checks", "Will they use outside help? Can we reach the buyer? What is confirmed, not known yet or blocked?"],
];
const OUTPUTS = [
  ["Today", "A short queue. Each card opens with a plain verdict and one next step."],
  ["Opportunity", "The full page: the seven checks, the sources, what is not known yet, and questions to ask."],
  ["Drafts and Pipedrive requests", "Prepared for you. A colleague approves before anything leaves."],
  ["Signal campaigns", "Pick a buying signal and write to every company showing it. A manager approves; it lands in Lemlist unlaunched."],
];
const WORKFLOW = [
  ["Today", "/today", "Start with open requests and current buying signals. Read the source and review the suggested next step."],
  ["Accounts & leads", "/accounts?view=leads", "Find your workbook leads, then open the company to see its signals, people and assigned sources."],
  ["All findings", "/feed", "Browse the evidence by region, date, type or buying signal. The status shows whether company ownership is confirmed."],
  ["People", "/people", "Everyone you can contact, from the workbooks, Apollo, Pipedrive and Lemlist, with their company's buying signal and what happened in outreach."],
  ["Campaigns", "/campaigns", "Turn one buying signal into a campaign: the platform picks the companies and people, writes each opening line from that company's news, and checks the copy."],
];
const WORDS: [string, string][] = [
  ["Account", "A company you can research and track."],
  ["Lead", "A buying signal from a workbook. Its source shows whether the claim has been checked."],
  ["Opportunity", "A specific piece of work we think we could win at an account, through one route in. Not the same as a Pipedrive deal."],
  ["Finding", "One dated fact we checked against the original page, for example “Bank X chose vendor Y on 10 November”."],
  ["Signal", "One of 64 patterns that suggest a company may need outside engineering help, such as a vendor being chosen or an engineering team being hired."],
  ["Programme", "A real project at a company, such as a core platform replacement. Many findings can belong to one."],
  ["Check", "One of seven questions about an opportunity. Each is confirmed, not known yet, or blocked."],
  ["What to do", "The one-line read at the top of a card, worked out from the seven checks."],
  ["Checked", "We read the original page and found the quote. Anything from your workbooks alone is marked not checked."],
  ["Signal score", "How strong an account’s recent signals are. Different kinds of signal count for more than repeats. Hot is 9 or more, warm 4 or more. It is not a probability."],
  ["Opportunity score", "Out of 100: company fit, engineering relevance, outside demand, way in, timing, and relationship. It ranks what to look at first."],
  ["Workbook score", "The score column from your own workbooks. We do not calculate it."],
  ["Source health", "Whether the sources we watch are working. A quiet screen only means something when this is healthy."],
];

export default function Guide() {
  return (
    <div className="guide-workspace">
      <header className="page-head"><div><h1>{tr("How it works")}</h1>
        <p className="sub">{tr("Find evidence of engineering demand, understand the account and decide what to do next.")}</p></div></header>
      <div className="guide-steps">
        {WORKFLOW.map(([title, href, description]) => <section className="card" key={href}>
          <h2>{tr(title)}</h2><p>{tr(description)}</p><Link to={href}>{tr("Open {}", tr(title))} →</Link>
        </section>)}
      </div>
      <div className="guide-secondary">
        <section className="card"><h2>{tr("Check source coverage")}</h2>
          <p>{tr("Use the Library to see which sources are working. If a source is delayed, check its last successful run.")}</p><Link to="/library?tab=sources">{tr("View source health")} →</Link></section>
        <section className="card"><h2>{tr("Research explorer")}</h2>
          <p>{tr("Search across companies, verified findings and opportunities. Use All findings to browse by date or tender type.")}</p><Link to="/explorer">{tr("Open Explorer")} →</Link></section>
      </div>
      <details className="card guide-details">
        <summary>{tr("How evidence is processed")}</summary>
        <div className="map3" role="group" aria-label={tr("Structure of the platform")}>
          <div className="col"><div className="col-h">{tr("Goes in")}</div>{INPUTS.map(([t, d]) => <div key={t} className="mbox"><b>{tr(t)}</b><span>{tr(d)}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col eng"><div className="col-h">{tr("What the platform does")}</div>{ENGINE.map(([t, d]) => <div key={t} className="mbox"><b>{tr(t)}</b><span>{tr(d)}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col"><div className="col-h">{tr("What you get")}</div>{OUTPUTS.map(([t, d]) => <div key={t} className="mbox out"><b>{tr(t)}</b><span>{tr(d)}</span></div>)}</div>
        </div>
      </details>
      <details className="card guide-details">
        <summary>{tr("Words used in the tool")}</summary>
        <dl className="gloss">{WORDS.map(([w, d]) => <div key={w}><dt>{tr(w)}</dt><dd>{tr(d)}</dd></div>)}</dl>
      </details>
      <details className="card guide-details">
        <summary>{tr("The seven checks")}</summary>
        <div className="gate-legend">
          {[["1", tr("Account fit"), tr("Is this a company we should pursue?")], ["2", tr("Real programme"), tr("Is there a checked programme, and what stage is it at?")], ["3", tr("Engineering fit"), tr("Does the work match what The Codest delivers?")],
            ["4", tr("Outside demand"), tr("Will the buyer use outside help? Often not known until you ask.")], ["5", tr("Supplier access"), tr("Can a supplier like us get in: directly, through the vendor or through an integrator?")],
            ["6", tr("Buyer path"), tr("Do we know who decides, and have they said so?")], ["7", tr("Delivery"), tr("Can we staff it in the time they need?")]].map(([g, n, q]) => (
            <div key={g} className="g"><div className="gi">{g}</div><div className="gn">{n}</div><div className="gr">{q}</div></div>))}
        </div>
        <p className="small muted mt-3">{tr("Green means confirmed. Amber means not known yet. Red means blocked. An opportunity can be worth acting on while checks 4 to 7 are amber. It is not qualified until they are answered.")}</p>

      </details>
    </div>
  );
}
