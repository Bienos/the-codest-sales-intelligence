"""Cold email rules by country (rules/email_rules.yaml): nobody at a company in a consent country is added to a Lemlist campaign unless consent
is recorded, a country the file does not list is held back, and the same rule holds at request time, at push time and in the counts.
Company and person names are invented; nothing here calls Lemlist."""
import pytest
from test_r14_outbound import STEPS, FakeLemlist, S, approve
from test_r23_people_campaigns import company, market, person, principal  # noqa: F401  (pytest fixture)

from ri import approvals as appr
from ri.db import qa
from ri.outbound import email_rules as er
from ri.outbound import enrol
from ri.outbound import signal_campaigns as sc


# ---------------------------------------------------------------- the rules file
def test_the_rules_file_loads_and_every_country_has_a_rule_a_note_and_a_source():
    r = er.load()
    assert {"GB", "SE", "DE", "AT", "CH", "DK", "NL", "PL", "FI", "NO"} <= set(r.countries)          # "NO" stays Norway, not YAML's false
    for code, c in r.countries.items():
        assert c["rule"] in er.RULES and c["name"] and c["note"] and c["name_pl"] and c["note_pl"], code
        assert all(s.startswith("https://") for s in c["sources"]), code
    assert {k for k, v in r.countries.items() if v["rule"] == "allowed"} == {"GB", "SE"}
    assert r.instead and r.instead_pl and "info" in r.generic


def test_a_broken_entry_is_an_error_not_skipped():
    ok = {"rule": "consent", "name": "X", "note": "n", "sources": ["https://example.org/x"]}
    assert er.parse({"countries": {"de": ok}}).countries["DE"]["rule"] == "consent"                  # codes are compared in capitals
    for bad in ({"rule": "maybe"}, {"sources": []}, {"sources": ["http://example.org"]}, {"note": ""}):
        with pytest.raises(ValueError):
            er.parse({"countries": {"DE": {**ok, **bad}}})
    with pytest.raises(ValueError):
        er.parse({"countries": {"Germany": ok}})


def test_the_rule_by_country_address_and_consent():
    assert er.check("GB", "ada@bank.example", None) == (True, None)
    assert er.check("se", "ada@bank.example", None) == (True, None)
    assert er.check("DE", "ada@bank.example", None) == (False, "needs_consent")
    assert er.check("PL", "ada@bank.example", None) == (False, "needs_consent")
    assert er.check("DE", "ada@bank.example", "2026-11-27T10:00:00Z") == (True, None)                # recorded consent lifts the rule
    assert er.check("FI", "info@bank.example", None) == (True, None)                                 # a generic role address in Finland
    assert er.check("FI", "ada.berg@bank.example", None) == (False, "needs_consent")
    assert er.check("FR", "ada@bank.example", None) == (False, "not_checked")                        # not in the file: held back
    assert er.check(None, "ada@bank.example", None) == (False, "not_checked")                        # no country on the lead list
    assert er.check("FR", "ada@bank.example", "2026-11-27") == (True, None)
    assert er.rule_for("fr")["rule"] == "not_checked" and "rules/email_rules.yaml" in er.rule_for("FR")["note"]
    assert "Germany" in er.blocked_reason(er.rule_for("DE"), 2) and "LinkedIn" in er.blocked_reason(er.rule_for("DE"), 2)


# ---------------------------------------------------------------- one account, one sequence
def _org(conn, country, people):
    org = str(conn.execute("insert into organizations(display_name, country_code) values (%s,%s) returning id", (f"Synthetic {country or 'Nowhere'} Bank", country)).fetchone()["id"])
    ids = []
    for name, email in people:
        pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values (%s,%s,'Verified') returning id", (name, email)).fetchone()["id"])
        conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, org))
        ids.append(pid)
    did = str(conn.execute("insert into campaign_drafts(organization_id, requested_by, status, steps) values (%s,'seller@x','approved',%s::jsonb) returning id",
                           (org, __import__("json").dumps(STEPS))).fetchone()["id"])
    conn.commit()
    return org, ids, did


