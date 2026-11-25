"""Campaigns built from buying signals.

A seller picks one or more signals (for example "new funding round" or "hiring engineers for one platform"). The platform finds every account in the
seller's scope with that signal live and verified, picks the right people there (verified email, not suppressed, not owned by another seller, no open
deal), and writes each lead's opening line from that account's own evidence. The sequence is checked against the brand rules for every lead.
Nothing is sent from here: pushing to Lemlist needs a manager's approval and the outbound switch, and launching stays a click in Lemlist.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import approvals as appr
from ..access import Principal, org_scope_sql
from ..audit import audit
from ..config import Settings
from ..db import Conn, jb, q1, qa
from .brand_check import PLACEHOLDERS, review_sequence
from .enrol import ownership_check, plan_contacts
from .lemlist import LemlistClient, LemlistError

KIND, SYSTEM, OP = "signal_campaign_push", "lemlist", "create_campaign_with_leads"
CAMPAIGN_PLACEHOLDERS = frozenset(PLACEHOLDERS | {"{{signalHook}}", "{{signalAngle}}"})
MAX_MEMBERS = 200

# One plain sentence per signal: why this kind of news tends to mean engineering work. No numbers, no claims beyond the evidence.
ANGLES: dict[str, str] = {
    "SRC-01": "If you are choosing suppliers for this work, I would like to understand the scope before anything else.",
    "SRC-02": "If the panel is open to engineering suppliers, I would like to understand how you want suppliers to apply.",
    "SRC-04": "Programmes like this usually still need a team for the integration and custom work around the main platform.",
    "SRC-05": "When a platform is selected, the integration and custom work around it still needs a team.",
    "SRC-06": "When contract engineers are needed on a named system, delivery usually waits on finding them.",
    "CAP-01": "When several engineering roles are open for one platform, delivery often waits on hiring.",
    "CAP-02": "Contract roles on a named system usually mean the work has a date and the team is short.",
    "CAP-05": "Moving AI work from a pilot into production usually needs engineers who have done it before.",
    "PRG-01": "Core platform migrations usually need extra engineering for integrations, data migration and testing.",
    "PRG-02": "Cloud and data platform moves usually need extra engineering for the parts that do not move by themselves.",
    "PRG-06": "Payment rail and scheme changes come with certification and test dates that do not move.",
    "MAN-01": "The first months in a technology leadership role are usually when the roadmap and the team model get decided.",
    "REL-03": "A new role is often when the roadmap and the team model get decided.",
    "COM-01": "New funding usually comes with a product roadmap that needs engineering capacity quickly.",
    "COM-02": "Integration and migration work after an acquisition usually has a hard date attached.",
    "COM-03": "A launch in a new market tends to bring local payment, compliance and integration work with a fixed date.",
    "COM-04": "A new licence tied to a launch usually means a fixed date for the platform behind it.",
    "COM-05": "A customer go-live puts the integration work on a fixed date.",
    "COM-06": "A new product line usually means new integrations while the existing platform keeps running.",
    "COM-07": "A partnership with integration scope usually means connector and API work on a timeline.",
    "COM-08": "Cost programmes with a technology scope often mean retiring old systems while the business keeps running.",
}
DEFAULT_ANGLE = "News like this usually comes with engineering work on a date."

# Who to write to first, by the kind of signal. Matched against job titles; earlier patterns win.
_TECH = [r"\bcto\b|chief technology", r"vp.{0,4}engineering|head of engineering|director of engineering", r"\bcio\b|chief information", r"head of platform|platform",
         r"engineering manager|head of development|head of software"]
TITLE_PRIORITY: dict[str, list[str]] = {
    "PRG-06": [r"payments?", *_TECH], "COM-03": [r"payments?|product", *_TECH], "COM-06": [r"product|payments?", *_TECH],
    "CAP-05": [r"\bai\b|data|machine learning|chief data", *_TECH], "MAN-01": _TECH, "SRC-01": [r"procurement|purchasing", *_TECH],
    "COM-01": [*_TECH, r"\bceo\b|founder|chief executive"],
}

DEFAULT_STEPS: list[dict[str, Any]] = [
    {"day": 0, "subject": "A question about {{companyName}}",
     "body": "Hello {{firstName}},\n\n{{signalHook}} {{signalAngle}}\n\nWe offer Product Development, Staff Augmentation and AI. If part of this work needs a team that can own it, "
             "I would like to understand what you need first.\n\nLet's talk through the problem. Would a short call next week work?",
     "claims_used": ["CLM-003"], "fact_refs": ["signal"]},
    {"day": 3, "subject": "Re: A question about {{companyName}}",
     "body": "Hello {{firstName}},\n\nA short follow-up on my note about {{companyName}}. When a date is fixed, the usual risk is that the roadmap waits for hiring.\n\n"
             "Let's plan the responsibility and the team model. I can show how we would split the work between your team and ours.",
     "fact_refs": ["signal"]},
    {"day": 7, "subject": "Re: A question about {{companyName}}",
     "body": "Hello {{firstName}},\n\nI will leave it here for now. If the timing is wrong, a one-line reply is enough and I will not follow up again.\n\n"
             "Start an evidence review. We would look at one part of the work and tell you plainly whether we are a fit.",
     "fact_refs": ["signal"]},
]


class CampaignError(Exception):
    pass


# ------------------------------------------------------------------ signals and audience
def _clean(text: str) -> str:
    t = re.sub(r"https?://\S+", "", text or "")
    t = t.replace("—", ",").replace("–", ",").replace("!", ".")
    return re.sub(r"\s+", " ", t).strip().rstrip(".")


def hook_for(signal: dict[str, Any]) -> str:
    """The opening sentence for one lead, taken from that account's own verified event."""
    when = signal.get("date")
    month = when.strftime("%B %Y") if hasattr(when, "strftime") else ""
    summary = _clean(signal.get("summary") or "")
    if not summary:
        return "I saw recent news about {{companyName}}."
    return f"I saw the news from {month}: {summary}." if month else f"I saw the recent news: {summary}."


