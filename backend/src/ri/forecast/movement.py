"""Two views for the weekly sales meeting, both read from what is already mirrored (no simulation):

Pipeline movement between two dates (a waterfall). The open funnel at the start, plus deals added or revived, minus deals won, lost or
parked, plus or minus value changes, gives the open funnel at the end. Each deal's state at a moment is its last stage change at or before
it (deal_stage_transitions: Pipedrive's own history where it was read, then the mirror's), so any past period can be replayed, not only
the days since snapshots began. Value at a past moment: the daily snapshot of that day when there is one, else the value recorded with the
last stage change; at the end of a period that is today, the value in Pipedrive now. Close dates moved later or earlier need a snapshot at
the start, so they appear once deal_snapshots_daily covers the period.

Leading indicators by week: what shows up weeks before revenue does. People contacted, replies and interest (Lemlist), meetings and calls
marked done (Pipedrive activities), new deals, deals reaching SQL and Proposal for the first time, and wins. Managers can set a weekly goal
for each (forecast settings, ``weekly_goals``)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..db import Conn, qa
from .history import Funnel, _deal_events

INDICATORS = [("contacted", "People contacted"), ("replied", "Replies"), ("interested", "Interested replies"), ("meetings", "Meetings held"),
              ("calls", "Calls logged"), ("new_deals", "New deals"), ("sql", "Deals reaching SQL"), ("proposal", "Deals reaching Proposal"),
              ("won", "Deals won")]
WEEKS = 12


def _stage_at(f: Funnel, needle: str) -> int | None:
    for i, s in enumerate(f.stages):
        if needle in s.name.lower():
            return i
    return None


def _week_start(t: datetime) -> datetime:
    d = t.astimezone(timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(days=d.weekday())


# ------------------------------------------------------------------ leading indicators
def indicators(conn: Conn, f: Funnel, goals: dict[str, Any], now: datetime | None = None, weeks: int = WEEKS) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    this_week = _week_start(now)
    start = this_week - timedelta(weeks=weeks - 1)
    starts = [start + timedelta(weeks=i) for i in range(weeks)]
    series: dict[str, list[int]] = {k: [0] * weeks for k, _ in INDICATORS}

    def put(key: str, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            i = (r["w"] - start).days // 7
            if 0 <= i < weeks:
                series[key][i] += int(r["n"])

    wk = "date_trunc('week', {} AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
    put("contacted", qa(conn, f"SELECT {wk.format('t')} w, count(*) n FROM (SELECT lower(email) e, min(happened_at) t FROM lemlist_activities "
                              "WHERE type='emailsSent' GROUP BY 1) x WHERE t >= %s GROUP BY 1", (start,)))
    for key, types in (("replied", ["emailsReplied", "linkedinReplied"]), ("interested", ["emailsInterested", "interested"])):
        put(key, qa(conn, f"SELECT {wk.format('t')} w, count(*) n FROM (SELECT lower(email) e, min(happened_at) t FROM lemlist_activities "
                          "WHERE type = ANY(%s) GROUP BY 1) x WHERE t >= %s GROUP BY 1", (types, start)))
    put("meetings", qa(conn, f"SELECT {wk.format('done_at')} w, count(*) n FROM crm_activities WHERE done AND type='meeting' AND done_at >= %s GROUP BY 1", (start,)))
    put("meetings", qa(conn, f"SELECT {wk.format('occurred_at')} w, count(*) n FROM contact_interactions WHERE channel='meeting' AND occurred_at >= %s GROUP BY 1", (start,)))
    put("calls", qa(conn, f"SELECT {wk.format('done_at')} w, count(*) n FROM crm_activities WHERE done AND type='call' AND done_at >= %s GROUP BY 1", (start,)))
    put("new_deals", qa(conn, f"SELECT {wk.format('add_time')} w, count(*) n FROM crm_deals WHERE add_time >= %s AND (%s::bigint IS NULL OR pipeline_id=%s) GROUP BY 1",
                        (start, f.pipeline_id, f.pipeline_id)))
    for key, needle in (("sql", "sql"), ("proposal", "proposal")):
        i = _stage_at(f, needle)
        if i is None:
            continue
        # first arrival, with Pipedrive's own history taking precedence over the mirror's rows where it was read (as in history._deal_events)
        put(key, qa(conn, f"SELECT {wk.format('t')} w, count(*) n FROM (SELECT crm_deal_id, min(at) t FROM deal_stage_transitions x WHERE to_stage_id=%s "
                          "AND (source='backfill' OR NOT EXISTS (SELECT 1 FROM deal_stage_transitions b WHERE b.crm_deal_id=x.crm_deal_id AND b.source='backfill' AND b.at >= x.at)) "
                          "GROUP BY 1) y WHERE t >= %s GROUP BY 1", (f.stages[i].id, start)))
    put("won", qa(conn, f"SELECT {wk.format('won_time')} w, count(*) n FROM crm_deals WHERE status='won' AND won_time >= %s "
                        "AND (%s::bigint IS NULL OR pipeline_id=%s) GROUP BY 1", (start, f.pipeline_id, f.pipeline_id)))

    elapsed = max(1, min(7, (now - this_week).days + 1))           # days of this week so far, today included
    rows = []
    for key, label in INDICATORS:
        s = series[key]
        prior4 = s[-5:-1]
        goal = goals.get(key)
        goal = float(goal) if goal not in (None, "") else None
        pace = s[-1] * 7 / elapsed
        rows.append({"key": key, "label": label, "weeks": s, "this_week": s[-1], "pace": round(pace, 1), "last_week": s[-2] if weeks > 1 else None,
                     "avg_4w": round(sum(prior4) / len(prior4), 1) if prior4 else None, "total": sum(s), "goal": goal,
                     "status": None if goal is None else ("on_pace" if pace >= goal else "behind")})
    meet_src = {"pipedrive": bool(qa(conn, "SELECT 1 FROM sync_state WHERE system='pipedrive' AND entity='activities' AND watermark IS NOT NULL")),
                "meetings_app": bool(qa(conn, "SELECT 1 FROM contact_interactions WHERE channel='meeting' LIMIT 1"))}
    return {"as_of": now.isoformat(), "week_starts": [w.date().isoformat() for w in starts], "days_into_week": elapsed, "rows": rows, "sources": meet_src,
            "stages": {"sql": f.stages[i].name if (i := _stage_at(f, "sql")) is not None else None,
                       "proposal": f.stages[j].name if (j := _stage_at(f, "proposal")) is not None else None}}


# ------------------------------------------------------------------ pipeline movement
def movement(conn: Conn, f: Funnel, convert: Callable[[Any, Any], float | None], start: datetime, end: datetime | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    end = min(end or now, now)
    live_end = end >= now - timedelta(minutes=5)
    funnel_ids = {s.id for s in f.stages}
    rows = qa(conn, "SELECT id, crm_deal_id, to_stage_id, to_status, at, source, value, currency FROM deal_stage_transitions "
                    "WHERE (%s::bigint IS NULL OR pipeline_id=%s OR to_stage_id = ANY(%s)) AND at <= %s",
              (f.pipeline_id, f.pipeline_id, list(funnel_ids | f.parking_ids | f.won_ids), end))
    by: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["crm_deal_id"], []).append(r)
    deals = {d["crm_id"]: d for d in qa(conn, "SELECT d.crm_id, d.title, d.value, d.currency, d.owner_id, d.expected_close_date, o.name AS org "
                                             "FROM crm_deals d LEFT JOIN crm_organizations o ON o.crm_id=d.crm_org_id WHERE d.crm_id = ANY(%s)", (list(by),))}
    snap_day = start.date()
    snaps = {r["crm_deal_id"]: r for r in qa(conn, "SELECT crm_deal_id, value, currency, expected_close_date FROM deal_snapshots_daily WHERE day=%s", (snap_day,))}
    have_snaps = bool(snaps)

    def money(v: Any, cur: Any) -> float:
        if not v:
            return 0.0
        out = convert(v, cur)
        return float(out if out is not None else v)

    def state(ev: list[dict[str, Any]], t: datetime) -> dict[str, Any] | None:
        last = None
        for e in ev:
            if e["at"] <= t:
                last = e
            else:
                break
        return last

    def where(s: dict[str, Any] | None) -> str:
        if s is None:
            return "absent"
        if s["to_status"] == "won" or s["to_stage_id"] in f.won_ids:
            return "won"
        if s["to_status"] in ("lost", "deleted"):
            return "lost"
        if s["to_stage_id"] in funnel_ids:
            return "open"
        if s["to_stage_id"] in f.parking_ids:
            return "parked"
        return "elsewhere"

    keys = ["added", "revived", "won", "lost", "parked"]
    agg = {k: {"n": 0, "value": 0.0, "deals": []} for k in ["start", "end", *keys, "value_up", "value_down"]}
    moves = {"forward": 0, "back": 0, "same": 0}
    close = {"pushed": 0, "pulled": 0, "same": 0, "measured": have_snaps}
    unvalued = 0

    def add(k: str, d: dict[str, Any], v: float, extra: dict[str, Any] | None = None) -> None:
        agg[k]["n"] += 1
        agg[k]["value"] += v
        agg[k]["deals"].append({"crm_id": d["crm_id"], "title": d["title"], "org": d.get("org"), "value": round(v, 2), **(extra or {})})

    for did, raw in by.items():
        d = deals.get(did)
        if not d:
            continue
        ev = _deal_events(raw)
        a, b = state(ev, start), state(ev, end)
        wa, wb = where(a), where(b)
        if wa != "open" and wb != "open" and not any(start < e["at"] <= end and where(e) == "open" for e in ev):
            continue                                            # never in the funnel during the period
        va = 0.0
        if wa == "open":
            sn = snaps.get(did)
            va = money(sn["value"], sn["currency"]) if sn else money(a["value"], a["currency"])
        vb = money(d["value"], d["currency"]) if live_end else (money(b["value"], b["currency"]) if b else 0.0)
        if wa == "open":
            add("start", d, va)
        if wb == "open":
            add("end", d, vb)
            if not vb:
                unvalued += 1
        if wa == "open" and wb == "open":
            ia, ib = f.index(a["to_stage_id"]), f.index(b["to_stage_id"])
            moves["forward" if ib > ia else "back" if ib < ia else "same"] += 1
            delta = vb - va
            if delta:
                add("value_up" if delta > 0 else "value_down", d, abs(delta))
            sn = snaps.get(did)
            if have_snaps and sn and sn["expected_close_date"] and d["expected_close_date"]:
                c0, c1 = sn["expected_close_date"], d["expected_close_date"]
                close["pushed" if c1 > c0 else "pulled" if c1 < c0 else "same"] += 1
            continue
        # entered the funnel during the period: brand new, or back from parking / another pipeline
        if wa != "open":
            first_in = next((e for e in ev if start < e["at"] <= end and where(e) == "open"), None)
            if first_in is not None:
                was_new = a is None or not any(e["at"] <= start for e in ev)
                v_in = money(first_in["value"], first_in["currency"]) or (vb if wb == "open" else 0.0)
                add("added" if was_new else "revived", d, v_in)
                if wb == "open":
                    if vb != v_in:
                        add("value_up" if vb > v_in else "value_down", d, abs(vb - v_in))
                    continue
                va = v_in
        # left the funnel during the period
        out = wb if wb in ("won", "lost", "parked") else "parked" if wb == "elsewhere" else None
        if out:
            add(out, d, va, {"stage_before": (f.stages[f.index(a["to_stage_id"])].name if wa == "open" and f.index(a["to_stage_id"]) is not None else None)})

    for k in agg:
        agg[k]["value"] = round(agg[k]["value"], 2)
        agg[k]["deals"] = sorted(agg[k]["deals"], key=lambda x: -(x["value"] or 0))[:25]
    check = agg["start"]["value"] + agg["added"]["value"] + agg["revived"]["value"] - agg["won"]["value"] - agg["lost"]["value"] - agg["parked"]["value"] \
        + agg["value_up"]["value"] - agg["value_down"]["value"]
    first_snap = qa(conn, "SELECT min(day) AS d FROM deal_snapshots_daily")
    return {"start": start.isoformat(), "end": end.isoformat(), "steps": agg, "stage_moves": moves, "close_dates": close,
            "end_without_value": unvalued, "balance_gap": round(agg["end"]["value"] - check, 2),
            "snapshots_since": first_snap[0]["d"].isoformat() if first_snap and first_snap[0]["d"] else None}
