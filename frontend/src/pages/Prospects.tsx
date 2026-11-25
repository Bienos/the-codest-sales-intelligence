import { useState } from "react";
import { post, trackSourceOpened } from "../api";
import { tr } from "../locale";
import { safeHref } from "../components/ui";
import { ago } from "../components/Findings";

/* A workbook lead lives on its company: the Accounts list shows the lead list's strongest signal on the company row, with its source
 * and whether that source was checked. (This file used to be the separate "Leads" tab; those leads are now part of the one list.) */

/** What happened to the check of a lead-list source: [dot colour, short word, longer explanation]. */
export const SOURCE_CHECK: Record<string, [string, string, string]> = {
  queued: ["info", "Checking…", "The source is queued for a check."],
  fetching: ["info", "Checking…", "The source is being read."],
  extracting: ["info", "Checking…", "The claim is being compared with the source."],
  verified: ["ok", "Checked", "The source was read and confirms a dated event."],
  no_event: ["warn", "Not confirmed", "The source was read but does not confirm the event."],
  failed: ["bad", "Unreadable", "The source could not be read."],
  blocked: ["bad", "Blocked", "The website blocked the check."],
  waiting_budget: ["warn", "Waiting", "The check waits for this month's budget."],
};
export const CHECKING = ["queued", "fetching", "extracting"];

export type ListLead = {
  signal?: string | null; signal_type?: string | null; signal_date?: string | null; source_url?: string | null;
  why?: string | null; lane?: string | null; check?: string | null;
};

/** The lead list's own signal for a company: a headline, when it happened, the source, and a button to have that source checked. */
export function ListLeadLine({ lead, orgId, orgName, canCheck, onChecked }: {
  lead: ListLead; orgId: string; orgName: string; canCheck: boolean; onChecked: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const href = safeHref(lead.source_url);
  const state = lead.check ? SOURCE_CHECK[lead.check] : null;
  const headline = lead.signal_type && lead.signal_type !== lead.signal ? lead.signal_type : null;
  const when = lead.signal_date ? ago(lead.signal_date) : "";
  const check = async () => {
    setBusy(true); setFailed(false);
    try { await post("/api/prospects/verify", { organization_id: orgId, url: lead.source_url }); onChecked(); }
    catch { setFailed(true); } finally { setBusy(false); }
  };
  return <>
    <p className="acc-sig-k">
      <i className={`acc-mk${lead.check === "verified" ? " on" : ""}`} aria-hidden="true" />
      <b className="acc-sig-h" title={headline ?? undefined}>{headline ?? tr("From your lead list")}</b>
    </p>
    {lead.signal && <p className="acc-sig-t" title={lead.signal}><span className="sr">{tr("From your lead list:")} </span>{lead.signal}</p>}
    <p className="acc-sig-meta">
      <span className="acc-sig-from">{tr("Lead list")}</span>
      {when && <span className="acc-sig-when">{when}</span>}
      {href && <a href={href} target="_blank" rel="noreferrer noopener" aria-label={tr("Original source for {}", orgName)}
        onClick={() => trackSourceOpened({ organization_id: orgId, where: "prospect" })}>{tr("Source")} ↗</a>}
      {state ? <span className={`acc-state ${state[0]}`} title={tr(state[2])}>{tr(state[1])}<span className="sr"> {tr(state[2])}</span></span>
        : canCheck && href ? <button type="button" className="linkish" disabled={busy} onClick={check} title={tr("Read the source and confirm the claim")}
            aria-label={tr("Check the source for {}", orgName)}>{busy ? tr("Queuing…") : tr("Check it")}</button> : null}
      {failed && <span className="acc-state bad" role="alert">{tr("Not queued")}</span>}
    </p>
  </>;
}
