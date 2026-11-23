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
];
const CLICK: [string, string, string][] = [
  ["Today", "/today", "Your queue. Start here every morning. It also lists your shortlist from the workbooks, each with a Check button."],
  ["Accounts", "/accounts", "Every company we know about, with its signal score, contacts and opportunities. Add a company that is missing."],
  ["Market feed", "/feed", "Every checked finding, newest first, with why it could be work for us."],
  ["Renewals", "/renewals", "Public IT contracts likely to be re-tendered soon, and who holds them now."],
  ["Explorer", "/explorer", "Search opportunities, companies, public tenders and findings across the market."],
  ["Library", "/library", "The 64 signals, every source with its licence terms, and every page and feed we watch. Searchable."],
  ["Knowledge base", "/knowledge", "The ICP map and the sales field guide, inside the tool."],
  ["Search box (left)", "/search", "One box for companies, signals, sources, findings and the knowledge base."],
  ["Administration", "/admin", "Imports, people, sources, approvals, usage and cost, and the audit log."],
];
const WORDS: [string, string][] = [
  ["Account", "A company in our database, from your workbooks, added by someone, or found by us."],
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
    <>
      <div className="hero">
        <div className="eyebrow">Start here</div>
        <h1>How it works</h1>
        <p className="sub">The platform reads public sources and your account lists, checks every claim against the original page, and gives you a short list of what to do next. It tells you what it knows, what it is assuming and what only a buyer can confirm.</p>
      </div>
      <div className="page">
        <div className="section-title mt-2">The whole thing on one page</div>
        <div className="map3" role="group" aria-label="Structure of the platform">
          <div className="col"><div className="col-h">Goes in</div>{INPUTS.map(([t, d]) => <div key={t} className="mbox"><b>{t}</b><span>{d}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col eng"><div className="col-h">What the platform does</div>{ENGINE.map(([t, d]) => <div key={t} className="mbox"><b>{t}</b><span>{d}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col"><div className="col-h">What you get</div>{OUTPUTS.map(([t, d]) => <div key={t} className="mbox out"><b>{t}</b><span>{d}</span></div>)}</div>
        </div>
        <div className="controls"><b>Safeguards:</b> monthly spending limits on AI and search (Usage), a full audit log, a role for every person, and approval for anything that leaves the system.</div>

        <div className="section-title">Where do I click?</div>
        <div className="card table-wrap" tabIndex={0} role="region" aria-label="Place · What it is for"><table><thead><tr><th>Place</th><th>What it is for</th></tr></thead><tbody>
          {CLICK.map(([n, to, d]) => <tr key={n}><td><Link to={to}><b>{n}</b></Link></td><td>{d}</td></tr>)}</tbody></table></div>

        <div className="section-title">Your day in three moves</div>
        <div className="moves">
          <div className="card"><div className="eyebrow">1 · Today</div><p className="m-0">Read the top card. The right-hand panel is the one thing to do. On a shortlist item, press <b>Check this source</b> to have the platform read the original page. Dismiss what does not apply, with a reason.</p></div>
          <div className="card"><div className="eyebrow">2 · Opportunity</div><p className="m-0">Open it to see which of the seven checks are green, amber or red, the quoted sources, and the questions that would fill the gaps.</p></div>
          <div className="card"><div className="eyebrow">3 · Propose</div><p className="m-0">Write a draft or ask to add a Pipedrive activity. A colleague approves it in Administration before anything leaves the system.</p></div>
        </div>

        <div className="section-title">Words used in the tool</div>
        <dl className="gloss">{WORDS.map(([w, d]) => <div key={w}><dt>{w}</dt><dd>{d}</dd></div>)}</dl>

        <div className="section-title">The seven checks</div>
        <div className="gate-legend">
          {[["1", "Account fit", "Is this a company we should pursue?"], ["2", "Real programme", "Is there a checked programme, and what stage is it at?"], ["3", "Engineering fit", "Does the work match what The Codest delivers?"],
            ["4", "Outside demand", "Will the buyer use outside help? Often not known until you ask."], ["5", "Supplier access", "Can a supplier like us get in: directly, through the vendor or through an integrator?"],
            ["6", "Buyer path", "Do we know who decides, and have they said so?"], ["7", "Delivery", "Can we staff it in the time they need?"]].map(([g, n, q]) => (
            <div key={g} className="g"><div className="gi">{g}</div><div className="gn">{n}</div><div className="gr">{q}</div></div>))}
        </div>
        <p className="small muted mt-3">Green means confirmed. Amber means not known yet. Red means blocked. An opportunity can be worth acting on while checks 4 to 7 are amber. It is not qualified until they are answered.</p>

        <div className="card mt-5">
          <div className="eyebrow">What this is not</div>
          <ul className="tight mb-0">
            <li>It does not send email or messages. It only writes drafts.</li>
            <li>It does not change Pipedrive without a colleague's approval.</li>
            <li>Scores are our best current weighting. They have not yet been checked against deals won and lost, so treat them as a guide, not a forecast.</li>
            <li>It only sees the sources we watch. It does not claim to see the whole market.</li>
          </ul>
        </div>
        <p className="mt-4"><Link to="/today"><button className="primary">Go to Today</button></Link></p>
      </div>
    </>
  );
}
