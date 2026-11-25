"""Round 33: one list of companies, and missing company facts filled in automatically with Exa.

Exa and the model are FAKES (no network). What matters: a company is a target account, still being filled in, or not a fit (with the
reason); a lead-list "Review" company becomes a target account as soon as its facts fit; Exa writes only empty fields and only facts whose
quote is really on the page; the daily limit, the 30-day rule and the budgets stop the work cleanly; the Accounts list counts every state
and carries the lead list's own signal on the company row."""
import json

import pytest
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user

from ri import budget, usage
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, jb, q1, qa
from ri.research import company_facts as cf
from ri.sources.base import AuthError
from ri.target_market import state_sql

SECRET = "f" * 40


def org(conn, name, *, list_eligibility=None, country="GB", segment="Banking", employees=None, ownership="unknown", source=None,
        domain=None, entity="company", list_priority=None, workbook_signal=None):
    oid = str(conn.execute(
        "insert into organizations(display_name, canonical_domain, country_code, segment, employees_estimate, company_ownership, "
        "company_profile_source_url, entity_type, list_eligibility, list_priority, in_master_list) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (name, domain, country, segment, employees, ownership, source, entity, list_eligibility, list_priority, list_eligibility is not None)).fetchone()["id"])
    if workbook_signal:
        link = conn.execute("insert into account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, "
                            "row_number, imported_hash, raw_company_name, observed_at) values (%s,'google_sheets','wb','s','MASTER ACCOUNTS',%s,1,'h',%s,now()) "
                            "returning id", (oid, oid, name)).fetchone()["id"]
        conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'master_account',%s,now())",
                     (oid, link, jb(workbook_signal)))
    return oid


def state(conn, oid):
    return q1(conn, f"select {state_sql()} as s from organizations o where o.id=%s", (oid,))["s"]  # noqa: S608


def caps(conn, exa=1.0, tokens=1_000_000):
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), exa, "usd")
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), tokens, "tokens")
    conn.commit()


PAGES = [
    {"url": "https://en.wikipedia.example/wiki/Harbour_Mutual_Bank", "title": "Harbour Mutual Bank - Wikipedia",
     "text": "Harbour Mutual Bank is a building society headquartered in Leeds, England. It is owned by its members. "
             "As of 2025 the society employs 1,850 people across 60 branches. Website: harbourmutual.example"},
    {"url": "https://harbourmutual.example/about", "title": "About us | Harbour Mutual",
     "text": "We are a mutual: Harbour Mutual Bank has no shareholders and is run for its members since 1866."},
    {"url": "https://news.example/other", "title": "Another lender raises funds", "text": "Kestrel Credit, a fintech lender, employs 90 staff."},
]


def answer(**over):
    base = {"ownership": {"value": "mutual_or_cooperative", "source": "P1", "quote": "It is owned by its members."},
            "employees": {"value": 1850, "source": "P1", "quote": "the society employs 1,850 people across 60 branches"},
            "country": {"value": "GB", "source": "P1", "quote": "headquartered in Leeds, England"},
            "sector": {"value": "Retail banking", "source": "P1", "quote": "is a building society headquartered in Leeds"},
            "website": {"value": "harbourmutual.example", "source": "P2", "quote": "We are a mutual: Harbour Mutual Bank has no shareholders"}}
    base.update(over)
    return base


def fake_post(calls, pages=PAGES, cost=0.008, fail=None):
    def factory():
        async def post(body):
            calls.append(body)
            if fail:
                fail(body)
            return {"results": pages, "costDollars": {"total": cost}}
        return post
    return factory


def fake_model(reply, seen=None):
    def factory():
        async def call(system, user):
            if seen is not None:
                seen.append(user)
            return (reply if isinstance(reply, str) else json.dumps(reply)), 900, 150, "fake-haiku"
        return call
    return factory


def run_job(conn, post, model, limit=5):
    return cf.job(conn, {"id": "00000000-0000-0000-0000-000000000033", "payload": {"limit": limit}}, post_factory=post, call_factory=model)