def test_people_at_a_consent_country_company_are_held_back_until_consent_is_recorded(conn):
    org, (ada, info), did = _org(conn, "DE", [("Ada Berg", "ada.berg@rhein.example"), ("Info Desk", "info@rhein.example")])
    plan = enrol.plan_contacts(conn, org)
    assert plan["eligible"] == [] and {c["id"] for c in plan["consent_needed"]} == {ada, info}       # Germany: a generic address needs consent too
    assert plan["email_rule"]["rule"] == "consent" and plan["email_rule"]["name"] == "Germany" and plan["consent_needed"][0]["reason"] == "needs_consent"
    with pytest.raises(appr.ApprovalError, match="Germany needs the person's prior consent"):
        enrol.request_enrolment(conn, did, [ada], by="seller@x", requester_crm_user=None)
    conn.execute("update people set email_consent_at=now(), email_consent_by='seller@x', email_consent_note='Agreed at a meetup' where id=%s", (ada,))
    conn.commit()
    plan = enrol.plan_contacts(conn, org)
    assert [c["id"] for c in plan["eligible"]] == [ada] and [c["id"] for c in plan["consent_needed"]] == [info]
    assert enrol.request_enrolment(conn, did, [ada], by="seller@x", requester_crm_user=None)["contacts"] == 1


def test_a_finnish_role_address_may_be_used_and_a_personal_one_may_not(conn):
    org, (ada, info), _ = _org(conn, "FI", [("Ada Berg", "ada.berg@helsinki.example"), ("Sales Desk", "sales@helsinki.example")])
    plan = enrol.plan_contacts(conn, org)
    assert [c["id"] for c in plan["eligible"]] == [info] and [c["id"] for c in plan["consent_needed"]] == [ada]


def test_a_company_without_a_country_or_in_an_unlisted_country_is_held_back(conn):
    for country in (None, "FR"):
        org, ids, did = _org(conn, country, [("Ada Berg", f"ada@{country or 'none'}.example")])
        plan = enrol.plan_contacts(conn, org)
        assert plan["eligible"] == [] and plan["consent_needed"][0]["reason"] == "not_checked" and plan["email_rule"]["rule"] == "not_checked"
        with pytest.raises(appr.ApprovalError, match="rules"):
            enrol.request_enrolment(conn, did, ids, by="seller@x", requester_crm_user=None)


def test_the_rule_is_checked_again_at_push_time(conn):
    """Approved for a UK company; the company's country is then corrected to Germany. The push sends nobody."""
    org, (ada,), did = _org(conn, "GB", [("Ada Berg", "ada@thames.example")])
    r = enrol.request_enrolment(conn, did, [ada], by="seller@x", requester_crm_user=None)
    approve(conn, r["approval_id"])
    conn.execute("update organizations set country_code='DE' where id=%s", (org,))
    conn.commit()
    fake = FakeLemlist()
    with pytest.raises(appr.ApprovalError, match="no approved contact is still eligible"):
        enrol.execute_enrolment(conn, r["approval_id"], fake, S(outbound_enabled=True))
    assert not [c for c in fake.calls if c[0] == "lead"] and not qa(conn, "select 1 from outbound_enrolments")


def test_the_sql_count_agrees_with_the_rule(conn):
    rows = [("GB", "a@x.example", False), ("DE", "b@x.example", False), ("DE", "c@x.example", True), ("FI", "info@x.example", False),
            ("FI", "d.e@x.example", False), ("FR", "f@x.example", False), (None, "g@x.example", True), ("se", "h@x.example", False)]
    for i, (country, email, consent) in enumerate(rows):
        org = str(conn.execute("insert into organizations(display_name, country_code) values (%s,%s) returning id", (f"Count Co {i}", country)).fetchone()["id"])
        pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status, email_consent_at) values (%s,%s,'Verified',%s) returning id",
                               (f"P {i}", email, "2026-11-27" if consent else None)).fetchone()["id"])
        conn.execute("insert into employment(person_id, organization_id) values (%s,%s)", (pid, org))
    got = {r["work_email"] for r in qa(conn, f"select pe.work_email from people pe join employment e on e.person_id=pe.id join organizations o on o.id=e.organization_id "
                                             f"where {er.consent_sql('pe', 'o')}")}           # noqa: S608
    want = {email for country, email, consent in rows if er.check(country, email, "x" if consent else None)[0]}
    assert got == want == {"a@x.example", "c@x.example", "info@x.example", "g@x.example", "h@x.example"}


