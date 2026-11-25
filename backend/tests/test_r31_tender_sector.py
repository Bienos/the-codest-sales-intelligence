"""Open tenders, 25 Nov 2026: our ICP only. Every open notice gets its buyer's sector by rules (banks, other financial services, other ICP
sectors, not ICP); the list shows the ICP sectors by default, banks first; values are shown in euros at fixed, dated rates; only ICP notices
(and the ones the rules cannot tell) are summarised, and the model's buyer_sector decides the unclear ones. No network: the model and TED are faked."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_r3_opportunity import env  # noqa: F401  (fixture)
from test_r4_api import world  # noqa: F401  (fixture)
from test_r27_tender_summaries import FakeModel, answer, plenty_of_budget, run

from ri import tender_sector as sec
from ri import tender_summary as ts
from ri import tenders

NOW = datetime.now(timezone.utc)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0041_tender_sector.sql"


def notice(conn, nid, buyer, *, days=20, country="DEU", source="procurement-ted", cpv=("72000000",), value=None, detail=None, title=None, org=None, summary=None):
    return str(conn.execute(
        "insert into procurement_notices(source_id, notice_id, buyer_name, title, status, deadline_at, official_url, country, service_codes, value_if_reported, "
        "published_at, last_status_check_at, source_detail, buyer_org_id, summary) values (%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,now(),%s,%s,%s) returning id",
        (source, nid, buyer, title or f"Deutschland – IT-Dienste – Projekt {nid}", NOW + timedelta(days=days, hours=1), f"https://ted.europa.eu/en/notice/-/detail/{nid}",
         country, list(cpv), value, NOW - timedelta(days=3), json.dumps(detail) if detail else None, org, json.dumps(summary) if summary else None)).fetchone()["id"])


# ------------------------------------------------------------------------------------------------ the rules
@pytest.mark.parametrize("name,sector,kind,sure", [
    ("Kreissparkasse Köln", "banking", "savings_bank", True),
    ("Stadtsparkasse München", "banking", "savings_bank", True),
    ("Volksbank Mittelhessen eG", "banking", "cooperative_bank", True),
    ("SpareBank 1 SR-Bank ASA", "banking", "savings_bank", True),
    ("Landesbank Hessen-Thüringen Girozentrale (Helaba)", "banking", "bank", True),
    ("Zürcher Kantonalbank", "banking", "bank", True),
    ("PKO Bank Polski S.A.", "banking", "bank", True),
    ("Nationwide Building Society", "banking", "building_society", True),
    ("Deutsche Bundesbank, Zentralbereich Beschaffungen", "banking", "central_bank", True),
    ("Narodowy Bank Polski", "banking", "central_bank", True),
    ("Sveriges riksbank", "banking", "central_bank", True),
    ("Bank Gospodarstwa Krajowego", "banking", "development_bank", True),
    ("KfW Bankengruppe", "banking", "development_bank", True),
    ("Urząd Komisji Nadzoru Finansowego", "financial_services", "financial_authority", True),
    ("Bundesanstalt für Finanzdienstleistungsaufsicht", "financial_services", "financial_authority", True),
    ("Financial Conduct Authority", "financial_services", "financial_authority", True),
    ("Bankowy Fundusz Gwarancyjny", "financial_services", "financial_authority", True),
    ("Krajowy Depozyt Papierów Wartościowych S.A.", "financial_services", "market_infrastructure", True),
    ("Deutsche Börse AG", "financial_services", "market_infrastructure", True),
    ("Versicherungskammer Bayern", "financial_services", "insurance", True),
    ("Powszechny Zakład Ubezpieczeń S.A.", "financial_services", "insurance", True),
    ("Gjensidige Forsikring ASA", "financial_services", "insurance", True),
    ("Länsförsäkringar AB", "financial_services", "insurance", True),
    ("Border to Coast Pensions Partnership Ltd", "financial_services", "pension", True),
    ("Fjärde AP-fonden", "financial_services", "pension", True),
    ("Finanz Informatik GmbH & Co. KG", "financial_services", "financial_software", True),
    # public bodies that only look financial
    ("Techniker Krankenkasse", "not_icp", "social_insurance", True),
    ("AOK Nordost - Die Gesundheitskasse", "not_icp", "social_insurance", True),
    ("Deutsche Rentenversicherung Bund", "not_icp", "social_insurance", True),
    ("Zakład Ubezpieczeń Społecznych", "not_icp", "social_insurance", True),
    ("Kasa Rolniczego Ubezpieczenia Społecznego", "not_icp", "social_insurance", True),
    ("Försäkringskassan", "not_icp", "social_insurance", True),
    ("Sociale Verzekeringsbank", "not_icp", "social_insurance", True),
    ("Department for Work and Pensions", "not_icp", "social_insurance", True),
    ("Ministerstwo Finansów", "not_icp", "public_finance", True),
    ("Centrum Informatyki Resortu Finansów", "not_icp", "public_finance", True),
    ("Technische Universität Dresden, Dezernat Finanzen und Beschaffung", "not_icp", "public_finance", True),
    ("Skatteverket", "not_icp", "public_finance", True),
    ("HM Revenue and Customs", "not_icp", "public_finance", True),
    ("Rural Payments Agency", "not_icp", "public_finance", True),
    ("IT-Allianz Nord AöR", "not_icp", "public_body", True),
    ("UK Biobank Limited", "not_icp", "company", False),                    # not a bank; a company, so the model decides
    ("Regional Blood Bank Service", "not_icp", "public_body", True),
    ("Stadt Beispiel", "not_icp", "public_body", True),
    ("Gmina Miasto Gdynia", "not_icp", "public_body", True),
    ("Example Borough Council", "not_icp", "public_body", True),
    ("Vilniaus miesto savivaldybė", "not_icp", "public_body", True),
    ("Universitätsklinikum Hamburg-Eppendorf", "not_icp", "public_body", True),
    # the rules cannot tell: the model decides when it summarises the notice
    ("Liseberg AB", "not_icp", "company", False),
    ("Amt für Recht, Vergabe und Versicherungen", "financial_services", "insurance", False),
    ("Allegro.eu marketplace", "other_icp", "digital_commerce", False),
])
def test_buyer_sector_rules(name, sector, kind, sure):
    s = sec.classify(name)
    assert (s.sector, s.kind, s.sure) == (sector, kind, sure), s


def test_central_and_development_banks_and_supervisors_are_flagged_public():
    assert sec.classify("Narodowy Bank Polski").public and sec.classify("Bank Gospodarstwa Krajowego").public
    assert sec.classify("Financial Conduct Authority").public
    assert not sec.classify("Kreissparkasse Köln").public


def test_linked_account_cpv_and_the_model_decide_what_the_name_cannot():
    # the linked company's segment on our lists, unless it is a public body or out of scope
    assert (sec.classify("Nordic Holding AB", segment="Insurance").sector, sec.classify("Nordic Holding AB", segment="Insurance").source) == ("financial_services", "account")
    assert sec.classify("Nordic Holding AB", segment="Banking", list_eligibility="Out of scope").source == "rules"
    assert sec.classify("Nordic Holding AB", list_segment="Enterprise", segment="Enterprise Software").sector == "other_icp"
    assert sec.classify("Zakład Ubezpieczeń Społecznych", segment="Insurance").sector == "not_icp"          # a wrong account link never makes ZUS a bank
    # a CPV 66 (financial services) code is a hint, not a decision
    assert not sec.classify("Achilles Information", cpv=["72000000", "66100000"]).sure
    # the model's answer counts only where the rules are not sure
    m = sec.classify("Liseberg AB", model_sector="bank")
    assert (m.sector, m.kind, m.source, m.sure) == ("banking", "bank", "model", True)
    assert sec.classify("Liseberg AB", model_sector="public_sector").sector == "not_icp"
    assert sec.classify("Stadt Beispiel", model_sector="bank").sector == "not_icp"                         # sure rules win
    assert sec.classify("Liseberg AB", model_sector="nonsense").sure is False


def test_migration_adds_the_sector_columns_and_runs_twice(conn, env):
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    cols = {r["column_name"] for r in conn.execute("select column_name from information_schema.columns where table_name='procurement_notices'")}
    assert {"buyer_sector", "buyer_kind", "buyer_public", "sector_sure", "sector_source", "sector_why", "sector_basis"} <= cols
    with pytest.raises(Exception):
        conn.execute("insert into procurement_notices(source_id, notice_id, status, buyer_sector) values ('procurement-ted','x','open','fintech')")


# ------------------------------------------------------------------------------------------------ euros
def test_values_are_converted_to_euros_at_the_fixed_dated_rates():
    assert tenders.EUR_RATES_DATE == "2026-11-25" and set(tenders.EUR_PER) >= {"SEK", "NOK", "DKK", "PLN", "GBP", "CHF", "CZK", "HUF", "RON", "BGN", "ISK", "USD"}
    assert tenders.value_eur(4_200_000, "SEK") == round(4_200_000 / 11.2385)            # about €373,700
    assert tenders.value_eur(9_800_000, "PLN") == round(9_800_000 / 4.48)
    assert tenders.value_eur(650_000, "GBP") == round(650_000 / 0.8373)
    assert tenders.value_eur(1_000_000, "eur") == 1_000_000
    assert tenders.value_eur(1_000_000, None) is None and tenders.value_eur(1_000_000, "JPY") is None and tenders.value_eur(None, "SEK") is None
    assert tenders.notice_value("procurement-ted", None, {"value": 5_000_000.0, "currency": "DKK", "value_basis": "lots_total"}) == (5_000_000.0, "DKK", "lots_total")
    assert tenders.notice_value("procurement-uk-fts", 200_000, None) == (200_000.0, "GBP", "reported")


# ------------------------------------------------------------------------------------------------ the list
def test_default_view_is_our_icp_banks_first_then_financial_services_then_other_icp(world):
    c, h = world["c"], world["h"]
    with world["app"].state.db.tx() as conn:
        city = notice(conn, "1-2026", "Stadt Beispiel", days=5, value=None, detail={"from": "ted", "value": 2_000_000.0, "currency": "EUR"})
        insurer = notice(conn, "2-2026", "Versicherungskammer Bayern", days=6, detail={"from": "ted", "value": 4_200_000.0, "currency": "SEK"})
        bank_late = notice(conn, "3-2026", "Kreissparkasse Köln", days=30, detail={"from": "ted", "value": 9_800_000.0, "currency": "PLN"})
        bank_soon = notice(conn, "4-2026", "Narodowy Bank Polski", days=10, country="POL")
        shop = notice(conn, "5-2026", "Allegro.eu marketplace", days=4, country="POL")
        uk = notice(conn, "ocds-1", "Financial Conduct Authority", days=12, country="GB", source="procurement-uk-fts", cpv=(), value=650_000)
        notice(conn, "6-2026", "Techniker Krankenkasse", days=7)
        notice(conn, "7-2026", "Gmina Miasto Gdynia", days=8, country="POL")
        # the model already said the marketplace is digital commerce
        conn.execute("update procurement_notices set summary=%s where id=%s", (json.dumps({"title_en": "Shop", "summary_en": "x", "buyer_sector": "digital_commerce", "v": 2}), shop))
    get = lambda qs="": c.get(f"/api/tenders?region=core&{qs}", headers=h("bdm")).json()      # noqa: E731
    out = get("sector=icp")
    assert [i["notice_id"] for i in out["items"]] == ["4-2026", "3-2026", "2-2026", "ocds-1", "5-2026"]
    assert [i["sector"] for i in out["items"]] == ["banking", "banking", "financial_services", "financial_services", "other_icp"]
    assert out["counts"]["by_sector"] == {"banking": 2, "financial_services": 2, "other_icp": 1, "not_icp": 3} and out["counts"]["icp"] == 5
    assert out["counts"]["not_icp"] == 3 and out["counts"]["total"] == 5
    nbp = out["items"][0]
    assert nbp["sector_kind"] == "central_bank" and nbp["sector_public"] is True and nbp["sector_sure"] is True and nbp["buyer_kind"] == "finance"
    assert next(i for i in out["items"] if i["notice_id"] == "5-2026")["sector_source"] == "model"
    # values in euros next to the original amount; the euro value sorts and filters
    ins = next(i for i in out["items"] if i["notice_id"] == "2-2026")
    assert (ins["value"], ins["currency"], ins["value_eur"]) == (4_200_000.0, "SEK", round(4_200_000 / 11.2385))
    assert next(i for i in out["items"] if i["notice_id"] == "ocds-1")["value_eur"] == round(650_000 / 0.8373)
    assert out["eur_rates"]["date"] == "2026-11-25" and out["eur_rates"]["per_eur"]["SEK"] == 11.2385
    assert [i["notice_id"] for i in get("sector=icp&sort=value")["items"]] == ["3-2026", "4-2026", "ocds-1", "2-2026", "5-2026"]     # within each sector
    assert [i["notice_id"] for i in get("sector=icp&min_value=500000")["items"]] == ["3-2026", "ocds-1"]                       # SEK 4.2M is about €374k
    assert [i["notice_id"] for i in get("sector=icp&min_value=1000000")["items"]] == ["3-2026"]
    # every public-sector notice is one click away, after the ICP ones; nothing is lost
    every = get()
    assert [i["sector"] for i in every["items"]][-3:] == ["not_icp"] * 3 and every["counts"]["total"] == 8
    assert [i["notice_id"] for i in get("sector=not_icp")["items"]] == ["1-2026", "6-2026", "7-2026"]
    assert [i["notice_id"] for i in get("sector=banking")["items"]] == ["4-2026", "3-2026"]
    assert [i["notice_id"] for i in get("sector=finance")["items"]] == ["4-2026", "3-2026", "2-2026", "ocds-1"]
    city_item = next(i for i in every["items"] if i["id"] == city)
    assert city_item["summary_state"] == "not_needed" and city_item["value_eur"] == 2_000_000
    assert next(i for i in every["items"] if i["id"] == bank_late)["summary_state"] == "waiting"
    assert c.get("/api/tenders?sector=fintech", headers=h("bdm")).status_code == 422
    assert insurer and bank_soon and uk


def test_the_worth_it_facts_come_from_the_official_fields_then_the_summary(world):
    c, h = world["c"], world["h"]
    detail = {"from": "ted", "v": 2, "value": 1_200_000.0, "currency": "EUR", "lot_count": 2, "lots": ["Lot 1 core banking", "Lot 2 support"],
              "duration": "36 month", "procedure": "open", "framework": True, "languages": ["de", "en"], "place": {"city": "Köln", "country": "DE"},
              "award": {"price": 30, "quality": 70}, "renewals": 2, "start": "2027-03-01", "end": "2030-02-28",
              "contact": {"name": "Einkauf", "email": "einkauf@ksk-koeln.de"}}
    with world["app"].state.db.tx() as conn:
        notice(conn, "1-2026", "Kreissparkasse Köln", detail=detail,
               summary={"title_en": "Core banking", "summary_en": "x", "v": 2, "on_site": "partly", "renewal": True, "incumbent": "Legacy Bank System", "contract_months": 12})
        notice(conn, "ocds-2", "Nationwide Building Society", country="GB", source="procurement-uk-contracts-finder", cpv=(), value=400_000,
               summary={"title_en": "App", "summary_en": "x", "v": 2, "lots": 3, "contract_months": 24, "framework": False, "price_weight": 40, "languages": ["en"]})
    items = {i["notice_id"]: i for i in c.get("/api/tenders?sector=icp", headers=h("bdm")).json()["items"]}
    w = items["1-2026"]["worth"]
    assert w["lots"] == 2 and w["lot_titles"] == ["Lot 1 core banking", "Lot 2 support"] and w["duration"] == "36 month" and "months" not in w
    assert (w["procedure"], w["framework"], w["languages"], w["place"], w["award"], w["renewals"]) == ("open", True, ["de", "en"], {"city": "Köln", "country": "DE"}, {"price": 30, "quality": 70}, 2)
    assert (w["start"], w["end"], w["contact"]["email"], w["on_site"], w["renewal"], w["incumbent"]) == ("2027-03-01", "2030-02-28", "einkauf@ksk-koeln.de", "partly", True, "Legacy Bank System")
    u = items["ocds-2"]["worth"]                                       # no official fields: the summary's facts
    assert (u["lots"], u["months"], u["framework"], u["award"], u["languages"]) == (3, 24, False, {"price": 40, "quality": 60}, ["en"])
    assert items["ocds-2"]["value_eur"] == round(400_000 / 0.8373)


# ------------------------------------------------------------------------------------------------ the summary job
def test_the_summary_job_only_spends_on_icp_notices_and_lets_the_model_decide_unclear_ones(conn, env):
    plenty_of_budget(conn)
    bank = notice(conn, "1-2026", "Kreissparkasse Köln", days=30)
    insurer = notice(conn, "2-2026", "Versicherungskammer Bayern", days=5)
    unclear = notice(conn, "3-2026", "Liseberg AB", days=6, country="SWE")
    city = notice(conn, "4-2026", "Stadt Beispiel", days=4)
    kk = notice(conn, "5-2026", "Techniker Krankenkasse", days=4)
    conn.commit()
    assert {str(r["id"]) for r in ts.pending(conn)} == set()            # not classified yet: nothing is due
    sec.classify_open(conn)
    assert [str(r["id"]) for r in ts.pending(conn)] == [bank, insurer, unclear]          # banks first, then financial services, then the unclear ones
    model = FakeModel()
    model_sector = {unclear: "public_sector"}

    async def call(system, user):
        assert "buyer_sector" in system
        text, tin, tout, m = await model(system, user)
        items = json.loads(text.strip("`json\n"))["items"]
        for i in items:
            if i["id"] in model_sector:
                i["buyer_sector"] = model_sector[i["id"]]
        return json.dumps({"items": items}), tin, tout, m
    out = ts.summarise(conn, call, run)
    assert out["summarised"] == 3 and out["waiting"] == 0
    seen = {i["id"] for b in model.batches for i in b}
    assert seen == {bank, insurer, unclear} and city not in seen and kk not in seen           # public bodies never reach the model
    rows = {str(r["id"]): r for r in conn.execute("select id, buyer_sector, sector_source, summary from procurement_notices")}
    assert rows[unclear]["buyer_sector"] == "not_icp" and rows[unclear]["sector_source"] == "model"
    assert rows[bank]["summary"]["v"] == ts.SUMMARY_VERSION and rows[city]["summary"] is None
    assert ts.summarise(conn, FakeModel(), run)["batches"] == 0                                # nothing left, and the unclear one is not asked again
    # a summary written before the facts were asked for is written again, for ICP notices only
    conn.execute("update procurement_notices set summary = summary - 'v' where id = any(%s::uuid[])", ([bank, unclear],))
    assert [str(r["id"]) for r in ts.pending(conn)] == [bank]


def test_the_model_answer_carries_the_sector_and_the_worth_it_facts():
    s = ts.clean_summary(answer({"id": "1", "title": "t"}, buyer_sector="Savings bank", buyer_note="Regional savings bank in Bavaria", buyer_note_pl="Regionalna kasa",
                                lots="3", contract_months=48.0, framework=True, price_weight=140, languages=["DE", "english", "pl"], on_site="Partly",
                                renewal=True, incumbent="none"))
    assert s["buyer_sector"] == "bank" and s["buyer_note"] == "Regional savings bank in Bavaria" and s["buyer_note_pl"] == "Regionalna kasa"
    assert (s["lots"], s["contract_months"], s["framework"], s["languages"], s["on_site"], s["renewal"]) == (3, 48, True, ["de", "pl"], "partly", True)
    assert "price_weight" not in s and "incumbent" not in s and s["v"] == ts.SUMMARY_VERSION
    assert "buyer_sector" not in ts.clean_summary(answer({"id": "1", "title": "t"}, buyer_sector="martian"))
    assert ts.clean_summary(answer({"id": "1", "title": "t"}, buyer_sector="regulator"))["buyer_sector"] == "financial_authority"


def test_ted_facts_are_read_from_the_official_fields():
    # the shape TED v3 returns for these fields (checked against the live API on 25 Nov 2026)
    n = {"publication-number": "792880-2026", "procedure-type": "open", "framework-agreement-lot": ["none", "fa-wo-rc"], "submission-language": ["SWE", "ENG", "SWE"],
         "place-of-performance-country-lot": ["SWE"], "place-of-performance-city-lot": ["Göteborg"],
         "award-criterion-type-lot": ["price", "quality", "quality"], "award-criterion-number-lot": ["60", "10", "30"],
         "award-criterion-number-weight-lot": ["per-exa", "per-exa", "per-exa"], "renewal-maximum-lot": ["0", "2"], "recurrence-lot": [False],
         "contract-duration-start-date-lot": ["2027-02-15+01:00"], "contract-duration-end-date-lot": ["2029-02-14+01:00", "2031-02-14+01:00"],
         "buyer-contact-point": ["Upphandling"], "buyer-email": ["inkop@example.se", "inkop@example.se"], "buyer-partname": ["_Upphandlingsenheten"],
         "buyer-internet-address": ["https://example.se"], "buyer-legal-type": ["body-pl"],
         "estimated-value-lot": ["2800000", "1200000"], "estimated-value-cur-lot": ["SEK"]}
    d = ts.ted_detail(n)
    assert (d["procedure"], d["framework"], d["languages"], d["place"]) == ("open", True, ["sv", "en"], {"city": "Göteborg", "country": "SE"})
    assert d["award"] == {"price": 60, "quality": 40} and d["renewals"] == 2 and "recurring" not in d
    assert (d["start"], d["end"]) == ("2027-02-15", "2031-02-14") and d["contact"] == {"name": "Upphandling", "department": "_Upphandlingsenheten",
                                                                                       "email": "inkop@example.se", "web": "https://example.se"}
    assert (d["value"], d["currency"], d["value_basis"], d["legal_type"], d["v"]) == (4_000_000.0, "SEK", "lots_total", "body-pl", ts.DETAIL_VERSION)
    # weights that are not numbers to add up (order of importance) give the criteria without shares
    o = ts.ted_detail({**n, "award-criterion-number-weight-lot": ["ord-imp", "ord-imp", "ord-imp"]})
    assert o["award"] == {"price": None, "quality": None}
    assert all(k in ts.TED_DETAIL_FIELDS for k in ("procedure-type", "framework-agreement-lot", "submission-language", "award-criterion-number-lot"))
