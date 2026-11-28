"""Invoiced revenue month by month, for an IT-services firm: what existing clients pay (backlog), what continues after a contract ends
(renewals), open roles at existing clients (expansions), and what the sales funnel wins (from the simulation, spread over the months a
won contract is invoiced). Also: contracts ending soon, and how much of the backlog sits with the largest clients.

A deal's monthly value, in order of preference: its products (price x quantity per billing cycle), the "Monthly value" field, the deal
value over "Duration (months)", and else the deal value over the default contract length (for Delivery Clients deals the value can be
declared a monthly figure instead). Its start: products' billing start, "Planned start", else the date it was won (+ the start lag) or,
for a running engagement, the date it was created. Its end: the organisation's "Contract end", else start + duration."""
from __future__ import annotations

import statistics
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..db import Conn, qa

MONTH_DAYS = 30.44


def _months_between(a: date, b: date) -> float:
    return (b - a).days / MONTH_DAYS


def _add_months(d: date, m: float) -> date:
    return d + timedelta(days=round(m * MONTH_DAYS))


def _month_series(months: list[date], monthly: float, start: date, end: date) -> list[float]:
    out = []
    for m in months:
        dim = monthrange(m.year, m.month)[1]
        me = m + timedelta(days=dim)
        ov = (min(end, me) - max(start, m)).days
        out.append(monthly * max(0, ov) / dim)
    return out


def _add(a: list[float], b: list[float], f: float = 1.0) -> None:
    for i, x in enumerate(b):
        a[i] += x * f


def _blend(prior: float, measured: float | None, n: float, k: float) -> float:
    return prior if measured is None or n <= 0 else (prior * k + measured * n) / (k + n)


def _norm(names: list[str] | None) -> set[str]:
    return {n.strip().lower() for n in (names or []) if n and n.strip()}


def delivery_pipeline(conn: Conn, names: list[str], open_stage_names: set[str]) -> dict[str, Any] | None:
    """The pipeline of running client work: found by its name (synced pipelines), else as the pipeline holding an open-roles stage."""
    want = _norm(names)
    stages = qa(conn, "SELECT crm_id, pipeline_id, name FROM crm_stages WHERE NOT is_deleted ORDER BY order_nr NULLS LAST, crm_id")
    pick = None
    for p in qa(conn, "SELECT crm_id, name FROM crm_pipelines"):
        if (p["name"] or "").strip().lower() in want:
            pick = (p["crm_id"], p["name"])
            break
    if pick is None and open_stage_names:
        hit = next((r for r in stages if (r["name"] or "").strip().lower() in open_stage_names), None)
        if hit:
            pick = (hit["pipeline_id"], None)
    if pick is None:
        return None
    return {"id": pick[0], "name": pick[1], "stages": [r for r in stages if r["pipeline_id"] == pick[0]]}


