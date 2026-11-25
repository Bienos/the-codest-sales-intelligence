"""Company page and person page: Exa research profiles, finding decision makers with Exa, relationship and owner.
Exa and the model are FAKES (no network). What matters: only sourced statements survive, the Exa budget stops research cleanly,
people are de-duplicated before anyone is added, addresses never leave the API, and scope and permissions hold."""
import json

import pytest
from fastapi.testclient import TestClient

from ri import budget, usage
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, q1, qa
from ri.enrichment.exa_people import normalise_linkedin, parse_result
from ri.research import profile_jobs
from ri.research.company_profile import verify_profile
from ri.research.person_profile import verify_person
from ri.sources.base import AuthError, SchemaDrift
from test_r4_crm_notify import mk_user

SECRET = "s" * 40


def settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72, **kw)


def org(conn, name, domain, *, owner=None):
    oid = str(conn.execute("insert into organizations(display_name, canonical_domain, country_code, segment, employees_estimate, in_master_list) "
                           "values (%s,%s,'GB','Payments',1800,true) returning id", (name, domain)).fetchone()["id"])
    if owner is not None:
        crm = 100 + conn.execute("select count(*) n from crm_organizations").fetchone()["n"]
        conn.execute("insert into crm_organizations(crm_id, organization_id, name, owner_id, link_status) values (%s,%s,%s,%s,'linked')", (crm, oid, name, owner))
    return oid


def person(conn, oid, name, title, *, email=None, status="Verified", linkedin=None):
    pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status, linkedin_url) values (%s,%s,%s,%s) returning id",
                           (name, email, status if email else "unknown", linkedin)).fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,%s)", (pid, oid, title))
    return pid


def caps(conn, exa=1.0, tokens=1_000_000):
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), exa, "usd")
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), tokens, "tokens")
    conn.commit()


def fake_post(pages_by_purpose, calls, cost=0.012, fail=None):
    """Returns Exa-shaped answers; records every request body. `fail(body)` may raise to simulate Exa refusing."""
    def factory():
        async def post(body):
            calls.append(body)
            if fail:
                fail(body)
            key = next((k for k in pages_by_purpose if k in body["query"]), None)
            return {"results": pages_by_purpose.get(key, []), "costDollars": {"total": cost}}
        return post
    return factory


def fake_model(reply, seen=None):
    def factory():
        async def call(system, user):
            if seen is not None:
                seen.append(user)
            return json.dumps(reply), 1500, 400, "fake-model"
        return call
    return factory


@pytest.fixture()
def env(conn, db_url):
    users = {"bdm": mk_user(conn, "bdm@x", "bdm", 555), "bdm2": mk_user(conn, "bdm2@x", "bdm", 666), "mgr": mk_user(conn, "mgr@x", "sales_manager", 700),
             "analyst": mk_user(conn, "analyst@x", "research_analyst"), "comp": mk_user(conn, "comp@x", "compliance"), "admin": mk_user(conn, "admin@x", "admin")}
    o = {"open": org(conn, "Kestrel Pay Ltd", "kestrel.example"), "theirs": org(conn, "Harbour Bank", "harbour.example", owner=666)}
    p = {"ada": person(conn, o["open"], "Ada Clarke", "Chief Technology Officer", email="ada@kestrel.example", linkedin="https://www.linkedin.com/in/ada-clarke"),
         "dan": person(conn, o["open"], "Dan Nowak", "Head of Payments", email="dan@kestrel.example", status="Unverified"),
         "fay": person(conn, o["theirs"], "Fay Dahl", "CTO", email="fay@harbour.example")}
    conn.execute("insert into person_sources(person_id, system, external_id, detail) values (%s,'sheets','C1',%s)",
                 (p["ada"], json.dumps({"seniority": "Executive", "committee_role": "Technical sponsor candidate", "persona": "C-Level Technology", "priority": "P1",
                                        "angle": "Payments platform rebuild", "signal": ""})))
    conn.commit()
    db = Database(db_url, max_size=3)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    apps = {"plain": create_app(settings(), db=db),
            "ready": create_app(settings(exa_api_key="k", exa_monthly_cap_usd=20.0, anthropic_api_key="a", anthropic_monthly_token_cap=1e6), db=db)}
    yield {"c": TestClient(apps["plain"]), "cr": TestClient(apps["ready"]), "h": hdr, "o": o, "p": p, "u": users}
    db.close()


