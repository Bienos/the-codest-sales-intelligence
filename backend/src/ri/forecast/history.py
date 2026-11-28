"""What the deal history says: per funnel stage, how many deals moved on, how many were lost there, how long they took, plus the open
pipeline as it stands. Read-only over crm_deals, crm_stages and deal_stage_transitions."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..db import Conn, qa

DAY = 86400.0


@dataclass
class Stage:
    id: int
    name: str
    order_nr: int | None = None


@dataclass
class Funnel:
    pipeline_id: int | None
    stages: list[Stage]                       # the steps of the funnel, in order
    won_ids: set[int] = field(default_factory=set)
    parking_ids: set[int] = field(default_factory=set)
    source: str = "pipedrive"                 # 'pipedrive' (synced stage list) or 'placeholder' (nothing synced yet)

    def index(self, stage_id: Any) -> int | None:
        for i, s in enumerate(self.stages):
            if s.id == stage_id:
                return i
        return None


PLACEHOLDER_STAGES = ["Webform MQL", "DISCOVERY", "SQL", "Proposal/Shortlist", "Commercials/NDA"]


def load_funnel(conn: Conn, pipeline_id: int | None, parking_names: list[str], won_names: list[str]) -> Funnel:
    park = {n.strip().lower() for n in parking_names if n.strip()}
    won = {n.strip().lower() for n in won_names if n.strip()}
    rows = qa(conn, "SELECT crm_id, name, order_nr FROM crm_stages WHERE NOT is_deleted AND (%s::bigint IS NULL OR pipeline_id=%s) ORDER BY order_nr NULLS LAST, crm_id",
              (pipeline_id, pipeline_id))
    if not rows:
        return Funnel(pipeline_id, [Stage(-(i + 1), n) for i, n in enumerate(PLACEHOLDER_STAGES)], source="placeholder")
    f = Funnel(pipeline_id, [])
    for r in rows:
        name = (r["name"] or "").strip()
        if name.lower() in park:
            f.parking_ids.add(r["crm_id"])
        elif name.lower() in won:
            f.won_ids.add(r["crm_id"])
        else:
            f.stages.append(Stage(r["crm_id"], name, r["order_nr"]))
    if not f.stages:
        return Funnel(pipeline_id, [Stage(-(i + 1), n) for i, n in enumerate(PLACEHOLDER_STAGES)], f.won_ids, f.parking_ids, source="placeholder")
    return f


def _deal_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """When Pipedrive's own history was read for a deal, it wins; rows logged by the mirror count only after the last backfilled change."""
    back = [r for r in rows if r["source"] == "backfill"]
    if not back:
        return sorted(rows, key=lambda r: (r["at"], r["id"]))
    last = max(r["at"] for r in back)
    return sorted(back + [r for r in rows if r["source"] != "backfill" and r["at"] > last], key=lambda r: (r["at"], r["id"]))


@dataclass
class StageStats:
    passed: int = 0          # deals that reached this stage and moved on (or won)
    lost: int = 0            # deals lost (or parked) while this was the furthest stage they reached
    still_open: int = 0      # deals sitting here now: not counted in the rate yet
    days: list[float] = field(default_factory=list)

    @property
    def resolved(self) -> int:
        return self.passed + self.lost

    def rate(self) -> float | None:
        return self.passed / self.resolved if self.resolved else None

    def median_days(self) -> float | None:
        return statistics.median(self.days) if self.days else None


@dataclass
class History:
    funnel: Funnel
    stats: list[StageStats]
    open_deals: list[dict[str, Any]]          # {crm_id, title, stage_index, value, currency, days_in_stage, owner_id}
    parked: dict[str, Any]
    entries_by_stage: list[int]               # deals created at each stage within the creation window
    entry_window_days: int
    won_values: list[float]
    won_in_year: dict[str, Any]
    health: dict[str, Any]
    deal_rows: list[dict[str, Any]] = field(default_factory=list)      # every deal in the pipeline: status, dates, value, source


