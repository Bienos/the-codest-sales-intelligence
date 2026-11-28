"""Builds the forecast page: settings (priors and planning inputs), measured history blended with the priors, lemlist outbound actuals,
the simulation, the sensitivity of the year to each input, and the daily log used to score past forecasts.

Blending: a measured rate counts as `prior_strength` deals' worth of evidence for the prior, so with few deals the prior dominates and with
many the measured rate does: blended = (prior x k + moved_on) / (k + decided). Durations and the average deal value blend the same way."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

from ..db import Conn, jb, q1, qa
from . import engine, outlook, quality, revenue
from .history import History, load_funnel, measure

DEFAULT_CONV = [55, 55, 50, 55, 65]
DEFAULT_DAYS = [10, 18, 25, 30, 21]
DEFAULTS: dict[str, Any] = {
    "year": None,                      # None: next year from October on, else this year
    "currency": None,                  # None: EUR
    "fx_to_eur": {"EUR": 1.0, "PLN": 0.235, "GBP": 1.17, "USD": 0.92},       # rough rates, edited on the page: 1 unit of the currency in EUR
    "target": 0,
    "pipeline_id": None,               # None: RI_PIPEDRIVE_PIPELINE_ID
    "parking_stage_names": ["Marketing Nurturing", "On-Hold", "Passive"],      # deals here are not moving through the funnel
    "won_stage_names": ["Active Delivery"],
    "outbound_entry_stage_id": None,   # None: RI_PIPEDRIVE_STAGE_ID (where a positive reply starts a deal), else the first stage
    "inbound_entry_stage_id": None,    # None: the first stage
    "prior_strength": 20,
    "prior_conv": {},                  # {stage id: % moving on}; missing stages use DEFAULT_CONV by position
    "prior_days": {},                  # {stage id: median days}
    "deal_value": 120000,
    "leads_per_month": None,           # None: measured from lemlist (last 90 days), else 800
    "reply_rate": 2.5,                 # priors, in %
    "positive_share": 25,
    "deal_from_positive": 60,
    "inbound_per_month": None,         # None: measured from deals created at the inbound stage, else 4
    # invoiced revenue (forecast/revenue.py)
    "contract_months": 12,             # how long a contract is invoiced when the deal does not say
    "start_lag_days": 30,              # from signing to the first invoiced day
    "renewal_rate": 60,                # % chance a contract continues after its end
    "open_role_win_rate": 50,          # prior, in %: an open role at an existing client gets filled
    "delivery_pipeline_names": ["Delivery Clients"],
    "delivery_open_stage_names": ["OPEN ROLES"],
    "delivery_hold_stage_names": ["On hold"],
    "delivery_value_means": "total",   # Delivery Clients deal value: 'total' (spread over the contract) or 'monthly'
}
RUNS = 1000
SENS_STEP = 0.2


def load_settings(conn: Conn) -> dict[str, Any]:
    row = q1(conn, "SELECT settings, updated_by, updated_at FROM forecast_settings WHERE id=1")
    s = copy.deepcopy(DEFAULTS)
    if row:
        s.update({k: v for k, v in (row["settings"] or {}).items() if k in DEFAULTS})
    s["_updated_by"] = row["updated_by"] if row else None
    s["_updated_at"] = row["updated_at"].isoformat() if row and row["updated_at"] else None
    return s


def save_settings(conn: Conn, patch: dict[str, Any], actor: str) -> dict[str, Any]:
    cur = {k: v for k, v in load_settings(conn).items() if not k.startswith("_")}
    cur.update({k: v for k, v in patch.items() if k in DEFAULTS})
    conn.execute("INSERT INTO forecast_settings(id, settings, updated_by, updated_at) VALUES (1,%s,%s,now()) "
                 "ON CONFLICT (id) DO UPDATE SET settings=EXCLUDED.settings, updated_by=EXCLUDED.updated_by, updated_at=now()", (jb(cur), actor))
    return cur


def _blend(prior: float, measured: float | None, n: float, k: float) -> float:
    if measured is None or n <= 0:
        return prior
    return (prior * k + measured * n) / (k + n)


def outbound_actuals(conn: Conn, days: int = 90) -> dict[str, Any]:
    """Lemlist activity mirrored in lemlist_activities. Leads count once, by the first email they were sent; opens are ignored."""
    agg = q1(conn, """
        WITH first_sent AS (SELECT lower(email) e, min(happened_at) t FROM lemlist_activities WHERE type='emailsSent' GROUP BY 1)
        SELECT (SELECT count(*) FROM first_sent WHERE t >= now() - make_interval(days => %s)) AS contacted,
               count(DISTINCT lower(email)) FILTER (WHERE type IN ('emailsReplied','linkedinReplied')) AS replied,
               count(DISTINCT lower(email)) FILTER (WHERE type IN ('emailsInterested','interested')) AS interested,
               count(DISTINCT lower(email)) FILTER (WHERE type='emailsBounced') AS bounced,
               count(*) FILTER (WHERE type='emailsSent') AS sends
        FROM lemlist_activities WHERE happened_at >= now() - make_interval(days => %s)""", (days, days)) or {}
    camps = qa(conn, """
        SELECT coalesce(campaign_name, campaign_id, 'Unknown campaign') AS name,
               count(DISTINCT lower(email)) FILTER (WHERE type='emailsSent') AS leads, count(*) FILTER (WHERE type='emailsSent') AS sends,
               count(DISTINCT lower(email)) FILTER (WHERE type IN ('emailsReplied','linkedinReplied')) AS replied,
               count(DISTINCT lower(email)) FILTER (WHERE type IN ('emailsInterested','interested')) AS interested,
               count(DISTINCT lower(email)) FILTER (WHERE type='emailsBounced') AS bounced,
               min(happened_at) FILTER (WHERE type='emailsSent') AS first_sent
        FROM lemlist_activities WHERE happened_at >= now() - make_interval(days => %s) GROUP BY 1 ORDER BY 3 DESC LIMIT 40""", (days,))
    first = q1(conn, "SELECT min(happened_at) AS t FROM lemlist_activities WHERE type='emailsSent'")
    since = first["t"] if first else None
    span = days if not since else max(1.0, min(days, (datetime.now(timezone.utc) - since).total_seconds() / 86400))
    return {"window_days": days, "observed_days": round(span, 1), "contacted": int(agg.get("contacted") or 0), "replied": int(agg.get("replied") or 0),
            "interested": int(agg.get("interested") or 0), "bounced": int(agg.get("bounced") or 0), "sends": int(agg.get("sends") or 0),
            "contacted_per_month": (int(agg.get("contacted") or 0) / span * 30.4) if agg.get("contacted") else None,
            "campaigns": [{**c, "first_sent": c["first_sent"].isoformat() if c["first_sent"] else None} for c in camps]}


def make_convert(currency: str, fx: dict[str, float]):
    def convert(value: Any, cur: Any) -> float | None:
        c = str(cur or currency).upper()
        if c == currency:
            return float(value)
        if c not in fx or currency not in fx:
            return None
        return float(value) * fx[c] / fx[currency]
    return convert


def _open_months(conn: Conn, h: History, default: float) -> list[float]:
    """Months each open deal's contract runs, when the deal says (products, Duration), else the default contract length."""
    rows = {r["crm_id"]: r for r in qa(conn, "SELECT crm_id, duration_months, products FROM crm_deals WHERE crm_id = ANY(%s)", ([o["crm_id"] for o in h.open_deals],))}
    out = []
    for o in h.open_deals:
        r = rows.get(o["crm_id"]) or {}
        p = r.get("products") or {}
        out.append(float(p.get("months") or r.get("duration_months") or default))
    return out


