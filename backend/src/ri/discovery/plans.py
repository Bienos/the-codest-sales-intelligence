from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path

import yaml

from ..db import Conn, q1, qa
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


# ---------------------------------------------------------------------------------------------------------------------------------
# Web search for target accounts that have no finding yet (round 7, area 2)
#
# Exa web search plus the page checks is the most productive channel (297 findings from 478 checked pages, 88 on target accounts), yet only 83
# of about 920 target accounts had any finding in 90 days. So every target account WITHOUT a finding in the last 90 days gets one saved web
# search per cycle, written for what The Codest sells to (engineering demand): a technology programme or platform migration, engineering
# hiring, choosing a technology partner or vendor, a product launch, or a regulatory technology deadline. Where the account's market has a
# language the plans already search in (German, Swedish, Polish), the account gets a second saved search in that language. Each cycle runs ONE
# of the account's searches: the one run longest ago (never-run first), so the two alternate. Accounts are taken never-searched first, then
# the one searched longest ago; an account that gets a finding drops out of the group by itself.
#
# Money (all figures per 30-day month; the reservation per search is 0.01 dollars, what Exa bills for this call is about 0.005 to 0.007):
#   25 searches a day x 30 = 750 searches = about $5.25 reported ($7.50 if every search were billed at the reservation; the cap in
#   Settings.discovery_coverage_searches_per_run is what bounds it, so even then it stays under $8). The 81 general saved searches add about
#   $2.50. Everything automatic stops at 60% of the monthly Exa cap ($24 of $40), so sellers' own buttons, company research and people
#   search always keep at least $8.
# Pace: about 840 accounts / 25 a day = about 34 days, inside the 4 to 6 weeks asked for; an account is not searched again before
#   Settings.discovery_coverage_cycle_days (35) have passed, so a smaller group costs less, never more.

COVERAGE_THEME = "account_coverage"
COVERAGE_RULE = "ACCOUNT"                # the same marker the on-demand account searches use (a search about one account, not one signal rule)
COVERAGE_WINDOW_DAYS = 90                # "no finding in the last 90 days"; also how far back an account's first search looks
COVERAGE_MIN_WINDOW_DAYS = 14

# the market languages the general plans already search in (rules/discovery_plans.yaml: DACH de, Nordics sv, Poland pl); Danish, Norwegian and
# Finnish accounts are searched in English only
_LOCAL_LANGUAGE = {"DE": "de", "AT": "de", "CH": "de", "LI": "de", "SE": "sv", "PL": "pl"}
_COVERAGE_QUERY = {
    "en": '"{name}" technology programme or platform migration, engineering hiring, choosing a technology partner or vendor, product launch, or a regulatory technology deadline',
    "de": '"{name}" Technologieprogramm oder Plattform-Migration, Softwareentwickler gesucht, Auswahl eines Technologiepartners oder Dienstleisters, Produkteinführung, regulatorische IT-Frist',
    "sv": '"{name}" teknikprogram eller plattformsmigrering, rekryterar utvecklare, väljer teknikpartner eller leverantör, produktlansering, regulatorisk teknikdeadline',
    "pl": '"{name}" program technologiczny lub migracja platformy, rekrutacja programistów, wybór partnera technologicznego lub dostawcy, nowy produkt, regulacyjny termin IT',
}


def _clean_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r'["\u201c\u201d\u201e]', " ", name or "")).strip()[:80]


def coverage_queries(name: str | None, country: str | None) -> list[tuple[str, str, str]]:
    """The account's small set of saved searches as (variant, language, query). The market-language search comes first: that is where the
    account's own press writes about it; English follows (and is the only one for markets without a language above)."""
    clean = _clean_name(name)
    if not clean:
        return []
    out = []
    local = _LOCAL_LANGUAGE.get((country or "").upper())
    if local:
        out.append(("local", local, _COVERAGE_QUERY[local].format(name=clean)))
    out.append(("en", "en", _COVERAGE_QUERY["en"].format(name=clean)))
    return out


def coverage_plan_id(org_id: str, variant: str) -> str:
    return sha("account-coverage", str(org_id), variant)[:16]


def ensure_coverage_plans(conn: Conn, org_id: str, name: str | None, country: str | None) -> list[dict]:
    """Creates, or refreshes when the name, country or wording changed, the account's saved searches. Never duplicates (the id is the account
    and the variant) and never switches back on one a person switched off. Returns the ones that are on, the one run longest ago first."""
    for variant, language, query in coverage_queries(name, country):
        conn.execute(
            "INSERT INTO discovery_plans(plan_id, signal_rule_id, theme, geography, language, recency_days, query, organization_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (plan_id) DO UPDATE SET query=EXCLUDED.query, language=EXCLUDED.language, geography=EXCLUDED.geography, recency_days=EXCLUDED.recency_days, organization_id=EXCLUDED.organization_id "
            "WHERE (discovery_plans.query, discovery_plans.language, discovery_plans.geography, discovery_plans.recency_days, discovery_plans.organization_id) "
            "IS DISTINCT FROM (EXCLUDED.query, EXCLUDED.language, EXCLUDED.geography, EXCLUDED.recency_days, EXCLUDED.organization_id)",
            (coverage_plan_id(org_id, variant), COVERAGE_RULE, COVERAGE_THEME, (country or "n/a").upper(), language, COVERAGE_WINDOW_DAYS, query, org_id))
    rows = qa(conn, "SELECT * FROM discovery_plans WHERE organization_id=%s AND theme=%s AND enabled", (org_id, COVERAGE_THEME))
    rows.sort(key=lambda r: (r["last_run_at"] is not None, r["last_run_at"] or datetime.min.replace(tzinfo=timezone.utc), r["language"] == "en"))
    return rows


