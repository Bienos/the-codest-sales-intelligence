"""Client book: every client we deliver for, in one table. Where most of the revenue is, so what is running, what it is worth, when it ends,
who last spoke to them, and what suggests they will need more people or may leave.

Who counts as a client: an organisation with running work in the Delivery Clients pipeline (an open or won deal there), or a sales deal won
in the last three years. The work, its monthly value, start and end come from the revenue forecast (``forecast.revenue.build``, without the
simulation), so the two pages never disagree. On top of that, per client:
  * last contact and next step, from the Pipedrive activities on the client or its deals;
  * open job ads at the linked account (they are hiring: a chance to offer people);
  * active buying signals at the linked account;
  * flags: ending within 90 days, end only assumed, no monthly value, on hold, no contact for 30 days, not linked to an account.

Team-wide money, so the API serves it to roles that read the forecast."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import Conn, qa

ENDING_DAYS = 90
NO_CONTACT_DAYS = 30
FLAG_ORDER = ["ending_soon", "on_hold", "no_contact", "no_value", "end_assumed", "not_linked"]


def _engagements(conn: Conn, app_settings: Any, now: datetime) -> dict[str, Any]:
    from .forecast import revenue, service
    from .forecast.history import load_funnel
    s = service.load_settings(conn)
    pipeline_id = s["pipeline_id"] if s["pipeline_id"] is not None else getattr(app_settings, "pipedrive_pipeline_id", None)
    funnel = load_funnel(conn, pipeline_id, s["parking_stage_names"] or [], s["won_stage_names"] or [])
    currency = (s["currency"] or "EUR").upper()
    fx = {k.upper(): float(v) for k, v in (s["fx_to_eur"] or {}).items() if v}
    rev = revenue.build(conn, s, now=now, year=now.year + 1, won_stage_ids=funnel.won_ids, funnel_pipeline_id=pipeline_id,
                        convert=service.make_convert(currency, fx), sim=None, open_deals=[])
    return {"rev": rev, "currency": currency}


def book(conn: Conn, app_settings: Any, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    e = _engagements(conn, app_settings, now)
    rev = e["rev"]
    items: list[tuple[str, dict[str, Any]]] = [("work", x) for x in rev["engagements"]] + [("role", x) for x in rev["open_roles"]] + \
                                              [("hold", x) for x in rev["paused"]["deals"]] + [("no_value", x) for x in rev["missing_value"]]
    deal_ids = sorted({int(x["crm_id"]) for _, x in items})
    org_of = {r["crm_id"]: r["crm_org_id"] for r in qa(conn, "SELECT crm_id, crm_org_id FROM crm_deals WHERE crm_id = ANY(%s)", (deal_ids,))}
    org_ids = sorted({o for o in org_of.values() if o is not None})
    orgs = {r["crm_id"]: r for r in qa(conn, """
        SELECT c.crm_id, c.name, c.owner_name, c.organization_id, c.contract_end, c.fte_now, o.display_name AS account_name
        FROM crm_organizations c LEFT JOIN organizations o ON o.id = c.organization_id WHERE c.crm_id = ANY(%s)""", (org_ids,))}

    acts_on = bool(qa(conn, "SELECT 1 FROM sync_state WHERE system='pipedrive' AND entity='activities' AND watermark IS NOT NULL"))
    acts = {r["org"]: r for r in qa(conn, """
        SELECT coalesce(a.crm_org_id, d.crm_org_id) AS org, max(a.done_at) FILTER (WHERE a.done) AS last_done,
               min(a.due_at) FILTER (WHERE NOT a.done AND a.due_at >= %s) AS next_due
        FROM crm_activities a LEFT JOIN crm_deals d ON d.crm_id = a.crm_deal_id
        WHERE coalesce(a.crm_org_id, d.crm_org_id) = ANY(%s) GROUP BY 1""", (now - timedelta(days=1), org_ids))} if acts_on else {}
    linked = sorted({str(o["organization_id"]) for o in orgs.values() if o["organization_id"]})
    talk = {str(r["organization_id"]): r["t"] for r in qa(conn, "SELECT organization_id, max(occurred_at) t FROM contact_interactions "
                                                              "WHERE organization_id = ANY(%s::uuid[]) GROUP BY 1", (linked,))}
    jobs = {str(r["organization_id"]): r for r in qa(conn, """
        SELECT organization_id, count(*) AS n, (array_agg(title ORDER BY coalesce(posted_at, first_seen_at) DESC))[1:3] AS titles
        FROM job_postings WHERE organization_id = ANY(%s::uuid[]) AND open_status = 'open' GROUP BY 1""", (linked,))}
    sigs = {str(r["organization_id"]): r for r in qa(conn, """
        SELECT organization_id, count(*) AS n, max(created_at) AS latest, array_agg(DISTINCT rule_id) AS rules
        FROM signals WHERE organization_id = ANY(%s::uuid[]) AND status = 'active' GROUP BY 1""", (linked,))}

    by: dict[Any, dict[str, Any]] = {}
    for kind, x in items:
        oid = org_of.get(int(x["crm_id"]))
        o = orgs.get(oid) or {}
        c = by.setdefault(oid if oid is not None else f"deal:{x['crm_id']}", {
            "crm_org_id": oid, "name": o.get("name") or x.get("org") or x.get("title"), "owner": o.get("owner_name"),
            "account_id": str(o["organization_id"]) if o.get("organization_id") else None, "contract_end": o["contract_end"].isoformat() if o.get("contract_end") else None,
            "fte_recorded": float(o["fte_now"]) if o.get("fte_now") is not None else None,
            "monthly": 0.0, "fte": 0.0, "work": [], "open_roles": [], "on_hold": [], "no_value": [], "ends": []})
        row = {"crm_id": x["crm_id"], "title": x["title"]}
        if kind == "work":
            c["monthly"] += float(x["monthly"] or 0)
            c["fte"] += float(x.get("fte") or 0)
            c["work"].append({**row, "monthly": x["monthly"], "start": x["start"], "end": x["end"], "end_source": x["end_source"], "fte": x.get("fte")})
            c["ends"].append((x["end"], x["end_source"]))
        elif kind == "role":
            c["open_roles"].append({**row, "monthly": x["monthly"], "estimated": x.get("estimated"), "start": x["start"]})
        elif kind == "hold":
            c["on_hold"].append({**row, "monthly": x.get("monthly")})
        else:
            c["no_value"].append({**row, "stage": x.get("stage")})

    out = []
    for c in by.values():
        ends = c.pop("ends")
        known = [date.fromisoformat(d) for d, src in ends if src != "assumed"]
        nearest = min(known) if known else None
        if c["contract_end"]:
            nearest = min([date.fromisoformat(c["contract_end"])] + ([nearest] if nearest else []))
        a = acts.get(c["crm_org_id"]) or {}
        last = max([t for t in (a.get("last_done"), talk.get(c["account_id"] or "")) if t], default=None)
        flags = []
        if nearest and nearest <= today + timedelta(days=ENDING_DAYS):
            flags.append("ending_soon")
        if c["on_hold"]:
            flags.append("on_hold")
        if acts_on and (last is None or last < now - timedelta(days=NO_CONTACT_DAYS)):
            flags.append("no_contact")
        if c["no_value"]:
            flags.append("no_value")
        if c["work"] and not known and not c["contract_end"]:
            flags.append("end_assumed")
        if not c["account_id"]:
            flags.append("not_linked")
        j, sg = jobs.get(c["account_id"] or "") or {}, sigs.get(c["account_id"] or "") or {}
        out.append({**c, "monthly": round(c["monthly"], 2), "fte": round(c["fte"], 2) or c["fte_recorded"],
                    "ends_on": nearest.isoformat() if nearest else None, "days_left": (nearest - today).days if nearest else None,
                    "last_contact": last.isoformat() if last else None, "days_since_contact": (now - last).days if last else None,
                    "next_step": a["next_due"].isoformat() if a.get("next_due") else None,
                    "hiring": {"n": int(j["n"]), "titles": list(j["titles"] or [])} if j else None,
                    "signals": {"n": int(sg["n"]), "latest": sg["latest"].isoformat(), "rules": sorted(r for r in sg["rules"] if r)} if sg else None,
                    "flags": sorted(flags, key=FLAG_ORDER.index)})
    out.sort(key=lambda c: (-c["monthly"], c["name"] or ""))
    total = sum(c["monthly"] for c in out)
    for c in out:
        c["share"] = (c["monthly"] / total) if total else None
    count = {f: sum(1 for c in out if f in c["flags"]) for f in FLAG_ORDER}
    return {"as_of": now.isoformat(), "currency": e["currency"], "clients": out, "monthly_total": round(total, 2),
            "fte_total": round(sum(c["fte"] or 0 for c in out), 2), "flags": count, "activities_synced": acts_on,
            "ending_days": ENDING_DAYS, "no_contact_days": NO_CONTACT_DAYS,
            "delivery_pipeline": rev["delivery_pipeline"], "renewal_rate": rev["assumptions"]["renewal_rate"]}