# ------------------------------------------------------------------ the three states
def test_every_company_has_one_state_and_review_rows_need_facts_that_fit(conn):
    src = "https://example.com/owner"
    ids = {
        "eligible": org(conn, "Eligible Bank", list_eligibility="Eligible", employees=200),                 # the list decides
        "eligible_state": org(conn, "Eligible Landesbank", list_eligibility="Eligible", ownership="state_owned", source=src),
        "review_blank": org(conn, "Review Bank", list_eligibility="Review"),                                # headcount and ownership missing
        "review_fits": org(conn, "Review Fits Bank", list_eligibility="Review", employees=900, ownership="private", source=src),
        "review_mutual": org(conn, "Review Coop Bank", list_eligibility="Review", employees=900, ownership="mutual_or_cooperative", source=src),
        "review_state": org(conn, "Review State Bank", list_eligibility="Review", employees=900, ownership="state_owned", source=src),
        "review_small": org(conn, "Review Small Pay", list_eligibility="Review", segment="Payments", employees=400),
        "review_vendor": org(conn, "Review Core Software", list_eligibility="Review", segment="Financial Software"),
        "out": org(conn, "Out Adtech", list_eligibility="Out of scope", segment="AdTech", employees=5000),
        "found_listed": org(conn, "Found Listed Bank", country="DE", employees=5000, ownership="listed", source=src),
        "found_no_source": org(conn, "Found Unsourced Bank", country="DE", employees=5000, ownership="listed"),
        "found_unknown": org(conn, "Found Unknown Insurer", country="SE", segment="Insurance", employees=None),
        "found_us_small": org(conn, "Found US Lender", country="US", segment="Lending", employees=1500, ownership="private", source=src),
        "found_retail": org(conn, "Found Retailer", segment="Retail", employees=9000, ownership="private", source=src),
        "found_retail_workbook": org(conn, "Workbook Retailer", segment="Retail", employees=9000, workbook_signal={"Verified Buying Signal": "x"}),
        "public": org(conn, "Review Public Body", list_eligibility="Review", entity="public_body", employees=900),
    }
    conn.commit()
    got = {k: state(conn, v) for k, v in ids.items()}
    assert got == {
        "eligible": "target", "eligible_state": "target",
        "review_blank": "review", "review_fits": "target", "review_mutual": "target", "review_state": "other", "review_small": "other",
        "review_vendor": "other", "out": "other",
        "found_listed": "target", "found_no_source": "review", "found_unknown": "review", "found_us_small": None, "found_retail": None,
        "found_retail_workbook": "other", "public": None,
    }


# ------------------------------------------------------------------ the fill-in
def test_exa_fills_only_empty_fields_with_quoted_facts_and_the_company_becomes_a_target(conn):
    caps(conn)
    oid = org(conn, "Harbour Mutual Bank", list_eligibility="Review", segment="Building society", domain=None)
    conn.commit()
    assert state(conn, oid) == "review"
    calls, seen = [], []
    # the country quote is not on any page: that fact is dropped; the others are kept
    res = run_job(conn, fake_post(calls), fake_model(answer(country={"value": "IE", "source": "P1", "quote": "headquartered in Dublin, Ireland"}), seen))
    assert res["status"] == "done" and res["checked"] == 1 and res["found"] == 1 and res["facts"] == 3, res
    assert len(calls) == 1 and calls[0]["numResults"] == 3 and calls[0]["contents"]["text"]["maxCharacters"] <= 1500
    assert "Harbour Mutual Bank" in calls[0]["query"]
    assert "UNTRUSTED" in cf.SYSTEM and "Harbour Mutual Bank" in seen[0]
    o = q1(conn, "select * from organizations where id=%s", (oid,))
    assert o["company_ownership"] == "mutual_or_cooperative"
    assert o["company_profile_source_url"] == PAGES[0]["url"]
    assert (o["employees_estimate"], o["employees_basis"], o["employees_uncertain"]) == (1850, "provider_estimate", True)
    assert o["segment"] == "Building society"                          # set by the lead list: never overwritten
    assert o["country_code"] == "GB"                                    # already known; the unquoted answer is ignored anyway
    assert o["canonical_domain"] == "harbourmutual.example"             # the page's own address
    assert o["facts_status"] == "found" and o["facts_checked_at"] is not None
    fs = o["facts_source"]
    assert fs["employees"]["url"] == PAGES[0]["url"] and "1,850 people" in fs["employees"]["quote"] and fs["employees"]["by"] == "exa"
    assert "country" not in fs and "sector" not in fs
    assert fs["_last"]["pages"] == [p["url"] for p in PAGES] and set(fs["_last"]["written"]) == {"ownership", "employees", "website"}
    assert state(conn, oid) == "target"                                 # its facts fit now: a target account, automatically
    # usage and cost are recorded like other research; the audit trail names what changed
    use = qa(conn, "select provider, operation, cost_usd, organization_id from provider_usage where operation='company_facts'")
    assert [(u["provider"], float(u["cost_usd"]), str(u["organization_id"])) for u in use] == [("exa", 0.008, oid)]
    assert float(q1(conn, "select spent from provider_budgets where provider='exa'")["spent"]) == pytest.approx(0.008)
    assert q1(conn, "select count(*) n from audit_events where operation='company_facts_filled' and record_id=%s", (oid,))["n"] == 1
    # never again within 30 days
    assert run_job(conn, fake_post(calls), fake_model(answer()))["checked"] == 0 and len(calls) == 1