def signal_options(conn: Conn, p: Principal, days: int = 90, *, target: bool = False) -> list[dict[str, Any]]:
    """Every active signal rule with how many accounts in your scope show it now, what it means and who to contact.
    `target` limits the count to the qualified target market (private financial companies), as Today does."""
    from ..target_market import target_sql
    where, params = org_scope_sql(p, "o.id")
    if target:
        where = f"({where}) AND {target_sql()}"
    counts = {r["rule_id"]: r for r in qa(conn, f"""
        SELECT s.rule_id, count(DISTINCT s.organization_id) AS accounts, max(coalesce(e.event_at, e.pub_at, s.effective_from)) AS latest,
               count(DISTINCT s.organization_id) FILTER (WHERE EXISTS (
                   SELECT 1 FROM employment em JOIN people pe ON pe.id=em.person_id WHERE em.organization_id=s.organization_id
                   AND pe.erased_at IS NULL AND NOT pe.suppressed AND pe.email_verification_status='Verified')) AS reachable
        FROM signals s JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id JOIN organizations o ON o.id=s.organization_id
        WHERE s.status='active' AND s.verification_status='verified' AND (s.expires_at IS NULL OR s.expires_at>now())
          AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay
          AND coalesce(e.event_at, e.pub_at, s.effective_from) > now() - make_interval(days => %s) AND ({where})
        GROUP BY s.rule_id""", [days, *params])}        # noqa: S608
    rules = qa(conn, "SELECT l.id, l.grp, l.signal, l.strength, l.how_to_spot, l.who_to_contact, l.check_it_is_real, v.status FROM legacy_signals l "
                     "JOIN signal_rule_versions v ON v.rule_id=l.id WHERE v.status='active' AND l.id NOT LIKE 'NEG-%%' ORDER BY l.ordinal")
    out = []
    for r in rules:
        c = counts.get(r["id"]) or {}
        out.append({"rule_id": r["id"], "group": r["grp"], "name": r["signal"], "strength": r["strength"], "what_it_means": ANGLES.get(r["id"], DEFAULT_ANGLE),
                    "how_to_spot": r["how_to_spot"], "who_to_contact": r["who_to_contact"], "check_it_is_real": r["check_it_is_real"],
                    "accounts": int(c.get("accounts") or 0), "reachable_accounts": int(c.get("reachable") or 0), "latest": c.get("latest")})
    return out


