"""Open tenders, round 3.5: the buyer chips by The Codest's ICP (banks first, then insurers, payments and fintech, lenders and asset managers,
the market's own institutions; "other sectors"; "all tenders" really is everything), each with a live count, and compact filters (region and
country, value, closing within, new or changed, fit, kind of work) whose counts leave out their own filter. Search also takes CPV codes."""
import json
from datetime import datetime, timedelta, timezone

from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)

from ri import tender_sector as sec

NOW = datetime.now(timezone.utc)


def notice(conn, nid, buyer, *, days=20, country="DEU", cpv=("72000000",), detail=None, summary=None, published_days=10, status="open"):
    return str(conn.execute(
        "insert into procurement_notices(source_id, notice_id, buyer_name, title, status, deadline_at, official_url, country, service_codes, "
        "published_at, last_status_check_at, source_detail, summary) values ('procurement-ted',%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s) returning id",
        (nid, buyer, f"Deutschland – IT-Dienste – Projekt {nid}", status, NOW + timedelta(days=days, hours=1), f"https://ted.europa.eu/en/notice/-/detail/{nid}",
         country, list(cpv), NOW - timedelta(days=published_days), json.dumps(detail) if detail else None,
         json.dumps(summary) if summary else None)).fetchone()["id"])


# ------------------------------------------------------------------------------------------------ the chips
def test_every_buyer_kind_belongs_to_one_chip_banks_first():
    assert sec.GROUPS == ("banks", "insurers", "payments", "lending", "markets", "other")
    every_kind = {k for kinds in sec.KINDS.values() for k in kinds}
    assert {sec.group_of(s, k) for s, kinds in sec.KINDS.items() for k in kinds} == set(sec.GROUPS)
    assert {k for kinds in sec.GROUP_KINDS.values() for k in kinds} <= every_kind
    assert sec.group_of("banking", "savings_bank") == "banks" and sec.group_of("banking", "development_bank") == "banks"
    assert sec.group_of("banking", "central_bank") == "markets" and sec.group_of("financial_services", "financial_authority") == "markets"
    assert sec.group_of("financial_services", "insurance") == "insurers" and sec.group_of("financial_services", "payments") == "payments"
    assert sec.group_of("financial_services", "pension") == "lending" and sec.group_of("financial_services", "asset_management") == "lending"
    assert sec.group_of("other_icp", "digital_commerce") == "other" and sec.group_of("not_icp", "public_body") == "other"
    assert sec.group_of(None, None) == "other" and sec.group_of("banking", "mystery") == "banks"


def test_cpv_codes_in_the_search_become_prefixes():
    assert sec.split_search("core banking 72262000") == ("core banking", ["72262"])
    assert sec.split_search("72000000 7226 48000000-8") == ("", ["72", "7226", "48"])
    assert sec.split_search("Sparkasse 2") == ("Sparkasse 2", [])          # short numbers stay words


def test_rules_v2_pensioners_homes_and_czech_development_banks():
    assert sec.RULES_VERSION == 2
    assert sec.classify("Fonds Kuratorium Wiener Pensionisten-Wohnhäuser").kind != "pension"
    assert (sec.classify("Národní rozvojová banka, a.s.").kind, sec.classify("Husbanken").kind) == ("development_bank", "development_bank")
    assert sec.classify("Fjärde AP-fonden").kind == "pension"                 # real pension funds unchanged


def item(i, sector, kind, *, days=20, region="dach", country="DE", value=None, level=None, work=None, new=False, status="open"):
    return {"id": f"{i:04d}", "sector": sector, "sector_kind": kind, "days_left": days, "deadline": NOW + timedelta(days=days), "region": region,
            "country": country, "value_eur": value, "status": status, "published": NOW - timedelta(days=1 if new else 30),
            "summary": {"title_en": "x", "fit": {"level": level} if level else None, "work_type": work} if level or work else None}


def test_arrange_counts_leave_out_their_own_filter():
    items = [item(1, "banking", "savings_bank", days=10, value=2_000_000, level="strong", work="software_development", new=True),
             item(2, "banking", "central_bank", days=5, region="europe", country="IT", value=600_000),
             item(3, "financial_services", "insurance", days=40, region="nordics", country="SE", value=150_000, level="possible", status="amended"),
             item(4, "financial_services", "payments", days=2, region="nordics", country="NO"),
             item(5, "not_icp", "public_body", days=12, value=5_000_000),
             item(6, "other_icp", "digital_commerce", days=8, region="poland", country="PL", new=True)]
    shown, counts, facets = sec.arrange([dict(i) for i in items], view="icp", region="core")
    assert [i["id"] for i in shown] == ["0001", "0003"]                 # banks, then insurers; the payments one closes in 2 days; the central bank is not in our markets
    assert counts["closing_too_soon"] == 1 and counts["other_regions"] == 1 and counts["new"] == 1
    assert facets["groups"] == {"banks": 1, "insurers": 1, "payments": 0, "lending": 0, "markets": 0, "other": 2, "icp": 2, "all": 4}
    assert facets["region"]["core"] == 2 and facets["region"]["europe"] == 1 and facets["region"]["all"] == 3
    assert facets["value"] == {"100k": 2, "500k": 1, "1m": 1, "any": 2} and facets["within"]["14"] == 1 and facets["status"] == {"new": 1, "changed": 1, "any": 2}
    # "all" really is everything; "other" is everyone outside the financial chips
    assert len(sec.arrange([dict(i) for i in items], view="all", region="", min_days=0)[0]) == 6
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="other", region="")[0]] == ["0006", "0005"]
    # one chip, a country, a status, a value
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="markets", region="")[0]] == ["0002"]
    shown, _, facets = sec.arrange([dict(i) for i in items], view="all", region="nordics", country="SE")
    assert [i["id"] for i in shown] == ["0003"] and facets["region"]["dach"] == 2 and facets["country"] == {"SE": 1}     # the region counts ignore the country
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="all", region="", status="new")[0]] == ["0001", "0006"]
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="all", region="", status="changed")[0]] == ["0003"]
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="all", region="", min_value=500_000, sort="value")[0]] == ["0001", "0002", "0005"]


