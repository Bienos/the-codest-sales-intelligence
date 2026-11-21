import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, Meter, humanize } from "../components/ui";

const usd = (v: number | null | undefined, d = 4) => (v === null || v === undefined ? "n/a" : `$${Number(v).toFixed(d)}`);
const num = (v: any) => Number(v ?? 0).toLocaleString();
type Tab = "overview" | "source" | "plan" | "ledger";

function Daily({ rows, provider, unit }: { rows: any[]; provider: string; unit: string }) {
  const days = rows.filter((r) => r.provider === provider);
  if (!days.length) return <p className="small muted">No calls this month yet.</p>;
  const max = Math.max(...days.map((d) => Number(d.units)), 1);
  return (
    <div className="chart" role="img" aria-label={`Daily ${provider} ${unit}`}>
      {days.map((d) => <div key={d.day} className="chart-col" title={`${d.day}: ${num(d.units)} ${unit}, ${d.calls} calls`}><i style={{ height: `${Math.max((Number(d.units) / max) * 100, 4)}%` }} /><span>{String(d.day).slice(8, 10)}</span></div>)}
    </div>
  );
}

function Provider({ name, label, unit, b, f, rows, month, cost, prices, capUsd }: any) {
  const used = b ? Number(b.spent) + Number(b.reserved) : 0;
  const cap = b ? Number(b.cap) : 0;
  const isUsd = unit === "usd";
  return (
    <div className="card kpi">
      <div className="eyebrow">{label}</div>
      <div className="kpi-n">{isUsd ? usd(used, 3) : num(used)}<span className="kpi-u">{isUsd ? " of " + usd(cap, 2) : ` of ${num(cap)} tokens${capUsd != null ? ` (about ${usd(capUsd, 2)})` : ""}`}</span></div>
      <Meter used={used} cap={cap} unit={unit} />
      <div className="small" style={{ marginTop: 10 }}>
        {f ? <>Month-end projection <b>{isUsd ? usd(f.projected_month_end, 2) : num(Math.round(f.projected_month_end))}</b> {f.will_exceed ? <Badge kind="bad">would exceed cap</Badge> : <Badge kind="ok">within cap</Badge>}</> : <span className="muted">No usage yet this month.</span>}
      </div>
      <div className="small muted" style={{ marginTop: 6 }}>
        {month ? `${num(month.calls)} calls${month.errors ? `, ${month.errors} failed` : ""}` : "0 calls"}
        {name === "anthropic" && month ? ` · ${num(month.tokens_in)} in / ${num(month.tokens_out)} out` : ""}
        {name === "anthropic" ? ` · ${prices.anthropic_in_per_mtok !== null ? `est. ${usd(cost, 3)}` : "dollar price not configured"}` : cost !== null && cost !== undefined ? ` · reported ${usd(cost, 3)}` : ""}
      </div>
      <Daily rows={rows} provider={name} unit={isUsd ? "searches" : "tokens"} />
    </div>
  );
}

function Ledger() {
  const [f, setF] = useState({ provider: "", status: "" });
  const [d, setD] = useState<any>(null); const [off, setOff] = useState(0); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { api(`/api/admin/usage/ledger?provider=${f.provider}&status=${f.status}&limit=50&offset=${off}`).then(setD).catch(setErr); }, [f, off]);
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <select aria-label="Provider" value={f.provider} onChange={(e) => { setOff(0); setF({ ...f, provider: e.target.value }); }}><option value="">All providers</option><option value="anthropic">Anthropic</option><option value="exa">Exa</option></select>
        <select aria-label="Status" value={f.status} onChange={(e) => { setOff(0); setF({ ...f, status: e.target.value }); }}><option value="">Any status</option><option value="ok">OK</option></select>
        {d && <span className="small muted">{num(d.total)} calls</span>}
      </div>
      <Err e={err} />
      {d && <div className="card table-wrap"><table><thead><tr><th>When</th><th>Call</th><th>Used</th><th>Cost</th><th>For</th></tr></thead><tbody>
        {d.items.map((u: any) => <tr key={u.id}><td>{fmtDateTime(u.at)}</td>
          <td><b>{humanize(u.provider)}</b> {u.operation}{u.status !== "ok" && <Badge kind="bad">{u.status}</Badge>}</td>
          <td>{num(u.units)} {u.unit}{u.tokens_in ? <div className="small muted">{num(u.tokens_in)} in · {num(u.tokens_out)} out</div> : null}</td>
          <td>{u.cost_usd === null ? <span className="muted">unknown</span> : usd(u.cost_usd)}<div className="small muted">{u.cost_basis}</div></td>
          <td className="small">{u.query ?? u.document_title ?? u.source_id ?? ""}{u.organization && <div className="muted">{u.organization}</div>}{u.document_url && <a href={u.document_url} target="_blank" rel="noreferrer noopener" className="small">source ↗</a>}</td></tr>)}
      </tbody></table></div>}
      <div className="row"><button disabled={off === 0} onClick={() => setOff(Math.max(0, off - 50))}>Newer</button><button disabled={!d || off + 50 >= d.total} onClick={() => setOff(off + 50)}>Older</button></div>
    </>
  );
}