SRC = {"W1": {"url": "https://kestrel.example/about", "title": "About Kestrel Pay", "published": None, "purpose": "about",
              "text": "Kestrel Pay runs account-to-account payments for UK merchants. Headquartered in London with offices in Berlin and Warsaw."},
       "W2": {"url": "https://news.example/kestrel-sepa", "title": "Kestrel Pay launches instant payouts", "published": "2026-09-14", "purpose": "news",
              "text": "Kestrel Pay has launched instant payouts across the eurozone and chose Mambu as its core platform partner for the programme."}}


# ------------------------------------------------------------------ parsing keeps only sourced statements
def test_company_profile_keeps_only_statements_tied_to_returned_pages():
    raw = {"about": {"text": "Kestrel Pay runs A2A payments for UK merchants.", "sources": ["W1", "W9"]},
           "products": [{"name": "Instant payouts", "detail": "Eurozone payouts", "source": "W2"}, {"name": "Cards", "detail": "Invented", "source": "W7"}],
           "footprint": {"hq": {"value": "London", "source": "W1"}, "size": {"value": "About 1,800 staff", "source": "R"}, "markets": {"value": "US", "source": "W8"}},
           "icp_points": [{"label": "Sector", "value": "Payments", "fit": "good", "source": "R"}, {"label": "Region", "value": "UK", "fit": "excellent", "source": "W1"},
                          {"label": "Tech", "value": "Invented", "fit": "good", "source": "W5"}],
           "recent": [{"date": "2026-09-14", "headline": "Instant payouts launched", "detail": "..", "source": "W2", "quote": "has launched instant payouts across the eurozone"},
                      {"date": "2026-10-01", "headline": "No quote", "source": "W2"},
                      {"date": "2026-10-02", "headline": "Quote not on the page", "source": "W2", "quote": "raised 50 million euros in a series C round"}],
           "vendors": [{"name": "Mambu", "role": "core platform", "source": "W2", "quote": "chose Mambu as its core platform partner"},
                       {"name": "Thought Machine", "role": "core", "source": "W2", "quote": "chose Mambu as its core platform partner"}],
           "how_we_help": [{"angle": "Delivery team for the payouts rollout", "why": "..", "sources": ["W2"]}, {"angle": "Unsourced idea", "sources": ["W42"]}],
           "open_questions": ["Who runs delivery?"]}
    out, dropped = verify_profile(raw, SRC)
    assert out["about"]["text"].startswith("Kestrel Pay") and [s["source"] for s in out["about"]["sources"]] == ["W1"]
    assert [p["name"] for p in out["products"]] == ["Instant payouts"]
    assert set(out["footprint"]) == {"hq", "size"} and out["footprint"]["size"]["source_title"] == "Our records"
    assert [i["label"] for i in out["icp_points"]] == ["Sector", "Region"] and out["icp_points"][1]["fit"] == "neutral"     # unknown fit word falls back to neutral
    assert [r["headline"] for r in out["recent"]] == ["Instant payouts launched"] and out["recent"][0]["source_url"] == "https://news.example/kestrel-sepa"
    assert [v["name"] for v in out["vendors"]] == ["Mambu"]                       # the page never names Thought Machine, whatever the quote
    assert [h["angle"] for h in out["how_we_help"]] == ["Delivery team for the payouts rollout"]
    assert dropped == 7