def _resolve(conn: Conn, s: dict[str, Any], app_settings: Any, now: datetime) -> tuple[dict[str, Any], History, dict[str, Any]]:
    year = int(s["year"] or (now.year + 1 if now.month >= 10 else now.year))
    pipeline_id = s["pipeline_id"] if s["pipeline_id"] is not None else getattr(app_settings, "pipedrive_pipeline_id", None)
    funnel = load_funnel(conn, pipeline_id, s["parking_stage_names"] or [], s["won_stage_names"] or [])
    currency = (s["currency"] or "EUR").upper()
    fx = {k.upper(): float(v) for k, v in (s["fx_to_eur"] or {}).items() if v}

    convert = make_convert(currency, fx)
    h = measure(conn, funnel, now=now, year=year, convert=convert)
    out = outbound_actuals(conn)
    k = float(s["prior_strength"] or 0)
    win_n = len(h.won_values)
    deal_value = {"prior": float(s["deal_value"]), "measured": (sum(h.won_values) / win_n) if win_n else None, "n": win_n,
                  "used": _blend(float(s["deal_value"]), (sum(h.won_values) / win_n) if win_n else None, win_n, k)}
    for o in h.open_deals:                    # a deal without a value in Pipedrive is worth the average deal
        o["estimated"] = not o["value"]
        o["value"] = o["value"] or deal_value["used"]
    stages = []
    for i, st in enumerate(funnel.stages):
        pc = float(s["prior_conv"].get(str(st.id), DEFAULT_CONV[i] if i < len(DEFAULT_CONV) else 55))
        pdays = float(s["prior_days"].get(str(st.id), DEFAULT_DAYS[i] if i < len(DEFAULT_DAYS) else 21))
        m = h.stats[i]
        rate, med = m.rate(), m.median_days()
        opened = [o for o in h.open_deals if o["stage_index"] == i]
        stages.append({"id": st.id, "name": st.name, "prior": {"conv": pc, "days": pdays},
                       "measured": {"conv": None if rate is None else rate * 100, "decided": m.resolved, "moved_on": m.passed, "lost": m.lost, "still_open": m.still_open,
                                    "days": med, "days_n": len(m.days)},
                       "used": {"conv": _blend(pc / 100, rate, m.resolved, k) * 100, "days": _blend(pdays, med, len(m.days), k)},
                       "open": {"n": len(opened), "value": sum(o["value"] for o in opened),
                                "avg_days": (sum(o["days_in_stage"] for o in opened) / len(opened)) if opened else None}})
    ids = [st.id for st in funnel.stages]
    out_entry = s["outbound_entry_stage_id"] if s["outbound_entry_stage_id"] is not None else getattr(app_settings, "pipedrive_stage_id", None)
    out_idx = ids.index(out_entry) if out_entry in ids else 0
    in_entry = s["inbound_entry_stage_id"]
    in_idx = ids.index(in_entry) if in_entry in ids else 0
    months = h.entry_window_days / 30.4
    inbound_measured = (h.entries_by_stage[in_idx] / months) if funnel.source == "pipedrive" and in_idx != out_idx else None
    outbound_deals = h.entries_by_stage[out_idx] if funnel.source == "pipedrive" else 0
    def rate(prior_pct: float, num: int, den: int) -> dict[str, Any]:
        meas = (num / den) if den else None
        return {"prior": prior_pct, "measured": None if meas is None else meas * 100, "n": den, "used": _blend(prior_pct / 100, meas, den, k) * 100}
    inputs = {
        "year": year, "pipeline_id": pipeline_id, "currency": currency, "fx_to_eur": fx,
        "target": float(s["target"] or 0),
        "leads_per_month": {"setting": s["leads_per_month"], "measured": out["contacted_per_month"],
                            "used": float(s["leads_per_month"] if s["leads_per_month"] is not None else out["contacted_per_month"] or 800)},
        "reply_rate": rate(float(s["reply_rate"]), out["replied"], out["contacted"]),
        "positive_share": rate(float(s["positive_share"]), out["interested"], out["replied"]),
        "deal_from_positive": rate(float(s["deal_from_positive"]), min(outbound_deals, out["interested"]), out["interested"]),
        "inbound_per_month": {"setting": s["inbound_per_month"], "measured": inbound_measured,
                              "used": float(s["inbound_per_month"] if s["inbound_per_month"] is not None else inbound_measured if inbound_measured is not None else 4)},
        "deal_value": deal_value,
        "outbound_entry": {"stage_id": ids[out_idx], "index": out_idx}, "inbound_entry": {"stage_id": ids[in_idx], "index": in_idx},
        "prior_strength": k, "stages": stages,
    }
    return inputs, h, out