def signal_accounts(conn: Conn, p: Principal, rule_ids: list[str], *, days: int, region: str = "") -> list[dict[str, Any]]:
    """One row per account: its freshest verified signal among `rule_ids`, with the quote and the source page."""
    from ..market import region_for
    where, params = org_scope_sql(p, "o.id")
    rows = qa(conn, f"""
        SELECT DISTINCT ON (s.organization_id) s.organization_id::text AS organization_id, o.display_name, o.country_code, o.segment, o.canonical_domain,
               s.rule_id, l.signal AS rule_name, s.demand_tier, e.id::text AS event_id, e.summary, coalesce(e.event_at, e.pub_at) AS date,
               c.evidence_anchor AS quote, coalesce(sd.canonical_url, c.object_json->>'official_url', c.object_json->>'url') AS source_url,
               coalesce(sc.stopped, false) AS stopped
        FROM signals s
        JOIN events e ON e.id=s.event_id AND e.subject_org_id=s.organization_id
        JOIN organizations o ON o.id=s.organization_id
        JOIN legacy_signals l ON l.id=s.rule_id
        JOIN event_claims ec ON ec.event_id=e.id AND ec.claim_id=ANY(s.evidence_claim_ids)
        JOIN claims c ON c.id=ec.claim_id
        LEFT JOIN source_documents sd ON sd.id=c.source_document_id
        LEFT JOIN account_scores sc ON sc.organization_id=o.id
        WHERE s.rule_id = ANY(%s) AND s.status='active' AND s.verification_status='verified' AND (s.expires_at IS NULL OR s.expires_at>now())
          AND e.verification_status='verified' AND e.lifecycle_status='active' AND NOT e.is_historical_replay AND e.lane IS DISTINCT FROM 'public'
          AND o.entity_type IS DISTINCT FROM 'public_body'
          AND c.status='supported' AND c.anchor_verified AND c.claim_kind IN ('quote','structured')
          AND coalesce(e.event_at, e.pub_at, s.effective_from) > now() - make_interval(days => %s) AND ({where})
        ORDER BY s.organization_id, s.demand_tier, coalesce(e.event_at, e.pub_at) DESC NULLS LAST, s.strength DESC""", [rule_ids, days, *params])   # noqa: S608
    return [r for r in rows if not region or region_for(r["country_code"]) == region]


def _rank(title: str | None, rule_id: str) -> int:
    t = (title or "").lower()
    for i, pat in enumerate(TITLE_PRIORITY.get(rule_id, _TECH)):
        if re.search(pat, t):
            return i
    return 99


def build_audience(conn: Conn, campaign_id: str, p: Principal) -> dict[str, int]:
    """(Re)build the members of a draft campaign. Excluded accounts and people stay listed with the reason, so nothing disappears silently."""
    c = q1(conn, "SELECT * FROM signal_campaigns WHERE id=%s", (campaign_id,))
    if not c or c["status"] != "draft":
        raise CampaignError("only a draft campaign can be rebuilt")
    f = c["filters"] or {}
    per_account = max(1, min(int(f.get("per_account") or 2), 5))
    kept = {(str(r["organization_id"]), str(r["person_id"])): r["included"] for r in qa(conn, "SELECT organization_id, person_id, included FROM signal_campaign_members WHERE campaign_id=%s AND person_id IS NOT NULL", (campaign_id,))}
    conn.execute("DELETE FROM signal_campaign_members WHERE campaign_id=%s", (campaign_id,))
    accounts = signal_accounts(conn, p, list(c["rule_ids"]), days=int(f.get("days") or 90), region=f.get("region") or "")
    stats = {"accounts": len(accounts), "included": 0, "excluded_accounts": 0}
    for a in accounts[:MAX_MEMBERS]:
        org = a["organization_id"]
        signal = {"rule_id": a["rule_id"], "name": a["rule_name"], "summary": a["summary"], "date": a["date"].isoformat() if a["date"] else None,
                  "quote": (a["quote"] or "")[:300], "source_url": a["source_url"], "event_id": a["event_id"]}
        reason = None
        if a["stopped"]:
            reason = "This account has a stop signal (for example a hiring freeze or an exclusive supplier)."
        else:
            blocker = ownership_check(conn, org, p.pipedrive_user_id)
            reason = blocker["detail"] if blocker else None
        plan = plan_contacts(conn, org)
        people = sorted(plan["eligible"], key=lambda x: (_rank(x["title"], a["rule_id"]), x["name"]))[:per_account] if not reason else []
        if not people:
            if not reason:
                bits = [f"{len(plan['needs_verification'])} need an email check" if plan["needs_verification"] else "",
                        f"{plan['suppressed']} opted out" if plan["suppressed"] else "", f"{plan['already_enrolled']} already in a sequence" if plan["already_enrolled"] else ""]
                reason = "No contact with a verified email" + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "") + ". Find decision makers on the account first."
            conn.execute("INSERT INTO signal_campaign_members(campaign_id, organization_id, included, exclusion, signal) VALUES (%s,%s,false,%s,%s)",
                         (campaign_id, org, reason, jb(signal)))
            stats["excluded_accounts"] += 1
            continue
        hook = hook_for({**signal, "date": a["date"]})
        for person in people:
            first, _, last = (person["name"] or "").partition(" ")
            variables = {"firstName": first, "lastName": last, "companyName": a["display_name"], "jobTitle": person["title"] or "",
                         "signalHook": hook, "signalAngle": ANGLES.get(a["rule_id"], DEFAULT_ANGLE)}
            included = kept.get((org, person["id"]), True)
            conn.execute("INSERT INTO signal_campaign_members(campaign_id, organization_id, person_id, email, included, signal, variables) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (campaign_id, org, person["id"], person["email"].lower(), included, jb(signal), jb(variables)))
            stats["included"] += 1 if included else 0
    conn.execute("UPDATE signal_campaigns SET updated_at=now() WHERE id=%s", (campaign_id,))
    return stats


# ------------------------------------------------------------------ copy
def render(text: str, variables: dict[str, Any]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), text or "")


