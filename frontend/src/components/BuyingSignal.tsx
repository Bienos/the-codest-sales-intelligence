import { tr } from "../locale";
import { fmtDate } from "../api";
import { SHORT } from "./Signals";

export type BuyingSignal = {
  rule_id?: string;
  meaning?: string;
  label: string;
  demand_tier: string;
  summary: string;
  happened_at: string | null;
  published_at: string | null;
  excerpt: string | null;
  source_url: string;
  source_name: string | null;
  commercial_questions: string[];
};

export const demandLabel = (tier: string) => ({
  D1: tr("External demand evidenced"),
  D2: tr("External need inferred"),
  D3: tr("Programme evidence · buying route unconfirmed"),
  D4: tr("Early context · no evidence of demand"),
}[tier] ?? "Demand unconfirmed");

export function SignalSummary({ signal, detailed = false }: { signal?: BuyingSignal | null; detailed?: boolean }) {
  if (!signal) return <span className="small muted">{tr("No current source-backed buying signal.")}</span>;
  let source: URL;
  try { source = new URL(signal.source_url); } catch { return <span className="small muted">{tr("Source link unavailable.")}</span>; }
  if (!["http:", "https:"].includes(source.protocol)) return <span className="small muted">{tr("Source link unavailable.")}</span>;
  const short = signal.rule_id && SHORT[signal.rule_id] ? tr(SHORT[signal.rule_id]) : null;
  return <div className="buying-signal">
    <strong>{short ?? signal.label}</strong>
    {short && detailed && <div className="small muted">{tr("Rule {}: {}", signal.rule_id, signal.label)}</div>}
    <div className="small muted">{demandLabel(signal.demand_tier)}</div>
    {detailed && signal.meaning && <p className="small mt-1 mb-1"><b>{tr("Why this is a buying signal")}:</b> {tr(signal.meaning)}</p>}
    <p className="small mt-1 mb-1">{signal.summary}</p>
    <div className="small">
      {signal.happened_at ? fmtDate(signal.happened_at) + " · " : signal.published_at ? "Published " + fmtDate(signal.published_at) + " · " : ""}
      <a href={signal.source_url} target="_blank" rel="noreferrer noopener" aria-label={tr("Original source for {}", signal.label)}>{signal.source_name || source.hostname} ↗</a>
    </div>
    {detailed && signal.excerpt && <blockquote className="fquote mt-2 mb-0">“{signal.excerpt}”</blockquote>}
    {detailed && signal.commercial_questions?.[0] && <p className="small mt-2 mb-0"><b>{tr("Qualify next:")}</b> {signal.commercial_questions[0]}</p>}
  </div>;
}