# ---------------------------------------------------------------- signal campaigns and the person page
def test_a_signal_campaign_leaves_out_a_german_account_with_the_reason(conn, market):  # noqa: F811
    c, h = market["c"], market["h"]
    de = company(conn, "Rheinwerk Pay", domain="rheinwerk.example", country="DE")
    pid = person(conn, de, "Uta Kern", "Chief Technology Officer", "uta@rheinwerk.example")
    conn.commit()
    d = c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01"], "per_account": 2}, headers=h("bdm")).json()
    item = next(i for i in d["items"] if i["company"] == "Rheinwerk Pay")
    assert item["people"] == [] and "Germany needs the person's prior consent" in item["reason"]
    opts = {o["rule_id"]: o for o in sc.signal_options(conn, principal(conn, "bdm@x"))}
    before = opts["COM-01"]["reachable_accounts"]
    conn.execute("update people set email_consent_at=now() where id=%s", (pid,))
    conn.commit()
    d = c.post("/api/signal-campaigns/preview", json={"rule_ids": ["COM-01"], "per_account": 2}, headers=h("bdm")).json()
    assert [p["name"] for p in next(i for i in d["items"] if i["company"] == "Rheinwerk Pay")["people"]] == ["Uta Kern"]
    assert {o["rule_id"]: o for o in sc.signal_options(conn, principal(conn, "bdm@x"))}["COM-01"]["reachable_accounts"] == before + 1


def test_a_seller_records_and_withdraws_consent_on_the_person_page(conn, market):  # noqa: F811
    c, h = market["c"], market["h"]
    de = company(conn, "Isar Pay", domain="isar.example", country="DE")
    pid = person(conn, de, "Uta Kern", "Chief Technology Officer", "uta@isar.example")
    conn.commit()
    v = c.get(f"/api/people/{pid}/email-rule", headers=h("bdm")).json()
    assert (v["may_email"], v["reason"], v["consent"], v["rule"]["rule"], v["rule"]["name"]) == (False, "needs_consent", None, "consent", "Germany")
    assert "@" not in c.get(f"/api/people/{pid}/email-rule", headers=h("bdm")).text                  # the address is never returned
    assert c.post(f"/api/people/{pid}/email-consent", json={"note": ""}, headers=h("bdm")).status_code == 422
    v = c.post(f"/api/people/{pid}/email-consent", json={"note": "Agreed by email after the supplier day on 12 March"}, headers=h("bdm")).json()
    assert v["may_email"] and v["consent"]["by"] == "bdm@x" and v["consent"]["note"].startswith("Agreed by email")
    assert qa(conn, "select operation from audit_events where record_id=%s and operation like 'email_consent%%'", (pid,))[0]["operation"] == "email_consent_recorded"
    v = c.delete(f"/api/people/{pid}/email-consent", headers=h("bdm")).json()
    assert not v["may_email"] and v["consent"] is None
    assert {r["operation"] for r in qa(conn, "select operation from audit_events where record_id=%s", (pid,))} >= {"email_consent_recorded", "email_consent_withdrawn"}
    # another seller's account: not found, and nothing changes
    theirs = market["p"]["their"]
    assert c.get(f"/api/people/{theirs}/email-rule", headers=h("bdm")).status_code == 404
    assert c.post(f"/api/people/{theirs}/email-consent", json={"note": "agreed on a call"}, headers=h("bdm")).status_code == 404
    assert c.post(f"/api/people/{pid}/email-consent", json={"note": "agreed on a call"}).status_code in (401, 403)
