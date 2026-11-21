import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import { Badge, Err, humanize } from "../components/ui";

const TIERS: Record<string, string> = { S1: "S1 Official records", S2: "S2 Company primary", S3: "S3 Vendors and integrators", S4: "S4 Trade media", S5: "S5 Discovery", internal: "Internal" };
const hk = (s: string) => (s === "healthy" ? "ok" : !s || s === "unconfigured" || s === "paused" ? "" : "bad");
const every = (s: number) => (s >= 86400 ? `${Math.round(s / 86400)}d` : `${Math.round(s / 3600)}h`);

export default function Library({ render, query, canSetup = true }: { render: (s: any, reload: () => void) => any; query?: string; canSetup?: boolean }) {
  const [d, setD] = useState<any>(null); const [err, setErr] = useState<unknown>(null); const [open, setOpen] = useState<string | null>(null); const [qLocal, setQ] = useState("");
  const q = query ?? qLocal;
  const load = () => api("/api/admin/library").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  if (err) return <Err e={err} />;
  if (!d) return <p className="muted">Loading</p>;
  const items = d.items.filter((s: any) => !q || `${s.display_name} ${s.source_id} ${s.provider}`.toLowerCase().includes(q.toLowerCase()));
  const groups = Object.keys(TIERS).map((t) => [t, items.filter((s: any) => s.source_tier === t)] as [string, any[]]).filter(([, l]) => l.length);
  const live = d.items.filter((s: any) => s.status === "active" && s.collection_method !== "internal").length;
  return (
    <>
      <div className="row spread" style={{ marginBottom: 6 }}>
        <p className="small muted" style={{ margin: 0 }}><b>{d.items.length}</b> sources in the library, <b>{live}</b> live. A source only runs once it has a passing test and a certification record.</p>
        {query === undefined && <input aria-label="Search sources" placeholder="Search sources" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />}
      </div>
      {groups.map(([t, list]) => (
        <section key={t}><div className="section-title">{TIERS[t]}</div>
          <div className="lib-grid">{list.map((s) => (
            <article key={s.source_id} className="lib-card">
              <div className="row spread" style={{ alignItems: "flex-start" }}>
                <div><b className="lib-name">{s.display_name}</b><div className="small muted">{s.provider ?? s.source_id} · {humanize(s.source_family)}</div></div>
                <div className="row"><Badge kind={s.status === "active" ? "ok" : s.status === "pending_connector_validation" ? "warn" : "bad"}>{s.status === "pending_connector_validation" ? "Pending" : humanize(s.status)}</Badge></div>
              </div>
              <dl className="lib-facts">
                <dt>Collects by</dt><dd>{humanize(s.collection_method)}, every {every(s.poll_interval_s)}</dd>
                <dt>Health</dt><dd><Badge kind={hk(s.health)}>{s.health ?? "unconfigured"}</Badge>{s.last_success_at && <span className="small muted"> last ok {fmtDateTime(s.last_success_at)}</span>}</dd>
                <dt>Feeds rules</dt><dd>{s.signal_rule_ids?.length ? s.signal_rule_ids.map((r: string) => <span key={r} className="pill" style={{ marginRight: 4 }}>{r}</span>) : <span className="muted">none yet</span>}</dd>
                <dt>Collected</dt><dd>{s.documents} documents, {s.events} events{Number(s.cost_usd_month) > 0 && <span className="small muted"> · ${Number(s.cost_usd_month).toFixed(3)} this month</span>}</dd>
                <dt>Licence</dt><dd className="small">{s.licence_notes ?? humanize(s.retention_policy)}</dd>
                {s.url && <><dt>Page</dt><dd className="small"><a href={s.url} target="_blank" rel="noreferrer noopener">{s.url.replace(/^https?:\/\//, "").slice(0, 52)} ↗</a></dd></>}
              </dl>
              {canSetup && ["feed", "change_monitor"].includes(s.collection_method) && <button className="ghost" aria-expanded={open === s.source_id} onClick={() => setOpen(open === s.source_id ? null : s.source_id)}>{open === s.source_id ? "Close setup" : s.url ? "Manage" : "Set up"}</button>}
              {open === s.source_id && <div style={{ marginTop: 10 }}>{render(s, load)}</div>}
            </article>))}</div>
        </section>))}
    </>
  );
}
