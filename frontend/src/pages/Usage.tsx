import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, Meter, humanize, Skeleton, reloadPage, Tabs } from "../components/ui";
import "../styles/admin.css";

const usd = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? tr("Not known") : `$${Number(v).toFixed(d)}`);
const num = (v: any) => number(Number(v ?? 0));
type Tab = "overview" | "source" | "plan" | "ledger";

function Daily({ rows, provider, unit }: { rows: any[]; provider: string; unit: string }) {
  const days = rows.filter((r) => r.provider === provider);
  if (!days.length) return <p className="small muted mb-0">{tr("Nothing used this month yet.")}</p>;
  const max = Math.max(...days.map((d) => Number(d.units)), 1);
  const first = days[0].day;
  const last = days[days.length - 1].day;
  return (
    <figure className="usage-chart">
    <div className="chart" role="img" aria-label={tr("Daily {} use, in {}", provider, tr(unit))}>
      {days.map((d) => <div key={d.day} className="chart-col" title={tr("{}: {} {}, {} calls", d.day, num(d.units), tr(unit), d.calls)}><i style={{ height: `${Math.max((Number(d.units) / max) * 100, 4)}%` }} /></div>)}
    </div>
    <figcaption className="small muted row spread"><span>{first}</span>{first !== last && <span>{last}</span>}</figcaption>
    </figure>
  );
}

/** One paid service this month: what it cost against its limit, where the month is heading, and a small daily chart. */
function Provider({ name, label, unit, b, f, rows, month, cost, prices, capUsd }: any) {
  const used = b ? Number(b.spent) + Number(b.reserved) : 0;
  const cap = b ? Number(b.cap) : 0;
  const isUsd = unit === "usd";
  const priced = !isUsd && capUsd != null && cap > 0;
  return (
    <div className="spend-card">
      <h3 className="spend-t">{tr(label)}</h3>
      <div className="spend-n">{isUsd ? usd(used) : priced ? usd(cost ?? (used / cap) * capUsd) : num(used)}<span>{" "}{isUsd ? tr("of {} limit", usd(cap)) : priced ? tr("of about {} limit", usd(capUsd)) : tr("tokens used")}</span></div>
      {!isUsd && <p className="small muted m-0">{tr("{} of {} tokens. A token is a small chunk of text the AI reads or writes.", num(used), num(cap))}</p>}
      <Meter used={used} cap={cap} unit={unit} />
      <p className="small m-0">
        {f ? <>{tr("On this pace the month ends at") + " "}<b>{isUsd ? usd(f.projected_month_end) : priced ? usd((f.projected_month_end / cap) * capUsd) : num(Math.round(f.projected_month_end))}</b> {f.will_exceed ? <Badge kind="bad">{tr("Would go over the limit")}</Badge> : <Badge kind="ok">{tr("Within the limit")}</Badge>}</> : <span className="muted">{tr("Nothing used yet this month.")}</span>}
      </p>
      <p className="small muted m-0">
        {month ? tr("{} calls", num(month.calls)) + (month.errors ? tr(", {} failed", month.errors) : "") : tr("No calls yet")}
        {name === "anthropic" && month ? ` · ${tr("{} in / {} out", num(month.tokens_in), num(month.tokens_out))}` : ""}
        {name === "anthropic" ? ` · ${prices.anthropic_in_per_mtok !== null ? tr("estimated cost {}", usd(cost)) : tr("price per token not set")}` : cost !== null && cost !== undefined ? ` · ${tr("reported cost {}", usd(cost))}` : ""}
      </p>
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
      <div className="row mb-3">
        <select aria-label={tr("Service")} value={f.provider} onChange={(e) => { setOff(0); setF({ ...f, provider: e.target.value }); }}><option value="">{tr("All services")}</option><option value="anthropic">Anthropic</option><option value="exa">Exa</option></select>
        <select aria-label={tr("Status")} value={f.status} onChange={(e) => { setOff(0); setF({ ...f, status: e.target.value }); }}><option value="">{tr("Any status")}</option><option value="ok">OK</option></select>
        {d && <span className="small muted">{num(d.total)}{" " + tr("calls")}</span>}
      </div>
      <Err e={err} />
      {d && d.items.length === 0 && <p className="quiet">{tr("No paid calls match.")}</p>}
      {d && d.items.length > 0 && <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Every paid call")}><table><thead><tr><th>{tr("When")}</th><th>{tr("Call")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th><th>{tr("What it was for")}</th></tr></thead><tbody>
        {d.items.map((u: any) => <tr key={u.id}><td>{fmtDateTime(u.at)}</td>
          <td><b>{humanize(u.provider)}</b> {u.operation}{u.status !== "ok" && <Badge kind="bad">{u.status}</Badge>}</td>
          <td>{num(u.units)} {u.unit}{u.tokens_in ? <div className="small muted">{num(u.tokens_in)}{" " + tr("in ·") + " "}{num(u.tokens_out)}{" " + tr("out")}</div> : null}</td>
          <td>{u.cost_usd === null ? <span className="muted">{tr("unknown")}</span> : usd(u.cost_usd)}<div className="small muted">{u.cost_basis}</div></td>
          <td className="small">{u.query ?? u.document_title ?? u.source_id ?? ""}{u.organization && <div className="muted">{u.organization}</div>}{u.document_url && <a href={u.document_url} target="_blank" rel="noreferrer noopener" className="small">{tr("source ↗")}</a>}</td></tr>)}
      </tbody></table></div>}
      {d && d.total > 50 && <div className="row mt-3"><button disabled={off === 0} onClick={() => setOff(Math.max(0, off - 50))}>{tr("Newer")}</button><button disabled={!d || off + 50 >= d.total} onClick={() => setOff(off + 50)}>{tr("Older")}</button></div>}
    </>
  );
}

