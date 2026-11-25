import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";

const TIERS: Record<string, string> = { S1: "Official records", S2: "Company’s own sites", S3: "Vendors and integrators", S4: "Trade press", S5: "Search finds", internal: "Internal" };
const hk = (s: string) => s === "healthy" ? "ok" : ["degraded", "failed", "blocked", "failing"].includes(s) ? "bad" : "";
const HEALTH: Record<string, string> = { healthy: "Working", unconfigured: "Not set up yet", paused: "Paused", degraded: "Having problems", failing: "Failing", failed: "Failed", blocked: "Access blocked" };
const METHOD: Record<string, string> = { feed: "Reads its feed", change_monitor: "Checks the page for changes", api: "Calls its API", official_api: "Calls its API", search: "Searches the web", internal: "Internal" };
const FAMILY: Record<string, string> = { attention: "Press and news", vendor: "Vendors and integrators", regulatory: "Regulators", procurement: "Tenders", jobs: "Job boards",
  verification: "Page checks", registry: "Company registers", internal: "Internal", company_ir: "Company news and careers", discovery: "Web search" };
const MODES: Record<string, string> = { account: "Runs per account", identity: "Company identity checks", on_demand: "On demand or requires setup" };

/** Pipedrive is not connected and is hidden from the interface for now; the API still lists it as an internal source. */
const hidden = (s: any) => /pipedrive/i.test(`${s.source_id} ${s.provider ?? ""}`);

const cadence = (seconds: number) => {
  if (!seconds) return "";
  if (seconds === 86400) return ` · ${tr("daily")}`;
  if (seconds === 3600) return ` · ${tr("hourly")}`;
  return seconds >= 86400 ? ` · ${tr("every {} days", Math.round(seconds / 86400))}` : ` · ${tr("every {} minutes", Math.max(1, Math.round(seconds / 60)))}`;
};