def review_campaign(conn: Conn, campaign_id: str) -> dict[str, Any]:
    """Brand review of the template, then of every included lead's rendered copy (each lead's numbers must come from its own evidence)."""
    c = q1(conn, "SELECT steps FROM signal_campaigns WHERE id=%s", (campaign_id,))
    steps = list(c["steps"] or []) if c else []
    template = review_sequence(steps, {"signal": ""}, placeholders=CAMPAIGN_PLACEHOLDERS)
    template["breaks"] = [b for b in template["breaks"] if b["code"] != "number_not_in_source"]       # numbers are judged per lead, against that lead's evidence
    template["passed"] = not template["breaks"]
    leads = []
    for m in qa(conn, "SELECT id, variables, organization_id FROM signal_campaign_members WHERE campaign_id=%s AND included AND person_id IS NOT NULL", (campaign_id,)):
        v = m["variables"] or {}
        rendered = [{**s, "subject": render(s.get("subject", ""), v), "body": render(s.get("body", ""), v)} for s in steps]
        r = review_sequence(rendered, {"signal": f"{v.get('signalHook', '')} {v.get('companyName', '')}"})
        if not r["passed"]:
            leads.append({"member_id": str(m["id"]), "company": v.get("companyName"), "breaks": r["breaks"][:5]})
    out = {"passed": template["passed"] and not leads, "template": template, "leads_with_issues": leads, "checked_at": datetime.now(timezone.utc).isoformat()}
    conn.execute("UPDATE signal_campaigns SET review=%s WHERE id=%s", (jb(out), campaign_id))
    return out


def preview(steps: list[dict[str, Any]], variables: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"day": s.get("day", 0), "subject": render(s.get("subject", ""), variables), "body": render(s.get("body", ""), variables)} for s in steps]


# ------------------------------------------------------------------ approval and push
def _payload(conn: Conn, c: dict[str, Any]) -> dict[str, Any]:
    members = qa(conn, "SELECT id, organization_id, person_id, email, variables FROM signal_campaign_members WHERE campaign_id=%s AND included AND person_id IS NOT NULL ORDER BY organization_id, email", (str(c["id"]),))
    steps, prev = [], 0
    for s in c["steps"]:
        day = int(s.get("day") or 0)
        steps.append({"subject": s["subject"], "body": s["body"], "delay_days": max(day - prev, 0)})
        prev = day
    return {"campaign_id": str(c["id"]), "campaign_name": f"RI signals: {c['name']}", "steps": steps,
            "members": [{"member_id": str(m["id"]), "organization_id": str(m["organization_id"]), "person_id": str(m["person_id"]), "email": m["email"], "variables": m["variables"]} for m in members]}


