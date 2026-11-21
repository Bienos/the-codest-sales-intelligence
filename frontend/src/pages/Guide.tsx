import { Link } from "react-router-dom";

const STEPS = [
  { n: "00", t: "Load", d: "Your two workbooks are imported with their tab headers checked. They give the platform your accounts, contacts and starting signals.", see: "Today, From your lists", guard: "Sheet content is shown as unverified until a live source confirms it. A renamed column pauses that tab." },
  { n: "01", t: "Watch", d: "Official tender feeds, company newsrooms and your two account sheets are checked on a schedule.", see: "Administration, Sources", guard: "A failed check is shown as degraded coverage. It is never read as 'nothing happened'." },
  { n: "02", t: "Verify", d: "Each change becomes a claim with a verbatim quote and a link to the original page.", see: "Evidence pills on every card", guard: "A claim without a matching quote cannot trigger a recommendation." },
  { n: "03", t: "Group", d: "Related events are joined into one initiative, such as a core banking programme, so repeat coverage appears once.", see: "Initiative name on the card", guard: "Syndicated copies of one announcement count as one event." },
  { n: "04", t: "Test", d: "Seven gates ask whether the account fits, the programme is real, the demand is external, and we can reach a buyer.", see: "Gate tracker, G1 to G7", guard: "Unknown demand stays unknown. The score is a priority weight, not a win probability." },
  { n: "05", t: "Recommend", d: "You get one next action with an objective, an owner, a trigger and a stop condition.", see: "Today and Opportunity", guard: "If only a buyer can answer, the action is a conversation, not more web research." },
  { n: "06", t: "Approve", d: "Drafts and Pipedrive changes are proposals. A second person approves the exact change before anything is written.", see: "Opportunity, then Administration, Approvals", guard: "Nothing is emailed from this system. CRM writes are off unless switched on." },
];

const GATES: [string, string, string][] = [
  ["G1", "Account and ICP", "Is this an account we should pursue?"],
  ["G2", "Initiative is real", "Is there a verified programme, with a stage?"],
  ["G3", "Engineering fit", "Does the work match what The Codest delivers?"],
  ["G4", "External demand", "Will the buyer source outside help? Often unknown until you ask."],
  ["G5", "Supplier access", "Can a supplier like us get in: direct, via a vendor or via a prime?"],
  ["G6", "Buyer path", "Do we know who decides, and have they said so?"],
  ["G7", "Delivery feasibility", "Can we staff it in the window they need?"],
];

export default function Guide() {
  return (
    <>
      <div className="hero">
        <div className="eyebrow">Start here</div>
        <h2>How it works</h2>
        <p className="sub">The platform turns public change signals and your account lists into a short, evidenced list of what to do next. It tells you what it knows, what it is assuming and what only a buyer can confirm.</p>
      </div>
      <div className="page">
        <div className="section-title" style={{ marginTop: 6 }}>From signal to action</div>
        <ol className="flow">
          {STEPS.map((s) => (
            <li key={s.n} className="flow-step">
              <div className="flow-n">{s.n}</div>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
              <div className="flow-see"><span className="k">You see it in</span>{s.see}</div>
              <div className="flow-guard"><span className="k">Guard rail</span>{s.guard}</div>
            </li>
          ))}
        </ol>

        <div className="section-title">Your day in three moves</div>
        <div className="moves">
          <div className="card"><div className="eyebrow">1 · Today</div><p style={{ margin: 0 }}>Read the top card. The right-hand panel is the one thing to do. Dismiss anything that does not apply and give a reason. It stays hidden until something material changes.</p></div>
          <div className="card"><div className="eyebrow">2 · Opportunity</div><p style={{ margin: 0 }}>Open it to see which gates are green, amber or red, the evidence behind each, and the questions that would close the gaps.</p></div>
          <div className="card"><div className="eyebrow">3 · Propose</div><p style={{ margin: 0 }}>Generate a draft or propose a Pipedrive activity. A colleague approves it in Administration before anything leaves the system.</p></div>
        </div>

        <div className="section-title">The seven gates</div>
        <div className="gate-legend">
          {GATES.map(([g, n, q]) => (<div key={g} className="g"><div className="gi">{g}</div><div className="gn">{n}</div><div className="gr">{q}</div></div>))}
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
