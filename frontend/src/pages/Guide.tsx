import { Link } from "react-router-dom";

const INPUTS = [
  ["Your two workbooks", "Accounts, contacts and the signals you already found. Loaded in Administration, Imports."],
  ["Official records", "Tenders and regulators. The most trusted kind of source."],
  ["Company newsrooms and trade media", "Press pages and RSS feeds. Listed in Library, URLs and feeds."],
  ["Search discovery (Exa)", "Finds announcements nobody told us about. A hit is only a lead to check."],
];
const ENGINE = [
  ["1. Read the original", "The platform fetches the page and asks a model to pull out events with word-for-word quotes."],
  ["2. Keep only what is quoted", "If the quote is not on the page, the claim is dropped. Nothing is taken on trust."],
  ["3. Match to the 64 signals", "A verified event can switch on a signal, such as a core banking replacement or a new CTO."],
  ["4. Join into a programme", "Ten articles about one announcement become one programme, counted once."],
  ["5. Run the seven gates", "Is the demand external? Can we reach the buyer? What is proven, unknown or blocked?"],
];
const OUTPUTS = [
  ["Today", "A short queue. Each card opens with a plain verdict and one next step."],
  ["Opportunity", "The full workspace: gates, evidence, what is unknown, and discovery questions."],
  ["Drafts and CRM proposals", "Prepared for you. A second person approves before anything leaves."],
];
const CLICK: [string, string, string][] = [
  ["Today", "/today", "Your queue. Start here every morning. It also lists your shortlist from the sheets, each with a Verify button."],
  ["Accounts", "/accounts", "Every company, with what the sheets say, who the contacts are and which opportunities exist."],
  ["Market Explorer", "/explorer", "Browse verified events across the market, not only your accounts."],
  ["Library", "/library", "The 64 signals, every source with its licence terms, and every URL and feed we use. Searchable."],
  ["Knowledge base", "/knowledge", "The ICP map and the sales field guide, inside the tool."],
  ["Search box (left)", "/search", "One box for companies, keywords, signals, sources, URLs and the knowledge base."],
  ["Administration", "/admin", "Imports, people, sources, discovery, verification queue, usage and cost, approvals and audit."],
];
const WORDS: [string, string][] = [
  ["Signal", "A pattern that suggests a company may need outside engineering help, such as a vendor selected or an engineering team being hired. There are 64, each with the original wording."],
  ["Event", "One verified fact with a date, for example “Bank X selected vendor Y on 10 November”."],
  ["Claim", "A single statement tied to a quote on a page. Events are built from claims."],
  ["Programme", "A real piece of work at a company, such as a core platform replacement. Many events can belong to one."],
  ["Gate", "A yes, no or unknown test. There are seven, from account fit to delivery feasibility."],
  ["Verdict", "The one-line read at the top of a card, written from the gates."],
  ["Verified", "Checked by the platform against the original page. Anything from the sheets alone is marked unverified."],
  ["Coverage", "Which sources are being watched. A quiet screen only means something when coverage is healthy."],
];

export default function Guide() {
  return (
    <>
      <div className="hero">
        <div className="eyebrow">Start here</div>
        <h2>How it works</h2>
        <p className="sub">The platform reads public sources and your account lists, checks every claim against the original page, and gives you a short list of what to do next. It tells you what it knows, what it is assuming and what only a buyer can confirm.</p>
      </div>
      <div className="page">
        <div className="section-title" style={{ marginTop: 6 }}>The whole thing on one page</div>
        <div className="map3" role="group" aria-label="Structure of the platform">
          <div className="col"><div className="col-h">Goes in</div>{INPUTS.map(([t, d]) => <div key={t} className="mbox"><b>{t}</b><span>{d}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col eng"><div className="col-h">What the platform does</div>{ENGINE.map(([t, d]) => <div key={t} className="mbox"><b>{t}</b><span>{d}</span></div>)}</div>
          <div className="arrow" aria-hidden="true">→</div>
          <div className="col"><div className="col-h">What you get</div>{OUTPUTS.map(([t, d]) => <div key={t} className="mbox out"><b>{t}</b><span>{d}</span></div>)}</div>
        </div>
        <div className="controls"><b>Controls underneath:</b> hard monthly caps on model and search spend (Usage), a full audit log, roles for every person, and approvals for anything that leaves the system.</div>

        <div className="section-title">Where do I click?</div>
        <div className="card table-wrap"><table><thead><tr><th>Place</th><th>What it is for</th></tr></thead><tbody>
          {CLICK.map(([n, to, d]) => <tr key={n}><td><Link to={to}><b>{n}</b></Link></td><td>{d}</td></tr>)}</tbody></table></div>

        <div className="section-title">Your day in three moves</div>
        <div className="moves">
          <div className="card"><div className="eyebrow">1 · Today</div><p style={{ margin: 0 }}>Read the top card. The right-hand panel is the one thing to do. On a sheet shortlist item, press <b>Verify this source</b> to have the platform read the original page. Dismiss what does not apply, with a reason.</p></div>
          <div className="card"><div className="eyebrow">2 · Opportunity</div><p style={{ margin: 0 }}>Open it to see which gates are green, amber or red, the quoted evidence, and the questions that would close the gaps.</p></div>
          <div className="card"><div className="eyebrow">3 · Propose</div><p style={{ margin: 0 }}>Generate a draft or propose a Pipedrive activity. A colleague approves it in Administration before anything leaves the system.</p></div>
        </div>

        <div className="section-title">Words used in the tool</div>
        <dl className="gloss">{WORDS.map(([w, d]) => <div key={w}><dt>{w}</dt><dd>{d}</dd></div>)}</dl>

        <div className="section-title">The seven gates</div>
        <div className="gate-legend">
          {[["G1", "Account and ICP", "Is this an account we should pursue?"], ["G2", "Initiative is real", "Is there a verified programme, with a stage?"], ["G3", "Engineering fit", "Does the work match what The Codest delivers?"],
            ["G4", "External demand", "Will the buyer source outside help? Often unknown until you ask."], ["G5", "Supplier access", "Can a supplier like us get in: direct, via a vendor or via a prime?"],
            ["G6", "Buyer path", "Do we know who decides, and have they said so?"], ["G7", "Delivery feasibility", "Can we staff it in the window they need?"]].map(([g, n, q]) => (
            <div key={g} className="g"><div className="gi">{g}</div><div className="gn">{n}</div><div className="gr">{q}</div></div>))}
        </div>
        <p className="small muted" style={{ marginTop: 10 }}>Green means evidenced. Amber means unknown. Red means blocked or contradicted. A recommendation can be worth acting on while G4 to G7 are amber. It is not commercially qualified until they are resolved.</p>

        <div className="card" style={{ marginTop: 26 }}>
          <div className="eyebrow">What this is not</div>
          <ul className="tight" style={{ marginBottom: 0 }}>
            <li>It does not send email or messages. Drafts only.</li>
            <li>It does not write to Pipedrive without a second person's approval.</li>
            <li>Its scores are provisional weights that have not yet been calibrated against outcomes.</li>
            <li>Coverage is bounded by the configured sources. It does not claim to see the whole market.</li>
          </ul>
        </div>
        <p style={{ marginTop: 18 }}><Link to="/today"><button className="primary">Go to Today</button></Link></p>
      </div>
    </>
  );
}