def test_person_profile_drops_contact_details_and_unquoted_activity():
    src = {"W1": {"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada Clarke - CTO - Kestrel Pay", "published": None, "purpose": "linkedin",
                  "text": "CTO at Kestrel Pay since 2023. Previously Head of Engineering at Northbridge Bank 2018-2023."},
           "W2": {"url": "https://conf.example/talks/ada", "title": "Money20/20 talk", "published": "2026-06-03", "purpose": "activity",
                  "text": "Ada Clarke spoke about moving card processing to the cloud. Call her on +44 20 7946 0000."}}
    raw = {"identity": {"confident": True, "note": "Same company on both pages"},
           "current_role": {"title": "CTO", "remit": "Runs engineering and platform", "since": "2023", "source": "W1"},
           "previous_roles": [{"title": "Head of Engineering", "company": "Northbridge Bank", "period": "2018-2023", "source": "W1"}],
           "focus_areas": [{"label": "Cloud card processing", "source": "W2"}],
           "recent_activity": [{"date": "2026-06-03", "kind": "talk", "title": "Money20/20 talk", "detail": "Cloud card processing", "source": "W2", "quote": "spoke about moving card processing to the cloud"},
                               {"date": "2026-07-01", "kind": "talk", "title": "Phone", "detail": "Call +44 20 7946 0000", "source": "W2", "quote": "Call her on +44 20 7946 0000"},
                               {"date": "2026-08-01", "kind": "podcast", "title": "Invented podcast", "source": "W2", "quote": "a podcast about the future of banking"}],
           "talking_points": [{"point": "Ask how the cloud move went", "why": "Her talk", "sources": ["W2"]}]}
    out, dropped = verify_person(raw, src)
    assert out["identity"]["confident"] is True and out["current_role"]["title"] == "CTO" and out["previous_roles"][0]["company"] == "Northbridge Bank"
    assert [a["title"] for a in out["recent_activity"]] == ["Money20/20 talk"] and dropped == 2
    assert out["talking_points"][0]["sources"][0]["source_url"] == "https://conf.example/talks/ada"


def test_linkedin_results_are_parsed_and_checked_against_the_company():
    keys = ["kestrel pay", "kestrel"]
    assert normalise_linkedin("https://uk.linkedin.com/in/Ben-Weber/?trk=x") == "https://www.linkedin.com/in/ben-weber"
    assert normalise_linkedin("https://www.linkedin.com/company/kestrel") is None
    ben = parse_result({"url": "https://www.linkedin.com/in/ben-weber", "title": "Ben Weber - Head of Engineering - Kestrel Pay | LinkedIn", "text": "Head of Engineering at Kestrel Pay"}, keys)
    assert ben["name"] == "Ben Weber" and ben["title"] == "Head of Engineering" and ben["company_match"] == "headline" and ben["seniority"] == "Head / Director"
    other = parse_result({"url": "https://www.linkedin.com/in/cat", "title": "Cat Other - CTO - Other Bank | LinkedIn", "text": "CTO at Other Bank"}, keys)
    assert other is None
    intern = parse_result({"url": "https://www.linkedin.com/in/zoe", "title": "Zoe Young - Software Engineering Intern - Kestrel Pay | LinkedIn"}, keys)
    assert intern == {"skip": "not a decision role", "name": "Zoe Young"}


# ------------------------------------------------------------------ company research job, cost and budget
def _company_pages():
    return {"overview": [{"url": "https://kestrel.example/about", "title": "About Kestrel Pay", "text": SRC["W1"]["text"]}],
            "news": [{"url": "https://news.example/kestrel-sepa", "title": "Kestrel Pay launches instant payouts", "publishedDate": "2026-09-14T08:00:00.000Z", "text": SRC["W2"]["text"]}],
            "partner": [{"url": "https://news.example/kestrel-sepa", "title": "duplicate page is numbered once", "text": SRC["W2"]["text"]}],
            "hiring": []}


REPLY = {"about": {"text": "Kestrel Pay runs account-to-account payments for UK merchants.", "sources": ["W1"]},
         "recent": [{"date": "2026-09-14", "headline": "Instant payouts", "source": "W2", "quote": "has launched instant payouts across the eurozone"}],
         "vendors": [{"name": "Mambu", "role": "core platform", "source": "W2", "quote": "chose Mambu as its core platform partner"}],
         "icp_points": [{"label": "Sector", "value": "Payments", "fit": "good", "source": "R"}], "how_we_help": [], "open_questions": []}