def measure(conn: Conn, funnel: Funnel, *, now: datetime | None = None, lookback_days: int = 730, entry_window_days: int = 180, year: int | None = None,
            convert: Callable[[Any, Any], float | None] | None = None) -> History:
    """`convert(value, currency)` turns a deal value into the forecast's currency (None when the currency has no rate)."""
    now = now or datetime.now(timezone.utc)
    unconverted: set[str] = set()

    def money(d: dict[str, Any]) -> float:
        v = float(d["value"] or 0)
        if not v or convert is None:
            return v
        out = convert(v, d["currency"])
        if out is None:
            unconverted.add(str(d["currency"]))
            return v
        return out
    S = len(funnel.stages)
    stats = [StageStats() for _ in range(S)]
    known = [s.id for s in funnel.stages] + list(funnel.won_ids) + list(funnel.parking_ids)
    deals = qa(conn, "SELECT crm_id, title, stage_id, status, value, currency, owner_id, add_time, stage_change_time, won_time, lost_time, history_backfilled_at, pipeline_id, "
                     "lead_source_raw, origin "
                     "FROM crm_deals WHERE (%s::bigint IS NOT NULL AND pipeline_id=%s) OR (stage_id = ANY(%s))", (funnel.pipeline_id, funnel.pipeline_id, known))
    ids = [d["crm_id"] for d in deals]
    by_deal: dict[int, list[dict[str, Any]]] = {}
    for r in qa(conn, "SELECT id, crm_deal_id, to_stage_id, to_status, at, source FROM deal_stage_transitions WHERE crm_deal_id = ANY(%s)", (ids,)):
        by_deal.setdefault(r["crm_deal_id"], []).append(r)
    since = now - timedelta(days=lookback_days)
    entry_since = now - timedelta(days=entry_window_days)
    entries = [0] * S
    open_deals: list[dict[str, Any]] = []
    parked = {"n": 0, "value": 0.0}
    won_values: list[float] = []
    yr = year or now.year
    won_year = {"n": 0, "value": 0.0}
    sources: dict[str, int] = {}
    with_history = 0
    for d in deals:
        ev = _deal_events(by_deal.get(d["crm_id"], []))
        for e in ev:
            sources[e["source"]] = sources.get(e["source"], 0) + 1
        if d["status"] == "won" and money(d) > 0 and (d["won_time"] is None or d["won_time"] >= since):      # a won deal with no value says nothing about size
            won_values.append(money(d))
        if d["status"] == "won" and d["won_time"] and d["won_time"].year == yr:
            won_year["n"] += 1
            won_year["value"] += money(d)
        cur_idx = funnel.index(d["stage_id"])
        if d["status"] == "open":
            if d["stage_id"] in funnel.parking_ids:
                parked["n"] += 1
                parked["value"] += money(d)
            elif cur_idx is not None:
                entered = next((e["at"] for e in reversed(ev) if e["to_stage_id"] == d["stage_id"]), None) or d["stage_change_time"] or d["add_time"]
                open_deals.append({"crm_id": d["crm_id"], "title": d["title"], "stage_index": cur_idx, "value": money(d) or None, "currency": d["currency"],
                                   "lead_source_raw": d["lead_source_raw"], "origin": d["origin"],
                                   "days_in_stage": max(0.0, (now - entered).total_seconds() / DAY) if entered else 0.0, "owner_id": d["owner_id"]})
        if not ev:
            continue
        with_history += 1
        first_at: dict[int, datetime] = {}
        won_at = lost_at = None
        parked_now = False
        for e in ev:
            i = funnel.index(e["to_stage_id"])
            if i is not None and i not in first_at:
                first_at[i] = e["at"]
            if (e["to_stage_id"] in funnel.won_ids or e["to_status"] == "won") and won_at is None:
                won_at = e["at"]
            if e["to_status"] == "lost":
                lost_at = e["at"]
            elif e["to_status"] == "open" and lost_at is not None:
                lost_at = None                       # re-opened
        if d["status"] == "open" and d["stage_id"] in funnel.parking_ids:
            parked_now = True
        if not first_at:
            continue
        entry = min(first_at)
        start_at = first_at[entry]
        if start_at < since:
            continue
        if ev[0]["source"] in ("created", "backfill", "webhook", "sync") and start_at >= entry_since and ev[0]["to_stage_id"] == funnel.stages[entry].id:
            entries[entry] += 1
        top = max(first_at)
        reached = sorted(first_at)
        for a, b in zip(reached, reached[1:], strict=False):                    # time from entering a stage to entering the next one reached
            if first_at[b] >= first_at[a]:
                stats[a].days.append((first_at[b] - first_at[a]).total_seconds() / DAY)
        if won_at is not None and won_at >= first_at[top]:
            stats[top].days.append((won_at - first_at[top]).total_seconds() / DAY)
        for i in range(entry, top):
            stats[i].passed += 1
        if won_at is not None:
            stats[top].passed += 1
        elif lost_at is not None or parked_now:
            stats[top].lost += 1
        else:
            stats[top].still_open += 1
    currencies = sorted({o["currency"] for o in open_deals if o["currency"]})
    total_backfilled = sum(1 for d in deals if d["history_backfilled_at"])
    warnings = []
    if funnel.source == "placeholder":
        warnings.append("No Pipedrive stages synced yet: the funnel uses placeholder stage names and every rate is a prior.")
    if deals and total_backfilled < len(deals):
        warnings.append(f"{len(deals) - total_backfilled} of {len(deals)} deals have no Pipedrive history read yet: run backfill-deal-history.")
    if unconverted:
        warnings.append(f"No exchange rate for {', '.join(sorted(unconverted))}: those deal values are counted as they are. Add a rate under Assumptions.")
    no_value = sum(1 for o in open_deals if not o["value"])
    if no_value:
        warnings.append(f"{no_value} of {len(open_deals)} open deals have no value in Pipedrive; the forecast gives each the average deal value.")
    health = {"deals": len(deals), "deals_with_history": with_history, "deals_backfilled": total_backfilled, "events_by_source": sources,
              "first_event_at": min((e["at"] for rows in by_deal.values() for e in rows), default=None), "open_currencies": currencies, "warnings": warnings}
    rows = [{"status": d["status"], "add_time": d["add_time"], "won_time": d["won_time"], "lost_time": d["lost_time"], "value": money(d),
             "lead_source_raw": d["lead_source_raw"], "origin": d["origin"]} for d in deals if d["add_time"] is None or d["add_time"] >= since]
    return History(funnel, stats, open_deals, parked, entries, entry_window_days, won_values, won_year, health, rows)