def test_sort_works_inside_each_chip_across_sectors():
    items = [item(1, "banking", "central_bank", days=30, value=100_000), item(2, "financial_services", "financial_authority", days=10, value=900_000),
             item(3, "financial_services", "market_infrastructure", days=20)]
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="markets", region="", sort="deadline")[0]] == ["0002", "0003", "0001"]
    assert [i["id"] for i in sec.arrange([dict(i) for i in items], view="markets", region="", sort="value")[0]] == ["0002", "0001", "0003"]


# ------------------------------------------------------------------------------------------------ the API
def test_api_group_view_chips_counts_all_tenders_and_cpv_search(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        notice(conn, "1-2026", "Kreissparkasse Köln", days=19, cpv=("72200000", "72262000"), published_days=2,
               detail={"from": "ted", "value": 1_250_000.0, "currency": "EUR"}, summary={"title_en": "Open banking APIs", "summary_en": "x", "v": 2,
                                                                                          "fit": {"level": "strong", "reason": "Bank API work"}})
        notice(conn, "2-2026", "Versicherungskammer Bayern", days=12)
        notice(conn, "3-2026", "Deutsche Bundesbank", days=8, cpv=("72260000",))
        notice(conn, "4-2026", "Stadt Beispiel", days=6, cpv=("72262000",), detail={"from": "ted", "value": 300_000.0, "currency": "EUR"})
        notice(conn, "5-2026", "Gmina Miasto Gdynia", days=9, country="POL", status="amended")
        notice(conn, "6-2026", "Banca d'Italia", days=15, country="ITA")
    get = lambda qs: c.get(f"/api/tenders?{qs}", headers=h("bdm")).json()      # noqa: E731
    icp = get("group=icp&region=core&sort=fit")
    assert [i["notice_id"] for i in icp["items"]] == ["1-2026", "2-2026", "3-2026"]           # banks, insurers, then central banks; no public body
    assert [i["group"] for i in icp["items"]] == ["banks", "insurers", "markets"]
    assert icp["groups"] == {"banks": 1, "insurers": 1, "payments": 0, "lending": 0, "markets": 1, "other": 2, "icp": 3, "all": 5}
    assert icp["counts"]["other_regions"] == 1 and icp["facets"]["region"]["europe"] == 1
    first = icp["items"][0]
    assert first["is_new"] is True and first["cpv"] == ["72200000", "72262000"] and first["value_eur"] == 1_250_000
    every = get("group=all&region=")
    assert len(every["items"]) == 6 and every["groups"]["all"] == 6 and {i["group"] for i in every["items"]} == {"banks", "insurers", "markets", "other"}
    assert [i["notice_id"] for i in get("group=other&region=")["items"]] == ["4-2026", "5-2026"]
    # search by CPV code (and words), by country, by status, by value; the chips count the search
    cpv = get("group=all&region=&q=72262000")
    assert [i["notice_id"] for i in cpv["items"]] == ["1-2026", "4-2026"] and cpv["groups"]["banks"] == 1 and cpv["groups"]["other"] == 1
    assert [i["notice_id"] for i in get("group=all&region=&q=Gdynia")["items"]] == ["5-2026"]
    assert [i["notice_id"] for i in get("group=all&region=&country=PL")["items"]] == ["5-2026"]
    assert [i["notice_id"] for i in get("group=all&region=&status=changed")["items"]] == ["5-2026"]
    assert [i["notice_id"] for i in get("group=all&region=&status=new")["items"]] == ["1-2026"]
    assert [i["notice_id"] for i in get("group=all&region=&min_value=1000000")["items"]] == ["1-2026"]
    paged = get("group=all&region=&limit=4&page=2")
    assert len(paged["items"]) == 2 and paged["has_more"] is False and paged["page"] == 2
    # the older sector view is unchanged, and unknown chips are refused
    assert [i["notice_id"] for i in get("sector=icp&region=core")["items"]] == ["3-2026", "1-2026", "2-2026"]
    assert c.get("/api/tenders?group=fintech", headers=h("bdm")).status_code == 422
    assert c.get("/api/tenders?group=all&country=Poland", headers=h("bdm")).status_code == 422
    assert c.get("/api/tenders?group=all", headers=h("mkt")).status_code == 403