def test_facts_must_be_quoted_on_a_page_about_this_company():
    src = {"P1": PAGES[0], "P2": PAGES[1], "P3": PAGES[2]}
    me = {"display_name": "Harbour Mutual Bank", "canonical_domain": None}
    kept = cf.verify_facts(answer(), src, me)
    assert set(kept) == {"ownership", "employees", "country", "sector", "website"}
    wrong = cf.verify_facts(answer(employees={"value": 1900, "source": "P1", "quote": "the society employs 1,850 people across 60 branches"},
                                   website={"value": "linkedin.com", "source": "P1", "quote": "Harbour Mutual Bank is a building society"},
                                   country={"value": "GB", "source": "P9", "quote": "headquartered in Leeds, England"},
                                   ownership={"value": "private", "source": "P3", "quote": "Kestrel Credit, a fintech lender, employs 90 staff."}), src, me)
    assert set(wrong) == {"sector"}           # number not in the quote, not its own site, a page that was not returned, a page about another company
    assert cf.verify_facts({"ownership": {"value": "unknown", "source": "P1", "quote": "It is owned by its members."}}, src, me) == {}


def test_a_value_set_by_a_person_or_the_list_is_never_overwritten(conn, db_url):
    caps(conn)
    mk_user(conn, "analyst@x", "research_analyst")
    oid = org(conn, "Harbour Mutual Bank", list_eligibility="Review", domain="harbour.example", country="GB")
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'analyst@x')}"}
        r = c.put(f"/api/accounts/{oid}/company-profile", headers=h, json={"ownership": "unknown", "employees": 700})
        assert r.status_code == 200 and r.json()["target_qualification"] == "review"
        assert c.put(f"/api/accounts/{oid}/company-profile", headers=h, json={"ownership": "mutual_or_cooperative"}).status_code == 422   # needs its source
    finally:
        db.close()
    assert q1(conn, "select facts_source from organizations where id=%s", (oid,))["facts_source"]["employees"]["by"] == "person"
    run_job(conn, fake_post([]), fake_model(answer()))
    o = q1(conn, "select * from organizations where id=%s", (oid,))
    assert o["employees_estimate"] == 700 and o["facts_source"]["employees"]["by"] == "person"      # the person's number stays
    assert o["canonical_domain"] == "harbour.example"                                             # the list's website stays
    assert o["company_ownership"] == "mutual_or_cooperative"                                       # this was empty: filled in
    assert state(conn, o["id"]) == "target"


def test_order_daily_limit_and_thirty_days(conn):
    caps(conn, exa=5.0)
    found = org(conn, "Found Savings Bank", country="DE", employees=None)
    eligible = org(conn, "Eligible Without Website", list_eligibility="Eligible")
    review = [org(conn, f"Review Bank {i:02d}", list_eligibility="Review", list_priority="P1" if i == 7 else None) for i in range(22)]
    org(conn, "Eligible With Website", list_eligibility="Eligible", domain="ok.example")            # nothing to fill in: never searched
    conn.commit()
    first = [r["id"] for r in cf.due(conn, 3)]
    assert first[0] == review[7] and set(first) <= set(review)        # lead-list Review rows first, the list's priority first among them
    order = [r["id"] for r in cf.due(conn, 30)]
    assert order[-2:] == [eligible, found]                            # then Eligible rows without a website, then research finds
    calls = []
    res = run_job(conn, fake_post(calls, pages=[]), fake_model(answer()), limit=50)
    assert res["checked"] == cf.DAILY_LIMIT == 20 and len(calls) == 20 and res["nothing"] == 20
    assert run_job(conn, fake_post(calls, pages=[]), fake_model(answer()))["stopped"].startswith("Daily limit")
    assert cf.schedule(conn) == 0                                     # nothing queued while the day's limit is used
    # a day later the rest are looked at; the ones already looked at wait 30 days
    conn.execute("update organizations set facts_checked_at = facts_checked_at - interval '2 days' where facts_checked_at is not null")
    conn.commit()
    res = run_job(conn, fake_post(calls, pages=[]), fake_model(answer()), limit=50)
    assert res["checked"] == 4 and len(calls) == 24
    conn.execute("update organizations set facts_checked_at = now() - interval '31 days'")
    conn.commit()
    assert len(cf.due(conn, 50)) == 24


