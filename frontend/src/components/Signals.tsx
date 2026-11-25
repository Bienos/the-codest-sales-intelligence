import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { counted, number, tr } from "../locale";
import { Err, Skeleton, Strength, groupName } from "./ui";

/** Short, plain names for the signals that can fire. The full rule text stays one click away. */
export const SHORT: Record<string, string> = {
  "SRC-01": "Open tender for software work", "SRC-02": "Supplier panel open", "SRC-04": "Integrator won a programme", "SRC-05": "Platform vendor signed a client",
  "SRC-06": "Contract engineers requested", "CAP-01": "Hiring several engineers", "CAP-02": "Contract engineering roles", "CAP-05": "Hiring for AI in production",
  "PRG-01": "Core banking replacement", "PRG-02": "Cloud or data migration", "PRG-06": "Payments scheme change", "MAN-01": "New technology leader",
  "REL-03": "Known contact in a new role", "COM-01": "Funding round", "COM-02": "Acquisition with integration work", "COM-03": "New market launch",
  "COM-04": "New licence for a launch", "COM-05": "Customer go-live", "COM-06": "New product line", "COM-07": "Partnership with integration work",
  "COM-08": "Cost programme with a technology scope",
};
export const signalName = (id: string, fallback?: string) => tr(SHORT[id] ?? fallback ?? id);

/** The families of buying signal (the letters before the dash), in the order pages show them, with a plain name and one line on what they mean. */
export const FAMILIES = ["SRC", "CAP", "PRG", "MAN", "COM", "REL"] as const;
export const FAMILY: Record<string, { name: string; meaning: string }> = {
  SRC: { name: "Tenders and buying routes", meaning: "They are buying now: a tender, a supplier panel, or a partner chosen for a programme." },
  CAP: { name: "Hiring", meaning: "They are hiring more engineers than they can easily find." },
  PRG: { name: "Funded projects", meaning: "A funded programme with named systems is under way." },
  MAN: { name: "New technology leaders", meaning: "A new technology leader is setting the roadmap and the team model." },
  COM: { name: "Funding and deals", meaning: "Money raised or a deal done that comes with build or integration work." },
  REL: { name: "People we know", meaning: "Someone who knows us has moved to a new role or asked for help." },
};
export const familyOf = (ruleId?: string | null) => (ruleId ?? "").slice(0, 3).toUpperCase();
export const familyName = (family: string) => tr(FAMILY[family]?.name ?? family);

export type SignalRef = { rule_id: string; name: string; strength: number; group?: string; meaning?: string };
export type SignalOption = { rule_id: string; group: string; name: string; strength: number; what_it_means: string; how_to_spot: string | null; who_to_contact: string | null;
  accounts: number; reachable_accounts: number; latest: string | null };

/** One signal on a finding: what kind it is, how strong, and (on hover or focus) what it means for us. */
export function SignalTag({ s }: { s: SignalRef }) {
  return <span className="sigtag" title={s.meaning ? tr(s.meaning) : s.name} tabIndex={0} aria-label={tr("Buying signal: {}. {}", signalName(s.rule_id, s.name), s.meaning ? tr(s.meaning) : "")}>
    <span className="sigtag-g">{groupName(s.rule_id.slice(0, 3))}</span>
    <b>{signalName(s.rule_id, s.name)}</b>
    {s.strength > 0 && <Strength n={s.strength} />}
  </span>;
}

/** Which buying signals are live right now and on how many accounts. Picking one filters the list below it. */
export function SignalBoard({ days = 30, target = false, active, onPick, canCampaign }: {
  days?: number; target?: boolean; active: string; onPick: (rule: string) => void; canCampaign: boolean;
}) {
  const [data, setData] = useState<{ items: SignalOption[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setError(null);
    api(`/api/signal-campaigns/options?days=${days}${target ? "&target=true" : ""}`, { signal: request.signal })
      .then(setData).catch((e) => { if (!request.signal.aborted) setError(e); });
    return () => request.abort();
  }, [days, target, revision]);
  if (error) return <Err e={error} onRetry={() => setRevision((n) => n + 1)} />;
  if (!data) return <Skeleton rows={2} label={tr("Loading signals")} />;
  const live = data.items.filter((s) => s.accounts > 0).sort((a, b) => b.accounts - a.accounts || b.strength - a.strength);
  const quiet = data.items.filter((s) => s.accounts === 0);
  return <section className="sigboard" aria-labelledby="sigboard-h">
    <div className="row spread finding-heading">
      <h2 id="sigboard-h">{tr("Buying signals right now")}</h2>
      {active && <button type="button" className="linkish" onClick={() => onPick("")}>{tr("Show all signals")}</button>}
    </div>
    <p className="small muted mt-0">{tr("Verified signals from the last {} days, by kind. Pick one to see only those companies.", days)}</p>
    {live.length === 0 ? <div className="empty"><b>{tr("No verified buying signal in this window yet.")}</b>{tr("Signals appear as sources are read and checked.")}</div> :
      <div className="sigboard-grid">
        {live.map((s) => <div key={s.rule_id} className={`sigtile${active === s.rule_id ? " on" : ""}`}>
          <button type="button" className="sigtile-pick" aria-pressed={active === s.rule_id} onClick={() => onPick(active === s.rule_id ? "" : s.rule_id)}>
            <span className="sigtile-g">{groupName(s.rule_id.slice(0, 3))} <Strength n={s.strength} /></span>
            <span className="sigtile-n">{signalName(s.rule_id, s.name)}</span>
            <span className="sigtile-c"><b>{number(s.accounts)}</b> {counted(s.accounts, ["company", "companies"], ["firma", "firmy", "firm"]).replace(/^[\d\s.,\u00a0]+/, "")}
              {s.latest && <span className="muted"> · {tr("latest {}", fmtDate(s.latest))}</span>}</span>
            <span className="sigtile-m">{tr(s.what_it_means)}</span>
          </button>
          <div className="sigtile-foot small">
            <span className="muted">{tr("{} with an email contact", number(s.reachable_accounts))}</span>
            {canCampaign && s.reachable_accounts > 0 && <Link to={`/campaigns/new?rules=${s.rule_id}`}>{tr("Start a campaign")} →</Link>}
          </div>
        </div>)}
      </div>}
    {quiet.length > 0 && <details className="small mt-2"><summary>{tr("Signals we watch for with nothing right now ({})", quiet.length)}</summary>
      <ul className="tight">{quiet.map((s) => <li key={s.rule_id}><b>{signalName(s.rule_id, s.name)}</b>: {tr(s.what_it_means)}</li>)}</ul></details>}
  </section>;
}
