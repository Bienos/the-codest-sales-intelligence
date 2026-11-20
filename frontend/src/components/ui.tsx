import { ReactNode } from "react";
import { fmtDate } from "../api";

export const Badge = ({ kind = "", children }: { kind?: string; children: ReactNode }) => <span className={`badge ${kind}`}>{children}</span>;

export const priorityKind = (p: string) => ({ urgent: "bad", high: "warn", medium: "info", blocked: "", low: "" } as Record<string, string>)[p] ?? "";
export const gateKind = (s: string) => (s === "pass" ? "ok" : s === "fail" ? "bad" : "warn");
export const humanize = (s?: string | null) => (s ?? "unknown").replace(/_/g, " ");

export function Evidence({ items }: { items: any[] }) {
  if (!items?.length) return <span className="muted small">No cited evidence attached.</span>;
  return (
    <div>
      {items.map((e) => (
        <div key={e.claim_id} className="small" style={{ margin: "4px 0" }}>
          {e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" title="Open the original source">{e.source ?? "source"} ↗</a> : <span className="pill">no URL (structured record)</span>}
          <span className="muted"> authority {e.authority ?? "n/a"} · published {fmtDate(e.published_at)} · retrieved {fmtDate(e.retrieved_at)}</span>
          {e.claim && <div>“{e.claim}”{!e.anchor_verified && e.kind === "quote" && <Badge kind="warn">anchor unverified</Badge>}</div>}
        </div>
      ))}
    </div>
  );
}

export function Coverage({ c, empty }: { c: any; empty?: string | null }) {
  if (!c) return null;
  const bad = c.unhealthy?.length > 0 || c.no_active_monitoring;
  return (
    <div className={`cov ${bad ? "bad" : c.all_monitored_healthy ? "ok" : ""}`} role="status">
      <b>Coverage:</b> {c.monitored_sources} source(s) actively monitored · {c.pending_validation_sources} candidate source(s) awaiting qualification
      {c.unhealthy?.length > 0 && <> · <b>degraded:</b> {c.unhealthy.map((u: any) => `${u.source} (${u.state})`).join(", ")}</>}
      {c.no_active_monitoring && <> · <b>no source is activated yet</b>: absence of findings means nothing</>}
      {empty && <div style={{ marginTop: 4 }}>{empty}</div>}
    </div>
  );
}

export const Err = ({ e }: { e: unknown }) => (e ? <div className="err" role="alert">{String((e as Error).message ?? e)}</div> : null);