def _engine_inputs(inp: dict[str, Any], h: History, open_months: list[float] | None = None, s: dict[str, Any] | None = None) -> engine.Inputs:
    out_lam = inp["leads_per_month"]["used"] * inp["reply_rate"]["used"] / 100 * inp["positive_share"]["used"] / 100 * inp["deal_from_positive"]["used"] / 100
    return engine.Inputs(conv=[min(1.0, max(0.0, st["used"]["conv"] / 100)) for st in inp["stages"]], days=[max(1.0, st["used"]["days"]) for st in inp["stages"]],
                         open_stage=[o["stage_index"] for o in h.open_deals], open_value=[o["value"] for o in h.open_deals],
                         open_age=[o["days_in_stage"] for o in h.open_deals],
                         streams=[(out_lam, inp["outbound_entry"]["index"]), (inp["inbound_per_month"]["used"], inp["inbound_entry"]["index"])],
                         deal_value=inp["deal_value"]["used"], lag_days=float((s or DEFAULTS)["start_lag_days"] or 0),
                         open_months=open_months, new_months=float((s or DEFAULTS)["contract_months"] or 12))


def _variants(base: engine.Inputs, inp: dict[str, Any]) -> list[tuple[str, str, Any]]:
    """(key, label, function(Inputs, factor) -> Inputs) for each input the year is tested against."""
    def stream(i: int):
        return lambda e, f: _with(e, streams=[(lam * (f if j == i else 1.0), ent) for j, (lam, ent) in enumerate(e.streams)])
    v: list[tuple[str, str, Any]] = [
        ("leads_per_month", "Leads contacted per month", stream(0)), ("reply_rate", "Reply rate", stream(0)),
        ("positive_share", "Positive share of replies", stream(0)), ("deal_from_positive", "Positive reply becomes a deal", stream(0)),
        ("inbound_per_month", "Inbound deals per month", stream(1)), ("deal_value", "Average deal value",
                                                                       lambda e, f: _with(e, deal_value=e.deal_value * f, open_value=[x * f for x in e.open_value])),
        ("days", "Time spent in each stage", lambda e, f: _with(e, days=[d * f for d in e.days])),
    ]
    names = [st["name"] for st in inp["stages"]]
    for i, n in enumerate(names):
        label = f"{n} to {names[i + 1]}" if i + 1 < len(names) else f"{n} to won"
        v.append((f"conv:{inp['stages'][i]['id']}", label, (lambda j: lambda e, f: _with(e, conv=[min(1.0, c * f) if x == j else c for x, c in enumerate(e.conv)]))(i)))
    return v