def build(conn: Conn, s: dict[str, Any], *, now: datetime, year: int, won_stage_ids: set[int], funnel_pipeline_id: int | None,
          convert: Callable[[Any, Any], float | None], sim: dict[str, Any] | None, open_deals: list[dict[str, Any]]) -> dict[str, Any]:
    today = now.date()
    months: list[date] = []
    m = date(today.year, today.month, 1)
    while m.year <= year:
        months.append(m)
        m = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
    NM = len(months)
    contract = float(s.get("contract_months") or 12)
    lag = float(s.get("start_lag_days") if s.get("start_lag_days") is not None else 30)
    renew = float(s.get("renewal_rate") if s.get("renewal_rate") is not None else 60) / 100
    value_monthly = (s.get("delivery_value_means") or "total") == "monthly"
    open_names, hold_names = _norm(s.get("delivery_open_stage_names")), _norm(s.get("delivery_hold_stage_names"))
    dp = delivery_pipeline(conn, s.get("delivery_pipeline_names") or [], open_names)
    if dp and dp["id"] == funnel_pipeline_id:
        dp = None
    dstages = {r["crm_id"]: r for r in (dp["stages"] if dp else [])}
    open_ids = {i for i, r in dstages.items() if (r["name"] or "").strip().lower() in open_names}
    hold_ids = {i for i, r in dstages.items() if (r["name"] or "").strip().lower() in hold_names}
    pids = [p for p in (funnel_pipeline_id, dp["id"] if dp else None) if p is not None]
    rows = qa(conn, """
        SELECT d.crm_id, d.title, d.pipeline_id, d.stage_id, d.stage_name, d.status, d.value, d.currency, d.add_time, d.won_time, d.stage_change_time,
               d.expected_close_date, d.start_date, d.duration_months, d.fte, d.monthly_value, d.products, d.owner_id, d.crm_org_id,
               o.name AS org_name, o.contract_end, o.client_status_raw
        FROM crm_deals d LEFT JOIN crm_organizations o ON o.crm_id = d.crm_org_id
        WHERE d.pipeline_id = ANY(%s) AND (d.status = 'open' OR (d.status = 'won' AND d.won_time >= %s))""",
              (pids or [-1], now - timedelta(days=round(3 * 365))))

    def money(v: Any, cur: Any) -> float | None:
        if v in (None, 0):
            return None
        x = convert(v, cur)
        return float(x if x is not None else v)

    def monthly_of(d: dict[str, Any], delivery: bool) -> tuple[float | None, str]:
        p = d["products"] or {}
        if p.get("monthly"):
            return money(p["monthly"], d["currency"]), "products"
        if d["monthly_value"]:
            return money(d["monthly_value"], d["currency"]), "monthly field"
        v = money(d["value"], d["currency"])
        if v is None:
            return None, "no value"
        if d["duration_months"]:
            return v / max(0.5, float(d["duration_months"])), "value / duration"
        if delivery and value_monthly:
            return v, "value is monthly"
        return v / contract, "value / default length"

    def months_of(d: dict[str, Any]) -> tuple[float, bool]:
        p = d["products"] or {}
        if p.get("months"):
            return float(p["months"]), True
        if d["duration_months"]:
            return float(d["duration_months"]), True
        return contract, False

    def start_of(d: dict[str, Any], running: bool) -> date:
        p = d["products"] or {}
        if p.get("start"):
            return date.fromisoformat(p["start"])
        if d["start_date"]:
            return d["start_date"]
        if running and not d["won_time"]:
            return (d["add_time"] or now).date()
        base = d["won_time"] or d["stage_change_time"] or d["add_time"] or now
        return base.date() + timedelta(days=round(lag))

    layers = {k: [0.0] * NM for k in ("backlog", "renewals", "expansions")}
    fte = [0.0] * NM
    engagements, roles, missing = [], [], []
    paused = {"n": 0, "monthly": 0.0, "deals": []}
    by_org: dict[str, float] = {}
    in_year = [mm.year == year for mm in months]
    known_monthly: list[float] = []

    # a client run from the delivery pipeline is counted there only: its signed sales deal would count the same work twice
    delivery_orgs = {d["crm_org_id"] for d in rows if dp and d["pipeline_id"] == dp["id"] and d["crm_org_id"] is not None and d["stage_id"] not in open_ids}
    covered = []
    for d in rows:
        delivery = bool(dp) and d["pipeline_id"] == dp["id"]
        if delivery and d["stage_id"] in open_ids and d["status"] == "open":
            roles.append(d)
            continue
        if not delivery and d["crm_org_id"] in delivery_orgs and (d["status"] == "won" or d["stage_id"] in won_stage_ids):
            covered.append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"]})
            continue
        engaged = (delivery and d["status"] in ("open", "won")) or (not delivery and (d["status"] == "won" or d["stage_id"] in won_stage_ids))
        if not engaged:
            continue
        mo, how = monthly_of(d, delivery)
        if delivery and d["stage_id"] in hold_ids:
            paused["n"] += 1
            paused["monthly"] += mo or 0
            paused["deals"].append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"], "monthly": mo})
            continue
        if mo is None:
            missing.append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"], "pipeline": "delivery" if delivery else "sales", "stage": d["stage_name"]})
            continue
        known_monthly.append(mo)
        start = start_of(d, running=delivery)
        dur, dur_known = months_of(d)
        if d["contract_end"]:
            end, end_src = d["contract_end"], "contract end"
        elif dur_known:
            end, end_src = _add_months(start, dur), "duration"
        else:
            end, end_src = _add_months(start, dur), "assumed"
            if d["status"] == "open" and end < today + timedelta(days=90):     # still running with no end on record: assume three more months
                end = today + timedelta(days=90)
        series = _month_series(months, mo, start, end)
        _add(layers["backlog"], series)
        after = _month_series(months, mo, end, _add_months(end, contract))
        _add(layers["renewals"], after, renew)
        f = float((d["products"] or {}).get("fte") or d["fte"] or 0)
        if f:
            _add(fte, _month_series(months, f, start, end))
        org = d["org_name"] or d["title"]
        by_org[org] = by_org.get(org, 0.0) + sum(x for x, y in zip(series, in_year, strict=True) if y) + renew * sum(x for x, y in zip(after, in_year, strict=True) if y)
        engagements.append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"], "pipeline": "delivery" if delivery else "sales", "stage": d["stage_name"],
                            "monthly": mo, "how": how, "start": start.isoformat(), "end": end.isoformat(), "end_source": end_src, "fte": f or None})

    # open roles at existing clients
    won9 = lost9 = 0
    if dp:
        r = qa(conn, "SELECT count(*) FILTER (WHERE status='won') w, count(*) FILTER (WHERE status='lost') l FROM crm_deals WHERE pipeline_id=%s AND add_time >= %s",
               (dp["id"], now - timedelta(days=730)))
        won9, lost9 = int(r[0]["w"] or 0), int(r[0]["l"] or 0)
    prior_role = float(s.get("open_role_win_rate") if s.get("open_role_win_rate") is not None else 50)
    measured = won9 / (won9 + lost9) if won9 + lost9 else None
    p_role = _blend(prior_role / 100, measured, won9 + lost9, float(s.get("prior_strength") or 20))
    typical = statistics.median(known_monthly) if known_monthly else None
    role_rows = []
    for d in roles:
        mo, how = monthly_of(d, True)
        est = mo is None
        mo = mo if mo is not None else typical
        if mo is None:
            missing.append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"], "pipeline": "delivery", "stage": d["stage_name"]})
            continue
        dur, _ = months_of(d)
        p = d["products"] or {}
        if p.get("start"):
            start = date.fromisoformat(p["start"])
        elif d["start_date"]:
            start = d["start_date"]
        else:
            start = max(d["expected_close_date"] or today, today) + timedelta(days=round(lag))
        _add(layers["expansions"], _month_series(months, mo, start, _add_months(start, dur)), p_role)
        role_rows.append({"crm_id": d["crm_id"], "title": d["title"], "org": d["org_name"], "monthly": mo, "estimated": est, "how": "typical monthly" if est else how,
                          "start": start.isoformat(), "months": dur, "chance": p_role})

    inv = (sim or {}).get("invoiced") or {}
    sim_open = inv.get("open") or [0.0] * NM
    sim_new = inv.get("new") or [0.0] * NM
    det = [layers["backlog"][i] + layers["renewals"][i] + layers["expansions"][i] for i in range(NM)]
    total = [det[i] + sim_open[i] + sim_new[i] for i in range(NM)]
    p10 = [det[i] + x for i, x in enumerate(inv.get("p10") or [0.0] * NM)]
    p90 = [det[i] + x for i, x in enumerate(inv.get("p90") or [0.0] * NM)]
    yr = inv.get("year") or {"p10": 0.0, "p50": 0.0, "p90": 0.0, "mean": 0.0}
    det_year = sum(x for x, y in zip(det, in_year, strict=True) if y)

    def ysum(a: list[float]) -> float:
        return sum(x for x, y in zip(a, in_year, strict=True) if y)
    horizon = today + timedelta(days=90)
    renewals = sorted([e for e in engagements if date.fromisoformat(e["end"]) <= horizon], key=lambda e: e["end"])
    orgs_ending = [{"org": r["name"], "contract_end": r["contract_end"].isoformat()} for r in qa(conn,
                   "SELECT name, contract_end FROM crm_organizations WHERE contract_end BETWEEN %s AND %s ORDER BY contract_end", (today, horizon))]
    total_year = sum(by_org.values())
    top = sorted(by_org.items(), key=lambda kv: -kv[1])[:5]
    return {
        "months": [mm.isoformat() for mm in months], "year": year,
        "layers": {**layers, "open_pipeline": sim_open, "new_deals": sim_new}, "total": total, "p10": p10, "p90": p90,
        "year_total": {"mean": det_year + yr.get("mean", 0.0), "p10": det_year + yr.get("p10", 0.0), "p50": det_year + yr.get("p50", 0.0), "p90": det_year + yr.get("p90", 0.0),
                       "by_layer": {"backlog": ysum(layers["backlog"]), "renewals": ysum(layers["renewals"]), "expansions": ysum(layers["expansions"]),
                                    "open_pipeline": ysum(sim_open), "new_deals": ysum(sim_new)}},
        "this_month": {"total": total[0] if total else 0.0, "backlog": layers["backlog"][0] if NM else 0.0},
        "fte_by_month": fte, "engagements": sorted(engagements, key=lambda e: -e["monthly"]), "open_roles": role_rows, "paused": paused, "missing_value": missing,
        "counted_in_delivery": covered,
        "renewals_due": renewals, "orgs_ending": orgs_ending,
        "concentration": [{"org": k, "year": v, "share": (v / total_year) if total_year else None} for k, v in top],
        "assumptions": {"contract_months": contract, "start_lag_days": lag, "renewal_rate": renew * 100, "delivery_value_means": "monthly" if value_monthly else "total",
                        "open_role_win_rate": {"prior": prior_role, "measured": None if measured is None else measured * 100, "n": won9 + lost9, "used": p_role * 100}},
        "delivery_pipeline": {"found": bool(dp), "id": dp["id"] if dp else None, "name": dp["name"] if dp else None,
                              "names": s.get("delivery_pipeline_names") or []},
    }