def test_company_research_job_searches_writes_a_cited_profile_and_records_cost(conn, env):
    caps(conn)
    oid = env["o"]["open"]
    rid = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    calls, prompts = [], []
    out = profile_jobs.account_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(_company_pages(), calls), call_factory=fake_model(REPLY, prompts))
    row = q1(conn, "select * from account_profiles where id=%s", (rid,))
    assert out["status"] == "done" and row["status"] == "done" and row["searches"] == 4 and float(row["exa_cost_usd"]) == pytest.approx(0.048)
    assert [c.get("category") for c in calls] == [None, "news", None, None] and calls[0]["includeDomains"] == ["kestrel.example"]
    assert all(c["contents"]["text"]["maxCharacters"] <= 2200 for c in calls) and "startPublishedDate" in calls[1]
    assert [s["id"] for s in row["sources"]] == ["W1", "W2"] and "text" not in row["sources"][0]      # page text is not stored
    assert row["content"]["vendors"][0]["name"] == "Mambu" and row["content"]["partial"] is False
    assert "<page id=\"W1\"" in prompts[0] and "Our records (R)" in prompts[0]
    used = qa(conn, "select cost_usd, cost_basis, operation from provider_usage where provider='exa'")
    assert len(used) == 4 and all(u["cost_basis"] == "reported" and u["operation"] == "account_profile" for u in used)
    b = q1(conn, "select spent, reserved from provider_budgets where provider='exa'")
    assert float(b["spent"]) == pytest.approx(0.048) and float(b["reserved"]) == 0


def test_exa_budget_exhaustion_stops_cleanly(conn, env):
    oid = env["o"]["open"]
    # room for one search only: the profile is written from it and marked partial
    caps(conn, exa=0.03)
    rid = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    calls = []
    profile_jobs.account_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(_company_pages(), calls), call_factory=fake_model(REPLY))
    row = q1(conn, "select * from account_profiles where id=%s", (rid,))
    assert len(calls) == 1 and row["status"] == "done" and row["content"]["partial"] is True and "budget" in row["content"]["notes"][0]
    assert q1(conn, "select count(*) n from budget_ledger where provider='exa' and entry='denied'")["n"] == 1
    # no budget at all: nothing is sent, the run fails with a plain reason
    conn.execute("update provider_budgets set spent=cap where provider='exa'")
    rid2 = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    calls2 = []
    out = profile_jobs.account_profile(conn, {"payload": {"profile_id": rid2}}, post_factory=fake_post(_company_pages(), calls2), call_factory=fake_model(REPLY))
    row2 = q1(conn, "select status, error from account_profiles where id=%s", (rid2,))
    assert out["reason"] == "budget" and calls2 == [] and row2["status"] == "failed" and "Exa budget" in row2["error"]


def test_exa_refusing_the_key_fails_the_run_and_releases_the_reservation(conn, env):
    caps(conn)
    rid = str(conn.execute("insert into account_profiles(organization_id, requested_by) values (%s,'bdm@x') returning id", (env["o"]["open"],)).fetchone()["id"])
    conn.commit()

    def refuse(body):
        raise AuthError("401 unauthorized", status=401)
    out = profile_jobs.account_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post({}, [], fail=refuse), call_factory=fake_model(REPLY))
    row = q1(conn, "select status, error from account_profiles where id=%s", (rid,))
    assert out["reason"] == "auth" and row["status"] == "failed" and "EXA_API_KEY" in row["error"]
    assert float(q1(conn, "select reserved from provider_budgets where provider='exa'")["reserved"]) == 0


