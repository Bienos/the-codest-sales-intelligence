"""Source review of 26 Nov 2026: dead-weight sources are paused (reversibly, in a way the daily automatic test keeps), eight new public news
feeds are registered so that test can switch them on, and the tender portals that were checked and not built say why.
No network: the daily test is a fake, and the migration is run against the throw-away test database."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from ri.sources import autovalidate, registry
from ri.sources.factory import NoConnector, build_connector
from ri.sources.registry import REVIEW_ACTOR, seed_registry

RULES = Path(__file__).resolve().parents[1] / "rules"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0050_source_review.sql"

PAUSED_BY_REVIEW = ["attention-heise", "regulator-fi-se", "regulator-ecb", "regulator-bis", "attention-leaddev", "attention-cashlesspl"]
# page monitors with a single stored page after five days: not enough evidence, so they stay on
KEPT_ON = ["si-accenture-news", "si-capgemini-news", "vendor-mambu-news", "vendor-worldline-news", "regulator-finma", "attention-ffnews", "attention-ctocraft", "attention-ebaday"]
NEW_FEEDS = ["attention-computerweekly-fs", "attention-openbankingexpo", "attention-paymentscardsmobile", "attention-retailbankerintl",
             "attention-insurtechinsights", "attention-gazetaubezpieczeniowa", "attention-vwheute", "attention-derbankblog"]
# sites that block our reader (robots.txt, bot check) or have no feed: never listed with a feed address that works around it
BLOCKED_HOSTS = ("techuk.org", "uktech.news", "fintechfutures.com", "finextra.com", "bankier.pl", "paymentexpert.com", "finanz-szene.de", "computing.co.uk")


def _yaml() -> list[dict]:
    return yaml.safe_load((RULES / "sources.yaml").read_text(encoding="utf-8"))["sources"]


def _by_id() -> dict[str, dict]:
    return {s["source_id"]: s for s in _yaml()}


def _src(conn, sid):
    return conn.execute("select * from sources where source_id=%s", (sid,)).fetchone()


def _note(sid: str) -> str:
    return _by_id()[sid]["review_note"]


# ------------------------------------------------------------------------------------------------ the registry file
def test_registry_ids_are_unique_and_every_source_has_the_required_fields():
    rows = _yaml()
    ids = [s["source_id"] for s in rows]
    assert len(ids) == len(set(ids))
    for s in rows:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", s["source_id"]), s["source_id"]
        for k in ("display_name", "source_tier", "source_type", "source_family", "provider", "collection_method"):
            assert s.get(k), (s["source_id"], k)
        assert s["source_tier"] in ("S1", "S2", "S3", "S4", "S5", "internal")


def test_the_new_feeds_are_public_news_feeds_the_daily_test_can_switch_on():
    by = _by_id()
    seen_urls = {}
    for s in _yaml():
        u = (s.get("config") or {}).get("url")
        if u:
            seen_urls.setdefault(u, []).append(s["source_id"])
    for sid in NEW_FEEDS:
        s = by[sid]
        assert sid.startswith("attention-") and s["source_tier"] == "S4" and s["source_family"] == "attention" and s["source_type"] == "news"
        assert s["collection_method"] == "feed" and s["retention_policy"] == "metadata_only" and s["poll_interval_s"] == 86400
        assert "metadata + short excerpt only" in s["licence_notes"]
        assert s["signal_rule_ids"] and s["languages"] and "status" not in s             # waiting for validation: the daily test decides
        assert "auth_mode" not in s or s["auth_mode"] == "none"
        url = s["config"]["url"]
        host = urlparse(url).hostname
        assert urlparse(url).scheme == "https" and (host == s["canonical_domain"] or host.endswith("." + s["canonical_domain"])), sid
        assert not any(host.endswith(b) for b in BLOCKED_HOSTS), sid
        assert seen_urls[url] == [sid], f"{url} is already registered under {seen_urls[url]}"
        assert "26 Nov 2026" in s["config"]["url_basis"] and not re.search(r"verify|terms vary|commercial|per-employer", s["licence_notes"], re.I)
    assert len(NEW_FEEDS) == 8


def test_six_sources_carry_a_plain_review_note_and_the_one_page_monitors_stay_on():
    by = _by_id()
    with_note = sorted(sid for sid, s in by.items() if s.get("review_note"))
    assert with_note == sorted(PAUSED_BY_REVIEW)
    for sid in PAUSED_BY_REVIEW:
        n = by[sid]["review_note"]
        assert n.startswith("Paused on 26 Nov 2026 after a review: ") and n.endswith("Switch on again by hand if wanted.")
        assert "no finding for our accounts" in n or "off-topic" in n
    assert not [sid for sid in KEPT_ON if "review_note" in by[sid]]


# ------------------------------------------------------------------------------------------------ a fresh database
def test_a_fresh_database_starts_with_the_six_paused_and_says_why(conn):
    seed_registry(conn)
    for sid in PAUSED_BY_REVIEW:
        s = _src(conn, sid)
        assert s["status"] == "paused" and s["config"]["review_note"] == _note(sid)
        assert s["config"]["auto"]["state"] == "paused" and s["config"]["auto"]["reason"] == _note(sid) and not s["config"]["auto"]["eligible"]
        h = conn.execute("select state, detail from source_health where source_id=%s", (sid,)).fetchone()
        assert h["state"] == "paused" and h["detail"] == _note(sid)
    audit = conn.execute("select record_id, actor, operation from audit_events where record_type='source' and operation='pause_source'").fetchall()
    assert sorted(a["record_id"] for a in audit) == sorted(PAUSED_BY_REVIEW) and {a["actor"] for a in audit} == {REVIEW_ACTOR}
    assert set(PAUSED_BY_REVIEW) == autovalidate.person_paused(conn)
    # nothing else was touched: every other source is still waiting for validation
    others = conn.execute("select status, count(*) n from sources where source_id <> all(%s) group by 1", (PAUSED_BY_REVIEW,)).fetchall()
    assert [(o["status"]) for o in others] == ["pending_connector_validation"]
    # and the new feeds are in, waiting for the daily test
    assert {r["source_id"] for r in conn.execute("select source_id from sources where status='pending_connector_validation'").fetchall()} >= set(NEW_FEEDS)


# ------------------------------------------------------------------------------------------------ the daily test keeps them paused
def test_the_daily_test_never_switches_a_paused_source_on_even_when_it_passes(conn):
    seed_registry(conn)
    conn.commit()
    tested = []

    def tester(c, sid):
        tested.append(sid)
        return {"result": "passed", "ok": True}
    out = autovalidate.run_daily(conn, tester=tester, only=PAUSED_BY_REVIEW)
    assert out["switched_on"] == [] and tested == []                              # not even tested: it is paused on purpose
    for sid in PAUSED_BY_REVIEW:
        s = _src(conn, sid)
        assert s["status"] == "paused"
        assert s["config"]["auto"]["state"] == "paused" and s["config"]["auto"]["reason"] == _note(sid) and s["config"]["auto"]["eligible"] is False
    assert out["summary"] == {"paused": 6}
    from ri.connections import directory
    rows = {r["source_id"]: r for r in directory(conn)}
    for sid in PAUSED_BY_REVIEW:
        assert rows[sid]["state"] == "paused" and _note(sid) in rows[sid]["what_to_do"] and "never switched on automatically" in rows[sid]["what_to_do"]
    # Administration, Sources lists them under "Paused" with the reason
    from ri.api import admin_ops
    ov = admin_ops.sources_overview(conn)
    grp = next(g for g in ov["groups"] if g["state"] == "paused")
    assert {i["source_id"] for i in grp["items"]} == set(PAUSED_BY_REVIEW)
    assert {i["reason"] for i in grp["items"]} == {_note(sid) for sid in PAUSED_BY_REVIEW}


def test_a_person_can_switch_one_on_again_by_hand_and_the_old_note_goes(conn):
    seed_registry(conn)
    conn.commit()
    cert = {"provider_doc_url": "https://x", "auth_entitlement_verified": True, "verified_endpoints": ["https://x"], "data_licence": "public", "technical_owner": "admin@x",
            "last_tested_date": "2026-11-27", "limits": "daily", "rollback_path": "pause it"}
    registry.activate(conn, "regulator-ecb", cert, "admin@x")
    conn.commit()
    s = _src(conn, "regulator-ecb")
    assert s["status"] == "active" and "review_note" not in s["config"]
    assert "regulator-ecb" not in autovalidate.person_paused(conn)                  # the latest entry is now a switch-on
    out = autovalidate.run_daily(conn, tester=lambda c, sid: {"result": "passed", "ok": True}, only=["regulator-ecb"])
    assert out["summary"] == {"live": 1} and _src(conn, "regulator-ecb")["status"] == "active"
    # a person pausing it later gets the ordinary reason, not the review note
    registry.pause(conn, "regulator-ecb", "admin@x", "too noisy again")
    conn.commit()
    autovalidate.run_daily(conn, tester=lambda c, sid: {"result": "passed", "ok": True}, only=["regulator-ecb"])
    a = _src(conn, "regulator-ecb")["config"]["auto"]
    assert a["state"] == "paused" and a["reason"] == "Paused by a person."


def test_the_new_feeds_are_eligible_and_switch_on_when_the_server_test_passes(conn):
    seed_registry(conn)
    conn.commit()
    notes = autovalidate._notes()
    for sid in NEW_FEEDS:
        p = autovalidate.policy(_src(conn, sid), notes.get(sid))
        assert p["eligible"] and p["state"] == "testing", (sid, p)
        assert isinstance(build_connector(conn, sid), __import__("ri.sources.webwatch", fromlist=["FeedConnector"]).FeedConnector)
    results = {"attention-vwheute": {"result": "failed", "ok": False, "error_class": "policy_block", "detail": "robots.txt disallows https://x"}}
    out = autovalidate.run_daily(conn, tester=lambda c, sid: results.get(sid, {"result": "passed", "ok": True}), only=NEW_FEEDS)
    assert sorted(out["switched_on"]) == sorted(set(NEW_FEEDS) - {"attention-vwheute"})
    assert _src(conn, "attention-vwheute")["status"] == "pending_connector_validation" and _src(conn, "attention-vwheute")["config"]["auto"]["state"] == "blocked_by_site"
    assert _src(conn, "attention-openbankingexpo")["certification"]["technical_owner"] == autovalidate.BY


# ------------------------------------------------------------------------------------------------ the migration for the existing database
def _as_production(conn):
    """The database as it is now: the six are live, nothing about the review is recorded."""
    seed_registry(conn)
    conn.execute("truncate audit_events")                      # a test database only: the trail is append-only everywhere else
    for sid in PAUSED_BY_REVIEW:
        conn.execute("update sources set status='active', config = (config - 'review_note' - 'auto') where source_id=%s", (sid,))
        conn.execute("update source_health set state='healthy', detail=null where source_id=%s", (sid,))
    conn.commit()


def _run_migration(conn):
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    conn.commit()


def test_the_migration_pauses_the_six_live_sources_once_with_the_same_note_as_the_registry_file(conn):
    _as_production(conn)
    before = {r["source_id"]: r["status"] for r in conn.execute("select source_id, status from sources").fetchall()}
    _run_migration(conn)
    for sid in PAUSED_BY_REVIEW:
        s = _src(conn, sid)
        assert s["status"] == "paused" and s["config"]["review_note"] == _note(sid) and s["config"]["auto"]["reason"] == _note(sid)
        assert s["config"]["url"] == _by_id()[sid]["config"]["url"]                    # nothing else in the config changed
        assert conn.execute("select state, detail from source_health where source_id=%s", (sid,)).fetchone() == {"state": "paused", "detail": _note(sid)}
    after = {r["source_id"]: r["status"] for r in conn.execute("select source_id, status from sources").fetchall()}
    assert {k for k in before if before[k] != after[k]} == set(PAUSED_BY_REVIEW)
    rows = conn.execute("select record_id, actor from audit_events where operation='pause_source'").fetchall()
    assert sorted(r["record_id"] for r in rows) == sorted(PAUSED_BY_REVIEW) and {r["actor"] for r in rows} == {REVIEW_ACTOR}
    assert autovalidate.person_paused(conn) == set(PAUSED_BY_REVIEW)
    # the daily test now leaves them alone
    out = autovalidate.run_daily(conn, tester=lambda c, sid: {"result": "passed", "ok": True}, only=PAUSED_BY_REVIEW)
    assert out["switched_on"] == [] and {_src(conn, s)["status"] for s in PAUSED_BY_REVIEW} == {"paused"}


def test_the_migration_is_safe_to_run_again_and_never_pauses_a_source_a_person_switched_on_again(conn):
    _as_production(conn)
    _run_migration(conn)
    n = conn.execute("select count(*) c from audit_events").fetchone()["c"]
    _run_migration(conn)                                                                # idempotent: nothing left to pause
    assert conn.execute("select count(*) c from audit_events").fetchone()["c"] == n
    cert = {"provider_doc_url": "https://x", "auth_entitlement_verified": True, "verified_endpoints": ["https://x"], "data_licence": "public", "technical_owner": "admin@x",
            "last_tested_date": "2026-11-27", "limits": "daily", "rollback_path": "pause it"}
    registry.activate(conn, "attention-heise", cert, "admin@x")
    conn.commit()
    _run_migration(conn)
    assert _src(conn, "attention-heise")["status"] == "active" and "review_note" not in _src(conn, "attention-heise")["config"]


def test_the_migration_leaves_a_source_alone_that_is_not_live_or_that_a_person_already_paused(conn):
    _as_production(conn)
    registry.pause(conn, "regulator-bis", "admin@x", "we do not need it")
    conn.execute("update sources set status='pending_connector_validation' where source_id='regulator-ecb'")
    conn.commit()
    _run_migration(conn)
    assert "review_note" not in _src(conn, "regulator-bis")["config"] and _src(conn, "regulator-ecb")["status"] == "pending_connector_validation"
    assert _src(conn, "attention-heise")["status"] == "paused"
    reasons = {r["record_id"]: r["actor"] for r in conn.execute("select record_id, actor from audit_events where operation='pause_source'").fetchall()}
    assert reasons["regulator-bis"] == "admin@x" and "regulator-ecb" not in reasons


def test_the_migration_on_a_database_without_these_sources_does_nothing(conn):
    n = conn.execute("select count(*) c from audit_events").fetchone()["c"]
    _run_migration(conn)
    assert conn.execute("select count(*) c from audit_events").fetchone()["c"] == n == 0


# ------------------------------------------------------------------------------------------------ tender portals: checked, not built
def test_doffin_dtvp_and_udbud_have_no_connector_and_say_why_in_plain_words(conn):
    seed_registry(conn)
    notes = autovalidate._notes()
    for sid in ("procurement-doffin", "procurement-dtvp", "procurement-udbud"):
        with pytest.raises(NoConnector):
            build_connector(conn, sid)
        p = autovalidate.policy(_src(conn, sid), notes.get(sid))
        assert p["state"] == "not_built" and not p["eligible"]
        assert "TED" in notes[sid]["todo"]
    assert "subscription key" in notes["procurement-doffin"]["todo"] and "subscription key" in _src(conn, "procurement-doffin")["config"]["note"]
    assert "no page scraping" in notes["procurement-dtvp"]["todo"] and "no page scraping" in notes["procurement-udbud"]["todo"]


def test_finanz_szene_has_no_public_feed_so_it_is_listed_as_blocked_and_never_read(conn):
    seed_registry(conn)
    p = autovalidate.policy(_src(conn, "attention-finanzszene"), autovalidate._notes().get("attention-finanzszene"))
    assert p["state"] == "blocked_by_site" and not p["eligible"] and "no public feed" in p["reason"]
    out = autovalidate.run_daily(conn, tester=lambda c, sid: {"result": "failed", "ok": False, "error_class": "server_error", "detail": "500 server error"}, only=["attention-finanzszene"])
    assert out["switched_on"] == [] and _src(conn, "attention-finanzszene")["config"]["auto"]["state"] == "blocked_by_site"