def _coverage_sql(select: str, tail: str = "") -> str:
    """Target accounts with no finding in the window and not stopped by a person; with the account's last coverage search time as dp.last_search."""
    return f"""
        SELECT {select}
        FROM organizations o
        LEFT JOIN account_scores sc ON sc.organization_id=o.id
        LEFT JOIN LATERAL (SELECT max(p.last_run_at) AS last_search, count(*) AS plans, count(*) FILTER (WHERE p.enabled) AS enabled_plans
                           FROM discovery_plans p WHERE p.organization_id=o.id AND p.theme=%(theme)s) dp ON true
        WHERE {target_sql('o')} AND NOT coalesce(sc.stopped, false)
          AND NOT EXISTS (SELECT 1 FROM events e WHERE e.subject_org_id=o.id AND NOT e.is_historical_replay AND e.lifecycle_status='active'
                          AND e.event_type <> 'deadline_approaching'      -- a calendar deadline pinned to the account is not news about it
                          AND coalesce(e.event_at, e.pub_at, e.first_observed_at) > %(since)s)
          AND (dp.plans = 0 OR dp.enabled_plans > 0)
        {tail}"""


def _coverage_args(now: datetime, cycle_days: int, extra: dict | None = None) -> dict:
    return {"theme": COVERAGE_THEME, "since": now - timedelta(days=COVERAGE_WINDOW_DAYS), "cut": now - timedelta(days=cycle_days), **(extra or {})}


def coverage_candidates(conn: Conn, *, now: datetime | None = None, cycle_days: int = 35, limit: int = 25) -> list[dict]:
    """The accounts to search next: target accounts without a finding in the last 90 days that were not searched within the cycle. Never-searched
    first (the shortlist and the best scored first), then the one searched longest ago."""
    now = now or datetime.now(timezone.utc)
    return qa(conn, _coverage_sql(
        "o.id AS organization_id, o.display_name, o.country_code, dp.last_search",
        "AND (dp.last_search IS NULL OR dp.last_search < %(cut)s) "
        "ORDER BY dp.last_search NULLS FIRST, o.in_master_list DESC, sc.score DESC NULLS LAST, o.id LIMIT %(limit)s"),
        _coverage_args(now, cycle_days, {"limit": max(0, limit)}))


def coverage_waiting(conn: Conn, *, now: datetime | None = None, cycle_days: int = 35, at_least_one: bool = False) -> int:
    """How many of those accounts are still waiting for their search in this cycle (with at_least_one: 1 as soon as one is, which is cheaper)."""
    now = now or datetime.now(timezone.utc)
    if at_least_one:
        return len(qa(conn, _coverage_sql("1", "AND (dp.last_search IS NULL OR dp.last_search < %(cut)s) LIMIT 1"), _coverage_args(now, cycle_days)))
    r = q1(conn, _coverage_sql("count(*) AS n", "AND (dp.last_search IS NULL OR dp.last_search < %(cut)s)"), _coverage_args(now, cycle_days))
    return int(r["n"]) if r else 0


def coverage_figures(conn: Conn, now: datetime | None = None, cycle_days: int = 35) -> dict[str, int]:
    """The progress of the account searches, as plain numbers for Administration > Data health (it shows each as 'N <name with spaces>'):
    how many accounts were searched in the last 24 hours, how many are still waiting for their search, and how big the group is.
    Empty while there is no such account and nothing was searched, so a workspace without them shows nothing."""
    now = now or datetime.now(timezone.utc)
    r = q1(conn, _coverage_sql("count(*) AS total, count(*) FILTER (WHERE dp.last_search IS NULL OR dp.last_search < %(cut)s) AS waiting"), _coverage_args(now, cycle_days)) or {}
    done = q1(conn, "SELECT count(DISTINCT organization_id) AS n FROM discovery_plans WHERE theme=%s AND last_run_at > %s", (COVERAGE_THEME, now - timedelta(days=1))) or {}
    total, waiting, searched = int(r.get("total") or 0), int(r.get("waiting") or 0), int(done.get("n") or 0)
    if not (total or searched):
        return {}
    return {"accounts_searched": searched, "accounts_still_without_a_search": waiting, "accounts_without_a_finding": total}