def request_push(conn: Conn, campaign_id: str, p: Principal, settings: Settings) -> dict[str, Any]:
    c = q1(conn, "SELECT * FROM signal_campaigns WHERE id=%s FOR UPDATE", (campaign_id,))
    if not c:
        raise CampaignError("campaign not found")
    if c["status"] != "draft":
        raise CampaignError(f"the campaign is {c['status'].replace('_', ' ')}")
    review = review_campaign(conn, campaign_id)
    if not review["passed"]:
        raise CampaignError("The copy does not pass the brand review yet. Fix the template or leave out the leads with issues.")
    payload = _payload(conn, c)
    if not payload["members"]:
        raise CampaignError("Nobody is included. Add contacts with verified emails to the accounts first.")
    orgs = len({m["organization_id"] for m in payload["members"]})
    a = appr.request(conn, kind=KIND, target_system=SYSTEM, target_id=campaign_id, operation=OP, payload=payload, requested_by=p.email,
                     justification=f"Create the Lemlist campaign '{c['name']}' for {len(payload['members'])} verified contact(s) at {orgs} account(s), each with a buying signal "
                                   f"({', '.join(c['rule_ids'])}). It is created in Lemlist but not launched.",
                     idempotency_key=f"signal-campaign:{campaign_id}:{appr.payload_hash(payload)[:16]}", ttl_hours=settings.approval_ttl_hours)
    conn.execute("UPDATE signal_campaigns SET status='pending_approval', approval_id=%s, updated_at=now() WHERE id=%s", (a["id"], campaign_id))
    audit(conn, actor=p.email, operation="signal_campaign_push_requested", record_type="signal_campaign", record_id=campaign_id, approval_id=str(a["id"]),
          detail={"members": len(payload["members"]), "accounts": orgs})
    return {"approval_id": str(a["id"]), "status": a["status"], "members": len(payload["members"]), "accounts": orgs}


def withdraw(conn: Conn, campaign_id: str, actor: str) -> None:
    """Back to draft (to edit after a rejection or to change the audience). A pending approval is cancelled."""
    c = q1(conn, "SELECT status, approval_id FROM signal_campaigns WHERE id=%s FOR UPDATE", (campaign_id,))
    if not c or c["status"] not in ("pending_approval", "approved"):
        raise CampaignError("only a campaign waiting for approval can go back to draft")
    if c["approval_id"]:
        conn.execute("UPDATE approvals SET status='rejected', approver=coalesce(approver, %s) WHERE id=%s AND status IN ('pending','approved')", (f"withdrawn by {actor}", c["approval_id"]))
    conn.execute("UPDATE signal_campaigns SET status='draft', approval_id=NULL, updated_at=now() WHERE id=%s", (campaign_id,))
    audit(conn, actor=actor, operation="signal_campaign_withdrawn", record_type="signal_campaign", record_id=campaign_id, detail={})