export default function Usage() {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => { api("/api/admin/usage").then(setD).catch(setErr); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const m = (p: string) => d.months.find((x: any) => x.period === d.period && x.provider === p);
  const cost = (p: string) => m(p)?.cost_usd;
  const outcomes: Record<string, number> = Object.fromEntries(d.verification_outcomes.map((o: any) => [o.status, o.n]));
  const aboveLimit = [[tr("AI reading"), "anthropic"], [tr("Web search"), "exa"]].filter(([, provider]) => d.forecast[provider]?.will_exceed).map(([label]) => label);
  const total = Object.values(outcomes).reduce((a, b) => a + b, 0);
  return (
    <>
      <p className="adm-lead">{tr("Every paid call is recorded with what it was for. This month:") + " "}<b>{d.period}</b>.</p>
      {aboveLimit.length > 0 && <div className="callout mb-4" role="status">{tr("On pace to exceed the monthly limit: {}.", aboveLimit.join(", "))}</div>}
      <div className="spend-grid">
        <Provider name="anthropic" label="AI reading (Anthropic)" unit="tokens" b={d.budgets.anthropic} f={d.forecast.anthropic} rows={d.daily} month={m("anthropic")} cost={cost("anthropic")} prices={d.prices} capUsd={d.anthropic_cap_usd_estimate} />
        <Provider name="exa" label="Web search (Exa)" unit="usd" b={d.budgets.exa} f={d.forecast.exa} rows={d.daily} month={m("exa")} cost={cost("exa")} prices={d.prices} />
        <div className="spend-card">
          <h3 className="spend-t">{tr("What the spend bought")}</h3>
          <div className="spend-n">{num(outcomes.verified ?? 0)}<span>{" " + tr("of {} pages gave a checked finding", num(total))}</span></div>
          {Object.keys(outcomes).length > 0 && <div className="chips mb-0">{Object.entries(outcomes).map(([k, v]) => <Badge key={k} kind={k === "verified" ? "ok" : k === "failed" || k === "blocked" ? "bad" : k === "waiting_budget" ? "warn" : ""}>{humanize(k)} {v}</Badge>)}</div>}
          <p className="small m-0">{d.cost_per_verified_event_usd === null ? tr("The cost of each checked finding appears once there is spend and a result.") : <>{tr("Cost of each checked finding:") + " "}<b>{usd(d.cost_per_verified_event_usd)}</b></>}</p>
        </div>
      </div>
      <p className="small muted">{d.notes.join(" ")}</p>
      <Tabs items={[["overview", "By month"], ["source", "By source"], ["plan", "By saved search"], ["ledger", "Every call"]].map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setTab(k as Tab)} label="Usage views">
      {tab === "overview" && (d.months.length === 0 ? <p className="quiet">{tr("No paid calls yet.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Paid calls by month")}><table><thead><tr><th>{tr("Month")}</th><th>{tr("Service")}</th><th>{tr("Calls")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th><th>{tr("Failed")}</th></tr></thead><tbody>
        {d.months.map((x: any, i: number) => <tr key={i}><td>{x.period}</td><td>{humanize(x.provider)}</td><td>{num(x.calls)}</td><td>{x.provider === "anthropic" ? tr("{} tokens ({} read, {} written)", num(x.units), num(x.tokens_in), num(x.tokens_out)) : tr("{} searches", num(x.units))}</td><td>{x.cost_usd === null ? <span className="muted">{tr("unknown")}</span> : usd(x.cost_usd)}</td><td>{x.errors}</td></tr>)}
        </tbody></table></div>)}
      {tab === "source" && (d.by_source.length === 0 ? <p className="quiet">{tr("Nothing recorded this month.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Paid calls by source")}><table><thead><tr><th>{tr("Source")}</th><th>{tr("Provider")}</th><th>{tr("Calls")}</th><th>{tr("Used")}</th><th>{tr("Cost")}</th></tr></thead><tbody>
        {d.by_source.map((x: any, i: number) => <tr key={i}><td>{x.display_name ?? x.source_id}<div className="small muted">{x.source_id}</div></td><td>{humanize(x.provider)}</td><td>{num(x.calls)}</td><td>{num(x.units)}</td><td>{x.cost_usd === null ? tr("unknown") : usd(x.cost_usd)}</td></tr>)}
        </tbody></table></div>)}
      {tab === "plan" && (d.by_plan.length === 0 ? <p className="quiet">{tr("No web searches yet.")}</p> : <div className="table-wrap" tabIndex={0} role="region" aria-label={tr("Web searches by saved search")}><table><thead><tr><th>{tr("Saved search")}</th><th>{tr("Searches")}</th><th>{tr("Results")}</th><th>{tr("Cost")}</th></tr></thead><tbody>
        {d.by_plan.map((x: any) => <tr key={x.plan_id}><td><b>{humanize(x.theme)}</b> · {x.geography}<div className="small muted">{x.query}</div></td><td>{x.searches}</td><td>{x.results}</td><td>{usd(x.cost_usd)}</td></tr>)}
        </tbody></table></div>)}
      {tab === "ledger" && <Ledger />}
      </Tabs>
    </>
  );
}
