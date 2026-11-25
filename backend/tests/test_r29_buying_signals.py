"""Buying signals (only the high-intent news at target companies) and the account list filters the redesigned list needs."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.ingest.process import finish_event
from ri.outbound.signal_campaigns import ANGLES
from ri.scoring import recompute_scores
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources.registry import seed_registry
from test_r4_crm_notify import mk_user

SECRET = "b" * 40
NOW = datetime.now(timezone.utc)

EVENTS = {
    # high intent by kind: funding raised for a stated build (COM-01)
    "funding": ("funding_announced", {"funding_stage": "series_b", "funding_use_stated": True}, "{} raises a Series B round to grow its payments engineering team"),
    # high intent by kind: a funded payments programme with named systems (PRG-06)
    "payments": ("programme_announced", {"programme_domain": "payments_rail", "named_systems": ["SEPA Instant"], "programme_status": "implementation"},
                 "{} starts a SEPA Instant programme"),
    # context only: a dated market launch (COM-03, demand tier D4, not on the high-intent list)
    "launch": ("market_launch", {"market_launch_dated": True}, "{} announces a launch in Germany next spring"),
}


def company(conn, name, *, country="GB", segment="Payments", eligibility="Eligible", priority=None, score=None, owner=None, employees=1500):
    oid = str(conn.execute(
        "insert into organizations(display_name, country_code, segment, employees_estimate, canonical_domain, entity_type, company_ownership, "
        "in_master_list, list_eligibility, list_priority, list_score) values (%s,%s,%s,%s,%s,'company','unknown',true,%s,%s,%s) returning id",
        (name, country, segment, employees, name.lower().replace(" ", "") + ".example", eligibility, priority, score)).fetchone()["id"])
    if owner is not None:
        crm = 300 + conn.execute("select count(*) n from crm_organizations").fetchone()["n"]
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (%s,%s,%s,%s,'linked')", (crm, oid, name, owner))
    return oid


def event(conn, oid, name, kind, *, days=10):
    etype, attrs, text = EVENTS[kind]
    text = text.format(name)
    when = NOW - timedelta(days=days)
    domain = name.lower().replace(" ", "") + ".example"
    key = f"{name}-{kind}-{days}"
    doc = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, published_at, content_text) "
                       "values ('verify-original-page',%s,'1',%s,%s,%s,%s) returning id",
                       (f"d-{key}", f"https://{domain}/{kind}", text, when, text)).fetchone()["id"]
    ev = conn.execute("insert into events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,"
                      "extraction_model_version,verification_status,independent_origin_group) values (%s,%s,%s,%s,%s,true,%s,%s,'[]','test','verified',%s) returning id",
                      (f"e-{key}", etype, oid, when, when, text, json.dumps({"source_tier": "S3", **attrs}), f"g-{key}")).fetchone()["id"]
    claim = conn.execute("insert into claims(subject,predicate,object_json,evidence_anchor,anchor_verified,claim_kind,status,source_document_id) "
                         "values (%s,'announced',%s,%s,true,'quote','supported',%s) returning id",
                         (name, json.dumps({"url": f"https://{domain}/{kind}"}), text, doc)).fetchone()["id"]
    conn.execute("insert into event_claims(event_id, claim_id) values (%s,%s)", (ev, claim))
    finish_event(conn, str(ev), now=NOW)
    return str(ev)


def person(conn, oid, name, email=None, suppressed=False):
    pid = conn.execute("insert into people(full_name, work_email, email_verification_status, suppressed) values (%s,%s,%s,%s) returning id",
                       (name, email, "Verified" if email else "Unknown", suppressed)).fetchone()["id"]
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, oid))


@pytest.fixture()
def world(conn, db_url):
    import_baseline(conn); sync_rule_versions(conn); seed_registry(conn)
    mk_user(conn, "bdm@x", "bdm", 555); mk_user(conn, "admin@x", "admin"); mk_user(conn, "mkt@x", "marketing")
    o = {
        "kestrel": company(conn, "Kestrel Pay", priority="P1", score=96),
        "fjord": company(conn, "Fjord Bank", country="NO", segment="Banking", priority="P2", score=70),
        "launch": company(conn, "Launch Insurer", country="DE", segment="Insurance", priority="P3", score=40),
        "outside": company(conn, "Outside Fintech", eligibility="Out of scope"),
        "theirs": company(conn, "Owned Elsewhere", owner=999),
        "stopped": company(conn, "Frozen Bank", segment="Banking"),
        "old": company(conn, "Old News Bank", segment="Banking"),
        "quiet": company(conn, "Quiet Bank", segment="Banking", priority="P1", score=99),
    }
    ev = {"kestrel": event(conn, o["kestrel"], "Kestrel Pay", "funding"),
          "kestrel_old": event(conn, o["kestrel"], "Kestrel Pay", "funding", days=40),
          "fjord": event(conn, o["fjord"], "Fjord Bank", "payments", days=3),
          "launch": event(conn, o["launch"], "Launch Insurer", "launch"),
          "outside": event(conn, o["outside"], "Outside Fintech", "funding"),
          "theirs": event(conn, o["theirs"], "Owned Elsewhere", "funding"),
          "stopped": event(conn, o["stopped"], "Frozen Bank", "funding"),
          "old": event(conn, o["old"], "Old News Bank", "payments", days=150)}
    person(conn, o["kestrel"], "Ada Clarke", "ada@kestrel.example")
    person(conn, o["kestrel"], "Ben Weber", "ben@kestrel.example", suppressed=True)
    person(conn, o["kestrel"], "Cat Lind")
    person(conn, o["quiet"], "Dan Nowak", "dan@quiet.example")
    recompute_scores(conn, NOW)
    conn.execute("update account_scores set stopped=true where organization_id=%s", (o["stopped"],))      # a hiring freeze, say
    conn.commit()
    db = Database(db_url, max_size=3)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(app), "h": hdr, "o": o, "ev": ev}
    db.close()


def _items(body):
    return [i for g in body["groups"] for i in g["items"]]


def test_only_high_intent_signals_at_target_companies(world):
    c, h, o = world["c"], world["h"], world["o"]
    r = c.get("/api/buying-signals", headers=h("bdm"))
    assert r.status_code == 200
    body = r.json()
    names = {i["organization"]["name"] for i in _items(body)}
    assert names == {"Kestrel Pay", "Fjord Bank"}
    # context-only news, companies outside the target market, another seller's account, a stopped account and stale news stay out
    assert not names & {"Launch Insurer", "Outside Fintech", "Owned Elsewhere", "Frozen Bank", "Old News Bank"}
    assert body["totals"] == {"signals": 2, "companies": 2, "reachable": 1, "fresh": 2, "held_back": 1}

    groups = {g["rule_id"]: g for g in body["groups"]}
    assert set(groups) == {"COM-01", "PRG-06"}
    funding = groups["COM-01"]
    assert funding["meaning"] == ANGLES["COM-01"] and funding["companies"] == 1 and funding["reachable"] == 1
    assert funding["who_to_contact"]
    item = funding["items"][0]
    assert item["more"] == 1                          # the older funding news at the same company is folded into one row
    assert item["intent"] == "business" and item["demand_tier"] == "D4"
    assert item["summary"].startswith("Kestrel Pay raises a Series B")
    assert item["evidence"]["url"] == "https://kestrelpay.example/funding"
    assert item["evidence"]["quote"] == item["summary"]
    assert item["organization"]["people"] == 3 and item["organization"]["people_with_email"] == 1      # the opted-out person does not count
    assert item["organization"]["list_priority"] == "P1" and item["organization"]["list_score"] == 96.0
    assert item["age_days"] == 10
    assert groups["PRG-06"]["items"][0]["intent"] == "programme"


def test_buying_signals_filters_scope_and_permissions(world):
    c, h = world["c"], world["h"]
    names = lambda q, who="bdm": {i["organization"]["name"] for i in _items(c.get(f"/api/buying-signals?{q}", headers=h(who)).json())}      # noqa: E731
    assert names("region=nordics") == {"Fjord Bank"}
    assert names("rule=COM-01") == {"Kestrel Pay"}
    assert names("q=sepa") == {"Fjord Bank"}
    assert names("days=7") == {"Fjord Bank"}
    assert names("days=365") == {"Kestrel Pay", "Fjord Bank", "Old News Bank"}
    assert "Owned Elsewhere" in names("", who="admin")             # the scope follows account ownership, as everywhere else
    assert c.get("/api/buying-signals", headers=h("mkt")).status_code == 403
    assert c.get("/api/buying-signals?region=mars", headers=h("bdm")).status_code == 422
    assert c.get("/api/buying-signals?days=3", headers=h("bdm")).status_code == 422


def test_account_list_filters_for_priority_email_and_verified_signal(world):
    c, h, o = world["c"], world["h"], world["o"]
    get = lambda q: c.get(f"/api/leads?scope=target&{q}", headers=h("bdm")).json()      # noqa: E731
    names = lambda q: [i["name"] for i in get(q)["items"]]      # noqa: E731
    everything = get("")
    assert everything["facets"]["priority"] == {"P1": 2, "P2": 1, "P3": 1, "none": 2}      # the account another seller owns is not visible
    assert everything["facets"]["has_email"] == 2
    assert everything["facets"]["has_signal"] == 5         # every visible company with a checked signal, high intent or not, stopped or not
    assert set(names("priority=P1")) == {"Kestrel Pay", "Quiet Bank"}
    assert set(names("has_email=true")) == {"Kestrel Pay", "Quiet Bank"}
    assert set(names("has_signal=true")) == {"Kestrel Pay", "Fjord Bank", "Launch Insurer", "Frozen Bank", "Old News Bank"}
    assert set(names("has_signal=true&has_email=true&priority=P1")) == {"Kestrel Pay"}
    assert set(names("segment=Banking")) == {"Fjord Bank", "Frozen Bank", "Old News Bank", "Quiet Bank"}
    kestrel = next(i for i in everything["items"] if i["name"] == "Kestrel Pay")
    assert kestrel["has_signal"] is True and kestrel["people_with_email"] == 1 and kestrel["buying_signal"]["rule_id"] == "COM-01"
    # the lead list's own ranking, across regions when asked for one flat list
    assert names("sort=priority&by_region=false")[:4] == ["Quiet Bank", "Kestrel Pay", "Fjord Bank", "Launch Insurer"]
    assert names("sort=list_score&by_region=false")[:2] == ["Quiet Bank", "Kestrel Pay"]
    assert names("sort=name&by_region=false") == sorted(names("sort=name&by_region=false"), key=str.lower)
    # without by_region the list keeps its region-by-region order, as before
    assert names("sort=name")[-1] == "Fjord Bank" and names("sort=name")[-2] == "Launch Insurer"
    assert c.get("/api/leads?priority=P4", headers=h("bdm")).status_code == 422
    assert c.get("/api/leads?sort=bogus", headers=h("bdm")).status_code == 422
    export = c.get("/api/leads.csv?scope=target&priority=P1&by_region=false&sort=priority", headers=h("bdm")).text
    rows = list(csv.reader(io.StringIO(export)))
    assert rows[0][-4:] == ["List priority", "List score", "People", "People with email"]
    assert [r[0] for r in rows[1:]] == ["Quiet Bank", "Kestrel Pay"]
    assert rows[2][-4:] == ["P1", "96.0", "3", "1"]
