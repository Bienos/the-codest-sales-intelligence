"""Open tenders, 25 Nov 2026: TED links open the English page (not the Bulgarian one), the list starts with our markets,
and an administrator can ask for the tender sources to be checked now."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)

from ri.db import jb
from ri.sources.base import RawItem
from ri.sources.procurement import TedConnector, ted_english_url
from ri.sources.util import first_text

NOW = datetime.now(timezone.utc)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0037_ted_english_links.sql"
# the shape TED returns, checked against the live API: upper-case language keys, Bulgarian first
TED_LINKS = {"html": {"BUL": "https://ted.europa.eu/bg/notice/-/detail/2382-2017", "SPA": "https://ted.europa.eu/es/notice/-/detail/2382-2017",
                      "ENG": "https://ted.europa.eu/en/notice/-/detail/2382-2017"}}


# ------------------------------------------------------------------------------------------------ the language lookup
def test_language_keys_match_whatever_their_case():
    assert first_text(TED_LINKS["html"]) == "https://ted.europa.eu/en/notice/-/detail/2382-2017"            # "ENG", not the first entry
    assert first_text({"swe": "Utveckling av system", "eng": "System development"}) == "System development"   # titles use lower-case keys
    assert first_text({"swe": "Utveckling av system", "lav": "Sistēmas izstrāde"}) == "Utveckling av system"  # no English: the first one, as before
    assert first_text({"EN": " Hello "}) == "Hello" and first_text({"eng": None, "ENG": "x"}) is None            # an exact key still wins
    assert first_text({"BUL": ["a"], "FRA": ["b"]}, ("fra",)) == "b"


def test_ted_notices_link_to_the_english_page():
    payload = {"publication-number": "2382-2017", "notice-title": {"lav": "Latvija – IT pakalpojumi – Sistēmas izstrāde"}, "buyer-name": {"lav": ["Rīgas dome"]},
               "buyer-country": ["LVA"], "notice-type": "cn-standard", "publication-date": "2026-11-18+01:00",
               "deadline-receipt-tender-date-lot": ["2026-12-20+01:00"], "classification-cpv": ["72000000"], "links": TED_LINKS}
    c = TedConnector(None, query="x", now_fn=lambda: NOW)       # type: ignore[arg-type]
    d = c.canonicalize(RawItem("2382-2017", None, payload))
    assert d.canonical_url == d.structured["official_url"] == "https://ted.europa.eu/en/notice/-/detail/2382-2017"
    only_bg = {**payload, "links": {"html": {"BUL": "https://ted.europa.eu/bg/notice/-/detail/2382-2017"}}}
    assert c.canonicalize(RawItem("2382-2017", None, only_bg)).canonical_url == "https://ted.europa.eu/en/notice/-/detail/2382-2017"
    assert ted_english_url("https://ted.europa.eu/en/notice/-/detail/1-2026") == "https://ted.europa.eu/en/notice/-/detail/1-2026"
    assert ted_english_url("https://ezamowienia.gov.pl/mp-client/search/list/ocds-148610-1") == "https://ezamowienia.gov.pl/mp-client/search/list/ocds-148610-1"
    assert ted_english_url(None) is None


def test_migration_points_every_stored_ted_link_at_the_english_page(conn, env):
    bg, other = "https://ted.europa.eu/bg/notice/-/detail/2382-2017", "https://www.find-tender.service.gov.uk/Notice/012345-2026"
    doc = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url) values ('procurement-ted','2382-2017','v1',%s) returning id", (bg,)).fetchone()["id"]
    conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url) values ('procurement-uk-fts','012345-2026','v1',%s)", (other,))
    conn.execute("insert into procurement_notices(source_id, notice_id, status, official_url, document_id) values ('procurement-ted','2382-2017','open',%s,%s)", (bg, doc))
    conn.execute("insert into procurement_notices(source_id, notice_id, status, official_url) values ('procurement-uk-fts','012345-2026','open',%s)", (other,))
    conn.execute("insert into awarded_contracts(notice_id, end_basis, official_url) values ('9999-2025','assumed','https://ted.europa.eu/de/notice/-/detail/9999-2025')")
    conn.execute("insert into claims(subject, predicate, object_json, claim_kind) values ('Rīgas dome','has_official_notice',%s,'structured')",
                 (jb({"notice_id": "2382-2017", "official_url": bg}),))
    conn.execute("insert into events(event_key, event_type, summary, extraction_model_version, attrs) values ('k1','procurement_published','Rīgas dome: tender','structured',%s)", (jb({"notice_id": "2382-2017", "authoritative_url": bg}),))
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    en = "https://ted.europa.eu/en/notice/-/detail/2382-2017"
    assert {r["official_url"] for r in conn.execute("select official_url from procurement_notices")} == {en, other}
    assert {r["canonical_url"] for r in conn.execute("select canonical_url from source_documents")} == {en, other}
    assert conn.execute("select official_url from awarded_contracts").fetchone()["official_url"] == "https://ted.europa.eu/en/notice/-/detail/9999-2025"
    assert conn.execute("select object_json from claims where subject='Rīgas dome'").fetchone()["object_json"] == {"notice_id": "2382-2017", "official_url": en}
    assert conn.execute("select attrs from events where event_key='k1'").fetchone()["attrs"] == {"notice_id": "2382-2017", "authoritative_url": en}
    conn.execute(MIGRATION.read_text(encoding="utf-8"))                       # running it again changes nothing
    assert conn.execute("select official_url from procurement_notices where source_id='procurement-ted'").fetchone()["official_url"] == en


# ------------------------------------------------------------------------------------------------ our markets first
def notice(conn, nid, title, country, *, days=20, source="procurement-ted"):
    conn.execute("insert into procurement_notices(source_id, notice_id, buyer_name, title, status, deadline_at, official_url, country, service_codes, last_status_check_at) "
                 "values (%s,%s,'Buyer',%s,'open',%s,%s,%s,'{72000000}',now())",
                 (source, nid, title, NOW + timedelta(days=days, hours=1), f"https://ted.europa.eu/en/notice/-/detail/{nid}", country))


def test_our_markets_filter_keeps_uk_dach_nordics_and_poland(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        notice(conn, "1", "Germany – IT services – Portal", "DEU", days=10)
        notice(conn, "2", "Lithuania – IT services – Sistema", "LTU", days=11)
        notice(conn, "3", "Sweden – Software development – Plattform", "SWE", days=12)
        notice(conn, "4", "Poland – IT services – System", "POL", days=13)
        notice(conn, "5", "France – IT services – Logiciel", "FRA", days=14)
        notice(conn, "6", "Greece – IT services – Too soon", "GRC", days=1)            # elsewhere and closing too soon: not counted anywhere
        notice(conn, "7", "Software engineering for a digital service", "GB", days=15, source="procurement-uk-contracts-finder")
    core = c.get("/api/tenders?region=core", headers=h("bdm")).json()
    assert [i["notice_id"] for i in core["items"]] == ["1", "3", "4", "7"]
    assert core["counts"]["total"] == 4 and core["counts"]["other_regions"] == 2 and core["counts"]["closing_too_soon"] == 0
    assert core["items"][0]["category"] == "IT services" and core["items"][0]["title"] == "Portal"
    every = c.get("/api/tenders", headers=h("bdm")).json()                                  # no region: every region, as before
    assert every["counts"]["total"] == 6 and every["counts"]["other_regions"] == 0 and every["counts"]["closing_too_soon"] == 1
    assert [i["notice_id"] for i in c.get("/api/tenders?region=dach", headers=h("bdm")).json()["items"]] == ["1"]
    assert [i["notice_id"] for i in c.get("/api/tenders?region=europe", headers=h("bdm")).json()["items"]] == ["2", "5"]
    assert c.get("/api/tenders?region=mars", headers=h("bdm")).status_code == 422


# ------------------------------------------------------------------------------------------------ last updated, check now
def test_last_updated_and_check_now_for_administrators(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        conn.execute("update sources set status='active' where source_id in ('procurement-ted','procurement-uk-fts')")
        conn.execute("insert into source_health(source_id, state, last_success_at) values ('procurement-ted','healthy',%s), ('procurement-uk-fts','healthy',%s) "
                     "on conflict (source_id) do update set state='healthy', last_success_at=excluded.last_success_at",
                     (NOW - timedelta(hours=5), NOW - timedelta(hours=2)))
    out = c.get("/api/tenders", headers=h("bdm")).json()
    assert abs(datetime.fromisoformat(out["last_updated"]) - (NOW - timedelta(hours=2))) < timedelta(seconds=1)      # the latest successful check
    assert out["refreshing"] is False
    assert c.post("/api/tenders/refresh", headers=h("bdm")).status_code == 403                  # sellers read the list; only administrators run sources
    first = c.post("/api/tenders/refresh", headers=h("admin")).json()
    assert first["queued"] == ["procurement-ted", "procurement-uk-fts"] and first["already_waiting"] == []
    assert sorted(first["not_switched_on"]) == ["procurement-ezamowienia", "procurement-uk-contracts-finder"]
    again = c.post("/api/tenders/refresh", headers=h("admin")).json()                         # clicking twice queues nothing new
    assert again["queued"] == [] and again["already_waiting"] == ["procurement-ted", "procurement-uk-fts"]
    with world["app"].state.db.tx() as conn:
        jobs = conn.execute("select payload->>'source_id' AS sid, status from jobs where kind='source_run' order by 1").fetchall()
        assert [(j["sid"], j["status"]) for j in jobs] == [("procurement-ted", "scheduled"), ("procurement-uk-fts", "scheduled")]
        assert conn.execute("select count(*) n from audit_events where operation='tenders_refresh_requested' and actor='admin@x'").fetchone()["n"] == 2
    assert c.get("/api/tenders", headers=h("bdm")).json()["refreshing"] is True
