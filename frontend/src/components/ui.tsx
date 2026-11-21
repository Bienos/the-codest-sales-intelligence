import { ReactNode } from "react";
import { fmtDate } from "../api";

const I = (d: ReactNode) => (p: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>{d}</svg>
);
export const IconToday = I(<><rect x="3" y="4" width="18" height="17" /><path d="M3 9h18M8 2v4M16 2v4" /></>);
export const IconAccounts = I(<><path d="M4 21V5h10v16M14 10h6v11M4 21h16M8 9h2M8 13h2M8 17h2" /></>);
export const IconExplorer = I(<><circle cx="11" cy="11" r="7" /><path d="M16 16l5 5" /></>);
export const IconAdmin = I(<><path d="M4 6h16M4 12h16M4 18h16" /><path d="M9 4v4M15 10v4M8 16v4" /></>);
export const IconGuide = I(<><circle cx="12" cy="12" r="9" /><path d="M10 14l-1 3 3-1 5-5-2-2-5 5zM13.5 8.5l2 2" /></>);
export const IconPass = I(<><circle cx="12" cy="12" r="9" /><path d="M7.5 12.5l3 3 6-7" /></>);
export const IconUnknown = I(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.6-2.5 2-2.5 3.5M12 17v.5" /></>);
export const IconFail = I(<><circle cx="12" cy="12" r="9" /><path d="M8.5 8.5l7 7M15.5 8.5l-7 7" /></>);

export const Badge = ({ kind = "", children }: { kind?: string; children: ReactNode }) => <span className={`badge ${kind}`}>{children}</span>;
export const priorityKind = (p: string) => ({ urgent: "bad", high: "warn", medium: "info", blocked: "", low: "" } as Record<string, string>)[p] ?? "";
export const gateKind = (s: string) => (s === "pass" ? "ok" : s === "fail" ? "bad" : "warn");
export const humanize = (s?: string | null) => (s ?? "unknown").replace(/_/g, " ");

export const PageHeader = ({ eyebrow, title, sub, right }: { eyebrow?: string; title: string; sub?: ReactNode; right?: ReactNode }) => (
  <div className="page-head row spread" style={{ alignItems: "flex-end" }}>
    <div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2>{sub && <p className="sub">{sub}</p>}</div>{right}
  </div>
);

export const Fact = ({ label, value, kind = "" }: { label: string; value: ReactNode; kind?: string }) => (
  <div className={`fact ${kind}`}><div className="l">{label}</div><div className="v">{value}</div></div>
);
export const demandKind = (s: string) => (s === "explicit" || s === "buyer_confirmed" ? "ok" : s === "inferred" ? "info" : "warn");
export const accessKind = (s: string) => (s === "verified" ? "ok" : s === "potential" ? "info" : s === "blocked" || s === "restricted" ? "bad" : "warn");

const GATE_NAMES: Record<string, string> = { G1: "Account and ICP", G2: "Initiative is real", G3: "Engineering fit", G4: "External demand", G5: "Supplier access", G6: "Buyer path", G7: "Delivery feasibility" };
export function GateBar({ gates }: { gates: Record<string, { status: string; reason: string }> }) {
  return (
    <div className="gatebar" role="list" aria-label="Qualification gates">
      {Object.entries(gates).map(([g, v]) => {
        const Ico = v.status === "pass" ? IconPass : v.status === "fail" ? IconFail : IconUnknown;
        return (
          <div key={g} role="listitem" className={`g ${v.status}`}>
            <div className="gi"><Ico />{g}</div>
            <div className="gn">{GATE_NAMES[g]}</div>
            <div className="gr">{v.reason}</div>
          </div>
        );
      })}
    </div>
  );
}

export function Evidence({ items }: { items: any[] }) {
  if (!items?.length) return <span className="muted small">No cited evidence attached.</span>;
  return (
    <div>
      {items.map((e) => (
        <div key={e.claim_id} className="small" style={{ margin: "8px 0" }}>
          {e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" title="Open the original source">{e.source ?? "Original source"} ↗</a> : <span className="pill">Structured record, no URL</span>}
          <span className="prov">authority {e.authority ?? "n/a"} · published {fmtDate(e.published_at)} · retrieved {fmtDate(e.retrieved_at)}</span>
          {e.claim && <div style={{ marginTop: 4 }}>“{e.claim}” {!e.anchor_verified && e.kind === "quote" && <Badge kind="warn">anchor unverified</Badge>}</div>}
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
      <b>Coverage.</b> {c.monitored_sources} source(s) monitored, {c.pending_validation_sources} candidate source(s) awaiting qualification.
      {c.unhealthy?.length > 0 && <> <b>Degraded:</b> {c.unhealthy.map((u: any) => `${u.source} (${u.state})`).join(", ")}.</>}
      {c.no_active_monitoring && <> <b>No source is activated yet</b>, so a quiet screen does not mean nothing happened.</>}
      {empty && <div style={{ marginTop: 4 }}>{empty}</div>}
    </div>
  );
}

export const Err = ({ e }: { e: unknown }) => (e ? <div className="err" role="alert">{String((e as Error).message ?? e)}</div> : null);
export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => <div className="empty"><b>{title}</b>{children}</div>;
