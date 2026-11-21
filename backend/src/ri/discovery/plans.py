from __future__ import annotations

from pathlib import Path

import yaml

from ..db import Conn
from ..sources.exa import SearchPlan

PATH = Path(__file__).resolve().parents[3] / "rules" / "discovery_plans.yaml"


def build_plans(path: Path = PATH) -> list[SearchPlan]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for t in cfg["themes"]:
        for g, geo in cfg["geographies"].items():
            out.append(SearchPlan(t["rule"], t["id"], g, "en", "news_and_press", int(t["recency_days"]), t["query"].format(geo=geo)))
    return out


def sync_plans(conn: Conn, path: Path = PATH) -> int:
    n = 0
    for p in build_plans(path):
        row = conn.execute(
            "INSERT INTO discovery_plans(plan_id, signal_rule_id, theme, geography, language, recency_days, query) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (plan_id) DO NOTHING RETURNING plan_id", (p.plan_id, p.signal_rule_id, p.sector, p.geography, p.language, p.recency_days, p.query)).fetchone()
        n += bool(row)
    return n
