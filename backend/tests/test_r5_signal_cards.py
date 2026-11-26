"""Round 5: what a buying-signal card carries: a reason that matters to us (not an outreach opener), the people to talk to, and the tender's own facts."""
import json
from datetime import datetime, timedelta, timezone

from ri.api.buying_signals import DEFAULT_WHY, FAMILY_WHY, RULE_WHY, _notice_extras, _why
from ri.outbound.signal_campaigns import ANGLES
from test_r29_buying_signals import world  # noqa: F401  (the world fixture: companies, events, people and an app)
from test_r4_crm_notify import mk_user

NOW = datetime.now(timezone.utc)


def _by_name(body):
    return {c["organization"]["name"]: c for c in body["companies"]}


def test_every_signal_says_why_it_matters_in_plain_third_person(world):
    c, h = world["c"], world["h"]
    body = c.get("/api/buying-signals", headers=h("bdm")).json()
    companies = _by_name(body)
    for company in companies.values():
        for s in company["signals"]:
            assert s["why"] and s["why"] == RULE_WHY[s["rule_id"]]
            assert s["why"] != ANGLES[s["rule_id"]]               # not the outreach opener ("I would like to...")
            assert " I " not in f" {s['why']} " and not s["why"].startswith("If you")
            assert s["why_specific"] is False and s["why_pl"] is None


def test_why_prefers_the_events_own_reason_then_the_notice_then_the_kind():
    assert _why("COM-01", {"level": "strong", "service": "team_extension", "reason": "They named a payments build."}) == ("They named a payments build.", None, True)
    assert _why("COM-01", {"level": "none", "reason": "Nothing here."})[0] == RULE_WHY["COM-01"]
    assert _why("COM-01", None, {"fit_reason": "From the notice.", "fit_reason_pl": "Z ogłoszenia."}) == ("From the notice.", "Z ogłoszenia.", True)
    assert _why("PRG-99", None) == (FAMILY_WHY["PRG"], None, False)
    assert _why("ZZZ-01", None) == (DEFAULT_WHY, None, False)
    assert all(not t.startswith("If ") and " I " not in f" {t} " for t in [*RULE_WHY.values(), *FAMILY_WHY.values(), DEFAULT_WHY])


def test_companies_list_the_people_to_talk_to_without_any_address(world, conn):
    c, h, o = world["c"], world["h"], world["o"]
    r = c.get("/api/buying-signals", headers=h("bdm"))
    body = r.json()
    kestrel = _by_name(body)["Kestrel Pay"]["organization"]
    # the opted-out person is left out; the person with an email comes before the one without
    assert [p["name"] for p in kestrel["contacts"]] == ["Ada Clarke", "Cat Lind"]
    assert kestrel["contacts_total"] == 2
    ada, cat = kestrel["contacts"]
    assert ada["email"] is True and cat["email"] is False and ada["linkedin"] is False and ada["title"] == "CTO"
    assert set(ada) == {"id", "name", "title", "email", "linkedin"}
    assert "@kestrel.example" not in r.text and "ben@" not in r.text.lower() and "Ben Weber" not in r.text
    # a company nobody is on file for says so with an empty list, not a missing field
    fjord = _by_name(body)["Fjord Bank"]["organization"]
    assert fjord["contacts"] == [] and fjord["contacts_total"] == 0
    assert o["kestrel"] == kestrel["id"]


def test_the_best_title_for_the_signal_comes_first(world, conn):
    c, h, o = world["c"], world["h"], world["o"]
    for name, title, email in (("Pat Procurement", "Head of Procurement", None), ("Tia Tech", "Chief Technology Officer", None), ("Una Unknown", None, "una@fjord.example")):
        pid = conn.execute("insert into people(full_name, work_email, email_verification_status) values (%s,%s,'Unknown') returning id", (name, email)).fetchone()["id"]
        conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,%s)", (pid, o["fjord"], title))
    conn.commit()
    fjord = _by_name(c.get("/api/buying-signals", headers=h("bdm")).json())["Fjord Bank"]["organization"]
    assert [p["name"] for p in fjord["contacts"]] == ["Tia Tech", "Una Unknown", "Pat Procurement"]      # a tech title first for a payments programme, then people with an email
    assert fjord["contacts_total"] == 3