/** Every source as one compact row per source, grouped by kind of source; a row opens to show its checks and, for administrators, its setup. */
export default function Library({ render, query, canSetup = true }: { render: (s: any, reload: () => void) => any; query?: string; canSetup?: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [setup, setSetup] = useState<string | null>(null); const [qLocal, setQ] = useState("");
  const q = query ?? qLocal;
  const [monitoring, setMonitoring] = useState("");
  const [signals, setSignals] = useState<Record<string, string>>({});
  useEffect(() => {
    api("/api/library/signals").then((r) => setSignals(Object.fromEntries(r.items.map((item: any) => [item.rule_id, item.signal])))).catch(() => undefined);
  }, []);
  const load = () => api("/api/admin/library").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const periodic = (s: any) => s.monitoring_mode === "scheduled";
  const isStale = (s: any) => periodic(s) && s.last_success_at && Date.now() - Date.parse(s.last_success_at) > 2000 * s.poll_interval_s;
  const isReporting = (s: any) => periodic(s) && s.status === "active" && s.health === "healthy" && s.last_success_at && !isStale(s);
  /** One plain status per source: a colour for the dot and the words next to it. */
  const state = (s: any): [string, string] => {
    if (!periodic(s)) return ["info", MODES[s.monitoring_mode] ?? "On demand or requires setup"];
    if (isReporting(s)) return ["ok", "Reporting successfully"];
    if (isStale(s)) return ["warn", "Last check overdue"];
    if (s.status === "pending_connector_validation") return ["warn", "Not set up yet"];
    if (s.status === "paused") return ["", "Paused"];
    if (s.health === "healthy") return ["warn", "Waiting for first successful check"];
    return ["bad", HEALTH[s.health] ?? "Needs attention"];
  };
  const all = d.items.filter((s: any) => !hidden(s));
  const items = all.filter((s: any) =>
    (!q || `${s.display_name} ${s.source_id} ${s.provider}`.toLowerCase().includes(q.toLowerCase())) &&
    (!monitoring || (monitoring === "reporting" ? isReporting(s) : monitoring === "needs_attention" ? periodic(s) && !isReporting(s) : !periodic(s))));
  const groups = Object.keys(TIERS).map((t) => [t, items.filter((s: any) => s.source_tier === t)] as [string, any[]]).filter(([, l]) => l.length);
  const live = all.filter(isReporting).length;
  return (
    <>
      <div className="row spread mb-2">
        <p className="small muted m-0"><b>{all.length}</b>{" " + tr("sources,") + " "}<b>{live}</b>{" " + tr("active with a successful check. Open a source to see its last test and whether monitoring is running.")}</p>
        {query === undefined && <input aria-label={tr("Search sources")} placeholder={tr("Search sources")} value={q} onChange={(e) => setQ(e.target.value)} className="field-md" />}
      </div>
      <div className="row mb-3">
        <label>{tr("Monitoring status")} <select value={monitoring} onChange={e => setMonitoring(e.target.value)}>
          <option value="">{tr("All sources")}</option><option value="reporting">{tr("Reporting successfully")}</option>
          <option value="needs_attention">{tr("Scheduled sources needing attention")}</option><option value="other">{tr("Sources outside the regular schedule")}</option>
        </select></label>
      </div>
      <p className="small muted">{tr("Company careers and supplier pages discovered for an account appear on its profile.")}</p>
      {items.length === 0 && <div className="empty">{tr("No sources match these filters")}</div>}
      {groups.map(([t, list]) => (
        <details key={t} className="srcgroup" open>
          <summary className="section-title"><span>{tr(TIERS[t])}</span><span className="srcgroup-n">{number(list.length)}</span></summary>
          <div className="srclist-head" aria-hidden="true"><span>{tr("Source")}</span><span>{tr("Kind")}</span><span>{tr("Status")}</span><span>{tr("Last successful check")}</span></div>
          <div className="sigs">{list.map((s) => {
            const [kind, label] = state(s);
            return (
              <details key={s.source_id} className="sigrow srcrow">
                <summary>
                  <span className="srcrow-name"><span className="srcrow-t">{s.display_name}</span><span className="small muted">{s.provider ?? s.source_id}</span></span>
                  <span className="srcrow-kind small">{FAMILY[s.source_family] ? tr(FAMILY[s.source_family]) : humanize(s.source_family)}</span>
                  <span className={`dotlabel ${kind}`}>{tr(label)}</span>
                  <span className="srcrow-last small">{s.last_success_at ? <><span className="srcrow-lab">{tr("Last successful check") + ": "}</span>{fmtDateTime(s.last_success_at)}</> : <span className="muted">{tr("No successful check yet")}</span>}</span>
                </summary>
                <dl className="lib-facts wide">
                  <dt>{tr("How")}</dt><dd>{tr(METHOD[s.collection_method] ?? humanize(s.collection_method))}{periodic(s) ? cadence(s.poll_interval_s) : ""}</dd>
                  {periodic(s) && <><dt>{tr("Monitoring")}</dt><dd><Badge kind={isStale(s) ? "warn" : hk(s.health)}>{isStale(s) ? tr("Last check overdue") : s.health === "healthy" && !s.last_success_at ? tr("Waiting for first successful check") : tr(HEALTH[s.health ?? "unconfigured"] ?? humanize(s.health))}</Badge>{s.last_success_at && <span className="small muted">{" " + tr("last worked") + " "}{fmtDateTime(s.last_success_at)}</span>}</dd></>}
                  {s.health_detail && <><dt>{tr("Last error")}</dt><dd className="small">{s.health_detail}</dd></>}
                  <dt>{tr("Latest test")}</dt><dd>{s.validation ? <>
                    <Badge kind={s.validation.ok ? "ok" : "bad"}>{s.validation.ok ? tr("Test passed") : tr("Test failed")}</Badge>
                    <span className="small muted"> {fmtDateTime(s.validation.at)}</span>
                    {!s.validation.ok && s.validation.detail && <div className="small">{s.validation.detail}</div>}
                  </> : <span className="muted">{tr("No test on record")}</span>}</dd>
                  {s.last_attempt_at && <><dt>{tr("Last attempt")}</dt><dd className="small">{fmtDateTime(s.last_attempt_at)}</dd></>}
                  <dt>{tr("Feeds signals")}</dt><dd>{s.signal_rule_ids?.length ? s.signal_rule_ids.map((r: string) => <span key={r} className="pill" title={r}>{signals[r] ?? r}</span>) : <span className="muted">{tr("None yet")}</span>}</dd>
                  <dt>{tr("So far")}</dt><dd>{s.documents}{" " + tr("pages read,") + " "}{s.events}{" " + tr("findings")}{Number(s.cost_usd_month) > 0 && <span className="small muted"> · ${Number(s.cost_usd_month).toFixed(3)}{" " + tr("this month")}</span>}</dd>
                  <dt>{tr("Last 30 days")}</dt><dd>{number(s.recent_buying_findings ?? 0)}{" " + tr("verified findings with current buying signals")}</dd>
                  <dt>{tr("Permission")}</dt><dd className="small">{s.licence_notes ?? humanize(s.retention_policy)}</dd>
                  {s.url && <><dt>{tr("Page")}</dt><dd className="small"><a href={s.url} target="_blank" rel="noreferrer noopener">{s.url.replace(/^https?:\/\//, "").slice(0, 52)} ↗</a></dd></>}
                </dl>
                {canSetup && s.canonical_domain && ["feed", "change_monitor"].includes(s.collection_method) && <button className="ghost mb-3" aria-expanded={setup === s.source_id} onClick={() => setSetup(setup === s.source_id ? null : s.source_id)}>{setup === s.source_id ? tr("Close setup") : s.url ? tr("Manage") : tr("Set up")}</button>}
                {setup === s.source_id && <div className="srcrow-setup">{render(s, load)}</div>}
              </details>);
          })}</div>
        </details>))}
    </>
  );
}
