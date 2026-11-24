from __future__ import annotations

from itertools import zip_longest
from pathlib import Path

import yaml

from ..db import Conn, qa
from ..sources.exa import SearchPlan
from ..sources.util import sha
from ..target_market import target_region, target_sql

FINANCE_PILOT_LIMIT = 25

PATH = Path(__file__).resolve().parents[3] / "rules" / "discovery_plans.yaml"


def build_plans(path: Path = PATH) -> list[SearchPlan]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    langs = cfg.get("local_languages") or {}
    for t in cfg["themes"]:
        for g, geo in (cfg["geographies"] | (t.get("extra_geographies") or {})).items():
            out.append(SearchPlan(t["rule"], t["id"], g, "en", "news_and_press", int(t["recency_days"]), t["query"].format(geo=geo)))
        for g, q in (t.get("local") or {}).items():            # the same theme in the market's own language
            if g in cfg["geographies"]:
                out.append(SearchPlan(t["rule"], t["id"], g, langs.get(g, "local"), "news_and_press", int(t["recency_days"]), q))
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


def priority_accounts(conn: Conn, limit: int = 100, *, finance_only: bool = False) -> list[dict]:
    """Your shortlist first (by its own rank), then P1 and P2 accounts by signal score."""
    from ..market import sector_sql
    finance = f" AND o.entity_type <> 'public_body' AND ({sector_sql('o.segment')})='finance'" if finance_only else ""
    short = qa(conn, "SELECT DISTINCT ON (r.organization_id) r.organization_id, o.display_name, o.country_code, o.segment, r.original FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
                     f"WHERE r.kind='priority_outreach'{finance} ORDER BY r.organization_id, r.observed_at DESC")
    short.sort(key=lambda r: _score((r["original"] or {}).get("Rank")) or 999)
    master = qa(conn, "SELECT DISTINCT ON (r.organization_id) r.organization_id, o.display_name, o.country_code, o.segment, r.original FROM legacy_account_research r JOIN organizations o ON o.id=r.organization_id "
                      f"WHERE r.kind='master_account' AND coalesce(r.original->>'Signal Priority','') IN ('P1','P2'){finance} ORDER BY r.organization_id, r.observed_at DESC")
    master.sort(key=lambda r: -_score((r["original"] or {}).get("Buying Signal Score")))
    chosen, seen = [], set()
    for r in [*short, *master]:
        if r["organization_id"] not in seen and r["display_name"]:
            seen.add(r["organization_id"])
            chosen.append(r)
        if len(chosen) >= limit:
            break
    return chosen


def finance_pilot_accounts(conn: Conn) -> list[dict]:
    """Prioritize confirmed companies, then size-qualified research candidates, across regions."""
    rows = qa(conn, f"""
        SELECT o.id AS organization_id, o.display_name, o.country_code, o.segment,
               {target_sql()} AS confirmed
        FROM organizations o
        LEFT JOIN account_scores sc ON sc.organization_id=o.id
        WHERE ({target_sql()} OR {target_sql(review=True)})
          AND o.canonical_domain IS NOT NULL AND NOT coalesce(sc.stopped,false)
        ORDER BY confirmed DESC, o.in_master_list DESC, sc.score DESC NULLS LAST, o.id
        """)
    selected = []
    for confirmed in (True, False):
        by_region: dict[str, list[dict]] = {}
        for row in rows:
            if row["confirmed"] == confirmed:
                by_region.setdefault(target_region(row["country_code"]), []).append(row)
        for turn in zip_longest(*by_region.values()):
            selected.extend(row for row in turn if row)
    return selected[:FINANCE_PILOT_LIMIT]


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
