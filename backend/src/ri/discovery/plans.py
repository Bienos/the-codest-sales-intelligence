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


def priority_accounts(conn: Conn, limit: int = 100) -> list[dict]:
    """Your shortlist first (by its own rank), then P1 and P2 accounts by signal score."""
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
    return chosen


def ensure_account_plan(conn: Conn, org_id: str, name: str, country: str | None) -> str:
    """The search for one account (created on demand; it only runs when somebody presses the button)."""
    pid = sha("account-watch", str(org_id))[:16]
    q = f'"{name}" announces technology programme, platform or vendor selection, engineering hiring, or new technology leadership'
    conn.execute("INSERT INTO discovery_plans(plan_id, signal_rule_id, theme, geography, language, recency_days, query, organization_id) VALUES (%s,'ACCOUNT','account_watch',%s,'en',45,%s,%s) "
                 "ON CONFLICT (plan_id) DO NOTHING", (pid, country or "n/a", q, org_id))
    return pid


def sync_account_plans(conn: Conn, limit: int = 100) -> int:
    """Creates the per-account search plans for the priority accounts. They run on demand only, never on a timer."""
    n = 0
    for r in priority_accounts(conn, limit):
        before = conn.execute("SELECT 1 FROM discovery_plans WHERE plan_id=%s", (sha("account-watch", str(r["organization_id"]))[:16],)).fetchone()
        ensure_account_plan(conn, r["organization_id"], r["display_name"], r["country_code"])
        n += 0 if before else 1
    return n