def execute_push(conn: Conn, approval_id: str, client: LemlistClient | None, settings: Settings) -> dict[str, Any]:
    """The only path from a signal campaign to Lemlist. Kill switch, exact approval, single use, every lead re-checked, resumable after an ambiguous failure."""
    if not settings.outbound_enabled:
        raise appr.ApprovalError("outbound is switched off in this environment (RI_OUTBOUND_ENABLED=false)")
    a = q1(conn, "SELECT * FROM approvals WHERE id=%s", (approval_id,))
    if not a:
        raise appr.ApprovalError("approval not found")
    appr.validate_for_execution(conn, approval_id, kind=KIND, target_system=SYSTEM, target_id=a["target_id"], operation=OP, payload=a["payload"])
    if client is None:
        from ..sheets.client import ConnectorBlocked
        raise ConnectorBlocked("Lemlist is not connected")
    p = a["payload"]
    requester = q1(conn, "SELECT pipedrive_user_id FROM users WHERE email=%s AND active", (a["requested_by"],))
    crm_user = requester["pipedrive_user_id"] if requester else None
    ok_members, dropped = [], 0
    eligible_by_org: dict[str, set[tuple[str, str]]] = {}
    for m in p["members"]:
        org = m["organization_id"]
        if org not in eligible_by_org:
            blocked = ownership_check(conn, org, crm_user)
            eligible_by_org[org] = set() if blocked else {(c["id"], c["email"].casefold()) for c in plan_contacts(conn, org)["eligible"]}
        if (m["person_id"], m["email"].casefold()) in eligible_by_org[org] or q1(conn, "SELECT 1 FROM outbound_enrolments WHERE signal_campaign_id=%s AND lower(email)=%s", (p["campaign_id"], m["email"].lower())):
            ok_members.append(m)
        else:
            dropped += 1          # opted out, enrolled elsewhere or now owned by someone else since the approval: left out, never sent
    if not ok_members:
        raise appr.ApprovalError("no approved contact is still eligible")
    appr.mark_executing(conn, approval_id, hashlib.sha256(approval_id.encode()).hexdigest()[:12])
    conn.commit()
    prior = a["result"] or {}
    try:
        cid = prior.get("lemlist_campaign_id")
        if not cid:
            cid = client.create_campaign(p["campaign_name"]).get("_id")
            if not cid:
                raise LemlistError("Lemlist did not return a campaign id")
            conn.execute("UPDATE approvals SET result = coalesce(result,'{}'::jsonb) || %s::jsonb WHERE id=%s", (jb({"lemlist_campaign_id": cid}), approval_id))
            conn.commit()
        if not prior.get("steps_added"):
            seqs = client.sequences(cid)
            seq_id = (seqs[0] if isinstance(seqs, list) and seqs else (list(seqs.values())[0] if isinstance(seqs, dict) and seqs else {})).get("_id") if seqs else None
            if not seq_id:
                raise LemlistError("Lemlist returned no sequence for the new campaign")
            for i, s in enumerate(p["steps"]):
                client.add_step(cid, seq_id, subject=s["subject"] if i == 0 else None, message=s["body"].replace("\n", "<br>"), delay_days=s["delay_days"])
            conn.execute("UPDATE approvals SET result = coalesce(result,'{}'::jsonb) || %s::jsonb WHERE id=%s", (jb({"steps_added": True}), approval_id))
            conn.commit()
        pushed = skipped = 0
        for m in ok_members:
            email = m["email"].lower()
            if q1(conn, "SELECT 1 FROM outbound_enrolments WHERE signal_campaign_id=%s AND lower(email)=%s", (p["campaign_id"], email)):
                pushed += 1
                continue
            history = client.leads_by_email(email)
            if any((lead.get("campaign") or {}).get("id") != cid or lead.get("status") == "unsubscribed" for lead in history):
                skipped += 1
                conn.execute("UPDATE signal_campaign_members SET status='skipped_in_lemlist' WHERE id=%s", (m["member_id"],))
                continue
            v = m["variables"] or {}
            try:
                r = client.add_lead(cid, email, {k: v.get(k, "") for k in ("firstName", "lastName", "companyName", "jobTitle", "signalHook", "signalAngle")})
            except LemlistError as exc:
                if exc.status == 409 and "LEAD_ALREADY_IN_OTHER_CAMPAIGN" in str(exc):
                    skipped += 1
                    continue
                raise
            conn.execute("INSERT INTO outbound_enrolments(signal_campaign_id, organization_id, person_id, email, lemlist_campaign_id, lemlist_lead_id, approval_id) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (p["campaign_id"], m["organization_id"], m["person_id"], email, cid, (r or {}).get("_id"), approval_id))
            conn.execute("UPDATE signal_campaign_members SET status='pushed', lemlist_lead_id=%s WHERE id=%s", ((r or {}).get("_id"), m["member_id"]))
            from ..people import record_source
            record_source(conn, m["person_id"], "lemlist", cid, {"campaign": p["campaign_name"]})
            pushed += 1
            conn.commit()
        conn.execute("UPDATE signal_campaigns SET status='pushed', lemlist_campaign_id=%s, pushed_at=now(), updated_at=now() WHERE id=%s", (cid, p["campaign_id"]))
        appr.mark_done(conn, approval_id, True, {"lemlist_campaign_id": cid, "leads": pushed, "skipped_existing": skipped, "dropped_since_approval": dropped})
        audit(conn, actor="system:executor", operation="signal_campaign_pushed", record_type="signal_campaign", record_id=p["campaign_id"], approval_id=approval_id,
              detail={"campaign": cid, "leads": pushed})
        return {"status": "executed", "lemlist_campaign_id": cid, "leads": pushed, "skipped_existing": skipped, "dropped_since_approval": dropped,
                "note": "The campaign is in Lemlist and has not been launched. Review it there and press Launch."}
    except (LemlistError, OSError, httpx.HTTPError) as exc:
        if isinstance(exc, LemlistError) and exc.status and exc.status < 500 and exc.status != 429:
            appr.mark_done(conn, approval_id, False, {"error": str(exc)[:200]})
            conn.commit()
            raise
        conn.execute("UPDATE approvals SET status='approved' WHERE id=%s AND status='executing'", (approval_id,))      # ambiguous: a retry resumes from the stored campaign id
        conn.commit()
        raise
