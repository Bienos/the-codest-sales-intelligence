from __future__ import annotations

from pathlib import Path

import yaml

from ..db import Conn
from ..sources.exa import SearchPlan
from ..sources.util import sha

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


def _score(v) -> float:
    import re
    m = re.search(r"-?\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else 0.0


def sync_account_plans(conn: Conn, limit: int = 100) -> int:
    """The lead monitor: one weekly search per priority account. Your shortlist first (by its own rank), then P1 and P2 accounts by signal score.
    The search looks for the account's own announcements, programmes, vendor choices, hiring and leadership changes; hits still only become verification requests."""
    from ..db import qa
    short = qa(conn, "SELECT DISTINCT ON (r.organization_id) r.organization_id, o.display_name, o.country_code, r.original FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
                     "WHERE r.kind='priority_outreach' ORDER BY r.organization_id, r.observed_at DESC")
    short.sort(key=lambda r: _score((r["original"] or {}).get("Rank")) or 999)
    master = qa(conn, "SELECT DISTINCT ON (r.organization_id) r.organization_id, o.display_name, o.country_code, r.original FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
                      "WHERE r.kind='master_account' AND coalesce(r.original->>'Signal Priority','') IN ('P1','P2') ORDER BY r.organization_id, r.observed_at DESC")
    master.sort(key=lambda r: -_score((r["original"] or {}).get("Buying Signal Score")))
    chosen, seen = [], set()
    for r in [*short, *master]:
        if r["organization_id"] not in seen and r["display_name"]:
            seen.add(r["organization_id"])
            chosen.append(r)
        if len(chosen) >= limit:
            break
    n = 0
    for r in chosen:
        pid = sha("account-watch", str(r["organization_id"]))[:16]
        q = f'"{r["display_name"]}" announces technology programme, platform or vendor selection, engineering hiring, or new technology leadership'
        row = conn.execute(
            "INSERT INTO discovery_plans(plan_id, signal_rule_id, theme, geography, language, recency_days, query, organization_id) VALUES (%s,'ACCOUNT','account_watch',%s,'en',45,%s,%s) "
            "ON CONFLICT (plan_id) DO NOTHING RETURNING plan_id", (pid, r["country_code"] or "n/a", q, r["organization_id"])).fetchone()
        n += bool(row)
    return n