# ------------------------------------------------------------------ find people with Exa
def _linkedin_pages():
    return {"Chief Technology Officer": [
                {"url": "https://uk.linkedin.com/in/ada-clarke/", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay"},
                {"url": "https://www.linkedin.com/in/ben-weber", "title": "Ben Weber - Head of Engineering - Kestrel Pay | LinkedIn", "text": "Leads engineering at Kestrel Pay"},
                {"url": "https://www.linkedin.com/company/kestrel-pay", "title": "Kestrel Pay | LinkedIn"},
                {"url": "https://www.linkedin.com/in/cat-other", "title": "Cat Other - CTO - Other Bank | LinkedIn", "text": "CTO at Other Bank"}],
            "Head of Payments": [
                {"url": "https://www.linkedin.com/in/dan-nowak-1", "title": "Dan Nowak - Head of Payments - Kestrel Pay | LinkedIn"},
                {"url": "https://www.linkedin.com/in/zoe-young", "title": "Zoe Young - Software Engineering Intern - Kestrel Pay | LinkedIn"},
                {"url": "https://www.linkedin.com/in/ben-weber", "title": "Ben Weber - Head of Engineering - Kestrel Pay | LinkedIn"}]}


def test_find_people_dedupes_against_known_people_and_add_creates_an_exa_person(conn, env):
    caps(conn)
    oid = env["o"]["open"]
    sid = str(conn.execute("insert into people_searches(organization_id, requested_by) values (%s,'bdm@x') returning id", (oid,)).fetchone()["id"])
    conn.commit()
    calls = []

    def category_refused_once(body):
        if body.get("category") == "linkedin profile" and not any(c.get("includeDomains") for c in calls[:-1]):
            raise SchemaDrift("unexpected status 400: invalid category", status=400)
    out = profile_jobs.people_search(conn, {"payload": {"search_id": sid}}, post_factory=fake_post(_linkedin_pages(), calls, fail=category_refused_once))
    assert out["status"] == "done"
    assert calls[0]["category"] == "linkedin profile" and calls[1]["includeDomains"] == ["linkedin.com"] and "category" not in calls[1]     # fell back once, then kept the fallback
    row = q1(conn, "select * from people_searches where id=%s", (sid,))
    cands = {r["name"]: r for r in qa(conn, "select * from people_candidates where organization_id=%s", (oid,))}
    assert set(cands) == {"Ada Clarke", "Ben Weber", "Dan Nowak"} and row["skipped"] == 1 and row["searches"] == 5
    assert cands["Ada Clarke"]["status"] == "already_known" and cands["Dan Nowak"]["status"] == "already_known"      # by LinkedIn address, and by name
    assert cands["Ben Weber"]["status"] == "found" and cands["Ben Weber"]["persona"] == "Technology leadership" and row["new_candidates"] == 1

    c, h = env["c"], env["h"]
    state = c.get(f"/api/accounts/{oid}/people-search", headers=h("bdm")).json()
    assert state["search"]["status"] == "done" and {i["name"] for i in state["items"]} == {"Ada Clarke", "Ben Weber", "Dan Nowak"} and state["setup"]["ready"] is False
    r = c.post(f"/api/people-candidates/{cands['Ben Weber']['id']}/add", headers=h("bdm"))
    assert r.status_code == 200 and r.json()["created"] is True
    pid = r.json()["person_id"]
    p = q1(conn, "select linkedin_url, work_email from people where id=%s", (pid,))
    assert p["linkedin_url"] == "https://www.linkedin.com/in/ben-weber" and p["work_email"] is None
    assert q1(conn, "select system from person_sources where person_id=%s", (pid,))["system"] == "exa"
    assert q1(conn, "select status from people_candidates where id=%s", (cands["Ben Weber"]["id"],))["status"] == "added"
    assert c.post(f"/api/people-candidates/{cands['Ben Weber']['id']}/add", headers=h("bdm")).json() == {"person_id": pid, "created": False}     # idempotent
    people = c.get(f"/api/accounts/{oid}/people", headers=h("bdm"))
    assert people.status_code == 200 and "@kestrel.example" not in people.text
    by = {x["name"]: x for x in people.json()["items"]}
    assert by["Ben Weber"]["sources"] == ["exa"] and by["Ada Clarke"]["lead_list"]["committee_role"] == "Technical sponsor candidate"
    assert by["Ada Clarke"]["email"] == "verified" and by["Dan Nowak"]["email"] == "unverified" and by["Ada Clarke"]["linkedin_url"].endswith("/ada-clarke")
    assert list(by)[0] == "Ada Clarke"                                           # lead-list priority first
    assert c.get("/api/people?source=exa", headers=h("bdm")).status_code == 200    # the global list knows the new source
    assert q1(conn, "select count(*) n from audit_events where operation='pii_read' and record_type='organization_people'")["n"] == 1


# ------------------------------------------------------------------ person research and the person page
def test_person_research_and_person_page(conn, env):
    caps(conn)
    pid = env["p"]["ada"]
    rid = str(conn.execute("insert into person_profiles(person_id, organization_id, requested_by) values (%s,%s,'bdm@x') returning id", (pid, env["o"]["open"])).fetchone()["id"])
    conn.commit()
    pages = {"Ada Clarke Kestrel Pay Ltd Chief": [{"url": "https://www.linkedin.com/in/ada-clarke", "title": "Ada Clarke - CTO - Kestrel Pay | LinkedIn", "text": "CTO at Kestrel Pay since 2023."},
                                                  {"url": "https://www.linkedin.com/in/ada-clarke-2", "title": "Ada Clarke - Nurse | LinkedIn", "text": "Nurse in Leeds"}],
             "conference": [{"url": "https://conf.example/ada", "title": "Talk", "publishedDate": "2026-06-03T00:00:00Z", "text": "Ada Clarke spoke about moving card processing to the cloud."}]}
    reply = {"identity": {"confident": True}, "current_role": {"title": "CTO", "remit": "Engineering", "source": "W1"},
             "recent_activity": [{"date": "2026-06-03", "kind": "talk", "title": "Cloud talk", "source": "W2", "quote": "spoke about moving card processing to the cloud"}],
             "talking_points": [{"point": "Ask about the cloud move", "sources": ["W2"]}]}
    calls, prompts = [], []
    profile_jobs.person_profile(conn, {"payload": {"profile_id": rid}}, post_factory=fake_post(pages, calls), call_factory=fake_model(reply, prompts))
    row = q1(conn, "select * from person_profiles where id=%s", (rid,))
    assert row["status"] == "done" and [s["url"] for s in row["sources"]] == ["https://www.linkedin.com/in/ada-clarke", "https://conf.example/ada"]   # the namesake nurse is filtered out
    assert calls[0]["category"] == "linkedin profile" and calls[1]["excludeDomains"] == ["linkedin.com"] and len(calls) == 3
    assert "ada@kestrel.example" not in prompts[0]                                 # the address never reaches the model
    assert all(u["query"] is None for u in qa(conn, "select query from provider_usage where operation='person_profile'"))   # names are not kept in usage logs

    c, h = env["c"], env["h"]
    r = c.get(f"/api/people/{pid}/profile", headers=h("bdm"))
    assert r.status_code == 200 and "ada@kestrel.example" not in r.text
    d = r.json()
    assert d["person"]["email"] == "verified" and d["employment"][0]["company"]["name"] == "Kestrel Pay Ltd" and d["lead_list"]["angle"] == "Payments platform rebuild"
    assert d["research"]["profile"]["content"]["recent_activity"][0]["title"] == "Cloud talk" and d["research"]["running"] is False
    assert q1(conn, "select count(*) n from audit_events where operation='pii_read' and record_type='person' and record_id=%s", (pid,))["n"] == 1


# ------------------------------------------------------------------ permissions, scope, starting research
def test_permissions_scope_and_research_start_rules(conn, env):
    c, cr, h, o, p = env["c"], env["cr"], env["h"], env["o"], env["p"]
    assert c.get(f"/api/accounts/{o['theirs']}/research", headers=h("bdm")).status_code == 404          # a colleague's account is invisible
    assert c.get(f"/api/accounts/{o['theirs']}/people", headers=h("bdm")).status_code == 404
    assert c.get(f"/api/people/{p['fay']}/profile", headers=h("bdm")).status_code == 404
    assert c.get(f"/api/people/{p['fay']}/profile", headers=h("bdm2")).status_code == 200
    assert c.get(f"/api/accounts/{o['open']}/people", headers=h("analyst")).status_code == 403          # no contact data for analysts
    assert c.get(f"/api/accounts/{o['open']}/research", headers=h("analyst")).status_code == 200
    assert c.post(f"/api/accounts/{o['open']}/people-search", headers=h("analyst")).status_code == 403
    assert c.get(f"/api/accounts/{o['open']}/research", headers=h("comp")).status_code == 403
    assert c.get("/api/accounts/not-a-uuid/research", headers=h("bdm")).status_code == 404
    # not configured: explained, nothing queued
    st = c.get(f"/api/accounts/{o['open']}/research", headers=h("bdm")).json()
    assert st["setup"]["ready"] is False and "EXA_API_KEY" in st["setup"]["note"] and st["profile"] is None
    r = c.post(f"/api/accounts/{o['open']}/research", headers=h("bdm"))
    assert r.status_code == 409 and "not set up" in r.json()["detail"]
    assert q1(conn, "select count(*) n from jobs")["n"] == 0
    # configured: one run at a time
    r = cr.post(f"/api/accounts/{o['open']}/research", headers=h("bdm"))
    assert r.status_code == 200 and q1(conn, "select kind from jobs")["kind"] == "account_profile"
    assert cr.post(f"/api/accounts/{o['open']}/research", headers=h("bdm")).status_code == 409
    assert cr.get(f"/api/accounts/{o['open']}/research", headers=h("bdm")).json()["running"] is True
    assert cr.post(f"/api/people/{p['ada']}/research", headers=h("bdm")).status_code == 200
    assert cr.post(f"/api/accounts/{o['open']}/people-search", headers=h("bdm")).status_code == 200
    assert {r["kind"] for r in qa(conn, "select kind from jobs")} == {"account_profile", "person_profile", "people_search"}
    # a finished profile is not redone straight away
    conn.execute("update account_profiles set status='done', finished_at=now(), content='{}'")
    conn.commit()
    assert cr.post(f"/api/accounts/{o['open']}/research", headers=h("bdm")).status_code == 409


# ------------------------------------------------------------------ relationship and owner
def test_relationship_is_computed_recorded_and_owner_assignment_respects_roles(conn, env):
    c, h, o, u = env["c"], env["h"], env["o"], env["u"]
    oid = o["open"]
    rel = c.get(f"/api/accounts/{oid}/relationship", headers=h("bdm")).json()
    assert rel["suggested"] == "prospect" and rel["lead_list"]["on_list"] is True and rel["recorded"]["status"] is None and rel["owner"] is None
    assert rel["people"] == {"n": 2, "with_email": 2, "verified": 1, "suppressed": 0}
    did = conn.execute("insert into campaign_drafts(organization_id, requested_by, status) values (%s,'bdm@x','approved') returning id", (oid,)).fetchone()["id"]
    conn.execute("insert into outbound_enrolments(campaign_draft_id, organization_id, person_id, email, status) values (%s,%s,%s,'ada@kestrel.example','interested')", (did, oid, env["p"]["ada"]))
    conn.commit()
    rel = c.get(f"/api/accounts/{oid}/relationship", headers=h("bdm"))
    assert rel.json()["suggested"] == "in_talks" and rel.json()["outreach"]["engagement"] == "interested" and "ada@kestrel.example" not in rel.text
    assert c.put(f"/api/accounts/{oid}/relationship", json={"status": "partner"}, headers=h("bdm")).status_code == 422
    assert c.put(f"/api/accounts/{oid}/relationship", json={"status": "past_client", "note": "Delivered a QA team in 2024"}, headers=h("comp")).status_code == 403
    r = c.put(f"/api/accounts/{oid}/relationship", json={"status": "past_client", "note": "Delivered a QA team in 2024"}, headers=h("bdm"))
    assert r.status_code == 200 and c.get(f"/api/accounts/{oid}/relationship", headers=h("bdm")).json()["recorded"]["status"] == "past_client"
    # a seller takes the account; cannot hand it to someone else; a colleague cannot take it away
    assert c.put(f"/api/accounts/{oid}/owner", json={"user_id": u["bdm2"]}, headers=h("bdm")).status_code == 403
    r = c.put(f"/api/accounts/{oid}/owner", json={"user_id": u["bdm"]}, headers=h("bdm"))
    assert r.status_code == 200 and r.json()["owner"]["name"] == "bdm"
    assert c.put(f"/api/accounts/{oid}/owner", json={"user_id": u["bdm2"]}, headers=h("bdm2")).status_code == 403
    assert c.put(f"/api/accounts/{oid}/owner", json={"user_id": None}, headers=h("bdm2")).status_code == 403
    # a manager reassigns; nobody can own an account with a role that does not sell
    assert c.put(f"/api/accounts/{oid}/owner", json={"user_id": u["comp"]}, headers=h("mgr")).status_code == 400
    r = c.put(f"/api/accounts/{oid}/owner", json={"user_id": u["bdm2"]}, headers=h("mgr"))
    assert r.status_code == 200 and str(q1(conn, "select owner_user_id from organizations where id=%s", (oid,))["owner_user_id"]) == u["bdm2"]
    assert q1(conn, "select count(*) n from audit_events where operation='account_owner_set'")["n"] == 2
    team = c.get(f"/api/accounts/{oid}/relationship", headers=h("bdm")).json()["team"]
    assert {t["role"] for t in team} <= {"bdm", "sales_manager", "admin"}
    assert c.get(f"/api/accounts/{oid}/tenders", headers=h("bdm")).json() == {"notices": [], "awards": []}


def test_a_broken_json_answer_is_repaired_once_then_raised():
    """Production run on 25 Nov 2026: the model left a raw double quote inside a copied excerpt, so the profile failed after Exa had been paid for."""
    import asyncio

    from ri.research.company_profile import ask_json

    calls = []
    good = '{"about": {"text": "A payments company.", "sources": ["W1"]}}'

    async def call(system, user):
        calls.append(user)
        if len(calls) == 1:
            return '{"about": {"text": "A payments company.", "sources": ["W1"]', 100, 50, "fake"          # cut off: nothing local can repair it
        return good, 10, 5, "fake"
    out, tin, tout, _ = ask_json(call, lambda c: asyncio.run(c), "sys", "user text")
    assert out["about"]["text"] == "A payments company." and (tin, tout) == (110, 55) and len(calls) == 2
    assert "not valid JSON" in calls[1] and "A payments company" in calls[1]

    async def always_bad(system, user):
        return '{"about": ', 1, 1, "fake"
    import pytest
    with pytest.raises(ValueError, match="still invalid after one repair"):
        ask_json(always_bad, lambda c: asyncio.run(c), "sys", "u")


def test_parse_json_survives_the_usual_small_model_slips():
    from ri.research.company_profile import parse_json
    assert parse_json('{"a": {"text": "He said "hello" to us", "sources": ["W1"]}}')["a"]["text"] == 'He said "hello" to us'
    assert parse_json('{"a": "He said "go", then fled", "b": "x"}') == {"a": 'He said "go", then fled', "b": "x"}
    assert parse_json('{"a": "line one\nline two"}')["a"] == "line one line two"
    assert parse_json('Here you go:\n```json\n{"a": "x"}\n```\nNote: done {ok}') == {"a": "x"}
    assert parse_json('{"a": "plain \\"escaped\\" quote", "b": 1, "c": true}')["b"] == 1


def test_research_is_ready_when_the_worker_has_the_keys(conn):
    """Production, 25 Nov 2026: the keys were on the worker but not the web app, so the research buttons stayed disabled."""
    from types import SimpleNamespace

    from ri.heartbeat import beat
    from ri.research.exa_search import configured
    web = SimpleNamespace(exa_api_key=None, exa_monthly_cap_usd=0, anthropic_api_key=None)
    assert configured(web, conn)["ready"] is False
    beat(conn, "worker-interactive", {"jobs_since_start": 0, "can": {"exa": True, "ai": True}})
    assert configured(web, conn)["ready"] is True and configured(web, conn)["exa"] is True
    beat(conn, "worker-interactive", {"jobs_since_start": 0, "can": {"exa": False, "ai": True}})
    assert configured(web, conn)["missing"] == ["EXA_API_KEY", "RI_EXA_MONTHLY_CAP_USD"]
    conn.execute("update system_heartbeats set at = now() - interval '1 hour' where component='worker-interactive'")
    assert configured(SimpleNamespace(exa_api_key=None, exa_monthly_cap_usd=0, anthropic_api_key="k"), conn)["ready"] is False      # a stale beat counts for nothing