def _with(e: engine.Inputs, **kw: Any) -> engine.Inputs:
    d = dict(e.__dict__)
    d.update(kw)
    return engine.Inputs(**d)


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def _fingerprint(conn: Conn, s: dict[str, Any], today: date) -> str:
    r = q1(conn, "SELECT (SELECT max(id) FROM deal_stage_transitions) a, (SELECT max(synced_at) FROM crm_deals) b, (SELECT max(received_at) FROM lemlist_activities) c, "
                 "(SELECT max(synced_at) FROM crm_stages) d") or {}
    return hashlib.sha256(json.dumps([s, today.isoformat(), [str(r.get(x)) for x in "abcd"]], sort_keys=True, default=str).encode()).hexdigest()


def build(conn: Conn, app_settings: Any, *, now: datetime | None = None, use_cache: bool = True) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    s = load_settings(conn)
    key = _fingerprint(conn, s, now.date())
    with _LOCK:
        hit = _CACHE.get(key)
        if use_cache and hit and time.time() - hit[0] < 600:
            return hit[1]
    inp, h, out = _resolve(conn, s, app_settings, now)
    cal = engine.Calendar(now.date(), inp["year"])
    base = _engine_inputs(inp, h, _open_months(conn, h, float(s["contract_months"] or 12)), s)
    variants = _variants(base, inp)
    max_lam = max([lam for lam, _ in base.streams] + [0.0]) * (1 + SENS_STEP) * 1.05
    dr = engine.Draws(RUNS, len(base.open_stage), len(base.conv), len(base.streams), len(cal.months), max_lam)
    res = engine.simulate(base, cal, dr)
    chance = engine.chance_at_least(res, inp["target"])
    res.pop("_year_samples", None)
    open_by_deal, win_by_deal = res.pop("_open_by_deal", []), res.pop("_win_by_deal", [])
    by_source = sources_view(conn, h, open_by_deal, res.get("stream_year") or [])
    sens = []
    if res["mean"] > 0:
        for key_, label, fn in variants:
            lo = engine.simulate(fn(base, 1 - SENS_STEP), cal, dr, detail=False)["mean"] / res["mean"] - 1
            hi = engine.simulate(fn(base, 1 + SENS_STEP), cal, dr, detail=False)["mean"] / res["mean"] - 1
            sens.append({"key": key_, "label": label, "lower": lo, "higher": hi})
        sens.sort(key=lambda x: -max(abs(x["lower"]), abs(x["higher"])))
    p_win = []
    acc = 1.0
    for st in reversed(inp["stages"]):
        acc *= st["used"]["conv"] / 100
        p_win.append(acc)
    p_win.reverse()
    for st, p in zip(inp["stages"], p_win, strict=True):
        st["win_chance"] = p
        st["open"]["weighted"] = st["open"]["value"] * p
    open_value = sum(st["open"]["value"] for st in inp["stages"])
    weighted = sum(st["open"]["weighted"] for st in inp["stages"])
    lam_out = base.streams[0][0]
    funnel_counts = []
    for i, st in enumerate(inp["stages"]):
        prev = funnel_counts[-1]["entering"] * inp["stages"][i - 1]["used"]["conv"] / 100 if i else 0.0
        fresh = 12 * ((lam_out if inp["outbound_entry"]["index"] == i else 0) + (base.streams[1][0] if inp["inbound_entry"]["index"] == i else 0))
        funnel_counts.append({"stage_id": st["id"], "name": st["name"], "entering": prev + fresh, "new_here": fresh})
    won_per_year = funnel_counts[-1]["entering"] * inp["stages"][-1]["used"]["conv"] / 100 if funnel_counts else 0
    payload = {
        "as_of": now.isoformat(), "year": inp["year"], "currency": inp["currency"], "target": inp["target"], "runs": RUNS,
        "stages_source": h.funnel.source, "inputs": inp, "result": {**res, "chance_of_target": chance},
        "buckets": [{"label": f"Before {inp['year']}"}] + [{"label": f"Q{q} {inp['year']}"} for q in range(1, 5)],
        "sensitivity": sens, "sensitivity_step": SENS_STEP,
        "pipeline": {"open_value": open_value, "weighted": weighted, "deals": len(h.open_deals), "parked": h.parked,
                     "without_value": sum(1 for o in h.open_deals if o.get("estimated")),
                     "coverage": (open_value / inp["target"]) if inp["target"] else None, "weighted_share_of_target": (weighted / inp["target"]) if inp["target"] else None,
                     "aging": [{"crm_id": o["crm_id"], "title": o["title"], "stage": inp["stages"][o["stage_index"]]["name"], "days": round(o["days_in_stage"]),
                                "value": o["value"]} for o in sorted(h.open_deals, key=lambda o: -o["days_in_stage"] / max(1.0, inp["stages"][o["stage_index"]]["used"]["days"]))
                               if o["days_in_stage"] > 1.5 * inp["stages"][o["stage_index"]]["used"]["days"]][:15]},
        "flow": {"outbound_deals_per_month": lam_out, "inbound_deals_per_month": base.streams[1][0], "funnel": funnel_counts, "won_per_year": won_per_year,
                 "cycle_days": sum(st["used"]["days"] for st in inp["stages"][inp["inbound_entry"]["index"]:])},
        "won_so_far": h.won_in_year, "outbound": out, "by_source": by_source,
        "health": {**h.health, "first_event_at": h.health["first_event_at"].isoformat() if h.health["first_event_at"] else None},
        "sources": data_sources(conn, app_settings),
        "settings": {k: v for k, v in s.items()}, "log": forecast_log(conn, inp["year"]),
    }
    names = [st["name"] for st in inp["stages"]]
    for key_, fn in (("revenue", lambda: revenue.build(conn, s, now=now, year=inp["year"], won_stage_ids=h.funnel.won_ids, funnel_pipeline_id=inp["pipeline_id"],
                                                         convert=make_convert(inp["currency"], inp["fx_to_eur"]), sim=res, open_deals=h.open_deals)),
                     ("categories", lambda: quality.categories(conn, h.open_deals, names, win_by_deal, open_by_deal)),
                     ("quality", lambda: quality.quality(conn, h.open_deals, names, [st["used"]["days"] for st in inp["stages"]], inp["pipeline_id"], now))):
        try:      # each view stands alone: one failing never takes the forecast down
            payload[key_] = fn()
        except Exception as exc:  # noqa: BLE001
            payload[key_] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    payload["result"].pop("invoiced", None)
    try:      # the funnel outlook never takes the revenue forecast down with it
        recent = measure(conn, h.funnel, now=now, lookback_days=365, year=inp["year"])
        payload["outlook"] = outlook.build(conn, inp, h.open_deals, base.streams, sorted(h.funnel.won_ids), recent, now)
    except Exception as exc:  # noqa: BLE001
        payload["outlook"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    with _LOCK:
        _CACHE.clear()
        _CACHE[key] = (time.time(), payload)
    return payload


# When a deal has no Lead source, Pipedrive's own "Source origin" can still say where it came from.
ORIGIN_SOURCE = {"WebForms": "Marketing", "Chatbot": "Marketing", "LiveChat": "Marketing", "WebVisitors": "Marketing", "Campaigns": "Marketing",
                 "Marketplace": "Marketing", "MessagingInbox": "Marketing", "Prospector": "Sales outbound", "LeadSuggestion": "Sales outbound"}
NOT_SET = "Not set"


def sources_view(conn: Conn, h: History, open_by_deal: list[float], stream_year: list[float]) -> dict[str, Any]:
    """Per source: what is open now and what it should bring this year, and how deals from it went in the last two years."""
    import os
    import statistics
    key = os.environ.get("RI_PIPEDRIVE_SOURCE_FIELD")
    labels = {r["option_id"]: r["label"] for r in qa(conn, "SELECT option_id, label FROM crm_field_options WHERE field_key=%s", (key,))} if key else {}

    def src(d: dict[str, Any]) -> tuple[str, bool]:
        raw = d.get("lead_source_raw")
        if raw:
            return labels.get(str(raw), f"Option {raw}"), False
        if d.get("origin") in ORIGIN_SOURCE:
            return ORIGIN_SOURCE[d["origin"]], True
        return NOT_SET, False

    rows: dict[str, dict[str, Any]] = {}

    def row(name: str) -> dict[str, Any]:
        return rows.setdefault(name, {"source": name, "open": 0, "open_value": 0.0, "expected": 0.0, "deals": 0, "won": 0, "lost": 0, "inferred": 0,
                                      "_won_values": [], "_days": []})
    for o, contrib in zip(h.open_deals, open_by_deal, strict=False):
        r = row(src(o)[0])
        r["open"] += 1
        r["open_value"] += o["value"] or 0
        r["expected"] += contrib
    tagged = 0
    for d in h.deal_rows:
        name, inferred = src(d)
        tagged += 1 if d.get("lead_source_raw") else 0
        r = row(name)
        r["deals"] += 1
        r["inferred"] += int(inferred)
        if d["status"] == "won":
            r["won"] += 1
            if d["value"]:
                r["_won_values"].append(d["value"])
            if d["won_time"] and d["add_time"]:
                r["_days"].append((d["won_time"] - d["add_time"]).total_seconds() / 86400)
        elif d["status"] == "lost":
            r["lost"] += 1
    out = []
    for r in rows.values():
        wv, days = r.pop("_won_values"), r.pop("_days")
        decided = r["won"] + r["lost"]
        out.append({**r, "win_rate": (r["won"] / decided) if decided else None, "avg_won_value": (sum(wv) / len(wv)) if wv else None,
                    "median_days_to_win": statistics.median(days) if days else None})
    out.sort(key=lambda x: (-x["expected"], -x["deals"]))
    new = [{"stream": "outbound", "expected": stream_year[0] if stream_year else 0.0}, {"stream": "inbound", "expected": stream_year[1] if len(stream_year) > 1 else 0.0}]
    return {"field_configured": bool(key), "labels_known": bool(labels), "deals_in_window": len(h.deal_rows), "tagged": tagged,
            "tagged_share": (tagged / len(h.deal_rows)) if h.deal_rows else None, "rows": out, "new_deals": new}


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def data_sources(conn: Conn, app_settings: Any) -> dict[str, Any]:
    """Is the forecast fed? When Pipedrive and lemlist last delivered, how far the history backfill is, whether webhooks arrive."""
    r = q1(conn, """SELECT (SELECT max(synced_at) FROM crm_deals) deals_synced, (SELECT count(*) FROM crm_deals) deals,
                           (SELECT count(*) FROM crm_deals WHERE history_backfilled_at IS NOT NULL) backfilled,
                           (SELECT count(*) FROM deal_stage_transitions) transitions,
                           (SELECT max(received_at) FROM crm_webhook_inbox) hook_last,
                           (SELECT count(*) FROM crm_webhook_inbox WHERE received_at > now() - interval '7 days') hook_week,
                           (SELECT count(*) FROM crm_webhook_inbox WHERE status='quarantined' AND received_at > now() - interval '7 days') hook_bad,
                           (SELECT count(*) FROM crm_webhook_inbox WHERE status='received') hook_waiting,
                           (SELECT max(received_at) FROM lemlist_activities) lemlist_last,
                           (SELECT max(made_at) FROM forecast_log) logged_last""") or {}
    return {"pipedrive": {"configured": bool(getattr(app_settings, "pipedrive_api_token", None) and getattr(app_settings, "pipedrive_domain", None)),
                          "last_sync": _iso(r.get("deals_synced")), "deals": int(r.get("deals") or 0)},
            "history": {"deals": int(r.get("deals") or 0), "read": int(r.get("backfilled") or 0), "changes": int(r.get("transitions") or 0)},
            "webhook": {"last_event": _iso(r.get("hook_last")), "last_7_days": int(r.get("hook_week") or 0), "failed_7_days": int(r.get("hook_bad") or 0),
                        "waiting": int(r.get("hook_waiting") or 0)},
            "lemlist": {"configured": bool(getattr(app_settings, "lemlist_api_key", None)), "last_activity": _iso(r.get("lemlist_last"))},
            "last_saved_forecast": _iso(r.get("logged_last"))}


def forecast_log(conn: Conn, year: int, limit: int = 60) -> list[dict[str, Any]]:
    return [{"made_on": r["made_on"].isoformat(), "trigger": r["trigger"], "p10": r["p10"], "p50": r["p50"], "p90": r["p90"]} for r in qa(conn, """
        SELECT made_on, trigger, (result->'year'->>'p10')::float p10, (result->'year'->>'p50')::float p50, (result->'year'->>'p90')::float p90
        FROM forecast_log WHERE year=%s ORDER BY made_at DESC LIMIT %s""", (year, limit))][::-1]


def log_forecast(conn: Conn, payload: dict[str, Any], trigger: str, made_on: date | None = None) -> bool:
    row = conn.execute("INSERT INTO forecast_log(made_on, year, trigger, inputs, result) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                       (made_on or date.fromisoformat(payload["as_of"][:10]), payload["year"], trigger, jb({k: payload["inputs"][k] for k in ("leads_per_month", "reply_rate", "positive_share", "deal_from_positive",
                                                                                         "inbound_per_month", "deal_value", "stages")}),
                        jb({"year": payload["result"]["year"], "buckets": payload["result"]["buckets"], "chance_of_target": payload["result"]["chance_of_target"],
                            "target": payload["target"], "pipeline": {k: payload["pipeline"][k] for k in ("open_value", "weighted", "deals")}}))).fetchone()
    return bool(row)


def snapshot_pipeline(conn: Conn, day: date) -> int:
    return conn.execute("INSERT INTO deal_snapshots_daily(day, crm_deal_id, pipeline_id, stage_id, status, value, currency, expected_close_date, owner_id) "
                        "SELECT %s, crm_id, pipeline_id, stage_id, status, value, currency, expected_close_date, owner_id FROM crm_deals WHERE status='open' "
                        "ON CONFLICT DO NOTHING", (day,)).rowcount or 0


def daily_step(conn: Conn, app_settings: Any, now: datetime | None = None) -> dict[str, Any]:
    """Once a day (the scheduler calls this every tick): copy the open pipeline and log the day's forecast."""
    now = now or datetime.now(timezone.utc)
    if q1(conn, "SELECT 1 FROM forecast_log WHERE made_on=%s AND trigger='daily' LIMIT 1", (now.date(),)):
        return {}
    n = snapshot_pipeline(conn, now.date())
    payload = build(conn, app_settings, now=now, use_cache=False)
    return {"deal_snapshots": n, "forecast_logged": log_forecast(conn, payload, "daily", now.date())}
