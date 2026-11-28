"""Two views over the open funnel deals: the sellers' forecast category next to the model's win chance (where they disagree is what the
weekly forecast call is for), and data quality per owner (which fields each deal still needs for the forecast to be trusted, plus pipeline
hygiene: a close date already passed, no next step in Pipedrive, no contact logged for NO_CONTACT_DAYS)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from ..db import Conn, qa

CATEGORY_ORDER = ["Commit", "Best case", "Pipeline"]
NO_CONTACT_DAYS = 20
HYGIENE = [("close_past", "Close date passed"), ("no_next_step", "No next step"), ("step_overdue", "Next step overdue"), ("no_contact", "No contact logged")]


def _stage_at(names: list[str], needle: str, default: int) -> int:
    for i, n in enumerate(names):
        if needle in n.lower():
            return i
    return min(default, max(0, len(names) - 1))


def _deals(conn: Conn, ids: list[int]) -> dict[int, dict[str, Any]]:
    return {r["crm_id"]: r for r in qa(conn, """
        SELECT d.crm_id, d.title, d.value, d.currency, d.owner_id, d.expected_close_date, d.lead_source_raw, d.origin, d.deal_type_raw, d.forecast_category_raw,
               d.start_date, d.duration_months, d.monthly_value, d.products, d.qualification, o.name AS org_name
        FROM crm_deals d LEFT JOIN crm_organizations o ON o.crm_id = d.crm_org_id WHERE d.crm_id = ANY(%s)""", (ids,))}


def _labels(conn: Conn, role: str) -> tuple[bool, dict[str, str]]:
    r = qa(conn, "SELECT field_key FROM crm_field_map WHERE role=%s", (role,))
    if not r:
        return False, {}
    return True, {x["option_id"]: x["label"] for x in qa(conn, "SELECT option_id, label FROM crm_field_options WHERE field_key=%s", (r[0]["field_key"],))}


def categories(conn: Conn, open_deals: list[dict[str, Any]], stage_names: list[str], win_by_deal: list[float], year_by_deal: list[float]) -> dict[str, Any]:
    mapped, labels = _labels(conn, "forecast_category")
    info = _deals(conn, [o["crm_id"] for o in open_deals])
    groups: dict[str, dict[str, Any]] = {}
    rows = []
    late = max(0, len(stage_names) - 2)
    for i, o in enumerate(open_deals):
        d = info.get(o["crm_id"], {})
        raw = d.get("forecast_category_raw")
        cat = labels.get(str(raw), f"Option {raw}") if raw else "Not set"
        p = win_by_deal[i] if i < len(win_by_deal) else None
        g = groups.setdefault(cat, {"category": cat, "n": 0, "value": 0.0, "model_expected": 0.0, "model_wins": 0.0})
        g["n"] += 1
        g["value"] += o["value"] or 0
        g["model_expected"] += year_by_deal[i] if i < len(year_by_deal) else 0.0
        g["model_wins"] += p or 0.0
        flag = None
        if cat == "Commit" and p is not None and p < 0.4:
            flag = "commit_model_doubts"
        elif cat in ("Not set", "Pipeline") and o["stage_index"] >= late and p is not None and p >= 0.5:
            flag = "late_not_committed"
        rows.append({"crm_id": o["crm_id"], "title": o["title"], "org": d.get("org_name"), "stage": stage_names[o["stage_index"]], "value": o["value"],
                     "estimated_value": bool(o.get("estimated")), "category": cat, "win_chance": p, "flag": flag})
    order = {c: i for i, c in enumerate(CATEGORY_ORDER + ["Not set"])}
    gl = sorted(groups.values(), key=lambda g: order.get(g["category"], 9))
    flagged = sorted([r for r in rows if r["flag"]], key=lambda r: -(r["value"] or 0))
    return {"field_set_up": mapped, "groups": gl, "flagged": flagged[:30],
            "set_share": (sum(1 for r in rows if r["category"] != "Not set") / len(rows)) if rows else None}


CHECKS = [("value", "Value"), ("lead_source", "Lead source"), ("deal_type", "Deal type"), ("close_date", "Expected close date"),
          ("category", "Forecast category"), ("start", "Planned start"), ("qual_early", "Pain and champion"), ("qual_late", "Decision maker and process")]


def _activity(conn: Conn, ids: list[int]) -> tuple[bool, dict[int, dict[str, Any]]]:
    """Whether Pipedrive activities have been read at least once, and per deal: the next open activity and the last one done."""
    if not qa(conn, "SELECT 1 FROM sync_state WHERE system='pipedrive' AND entity='activities' AND watermark IS NOT NULL"):
        return False, {}
    return True, {r["crm_deal_id"]: r for r in qa(conn, """
        SELECT crm_deal_id, min(due_at) FILTER (WHERE NOT done) AS next_due, count(*) FILTER (WHERE NOT done) AS open_n,
               max(done_at) FILTER (WHERE done) AS last_done
        FROM crm_activities WHERE crm_deal_id = ANY(%s) GROUP BY 1""", (ids,))}


def _hygiene(d: dict[str, Any], a: dict[str, Any] | None, acts_on: bool, now: datetime) -> tuple[list[str], int | None]:
    """Flags for one open deal, and days since the last contact logged in Pipedrive (None when activities are not read yet)."""
    flags = []
    close = d.get("expected_close_date")
    if close and close < now.date():
        flags.append("close_past")
    if not acts_on:
        return flags, None
    a = a or {}
    if not a.get("open_n"):
        flags.append("no_next_step")
    elif a.get("next_due") and a["next_due"] < now - timedelta(days=1):
        flags.append("step_overdue")                 # every open step is already past due
    last = a.get("last_done")
    since = (now - last).days if last else None
    if since is None or since >= NO_CONTACT_DAYS:
        flags.append("no_contact")
    return flags, since


def quality(conn: Conn, open_deals: list[dict[str, Any]], stage_names: list[str], stage_days: list[float], pipeline_id: int | None,
            now: datetime) -> dict[str, Any]:
    roles = {r["role"] for r in qa(conn, "SELECT role FROM crm_field_map")}
    users = {r["crm_id"]: r["name"] for r in qa(conn, "SELECT crm_id, name FROM crm_users")}
    info = _deals(conn, [o["crm_id"] for o in open_deals])
    acts_on, acts = _activity(conn, [o["crm_id"] for o in open_deals])
    hyg_totals = {k: 0 for k, _ in HYGIENE}
    sql = _stage_at(stage_names, "sql", 2)
    proposal = _stage_at(stage_names, "proposal", sql + 1)
    commercials = _stage_at(stage_names, "commercial", proposal + 1)
    src_on = bool(os.environ.get("RI_PIPEDRIVE_SOURCE_FIELD"))
    by_owner: dict[Any, dict[str, Any]] = {}
    totals = {k: [0, 0] for k, _ in CHECKS}
    deals = []
    for o in open_deals:
        d = info.get(o["crm_id"], {})
        q = d.get("qualification") or {}
        p = d.get("products") or {}
        i = o["stage_index"]
        need: dict[str, bool] = {"value": bool(d.get("value"))}
        if src_on:
            need["lead_source"] = bool(d.get("lead_source_raw"))
        if "deal_type" in roles:
            need["deal_type"] = bool(d.get("deal_type_raw"))
        if i >= sql:
            need["close_date"] = bool(d.get("expected_close_date"))
            if "forecast_category" in roles:
                need["category"] = bool(d.get("forecast_category_raw"))
        if i >= proposal:
            if "start_date" in roles or p:
                need["start"] = bool(d.get("start_date") or p.get("start"))
            if "q_pain" in roles:
                need["qual_early"] = bool(q.get("pain") and q.get("champion"))
        if i >= commercials and "q_decision_maker" in roles:
            need["qual_late"] = bool(q.get("decision_maker") and q.get("decision_process"))
        stale = i < len(stage_days) and o["days_in_stage"] > 2 * max(1.0, stage_days[i])
        missing = [k for k, ok in need.items() if not ok]
        for k, ok in need.items():
            totals[k][0] += int(ok)
            totals[k][1] += 1
        owner = d.get("owner_id")
        flags, since = _hygiene(d, acts.get(o["crm_id"]), acts_on, now)
        r = by_owner.setdefault(owner, {"owner_id": owner, "name": users.get(owner) or (f"User {owner}" if owner else "No owner"),
                                        "deals": 0, "filled": 0, "checks": 0, "stale": 0, "lost_no_reason": 0, **{k: 0 for k, _ in HYGIENE}})
        r["deals"] += 1
        for f in flags:
            r[f] += 1
            hyg_totals[f] += 1
        r["filled"] += sum(need.values())
        r["checks"] += len(need)
        r["stale"] += int(stale)
        if missing or stale or flags:
            deals.append({"crm_id": o["crm_id"], "title": o["title"], "org": d.get("org_name"), "owner": r["name"], "owner_id": owner, "stage": stage_names[i],
                          "value": d.get("value"), "missing": missing, "stale_days": round(o["days_in_stage"]) if stale else None, "hygiene": flags,
                          "close_date": d["expected_close_date"].isoformat() if d.get("expected_close_date") else None, "days_since_contact": since})
    for r in qa(conn, "SELECT owner_id, count(*) n FROM crm_deals WHERE status='lost' AND lost_time >= %s AND coalesce(lost_reason,'')='' "
                      "AND (%s::bigint IS NULL OR pipeline_id=%s) GROUP BY 1", (now - timedelta(days=90), pipeline_id, pipeline_id)):
        o = by_owner.setdefault(r["owner_id"], {"owner_id": r["owner_id"], "name": users.get(r["owner_id"]) or f"User {r['owner_id']}", "deals": 0, "filled": 0,
                                                "checks": 0, "stale": 0, "lost_no_reason": 0, **{k: 0 for k, _ in HYGIENE}})
        o["lost_no_reason"] = int(r["n"])
    lost = qa(conn, "SELECT count(*) n, count(*) FILTER (WHERE coalesce(lost_reason,'')='') blank FROM crm_deals WHERE status='lost' AND lost_time >= %s "
                    "AND (%s::bigint IS NULL OR pipeline_id=%s)", (now - timedelta(days=90), pipeline_id, pipeline_id))[0]
    owners = sorted(by_owner.values(), key=lambda r: (r["filled"] / r["checks"]) if r["checks"] else 1.0)
    for r in owners:
        r["score"] = (r["filled"] / r["checks"]) if r["checks"] else None
    labels = dict(CHECKS)
    return {"owners": owners, "checks": [{"key": k, "label": labels[k], "filled": v[0], "of": v[1], "share": (v[0] / v[1]) if v[1] else None}
                                         for k, v in totals.items() if v[1]],
            "deals": sorted(deals, key=lambda x: (-(len(x["missing"]) + len(x["hygiene"])), -(x["value"] or 0)))[:200],
            "hygiene": {"activities_synced": acts_on, "no_contact_days": NO_CONTACT_DAYS, "open_deals": len(open_deals),
                        "counts": [{"key": k, "label": lbl, "n": hyg_totals[k]} for k, lbl in HYGIENE if acts_on or k == "close_past"]},
            "pipedrive_url": f"https://{os.environ['PIPEDRIVE_DOMAIN']}.pipedrive.com" if os.environ.get("PIPEDRIVE_DOMAIN") else None,
            "lost_90d": {"n": int(lost["n"] or 0), "no_reason": int(lost["blank"] or 0)},
            "fields_set_up": sorted(roles), "stage_rules": {"sql": stage_names[sql] if stage_names else None, "proposal": stage_names[proposal] if stage_names else None,
                                                            "commercials": stage_names[commercials] if stage_names else None}}