def test_people_without_permission_to_read_contacts_get_no_names(world, conn):
    c, h = world["c"], world["h"]
    mk_user(conn, "ra@x", "research_analyst")
    conn.commit()
    body = c.get("/api/buying-signals", headers=h("ra")).json()
    assert body["companies"]
    for company in body["companies"]:
        assert "contacts" not in company["organization"] and "contacts_total" not in company["organization"]
        assert "people_with_email" in company["organization"]               # the counts stay, as before


def test_a_tender_carries_what_its_notice_says(world, conn):
    o = world["o"]
    conn.execute("""INSERT INTO procurement_notices(source_id, notice_id, buyer_name, buyer_org_id, title, status, deadline_at, official_url, source_detail, summary)
                    VALUES ('procurement-ted', 'T-1', 'Fjord Bank ASA', %s, 'Norway - IT services - Utvikling av nettbank', 'open', %s, 'https://ted.example/T-1', %s, %s)""",
                 (o["fjord"], NOW + timedelta(days=20, hours=3), json.dumps({"value": 4200000, "currency": "NOK"}),
                  json.dumps({"title_en": "Online bank development", "title_pl": "Rozwój bankowości", "summary_en": "The bank wants a partner for its online bank.",
                              "summary_pl": "Bank szuka partnera.", "wants": ["A delivery team"], "wants_pl": ["Zespół"], "work_type": "software_development",
                              "fit": {"level": "strong", "reason": "A bank buying a delivery team.", "reason_pl": "Bank kupuje zespół."}})))
    conn.execute("""INSERT INTO procurement_notices(source_id, notice_id, buyer_name, buyer_org_id, title, status, deadline_at, official_url)
                    VALUES ('procurement-ted', 'T-2', 'Fjord Bank ASA', %s, 'Plain notice', 'open', %s, 'https://ted.example/T-2')""", (o["fjord"], NOW + timedelta(days=5)))
    conn.commit()
    tenders = [{"organization_id": o["fjord"], "tender": {"notice_id": "T-1", "title": "x"}}, {"organization_id": o["fjord"], "tender": {"notice_id": "T-2", "title": "y"}},
               {"organization_id": o["fjord"], "tender": None}]
    got = _notice_extras(conn, tenders, NOW)
    full = got[(o["fjord"], "T-1")]
    assert full["days_left"] == 20 and full["value"] == 4200000.0 and full["currency"] == "NOK" and full["buyer"] == "Fjord Bank ASA"
    assert full["title_en"] == "Online bank development" and full["summary_pl"] == "Bank szuka partnera." and full["wants"] == ["A delivery team"]
    assert full["fit_reason"] == "A bank buying a delivery team." and full["fit_reason_pl"] == "Bank kupuje zespół." and full["work_type"] == "software_development"
    assert got[(o["fjord"], "T-1")]["value_eur"] > 0
    bare = got[(o["fjord"], "T-2")]
    assert bare["days_left"] == 5 and "value" not in bare and "summary_en" not in bare and "fit_reason" not in bare       # nothing is made up
    assert _notice_extras(conn, [{"organization_id": o["fjord"], "tender": None}], NOW) == {}
    assert _why("SRC-01", None, got[(o["fjord"], "T-1")]) == ("A bank buying a delivery team.", "Bank kupuje zespół.", True)
    assert _why("SRC-01", None, bare)[0] == RULE_WHY["SRC-01"]


def test_the_strongest_new_signal_at_the_top_carries_the_same_reason(world):
    c, h = world["c"], world["h"]
    top = c.get("/api/buying-signals", headers=h("bdm")).json()["pulse"]["top"]
    assert top["signal"]["why"] == RULE_WHY[top["signal"]["rule_id"]] and top["signal"]["why_pl"] is None