export default function Usage() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => { api("/api/admin/usage").then(setD).catch(setErr); }, []);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  const m = (p: string) => d.months.find((x: any) => x.period === d.period && x.provider === p);
  const cost = (p: string) => m(p)?.cost_usd;
  const outcomes: Record<string, number> = Object.fromEntries(d.verification_outcomes.map((o: any) => [o.status, o.n]));
  const total = Object.values(outcomes).reduce((a, b) => a + b, 0);
  return (
    <>
      <p className="small muted" style={{ marginTop: 0 }}>Every metered call is recorded with what it was for. Month: <b>{d.period}</b>. {d.notes.join(" ")}</p>
      <div className="kpis">
        <Provider name="anthropic" label="Anthropic, model reads" unit="tokens" b={d.budgets.anthropic} f={d.forecast.anthropic} rows={d.daily} month={m("anthropic")} cost={cost("anthropic")} prices={d.prices} capUsd={d.anthropic_cap_usd_estimate} />
        <Provider name="exa" label="Exa, discovery searches" unit="usd" b={d.budgets.exa} f={d.forecast.exa} rows={d.daily} month={m("exa")} cost={cost("exa")} prices={d.prices} />
        <div className="card kpi">
          <div className="eyebrow">What the spend bought</div>
          <div className="kpi-n">{outcomes.verified ?? 0}<span className="kpi-u"> verified of {total} checked</span></div>
          <div className="chips" style={{ marginTop: 8 }}>{Object.entries(outcomes).map(([k, v]) => <Badge key={k} kind={k === "verified" ? "ok" : k === "failed" || k === "blocked" ? "bad" : k === "waiting_budget" ? "warn" : ""}>{humanize(k)} {v}</Badge>)}</div>
          <p className="small" style={{ margin: "12px 0 0" }}>{d.cost_per_verified_event_usd === null ? "Cost per verified opportunity appears once there is reported spend and a verified result." : <>Reported spend per verified result: <b>{usd(d.cost_per_verified_event_usd, 3)}</b></>}</p>
        </div>
      </div>
      <div className="tabs" role="tablist">{([["overview", "Months"], ["source", "By source"], ["plan", "By search plan"], ["ledger", "Every call"]] as [Tab, string][]).map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === "overview" && <div className="card table-wrap"><table><thead><tr><th>Month</th><th>Provider</th><th>Calls</th><th>Used</th><th>Cost</th><th>Failed</th></tr></thead><tbody>
        {d.months.map((x: any, i: number) => <tr key={i}><td>{x.period}</td><td>{humanize(x.provider)}</td><td>{num(x.calls)}</td><td>{num(x.units)}{x.provider === "anthropic" ? ` tokens (${num(x.tokens_in)} in, ${num(x.tokens_out)} out)` : " searches"}</td><td>{x.cost_usd === null ? <span className="muted">unknown</span> : usd(x.cost_usd)}</td><td>{x.errors}</td></tr>)}
        {d.months.length === 0 && <tr><td colSpan={6} className="muted">No metered calls yet.</td></tr>}</tbody></table></div>}
      {tab === "source" && <div className="card table-wrap"><table><thead><tr><th>Source</th><th>Provider</th><th>Calls</th><th>Used</th><th>Cost</th></tr></thead><tbody>
        {d.by_source.map((x: any, i: number) => <tr key={i}><td>{x.display_name ?? x.source_id}<div className="small muted">{x.source_id}</div></td><td>{humanize(x.provider)}</td><td>{num(x.calls)}</td><td>{num(x.units)}</td><td>{x.cost_usd === null ? "unknown" : usd(x.cost_usd)}</td></tr>)}
        {d.by_source.length === 0 && <tr><td colSpan={5} className="muted">Nothing recorded this month.</td></tr>}</tbody></table></div>}
      {tab === "plan" && <div className="card table-wrap"><table><thead><tr><th>Search plan</th><th>Searches</th><th>Results</th><th>Cost</th></tr></thead><tbody>
        {d.by_plan.map((x: any) => <tr key={x.plan_id}><td><b>{humanize(x.theme)}</b> · {x.geography}<div className="small muted">{x.query}</div></td><td>{x.searches}</td><td>{x.results}</td><td>{usd(x.cost_usd)}</td></tr>)}
        {d.by_plan.length === 0 && <tr><td colSpan={4} className="muted">No discovery searches yet.</td></tr>}</tbody></table></div>}
      {tab === "ledger" && <Ledger />}
    </>
  );
}
