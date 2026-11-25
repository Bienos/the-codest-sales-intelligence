import { counted, number, tr } from "../locale";
import { useId, useState } from "react";
import { ReactNode } from "react";
import { fmtDate, trackSourceOpened } from "../api";

const I = (d: ReactNode) => (p: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true" className={p.className}>{d}</svg>
);
export const IconToday = I(<><rect x="3" y="4" width="18" height="17" /><path d="M3 9h18M8 2v4M16 2v4" /></>);
export const IconAccounts = I(<><path d="M4 21V5h10v16M14 10h6v11M4 21h16M8 9h2M8 13h2M8 17h2" /></>);
export const IconExplorer = I(<><circle cx="11" cy="11" r="7" /><path d="M16 16l5 5" /></>);
export const IconAdmin = I(<><path d="M4 6h16M4 12h16M4 18h16" /><path d="M9 4v4M15 10v4M8 16v4" /></>);
export const IconGuide = I(<><circle cx="12" cy="12" r="9" /><path d="M10 14l-1 3 3-1 5-5-2-2-5 5zM13.5 8.5l2 2" /></>);
export const IconLibrary = I(<><path d="M4 4h5v16H4zM11 4h5v16h-5zM18 6l3 .8-3.5 13.2-3-.8z" /></>);
export const IconKnowledge = I(<><path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2z M4 19a2 2 0 012-2h13" /></>);
export const IconFeed = I(<><path d="M4 5h16M4 10h16M4 15h10M4 20h7" /><circle cx="19" cy="17" r="2" /></>);
export const IconPass = I(<><circle cx="12" cy="12" r="9" /><path d="M7.5 12.5l3 3 6-7" /></>);
export const IconUnknown = I(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 1.6-2.5 2-2.5 3.5M12 17v.5" /></>);
export const IconPeople = I(<><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5" /><path d="M15.5 4.8a3.3 3.3 0 010 6.4M18 14.8c2 .7 3.2 2.4 3.5 5.2" /></>);
export const IconCampaign = I(<><path d="M3 10v4h3l6 4V6L6 10z" /><path d="M16 9a4 4 0 010 6M18.5 6.5a7.5 7.5 0 010 11" /></>);
export const IconTender = I(<><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 12h7M9 16h5" /></>);
export const IconFail = I(<><circle cx="12" cy="12" r="9" /><path d="M8.5 8.5l7 7M15.5 8.5l-7 7" /></>);

export const Badge = ({ kind = "", children }: { kind?: string; children: ReactNode }) => <span className={`badge ${kind}`}>{typeof children === "string" ? tr(children) : children}</span>;
export const priorityKind = (p: string) => ({ urgent: "bad", high: "warn", medium: "info", blocked: "", low: "" } as Record<string, string>)[p] ?? "";
export const gateKind = (s: string) => (s === "pass" ? "ok" : s === "fail" ? "bad" : "warn");
export const humanize = (s?: string | null) => { const t = (s ?? tr("unknown")).replace(/_/g, " "); return tr(t.charAt(0).toUpperCase() + t.slice(1)); };

/** Plain-English labels for every value the API sends as a code. Anything not listed falls back to sentence case. */
export const LABELS = {
  state: { observed: "Just spotted", verified: "Checked", relevant: "Relevant", investigating: "Being researched", actionable_hypothesis: "Research next", engaged: "In conversation",
    buyer_need_confirmed: "Buyer confirmed the need", commercially_qualified: "Qualified", proposal_procurement: "Proposal or tender", contracted_delivery: "Won", monitor: "Watching",
    blocked: "Blocked", disqualified: "Ruled out", expired: "Expired", superseded: "Replaced by a newer card" },
  route: { direct: "Direct", buyer_direct: "Direct", vendor: "Through the platform vendor", vendor_implementation: "Through the platform vendor", prime_subcontract: "Through an integrator", procurement: "Public tender", relationship: "Existing relationship", framework_panel: "Through a supplier framework", msp_vms: "Through a managed supplier programme", supplier_registration: "Through supplier registration" },
  access: { verified: "Confirmed", potential: "Possible", unknown: "Not confirmed", restricted: "Restricted", blocked: "Blocked" },
  demand: { explicit: "They have asked for help", buyer_confirmed: "Buyer confirmed", inferred: "Likely", unknown: "Not known yet" },
  buyer: { buyer_confirmed: "Buyer confirmed", person_verified: "Named person confirmed", role_identified: "Role known, person not", none: "No buyer yet" },
  icp: { strategic_5000_plus: "5,000+ staff", enterprise_1000_plus: "1,000 to 4,999 staff", scaleup_500_999: "500 to 999 staff", below_threshold: "Under 500 staff", unknown: "Size unknown" },
  priority: { urgent: "Urgent", high: "High priority", medium: "Medium priority", low: "Low priority", blocked: "Blocked" },
  action: { route_to_owner: "Hand to the account owner", monitor: "Keep watching", bid_no_bid_review: "Decide whether to bid", advance_discovery: "Move the conversation on",
    delivery_feasibility_check: "Check we can staff it", disqualify_review: "Review whether to drop this", identify_delivery_owner: "Find who owns delivery",
    investigate_partner_route: "Check the partner route", investigate_vendor_route: "Check the vendor route", qualify_external_demand: "Ask if they will use outside help" },
  event: { acquisition_announced: "Acquisition", contractor_job_opened: "Contractor role opened", cost_programme: "Cost-cutting programme", crm_buyer_response: "Buyer replied", deadline_approaching: "Deadline coming up",
    engineering_job_opened: "Engineering role opened", funding_announced: "Funding", implementation_launched: "Rollout started", integration_release: "Integration released", job_closed: "Role closed",
    licence_granted: "Licence granted", market_launch: "Market launch", negative_cancellation: "Cancelled", procurement_amended: "Tender changed", procurement_awarded: "Tender awarded",
    procurement_published: "Tender published", product_launch: "Product launch", programme_announced: "Programme announced", programme_completed: "Programme completed", programme_delayed: "Programme delayed",
    regulatory_change: "Regulation change", relationship_changed: "Relationship change", supplier_exit: "Supplier leaving", supplier_panel_closed: "Supplier panel closed",
    supplier_panel_opened: "Supplier panel opened", technology_leader_appointed: "New technology leader", vendor_selected: "Vendor chosen" },
  role: { bdm: "Business development", sales_manager: "Sales manager", research_analyst: "Research analyst", marketing: "Marketing", delivery_expert: "Delivery expert", admin: "Administrator", compliance: "Compliance" },
  group: { SRC: "buying route", CAP: "hiring", MAN: "new leader", PRG: "funded project", COM: "business event", REG: "deadline", REL: "existing relationship" },
  dismiss: { not_relevant: "Not relevant", wrong_entity: "Wrong company", stale: "Out of date", duplicate: "Duplicate", no_external_demand: "They will not outsource this",
    not_commercially_relevant: "Not worth pursuing", owned_elsewhere: "Someone else owns it", other: "Other" },
  approval: { signal_campaign_push: "Signal campaign to Lemlist", crm_write: "Pipedrive change", crm_activity: "Pipedrive activity", outbound_message: "Outreach message", sequence_enrolment: "Add to an email sequence", contact_reveal: "Reveal an email address", contact_enrichment: "Reveal an email address",
    supplier_application: "Supplier application", quote_or_commitment: "Quote or commitment", proof_release: "Release of proof" },
  committee: { problem_owner: "problem owner", programme_owner: "programme owner", economic_buyer: "budget holder", technical_evaluator: "technical evaluator", procurement_owner: "procurement",
    risk_security: "risk and security", technical_sponsor: "technical sponsor" },
} as const;
export const lab = (dict: keyof typeof LABELS, v?: string | null): string => { const d = LABELS[dict] as Record<string, string>; return v && d[v] ? tr(d[v]) : humanize(v); };
/** Rule IDs such as PRG-01 mean nothing to a seller: group letters become words. */
export const groupName = (g: string) => tr((LABELS.group as Record<string, string>)[g] ?? g);

export const PageHeader = ({ eyebrow, title, sub, right }: { eyebrow?: string; title: string; sub?: ReactNode; right?: ReactNode }) => (
  <div className="page-head row spread end">
    <div>{eyebrow && <div className="eyebrow">{tr(eyebrow)}</div>}<h1>{tr(title)}</h1>{sub && <p className="sub">{typeof sub === "string" ? tr(sub) : sub}</p>}</div>{right}
  </div>
);

export const Fact = ({ label, value, kind = "" }: { label: string; value: ReactNode; kind?: string }) => (
  <div className={`fact ${kind}`}><div className="l">{tr(label)}</div><div className="v">{typeof value === "string" ? tr(value) : value}</div></div>
);
export const demandKind = (s: string) => (s === "explicit" || s === "buyer_confirmed" ? "ok" : s === "inferred" ? "info" : "warn");
export const accessKind = (s: string) => (s === "verified" ? "ok" : s === "potential" ? "info" : s === "blocked" || s === "restricted" ? "bad" : "warn");

export const GATE_QUESTIONS: Record<string, string> = {
  G1: "Is this a company we should pursue?", G2: "Is there a verified programme, with a stage?", G3: "Does the work match what The Codest delivers?",
  G4: "Will the buyer source outside help? Often unknown until you ask.", G5: "Can a supplier like us get in: direct, via a vendor or via a prime?",
  G6: "Do we know who decides, and have they said so?", G7: "Can we staff it in the window they need?" };
export function verdict(c: any): { text: string; kind: string } {
  const demand = c.external_demand_status, access = c.route?.access_status;
  if (c.priority === "urgent") return { text: tr("A tender with a deadline. Check the date and whether we can bid."), kind: "bad" };
  if (demand === "explicit" || demand === "buyer_confirmed") return { text: tr("They have asked for outside help. Move on to qualifying it."), kind: "ok" };
  if (access === "restricted" || access === "blocked") return { text: tr("A real programme, but we cannot sell to them directly. Look for a partner route."), kind: "warn" };
  if (c.buyer_status === "none") return { text: tr("Worth a conversation, but you still need to find the buyer."), kind: "info" };
  return { text: tr("Worth a conversation. We do not know yet if they will use outside help, so ask."), kind: "info" };
}
export const EVIDENCE_CONFIDENCE = "High: an official source, or a company source plus another independent source. Medium: one company source or at least two other independent sources. Low: less evidence.";
export function actionReason(action: { action_type: string; reason?: string }): string {
  if (action.action_type === "qualify_external_demand") return tr("We checked that the programme exists. Only a conversation with the buyer can confirm if they need outside help.");
  return action.reason ?? "";
}
const GATE_NAMES: Record<string, string> = { G1: "Account fit", G2: "Real programme", G3: "Engineering fit", G4: "Outside demand", G5: "Supplier access", G6: "Buyer path", G7: "Delivery" };
const GATE_STATUS: Record<string, string> = { pass: "Confirmed", unknown: "Not known yet", fail: "Blocked" };
const RE = (r: string, x: RegExp) => r.match(x);
/** The backend writes gate reasons as short machine phrases. This turns the known patterns into plain English; anything else is only tidied. */
export function gateReason(g: string, r?: string | null): string {
  const t = (r ?? "").trim(); let m: RegExpMatchArray | null;
  if (!t) return "";
  if (g === "G1") {
    if ((m = RE(t, /^ICP band (\w+)/))) return tr("{}: in our target range", lab("icp", m[1]));
    if (t.startsWith("size unknown")) return tr("Size unclear: close to a size cut-off");
    if (t.startsWith("identity review")) return tr("We are still matching this company");
    if (t.includes("below ICP size threshold")) return tr("Under our size threshold. Needs a written exception to pursue.");
  }
  if (g === "G2") {
    if ((m = RE(t, /^stage (\w+) per cited event/))) return tr("Programme is at the {} stage", humanize(m[1]).toLowerCase());
    if ((m = RE(t, /^initiative (\w+)/))) return tr("Programme is {}", humanize(m[1]).toLowerCase());
    if (t.includes("stage not established")) return tr("Programme stage not known yet");
  }
  if (g === "G3") {
    if ((m = RE(t, /^(\w+) \((\w+)\)/))) return `${humanize(m[1])}: ${m[2] === "confirmed_scope" ? tr("scope confirmed") : m[2] === "implied_engineering_activity" ? tr("engineering work implied") : humanize(m[2]).toLowerCase()}`;
    if (t.startsWith("only inferred")) return tr("A possible fit, not confirmed");
    if (t.startsWith("no Codest")) return tr("Does not match what we deliver");
  }
  if (g === "G4" && (m = RE(t, /^external demand (\w+)/))) return m[1] === "unknown" ? tr("Not known yet if they will use outside help") : lab("demand", m[1]);
  if (g === "G5") {
    if (t === "route verified") return tr("Route confirmed");
    if ((m = RE(t, /^route (\w+)/))) return `Route ${lab("access", m[1]).toLowerCase()}`;
  }
  if (g === "G6" && (m = RE(t, /^buyer (\w+)/))) return lab("buyer", m[1]);
  if (g === "G7") {
    if (t.startsWith("internal delivery feasibility not")) return tr("Our delivery team has not confirmed we can staff it");
    if (t.startsWith("internal delivery validation")) return tr("Our delivery team confirmed we can staff it");
  }
  return humanize(t);
}
/** Same idea for the short "still unknown" list on each card. */
export function unknownText(u: string): string {
  let m: RegExpMatchArray | null;
  if (u.startsWith("external demand")) return tr("Not known yet if they will use outside help");
  if (u === "route unknown") return tr("How we would get in is not confirmed");
  if ((m = RE(u, /^buyer (\w+)/))) return lab("buyer", m[1]);
  if (u.startsWith("internal delivery")) return tr("Our delivery team has not confirmed we can staff it");
  return humanize(u);
}
export function GateBar({ gates }: { gates: Record<string, { status: string; reason: string }> }) {
  return (
    <>
      <div className="gatebar" role="list" aria-label={tr("The seven checks")}>
        {Object.entries(gates).map(([g, v]) => {
          const Ico = v.status === "pass" ? IconPass : v.status === "fail" ? IconFail : IconUnknown;
          return (
            <div key={g} role="listitem" className={`g ${v.status}`} aria-label={tr("Check {}, {}: {}. {}", g.slice(1), tr(GATE_NAMES[g] ?? g), tr(GATE_STATUS[v.status] ?? v.status), tr(GATE_QUESTIONS[g] ?? ""))}>
              <div className="gi"><Ico />{g.slice(1)}</div>
              <div className="gn">{tr(GATE_NAMES[g] ?? g)}</div>
              <div className="gs">{tr(GATE_STATUS[v.status] ?? v.status)}</div>
              <div className="gq">{tr(GATE_QUESTIONS[g] ?? "")}</div>
              <div className="gr">{gateReason(g, v.reason)}</div>
            </div>
          );
        })}
      </div>
      <p className="small muted mt-2">{tr("Green: confirmed. Amber: not known yet, which is normal early on. Red: blocked.")}</p>
    </>
  );
}

export const TIER: Record<string, string> = { S1: "Official record", S2: "Company\u2019s own site", S3: "Vendor or integrator", S4: "Trade press", S5: "Search find", internal: "Internal" };
export function Evidence({ items, hypothesisId }: { items: any[]; hypothesisId?: string }) {
  if (!items?.length) return <span className="muted small">{tr("No source attached yet.")}</span>;
  return (
    <div>
      {items.map((e) => (
        <div key={e.claim_id} className="small mt-2 mb-2">
          {e.url ? <a className="pill" href={e.url} target="_blank" rel="noreferrer noopener" title={tr("Open the original source")} onClick={() => hypothesisId && trackSourceOpened({ hypothesis_id: hypothesisId })}>{e.source ?? tr("Original source")} ↗</a> : <span className="pill">{tr("Structured record, no link")}</span>}
          <span className="prov">{[e.authority ? tr(TIER[e.authority] ?? e.authority) : null, e.published_at ? tr("published {}", fmtDate(e.published_at)) : null, e.retrieved_at ? tr("read {}", fmtDate(e.retrieved_at)) : null].filter(Boolean).join(" · ")}</span>
          {e.claim && <div className="mt-1">“{e.claim}” {!e.anchor_verified && e.kind === "quote" && <Badge kind="warn">{tr("quote not matched")}</Badge>}</div>}
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
      <b>{tr("Source health.")}</b> {tr("Monitoring {}. Still to set up: {}.", counted(c.monitored_sources, ["source", "sources"], ["źródło", "źródła", "źródeł"]), number(c.pending_validation_sources))}
      {c.unhealthy?.length > 0 && <> <b>{tr("Having problems:")}</b> {c.unhealthy.map((u: any) => u.source).join(", ")}.</>}
      {c.no_active_monitoring && <> <b>{tr("No source is switched on yet")}</b>{tr(", so a quiet screen does not mean nothing happened.")}</>}
      {empty && <div className="mt-1">{tr(empty)}</div>}
    </div>
  );
}

export function Err({ e, onRetry }: { e: unknown; onRetry?: () => void }) {
  if (!e) return null;
  const msg = String((e as Error).message ?? e); const st = (e as { status?: number }).status;
  const net = e instanceof TypeError;
  const plain = st === 401 || st === 403 ? tr("You do not have access to this, or your session has ended. Sign in again, or ask an administrator.")
    : st === 404 ? tr("This item no longer exists or has moved.") : (st && st >= 500) || net ? tr("The server did not respond. Try again in a minute.") : null;
  return <div className="err" role="alert">{plain ?? tr(msg)}{plain && msg && !net && <details><summary>{tr("Technical detail")}</summary>{msg}</details>}{onRetry && <div className="mt-2"><button onClick={onRetry}>{tr("Try again")}</button></div>}</div>;
}
/** Placeholder shown while a screen loads: same footprint as the content, so nothing jumps when it arrives. */
export const Skeleton = ({ rows = 3, label = tr("Loading") }: { rows?: number; label?: string }) => (
  <div className="skel" role="status" aria-busy="true" aria-label={tr(label)}>{Array.from({ length: rows }, (_, i) => <div key={i} className="skel-row" />)}</div>
);
/** The backend appends "; route: …; external demand: …" to this sentence. Both are already shown as facts, so keep only the first clause. */
export const whyShort = (s?: string | null) => String(s ?? "").split(/;\s*route:/)[0].replace(/\s*\(confirmed scope\)/, "");
/** Accessible tab strip: arrow keys, Home and End move between tabs; only the selected tab is in the tab order. */
export function Tabs({ items, value, onChange, label, children }: { items: { key: string; label: ReactNode }[]; value: string; onChange: (k: string) => void; label: string; children: ReactNode }) {
  const id = useId();
  const active = items.findIndex((item) => item.key === value);
  const go = (e: React.KeyboardEvent, i: number) => {
    const n = e.key === "ArrowRight" ? (i + 1) % items.length : e.key === "ArrowLeft" ? (i - 1 + items.length) % items.length : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : -1;
    if (n < 0) return;
    e.preventDefault();
    (e.currentTarget.parentElement?.children[n] as HTMLButtonElement | undefined)?.focus();
    onChange(items[n].key);
  };
  return (
    <>
      <div className="tabs" role="tablist" aria-label={tr(label)}>
        {items.map((t, i) => <button key={t.key} id={`${id}-tab-${i}`} type="button" role="tab" aria-controls={`${id}-panel-${i}`} aria-selected={i === active} tabIndex={i === active ? 0 : -1} onClick={() => onChange(t.key)} onKeyDown={(e) => go(e, i)}>{typeof t.label === "string" ? tr(t.label) : t.label}</button>)}
      </div>
      {items.map((t, i) => <div key={t.key} id={`${id}-panel-${i}`} role="tabpanel" aria-labelledby={`${id}-tab-${i}`} hidden={i !== active} tabIndex={i === active ? 0 : undefined}>
        {i === active ? children : null}
      </div>)}
    </>
  );
}
export const reloadPage = () => window.location.reload();
export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => <div className="empty"><b>{tr(title)}</b>{typeof children === "string" ? tr(children) : children}</div>;

/** Brand mark: the official logo file when it has been supplied, otherwise the plain text mark. Never redrawn here.
 * The text mark shows straight away and the logo replaces it once it has actually loaded, so the header is never blank. */
export function Brand({ sub = true }: { sub?: boolean }) {
  // 0 = logo file supplied with the app, 1 = the logo from thecodest.co (served by our API), 2 = plain text mark only
  const [stage, setStage] = useState(0);
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="brand brand-bare">
      {stage < 2 && <img className="brand-logo" src={stage === 0 ? "/brand/logo-white.svg" : "/brand/logo"} alt={loaded ? "The Codest" : ""} width={140}
                         style={loaded ? undefined : { position: "absolute", width: 1, height: 1, opacity: 0 }}
                         onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setStage(stage + 1); }} />}
      {!loaded && <div className="brand-row"><span className="brand-mark" /><div className="brand-name">The Codest</div></div>}
      {sub && <div className="brand-sub mt-0">{tr("Revenue Intelligence")}</div>}
    </div>
  );
}

/** Links built from stored or scraped data: only http(s) addresses become links (defence in depth next to the CSP). */
export const safeHref = (u?: string | null): string | undefined => {
  if (!u) return undefined;
  try { const x = new URL(u, window.location.origin); return ["http:", "https:"].includes(x.protocol) ? x.href : undefined; } catch { return undefined; }
};

/** Signal strength 0-3 as filled squares. */
export const Strength = ({ n }: { n: number }) => <span className="strength" role="img" aria-label={tr("Strength {} of 3", n)}>{[1, 2, 3].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>;

export function Meter({ used, cap, unit = "", warn = 0.8 }: { used: number; cap: number; unit?: string; warn?: number }) {
  const pct = cap > 0 ? Math.min(used / cap, 1) : 0;
  return (
    <div className="meter" role="img" aria-label={tr("{} of {} {} used", used, cap, unit)}>
      <div className="meter-bar"><i style={{ width: `${pct * 100}%` }} className={pct >= 1 ? "full" : pct >= warn ? "hot" : ""} /></div>
      <div className="small muted">{cap > 0 ? tr("{}% of the monthly limit", Math.round(pct * 100)) : tr("No limit set, so these calls are blocked")}</div>
    </div>
  );
}
