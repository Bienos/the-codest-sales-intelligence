"""People database (sheets + Apollo + Pipedrive + Lemlist in one list) and campaigns built from buying signals."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri import approvals as appr
from ri.access import principal_from_row
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.crm import pipedrive as pd
from ri.db import Database, q1
from ri.ingest.process import finish_event
from ri.outbound import signal_campaigns as sc
from ri.people import apply_lemlist_activity, sync_lemlist
from ri.scoring import recompute_scores
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions
from ri.sources.registry import seed_registry
from test_r14_outbound import FakeLemlist
from test_r4_crm_notify import mk_user

SECRET = "s" * 40
NOW = datetime.now(timezone.utc)


def settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72, **kw)


def company(conn, name, *, domain, country="GB", owner=None, open_deal=False, signal="funding", days=10):
    oid = str(conn.execute("insert into organizations(display_name, country_code, segment, employees_estimate, canonical_domain, entity_type, company_ownership) "
                           "values (%s,%s,'Payments',1500,%s,'company','private') returning id", (name, country, domain)).fetchone()["id"])
    if owner is not None or open_deal:
        crm = 100 + conn.execute("select count(*) n from crm_organizations").fetchone()["n"]
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, open_deals_count, link_status) values (%s,%s,%s,%s,%s,'linked')",
                     (crm, oid, name, owner, 1 if open_deal else 0))
    etype, attrs, text = {"funding": ("funding_announced", {"funding_stage": "series_b", "funding_use_stated": True}, f"{name} raises a Series B round to grow its payments engineering team"),
                          "payments": ("programme_announced", {"programme_domain": "payments_rail", "named_systems": ["SEPA Instant"], "programme_status": "implementation"},
                                       f"{name} starts a SEPA Instant programme")}[signal]
    when = NOW - timedelta(days=days)
    doc = conn.execute("insert into source_documents(source_id, external_id, version_key, canonical_url, title, published_at, content_text) values ('verify-original-page',%s,'1',%s,%s,%s,%s) returning id",
                       (f"d-{name}", f"https://{domain}/news", text, when, text)).fetchone()["id"]
    ev = conn.execute("insert into events(event_key,event_type,subject_org_id,pub_at,event_at,event_at_known,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                      "values (%s,%s,%s,%s,%s,true,%s,%s,'[]','test','verified',%s) returning id", (f"e-{name}", etype, oid, when, when, text, json.dumps({"source_tier": "S3", **attrs}), f"g-{name}")).fetchone()["id"]
    c = conn.execute("insert into claims(subject,predicate,object_json,evidence_anchor,anchor_verified,claim_kind,status,source_document_id) values (%s,'announced',%s,%s,true,'quote','supported',%s) returning id",
                     (name, json.dumps({"url": f"https://{domain}/news"}), text, doc)).fetchone()["id"]
    conn.execute("insert into event_claims(event_id, claim_id) values (%s,%s)", (ev, c))
    finish_event(conn, str(ev), now=NOW)
    return oid


def person(conn, oid, name, title, email=None, status="Verified", suppressed=False):
    pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status, suppressed) values (%s,%s,%s,%s) returning id",
                           (name, email, status if email else None, suppressed)).fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,%s)", (pid, oid, title))
    return pid


@pytest.fixture()
def market(conn, db_url):
    import_baseline(conn); sync_rule_versions(conn); seed_registry(conn)
    users = {"bdm": mk_user(conn, "bdm@x", "bdm", 555), "bdm2": mk_user(conn, "bdm2@x", "bdm", 666), "mgr": mk_user(conn, "mgr@x", "sales_manager", 700),
             "admin": mk_user(conn, "admin@x", "admin")}
    o = {"open": company(conn, "Kestrel Pay", domain="kestrel.example"), "mine": company(conn, "Northbridge", domain="northbridge.example", owner=555, signal="payments"),
         "theirs": company(conn, "Harbour Ltd", domain="harbour.example", owner=666), "deal": company(conn, "Fenwick", domain="fenwick.example", open_deal=True),
         "nobody": company(conn, "Quiet Bank", domain="quiet.example"), "old": company(conn, "Old News", domain="old.example", days=200)}
    pp = {"cto": person(conn, o["open"], "Ada Clarke", "Chief Technology Officer", "ada@kestrel.example"),
          "eng": person(conn, o["open"], "Ben Weber", "Engineering Manager", "ben@kestrel.example"),
          "hr": person(conn, o["open"], "Cat Lind", "HR Manager", "cat@kestrel.example"),
          "pay": person(conn, o["mine"], "Dan Nowak", "Head of Payments", "dan@northbridge.example"),
          "opt": person(conn, o["mine"], "Eve Berg", "CIO", "eve@northbridge.example", suppressed=True),
          "their": person(conn, o["theirs"], "Fay Dahl", "CTO", "fay@harbour.example"),
          "dealp": person(conn, o["deal"], "Gus Moreau", "CTO", "gus@fenwick.example"),
          "unver": person(conn, o["nobody"], "Hal Sousa", "CTO", "hal@quiet.example", status="Needs Verification")}
    recompute_scores(conn, NOW)
    conn.commit()
    db = Database(db_url, max_size=3)
    app = create_app(settings(outbound_enabled=True), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(app), "h": hdr, "o": o, "p": pp, "users": users, "app": app}
    db.close()


def principal(conn, email):
    return principal_from_row(q1(conn, "select * from users where email=%s", (email,)))


# ------------------------------------------------------------------ people database
def test_pipedrive_people_link_by_email_or_join_the_linked_account(conn, market):
    pd.upsert_org(conn, {"id": 900, "name": "Kestrel Pay", "owner_id": 555, "website": "https://kestrel.example", "update_time": "2026-11-20T10:00:00Z"})
    pd.upsert_person(conn, {"id": 51, "name": "Ada Clarke", "org_id": 900, "owner_id": 555, "emails": [{"value": "ADA@kestrel.example", "primary": True}]})
    pd.upsert_person(conn, {"id": 52, "name": "New Person", "org_id": {"value": 900}, "owner_id": 555, "job_title": "VP Engineering", "emails": [{"value": "new@kestrel.example"}]})
    pd.upsert_person(conn, {"id": 53, "name": "No Company", "emails": [{"value": "x@gmail.com"}]})
    assert str(q1(conn, "select person_id from crm_persons where crm_id=51")["person_id"]) == market["p"]["cto"]          # same person, not a duplicate
    new = q1(conn, "select p.id, p.email_verification_status, e.organization_id, e.title from crm_persons c join people p on p.id=c.person_id join employment e on e.person_id=p.id where c.crm_id=52")
    assert str(new["organization_id"]) == market["o"]["open"] and new["title"] == "VP Engineering" and new["email_verification_status"] == "Unverified (CRM)"
    assert q1(conn, "select person_id from crm_persons where crm_id=53")["person_id"] is None                            # a free-mail address with no company is not placed anywhere
    assert {r["system"] for r in conn.execute("select system from person_sources where person_id=%s", (market["p"]["cto"],))} == {"pipedrive"}


def test_lemlist_activity_links_people_and_suppresses_unsubscribes_everywhere(conn, market):
    class Client:
        def __init__(self):
            self.pages = [[{"_id": "a1", "type": "emailsReplied", "leadEmail": "ben@kestrel.example", "campaignId": "c1", "campaignName": "Q3", "createdAt": "2026-11-20T10:00:00Z"},
                           {"_id": "a2", "type": "emailsUnsubscribed", "leadEmail": "cat@kestrel.example", "campaignId": "c1"},
                           {"_id": "a3", "type": "emailsOpened", "leadEmail": "zed@kestrel.example", "leadFirstName": "Zed", "campaignId": "c1"},
                           {"_id": "a4", "type": "emailsOpened", "leadEmail": "who@unknown-domain.example", "campaignId": "c1"}]]

        def activities(self, offset=0, limit=100):
            return self.pages[offset // limit] if offset // limit < len(self.pages) else []
    out = sync_lemlist(conn, Client())
    assert out["new"] == 4 and out["linked"] == 3
    assert q1(conn, "select suppressed, suppression_reason from people where id=%s", (market["p"]["hr"],))["suppressed"] is True
    zed = q1(conn, "select p.full_name, e.organization_id from people p join employment e on e.person_id=p.id where lower(p.work_email)='zed@kestrel.example'")
    assert zed["full_name"] == "Zed" and str(zed["organization_id"]) == market["o"]["open"]                                # placed by the email's company domain
    assert sync_lemlist(conn, Client())["new"] == 0                                                                        # replays change nothing
    assert apply_lemlist_activity(conn, {"type": "emailsSendFailed", "leadEmail": "ben@kestrel.example"})["stored"] is False


def test_people_api_unifies_sources_signals_and_never_returns_addresses(conn, market):
    c, h = market["c"], market["h"]
    conn.execute("insert into contact_candidates(organization_id, provider, external_id, name, person_id, found_by) values (%s,'apollo','ap1','Ben Weber',%s,'bdm@x')",
                 (market["o"]["open"], market["p"]["eng"]))
    conn.execute("insert into lemlist_activities(id, type, email, person_id, happened_at) values ('x1','emailsReplied','ben@kestrel.example',%s,now())", (market["p"]["eng"],))
    conn.commit()
    r = c.get("/api/people?q=kestrel", headers=h("bdm"))
    assert r.status_code == 200 and "@" not in r.text
    ben = next(i for i in r.json()["items"] if i["name"] == "Ben Weber")
    assert set(ben["sources"]) == {"apollo", "lemlist"} and ben["engagement"] == "replied" and ben["signal"]["rule_id"] == "COM-01" and ben["email"] == "verified"
    names = lambda who, q="": {i["name"] for i in c.get(f"/api/people?size=200&q={q}", headers=h(who)).json()["items"]}  # noqa: E731
    assert "Fay Dahl" not in names("bdm") and "Fay Dahl" in names("bdm2")                                                # another seller's account stays hidden
    assert "Eve Berg" not in names("bdm")                                                                                # suppressed people are hidden unless asked for
    assert c.get("/api/people", headers=h("admin")).status_code == 200                                                    # administrators see the people database too
    assert c.get("/api/people", headers=h("mkt")).status_code == 403                                                      # contact data needs contacts.read
    assert c.get("/api/people?engagement=replied", headers=h("bdm")).json()["total"] == 1
    assert c.post("/api/people/sync/pipedrive", headers=h("bdm")).status_code == 403


# ------------------------------------------------------------------ signal campaigns
def test_audience_is_built_from_live_signals_and_explains_every_exclusion(conn, market):
    p = principal(conn, "bdm@x")
    opts = {o["rule_id"]: o for o in sc.signal_options(conn, p, 90)}
    assert opts["COM-01"]["accounts"] == 3 and opts["PRG-06"]["accounts"] == 1          # Kestrel, Fenwick, Quiet (Harbour is another seller's; Old News is too old)
    cid = str(conn.execute("insert into signal_campaigns(name, rule_ids, filters, steps, created_by) values ('t', %s, %s, %s, 'bdm@x') returning id",
                           (["COM-01", "PRG-06"], json.dumps({"days": 90, "per_account": 2}), json.dumps(sc.DEFAULT_STEPS))).fetchone()["id"])
    stats = sc.build_audience(conn, cid, p)
    rows = conn.execute("select m.included, m.exclusion, o.display_name, pe.full_name from signal_campaign_members m join organizations o on o.id=m.organization_id "
                        "left join people pe on pe.id=m.person_id where m.campaign_id=%s", (cid,)).fetchall()
    included = {r["full_name"] for r in rows if r["included"]}
    assert included == {"Ada Clarke", "Ben Weber", "Dan Nowak"}              # technology leaders first; HR manager and the opted-out CIO left out
    reasons = {r["display_name"]: r["exclusion"] for r in rows if not r["included"]}
    assert "open deal" in reasons["Fenwick"] and "No contact with a verified email" in reasons["Quiet Bank"]
    assert stats["accounts"] == 4 and "Harbour Ltd" not in reasons and "Old News" not in reasons
    review = sc.review_campaign(conn, cid)
    assert review["passed"], review
    hook = q1(conn, "select variables from signal_campaign_members where campaign_id=%s and person_id=%s", (cid, market["p"]["cto"]))["variables"]["signalHook"]
    assert "Kestrel Pay raises a Series B round" in hook


def test_signal_campaign_needs_a_manager_then_pushes_to_lemlist_once(conn, market):
    c, h = market["c"], market["h"]
    r = c.post("/api/signal-campaigns", json={"rule_ids": ["COM-01"], "per_account": 2}, headers=h("bdm"))
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert c.get(f"/api/signal-campaigns/{cid}", headers=h("bdm2")).status_code == 404                                   # another seller cannot open it
    d = c.get(f"/api/signal-campaigns/{cid}", headers=h("bdm")).json()
    assert d["campaign"]["review"]["passed"] and "Hello Ada" in d["preview"][0]["body"] and "@" not in json.dumps(d["members"])
    bad = c.put(f"/api/signal-campaigns/{cid}/steps", json={"steps": [{"day": 0, "subject": "Hi", "body": "We are world-class! Contact us."}]}, headers=h("bdm")).json()
    assert not bad["review"]["passed"]
    assert c.post(f"/api/signal-campaigns/{cid}/request-approval", headers=h("bdm")).status_code == 409                   # copy must pass the brand review
    ok = [{k: s[k] for k in ("day", "subject", "body")} | {"claims_used": s.get("claims_used", [])} for s in sc.DEFAULT_STEPS]
    assert c.put(f"/api/signal-campaigns/{cid}/steps", json={"steps": ok}, headers=h("bdm")).json()["review"]["passed"]
    req = c.post(f"/api/signal-campaigns/{cid}/request-approval", headers=h("bdm")).json()
    aid = req["approval_id"]
    assert req["members"] == 2 and req["accounts"] == 1
    assert c.post(f"/api/approvals/{aid}/decision", json={"approve": True}, headers=h("bdm2")).status_code == 403          # sellers cannot approve a bulk campaign
    assert c.post(f"/api/approvals/{aid}/decision", json={"approve": True}, headers=h("mgr")).status_code == 200
    assert c.get(f"/api/signal-campaigns/{cid}", headers=h("bdm")).json()["campaign"]["status"] == "approved"
    with pytest.raises(appr.ApprovalError):
        sc.execute_push(conn, aid, FakeLemlist(), settings(outbound_enabled=False))                                      # kill switch
    conn.rollback()
    conn.execute("update people set suppressed=true where id=%s", (market["p"]["eng"],))                                  # opted out after approval
    conn.commit()
    fake = FakeLemlist()
    fake.add_lead_fields = []
    orig = fake.add_lead
    fake.add_lead = lambda cid_, email, fields: (fake.add_lead_fields.append(fields), orig(cid_, email, fields))[1]
    out = sc.execute_push(conn, aid, fake, settings(outbound_enabled=True))
    conn.commit()
    assert out["leads"] == 1 and out["dropped_since_approval"] == 1
    assert fake.add_lead_fields[0]["firstName"] == "Ada" and "Series B" in fake.add_lead_fields[0]["signalHook"]
    assert [x[1] for x in fake.calls if x[0] == "step"][0] == "A question about {{companyName}}"
    with pytest.raises(appr.ApprovalError):
        sc.execute_push(conn, aid, FakeLemlist(), settings(outbound_enabled=True))                                       # single use
    conn.rollback()
    enrol = q1(conn, "select status, signal_campaign_id from outbound_enrolments where lower(email)='ada@kestrel.example'")
    assert str(enrol["signal_campaign_id"]) == cid
    apply_lemlist_activity(conn, {"_id": "r1", "type": "emailsReplied", "leadEmail": "ada@kestrel.example", "campaignId": "cam_1"})
    conn.commit()
    assert q1(conn, "select status from outbound_enrolments where lower(email)='ada@kestrel.example'")["status"] == "replied"
    lst = c.get("/api/signal-campaigns", headers=h("bdm")).json()["items"][0]
    assert lst["status"] == "pushed" and lst["replies"] == 1
