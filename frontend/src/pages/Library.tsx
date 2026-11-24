import { number, tr } from "../locale";
import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize, Skeleton, reloadPage } from "../components/ui";

const TIERS: Record<string, string> = { S1: "Official records", S2: "Company\u2019s own sites", S3: "Vendors and integrators", S4: "Trade press", S5: "Search finds", internal: "Internal" };
const hk = (s: string) => s === "healthy" ? "ok" : ["degraded", "failed", "blocked", "failing"].includes(s) ? "bad" : "";
const HEALTH: Record<string, string> = { healthy: "Working", unconfigured: "Not set up yet", paused: "Paused", degraded: "Having problems", failing: "Failing", failed: "Failed", blocked: "Access blocked" };
const METHOD: Record<string, string> = { feed: "Reads its feed", change_monitor: "Checks the page for changes", api: "Calls its API", official_api: "Calls its API", search: "Searches the web", internal: "Internal" };
const cadence = (seconds: number) => {
  if (!seconds) return "";
  if (seconds === 86400) return ` · ${tr("daily")}`;
  if (seconds === 3600) return ` · ${tr("hourly")}`;
  return seconds >= 86400 ? ` · ${tr("every {} days", Math.round(seconds / 86400))}` : ` · ${tr("every {} minutes", Math.max(1, Math.round(seconds / 60)))}`;
};

export default function Library({ render, query, canSetup = true }: { render: (s: any, reload: () => void) => any; query?: string; canSetup?: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [open, setOpen] = useState<string | null>(null); const [qLocal, setQ] = useState("");
  const q = query ?? qLocal;
  const [signals, setSignals] = useState<Record<string, string>>({});
  useEffect(() => {
    api("/api/library/signals").then((r) => setSignals(Object.fromEntries(r.items.map((item: any) => [item.rule_id, item.signal])))).catch(() => undefined);
  }, []);
  const load = () => api("/api/admin/library").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <Err e={err} onRetry={reloadPage} />;
  if (!d) return <Skeleton />;
  const items = d.items.filter((s: any) => !q || `${s.display_name} ${s.source_id} ${s.provider}`.toLowerCase().includes(q.toLowerCase()));
  const groups = Object.keys(TIERS).map((t) => [t, items.filter((s: any) => s.source_tier === t)] as [string, any[]]).filter(([, l]) => l.length);
  const live = d.items.filter((s: any) => s.status === "active" && s.collection_method !== "internal" && s.last_success_at).length;
  return (
    <>
      <div className="row spread mb-2">
        <p className="small muted m-0"><b>{d.items.length}</b>{" " + tr("sources,") + " "}<b>{live}</b>{" " + tr("active with a successful check. Open a source to see its last test and whether monitoring is running.")}</p>
        {query === undefined && <input aria-label={tr("Search sources")} placeholder={tr("Search sources")} value={q} onChange={(e) => setQ(e.target.value)} className="field-md" />}
      </div>
      {groups.map(([t, list]) => (
        <section key={t}><div className="section-title">{tr(TIERS[t])}</div>
          <div className="lib-grid">{list.map((s) => (
            <article key={s.source_id} className="lib-card">
              <div className="row spread start">
                <div><b className="lib-name">{s.display_name}</b><div className="small muted">{s.provider ?? s.source_id} · {humanize(s.source_family)}</div></div>
                <div className="row"><Badge kind={s.status === "active" ? "ok" : s.status === "pending_connector_validation" ? "warn" : "bad"}>{s.status === "pending_connector_validation" ? tr("Not set up yet") : humanize(s.status)}</Badge></div>
              </div>
              <dl className="lib-facts">
                <dt>{tr("How")}</dt><dd>{tr(METHOD[s.collection_method] ?? humanize(s.collection_method))}{cadence(s.poll_interval_s)}</dd>
                <dt>{tr("Monitoring")}</dt><dd><Badge kind={hk(s.health)}>{s.health === "healthy" && !s.last_success_at ? tr("Waiting for first successful check") : tr(HEALTH[s.health ?? "unconfigured"] ?? humanize(s.health))}</Badge>{s.last_success_at && <span className="small muted">{" " + tr("last worked") + " "}{fmtDateTime(s.last_success_at)}</span>}</dd>
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
              {canSetup && s.canonical_domain && ["feed", "change_monitor"].includes(s.collection_method) && <button className="ghost" aria-expanded={open === s.source_id} onClick={() => setOpen(open === s.source_id ? null : s.source_id)}>{open === s.source_id ? tr("Close setup") : s.url ? tr("Manage") : tr("Set up")}</button>}
              {open === s.source_id && <div className="mt-3">{render(s, load)}</div>}
            </article>))}</div>
        </section>))}
    </>
  );
}
