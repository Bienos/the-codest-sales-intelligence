import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const TIERS: Record<string, string> = { S1: "Official records", S2: "Company\u2019s own sites", S3: "Vendors and integrators", S4: "Trade press", S5: "Search finds", internal: "Internal" };
const hk = (s: string) => (s === "healthy" ? "ok" : !s || s === "unconfigured" || s === "paused" ? "" : "bad");
const HEALTH: Record<string, string> = { healthy: "Working", unconfigured: "Not set up yet", paused: "Paused", degraded: "Having problems", failing: "Failing" };
const METHOD: Record<string, string> = { feed: "Reads its feed", change_monitor: "Checks the page for changes", api: "Calls its API", internal: "Internal" };
const every = (s: number) => (s >= 86400 ? `${Math.round(s / 86400)}d` : `${Math.round(s / 3600)}h`);

export default function Library({ render, query, canSetup = true }: { render: (s: any, reload: () => void) => any; query?: string; canSetup?: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [open, setOpen] = useState<string | null>(null); const [qLocal, setQ] = useState("");
  const q = query ?? qLocal;
  const load = () => api("/api/admin/library").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading sources…</p>;
  const items = d.items.filter((s: any) => !q || `${s.display_name} ${s.source_id} ${s.provider}`.toLowerCase().includes(q.toLowerCase()));
  const groups = Object.keys(TIERS).map((t) => [t, items.filter((s: any) => s.source_tier === t)] as [string, any[]]).filter(([, l]) => l.length);
  const live = d.items.filter((s: any) => s.status === "active" && s.collection_method !== "internal").length;
  return (
    <>
      <div className="row spread mb-2">
        <p className="small muted m-0"><b>{d.items.length}</b> sources, <b>{live}</b> live. A source only runs once it has passed a test and someone has confirmed we may read it.</p>
        {query === undefined && <input aria-label="Search sources" placeholder="Search sources" value={q} onChange={(e) => setQ(e.target.value)} className="field-md" />}
      </div>
      {groups.map(([t, list]) => (
        <section key={t}><div className="section-title">{TIERS[t]}</div>
          <div className="lib-grid">{list.map((s) => (
            <article key={s.source_id} className="lib-card">
              <div className="row spread start">
                <div><b className="lib-name">{s.display_name}</b><div className="small muted">{s.provider ?? s.source_id} · {humanize(s.source_family)}</div></div>
                <div className="row">{s.validation && s.status !== "active" && <Badge kind={s.validation.ok ? "ok" : "bad"}>{s.validation.ok ? "Test passed" : "Test failed"}</Badge>}<Badge kind={s.status === "active" ? "ok" : s.status === "pending_connector_validation" ? "warn" : "bad"}>{s.status === "pending_connector_validation" ? "Not set up yet" : humanize(s.status)}</Badge></div>
              </div>
              <dl className="lib-facts">
                <dt>How</dt><dd>{METHOD[s.collection_method] ?? humanize(s.collection_method)}, every {every(s.poll_interval_s)}</dd>
                <dt>Status</dt><dd><Badge kind={hk(s.health)}>{HEALTH[s.health ?? "unconfigured"] ?? humanize(s.health)}</Badge>{s.last_success_at && <span className="small muted"> last worked {fmtDateTime(s.last_success_at)}</span>}</dd>
                <dt>Feeds signals</dt><dd>{s.signal_rule_ids?.length ? s.signal_rule_ids.map((r: string) => <span key={r} className="pill">{r}</span>) : <span className="muted">None yet</span>}</dd>
                <dt>So far</dt><dd>{s.documents} pages read, {s.events} findings{Number(s.cost_usd_month) > 0 && <span className="small muted"> · ${Number(s.cost_usd_month).toFixed(3)} this month</span>}</dd>
                <dt>Permission</dt><dd className="small">{s.licence_notes ?? humanize(s.retention_policy)}</dd>
                {s.url && <><dt>Page</dt><dd className="small"><a href={s.url} target="_blank" rel="noreferrer noopener">{s.url.replace(/^https?:\/\//, "").slice(0, 52)} ↗</a></dd></>}
              </dl>
              {canSetup && s.canonical_domain && ["feed", "change_monitor"].includes(s.collection_method) && <button className="ghost" aria-expanded={open === s.source_id} onClick={() => setOpen(open === s.source_id ? null : s.source_id)}>{open === s.source_id ? "Close setup" : s.url ? "Manage" : "Set up"}</button>}
              {open === s.source_id && <div className="mt-3">{render(s, load)}</div>}
            </article>))}</div>
        </section>))}
    </>
  );
}
