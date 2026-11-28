"""Campaign results: every Lemlist campaign side by side, whoever made it (in the app or in Lemlist itself), from the activity mirrored hourly
into lemlist_activities.

Counting follows the forecast's outbound view (``forecast.service.outbound_actuals``): a lead is a person sent at least one email; replies,
interest, bounces and opt-outs count people, not events; opens are left out because privacy proxies open emails on their own.
Per campaign it adds the email step, the sender (for managers), the signal campaign it came from, and what followed: Pipedrive deals created
at a contacted company after its first email. A bounce rate from BOUNCE_WARN hurts delivery for every campaign on the same mailboxes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Conn, qa

BOUNCE_WARN = 0.02
BOUNCE_BAD = 0.05
REPLIED = ("emailsReplied", "linkedinReplied")
INTERESTED = ("emailsInterested", "interested")
UNSUB = ("emailsUnsubscribed", "unsubscribed")


def bounce_state(bounced: int, leads: int) -> str | None:
    if not leads:
        return None
    r = bounced / leads
    return "bad" if r >= BOUNCE_BAD else "warn" if r >= BOUNCE_WARN else "ok"


def _counts(cols: str, group: str) -> str:
    return f"""
        SELECT {cols},
               count(DISTINCT lower(email)) FILTER (WHERE type='emailsSent') AS leads, count(*) FILTER (WHERE type='emailsSent') AS sends,
               count(DISTINCT lower(email)) FILTER (WHERE type = ANY(%(rep)s)) AS replied,
               count(DISTINCT lower(email)) FILTER (WHERE type = ANY(%(int)s)) AS interested,
               count(DISTINCT lower(email)) FILTER (WHERE type='emailsBounced') AS bounced,
               count(DISTINCT lower(email)) FILTER (WHERE type = ANY(%(uns)s)) AS unsubscribed,
               min(happened_at) FILTER (WHERE type='emailsSent') AS first_sent, max(happened_at) AS last_activity
        FROM lemlist_activities WHERE happened_at >= %(since)s GROUP BY {group}"""


def _rates(r: dict[str, Any]) -> dict[str, Any]:
    leads = int(r.get("leads") or 0)
    out = {k: int(r.get(k) or 0) for k in ("leads", "sends", "replied", "interested", "bounced", "unsubscribed")}
    out["reply_rate"] = (out["replied"] / leads) if leads else None
    out["bounce_rate"] = (out["bounced"] / leads) if leads else None
    out["bounce"] = bounce_state(out["bounced"], leads)
    return out


def results(conn: Conn, *, days: int = 90, by_sender: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    p = {"since": since, "rep": list(REPLIED), "int": list(INTERESTED), "uns": list(UNSUB)}
    camps = qa(conn, _counts("campaign_id, coalesce(campaign_name, campaign_id, 'Unknown campaign') AS name", "campaign_id, 2"), p)
    linked = {r["lemlist_campaign_id"]: r for r in qa(conn, "SELECT id, name, rule_ids, lemlist_campaign_id FROM signal_campaigns WHERE lemlist_campaign_id IS NOT NULL")}
    # what followed: a Pipedrive deal created at a contacted company after that company's first email in the campaign
    deals = {r["campaign_id"]: r for r in qa(conn, """
        WITH firsts AS (
            SELECT a.campaign_id, e.organization_id AS org, min(a.happened_at) AS t
            FROM lemlist_activities a
            LEFT JOIN people pe ON pe.id = a.person_id
            LEFT JOIN people pm ON a.person_id IS NULL AND lower(pm.work_email) = lower(a.email)
            LEFT JOIN employment e ON e.person_id = coalesce(pe.id, pm.id) AND e.valid_to IS NULL
            WHERE a.type = 'emailsSent' AND a.happened_at >= %(since)s
            GROUP BY 1, 2)
        SELECT f.campaign_id, count(DISTINCT f.org) FILTER (WHERE f.org IS NOT NULL) AS companies,
               count(DISTINCT d.crm_id) AS deals, count(DISTINCT d.crm_id) FILTER (WHERE d.status = 'won') AS won
        FROM firsts f
        LEFT JOIN crm_organizations c ON c.organization_id = f.org
        LEFT JOIN crm_deals d ON d.crm_org_id = c.crm_id AND d.add_time >= f.t
        GROUP BY 1""", p)}
    rows = []
    for c in camps:
        sc = linked.get(c["campaign_id"])
        dl = deals.get(c["campaign_id"]) or {}
        rows.append({"campaign_id": c["campaign_id"], "name": c["name"], "draft": "[draft]" in (c["name"] or "").lower(), **_rates(c),
                     "first_sent": c["first_sent"].isoformat() if c["first_sent"] else None,
                     "last_activity": c["last_activity"].isoformat() if c["last_activity"] else None,
                     "companies": int(dl.get("companies") or 0), "deals": int(dl.get("deals") or 0), "won": int(dl.get("won") or 0),
                     "signal_campaign": {"id": str(sc["id"]), "name": sc["name"], "rule_ids": list(sc["rule_ids"] or [])} if sc else None})
    rows.sort(key=lambda r: (-r["leads"], r["name"]))
    steps = [{"step": r["step"], **_rates(r)} for r in qa(conn, _counts("sequence_step AS step", "sequence_step"), p)]
    steps.sort(key=lambda r: (r["step"] is None, r["step"] or 0))
    total = qa(conn, _counts("true AS all_", "1"), p)
    out: dict[str, Any] = {"as_of": now.isoformat(), "window_days": days, "campaigns": rows, "steps": steps,
                           "total": _rates(total[0]) if total else _rates({}), "bounce_warn": BOUNCE_WARN, "bounce_bad": BOUNCE_BAD,
                           "deals_total": sum(r["deals"] for r in rows)}
    if by_sender:
        snd = qa(conn, _counts("coalesce(send_user_email, '') AS email, max(send_user_name) AS sender_name", "1"), p)
        out["senders"] = sorted([{"email": r["email"] or None, "name": r["sender_name"], **_rates(r)} for r in snd],
                                key=lambda r: (r["email"] is None, -r["leads"]))
    return out