def test_budgets_and_refusals_stop_cleanly_and_the_company_stays_due(conn):
    oid = org(conn, "Harbour Mutual Bank", list_eligibility="Review")
    conn.commit()
    caps(conn, exa=1.0, tokens=1000)
    budget.reserve(conn, "anthropic", "monthly", usage.period_now(), 800, key="sellers-research")     # 80% of the AI budget in use
    conn.commit()
    calls = []
    res = run_job(conn, fake_post(calls), fake_model(answer()))
    assert res["checked"] == 0 and "AI budget" in res["stopped"] and calls == []          # stopped before paying for a search
    budget.release(conn, "anthropic", "monthly", usage.period_now(), 800, key="sellers-research")
    budget.reserve(conn, "exa", "monthly", usage.period_now(), 0.75, key="someone-else")              # 75% of the Exa budget in use
    conn.commit()
    res = run_job(conn, fake_post(calls), fake_model(answer()))
    assert res["checked"] == 0 and "Exa budget" in res["stopped"] and calls == []

    def refuse(body):
        raise AuthError("bad key")
    caps(conn, exa=5.0)                                               # plenty again
    res = run_job(conn, fake_post(calls, fail=refuse), fake_model(answer()))
    assert res["checked"] == 0 and "API key" in res["stopped"]
    assert q1(conn, "select facts_checked_at from organizations where id=%s", (oid,))["facts_checked_at"] is None     # still due
    # an answer that cannot be read is not paid for again within 30 days
    res = run_job(conn, fake_post(calls), fake_model("this is not JSON"))
    assert res["failed"] == 1
    o = q1(conn, "select facts_status, facts_checked_at, company_ownership from organizations where id=%s", (oid,))
    assert o["facts_status"] == "failed" and o["facts_checked_at"] is not None and o["company_ownership"] == "unknown"


def test_schedule_runs_hourly_only_when_switched_on(conn):
    org(conn, "Harbour Mutual Bank", list_eligibility="Review")
    conn.commit()
    assert cf.schedule(conn) == 0                                     # no budgets saved by the worker: off
    caps(conn, exa=20.0)
    assert cf.schedule(conn) == 1
    assert cf.schedule(conn) == 0                                     # the same hour: one job
    assert q1(conn, "select kind, max_attempts from jobs")["kind"] == "company_facts"
    st = cf.status(conn)
    assert (st["state"], st["queue"], st["due"], st["per_day"]) == ("running", 1, 1, 20)


# ------------------------------------------------------------------ the Accounts list
def test_accounts_list_counts_each_state_and_carries_the_list_signal(conn, db_url):
    caps(conn, exa=20.0)
    mk_user(conn, "bdm@x", "bdm")
    lead = {"Verified Buying Signal": "Harbour opens a core banking tender", "Signal Type": "Platform tender", "Signal Date": "2026-11-12",
            "Primary Source URL": "https://harbour.example/press", "Potential Engineering Need": "Core migration help"}
    harbour = org(conn, "Harbour Mutual Bank", list_eligibility="Review", workbook_signal=lead)
    org(conn, "Eligible Bank", list_eligibility="Eligible", employees=3000, domain="eligible.example")
    org(conn, "Out Adtech", list_eligibility="Out of scope", segment="AdTech", employees=5000, workbook_signal={"Verified Buying Signal": "Adtech news"})
    conn.execute("update organizations set facts_checked_at=now() - interval '1 day', facts_status='nothing' where id=%s", (harbour,))
    conn.commit()
    db = Database(db_url, max_size=2)
    try:
        c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
        h = {"Authorization": f"Bearer {make_dev_token(SECRET, 'bdm@x')}"}
        target = c.get("/api/leads?scope=target&by_region=false", headers=h).json()
        assert [r["name"] for r in target["items"]] == ["Eligible Bank"]
        assert target["counts"] == {"target": 1, "review": 1, "other": 1}
        # a search that finds nothing here still says where the company is
        assert c.get("/api/leads?scope=target&q=core%20banking%20tender", headers=h).json()["counts"] == {"target": 0, "review": 1, "other": 0}
        filling = c.get("/api/leads?scope=review", headers=h).json()["items"]
        assert [(r["name"], r["missing"], r["facts_status"]) for r in filling] == [("Harbour Mutual Bank", ["ownership", "employees", "website"], "nothing")]
        assert filling[0]["list_lead"] == {"signal": "Harbour opens a core banking tender", "signal_type": "Platform tender", "signal_date": "2026-11-12",
                                           "source_url": "https://harbour.example/press", "why": "Core migration help", "lane": "signals",
                                           "age_days": filling[0]["list_lead"]["age_days"], "check": None}
        other = c.get("/api/leads?scope=other", headers=h).json()["items"]
        assert [(r["name"], r["not_fit"]) for r in other] == [("Out Adtech", "list_out_of_scope")]
        assert c.get("/api/leads?scope=bogus", headers=h).status_code == 422
        st = c.get("/api/company-facts/status", headers=h).json()
        assert (st["state"], st["queue"], st["due"], st["checked_week"]) == ("waiting", 1, 0, 1)
        csv_text = c.get("/api/leads.csv?scope=review", headers=h).text
        assert "Still being filled in" in csv_text.splitlines()[1]
    finally:
        db.close()
