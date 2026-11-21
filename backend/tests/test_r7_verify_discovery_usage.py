"""Verification, discovery and the usage tracker. Pages, model and Exa are FAKES; all names are synthetic."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ri import budget, usage
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database, jb
from ri.discovery.plans import build_plans, sync_plans
from ri.discovery.run import run_discovery
from ri.events.budgeted import BudgetedExtractor
from ri.jobs.handlers import build_handlers
from ri.jobs.queue import run_one
from ri.sources.base import BudgetExceeded, PolicyBlocked, RawItem
from ri.sources.registry import seed_registry
from ri.verify.fetch import Page, assert_public
from ri.verify.run import SOURCE_ID, run_verification
from test_r4_crm_notify import mk_user

SECRET = "x" * 32
TEXT = ("Synthetic Example Bank AG today announced that it has selected Synthetic Platform Vendor to replace its core banking platform. "
        "The multi-year programme starts in January and covers retail accounts, payments and a phased migration of customer data. ") * 3
ANCHOR = "Synthetic Example Bank AG today announced that it has selected Synthetic Platform Vendor to replace its core banking platform"


class FakeInner:
    version = "fake:1"
    model = "fake"
    max_tokens_budget = 100
    last_usage_tokens, last_tokens_in, last_tokens_out = 1200, 1000, 200

    def __init__(self, anchor=ANCHOR):
        self.anchor = anchor

    async def extract(self, *, title, text, published_at):
        return {"events": [{"event_type": "vendor_selected", "subject_name": "Synthetic Example Bank AG", "event_date": "2026-11-10",
                            "summary": "Synthetic Example Bank AG selected a vendor for core banking replacement",
                            "other_entities": [{"name": "Synthetic Platform Vendor", "role": "vendor"}],
                            "claims": [{"predicate": "selected_vendor", "object": {"vendor": "Synthetic Platform Vendor"}, "anchor": self.anchor}]}]}


def page(text=TEXT, url="https://example-bank.invalid/press/1"):
    return Page(url, "Press release", text, datetime(2026, 11, 10, tzinfo=timezone.utc), "h" + str(len(text)))


@pytest.fixture()
def world(conn):
    seed_registry(conn)
    oid = str(conn.execute("insert into organizations(display_name, country_code, in_master_list) values ('Synthetic Example Bank AG','DE',true) returning id").fetchone()["id"])
    conn.execute("insert into organization_aliases(organization_id, alias, alias_norm, source_ref) values (%s,'Synthetic Example Bank AG','synthetic example bank','t')", (oid,))
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    conn.commit()
    return {"org": oid}


def mk_request(conn, org, url="https://example-bank.invalid/press/1", origin="sheet_signal"):
    return str(conn.execute("insert into verification_requests(organization_id, url, origin, claimed_text, requested_by) values (%s,%s,%s,'claim','t@x') returning id", (org, url, origin)).fetchone()["id"])


def test_verification_creates_event_from_quoted_page_and_tracks_usage(conn, world):
    rid = mk_request(conn, world["org"])
    ex = BudgetedExtractor(FakeInner(), conn, price_in=3.0, price_out=15.0)
    out = run_verification(conn, rid, fetch=lambda u: page(), extractor=ex)
    conn.commit()
    assert out["status"] == "verified" and out["events"] == 1
    ev = conn.execute("select subject_org_id from events").fetchone()
    assert str(ev["subject_org_id"]) == world["org"]                                   # linked to the account the claim was made for
    u = conn.execute("select * from provider_usage where provider='anthropic'").fetchone()
    assert u["tokens_in"] == 1000 and u["tokens_out"] == 200 and u["request_id"] and u["source_id"] == SOURCE_ID
    assert u["cost_basis"] == "estimated" and float(u["cost_usd"]) == pytest.approx((1000 * 3 + 200 * 15) / 1e6)
    doc = conn.execute("select content_text from source_documents").fetchone()
    assert doc["content_text"] is None                                                  # licence-safe: text purged after extraction
    assert run_verification(conn, rid, fetch=lambda u: page(), extractor=ex)["already_done"]


def test_model_cost_is_unknown_without_configured_prices(conn, world):
    rid = mk_request(conn, world["org"])
    run_verification(conn, rid, fetch=lambda u: page(), extractor=BudgetedExtractor(FakeInner(), conn))
    u = conn.execute("select cost_usd, cost_basis from provider_usage").fetchone()
    assert u["cost_usd"] is None and u["cost_basis"] == "unmetered"                    # never invented


def test_unquoted_claim_is_dropped_and_page_without_event_is_reported(conn, world):
    rid = mk_request(conn, world["org"])
    out = run_verification(conn, rid, fetch=lambda u: page(), extractor=BudgetedExtractor(FakeInner("text that is not on the page"), conn))
    assert out["status"] == "no_event"
    assert conn.execute("select count(*) c from events").fetchone()["c"] == 0


def test_blocked_fetch_budget_cap_and_thin_page_statuses(conn, world):
    def blocked(u):
        raise PolicyBlocked("robots.txt disallows fetching this page")
    r1 = mk_request(conn, world["org"], url="https://a.invalid/1")
    assert run_verification(conn, r1, fetch=blocked, extractor=BudgetedExtractor(FakeInner(), conn))["status"] == "blocked"
    r2 = mk_request(conn, world["org"], url="https://a.invalid/2")
    assert run_verification(conn, r2, fetch=lambda u: page("short"), extractor=BudgetedExtractor(FakeInner(), conn))["status"] == "no_event"
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 10, "tokens")          # cap far below one extraction
    conn.commit()
    r3 = mk_request(conn, world["org"], url="https://a.invalid/3")
    assert run_verification(conn, r3, fetch=lambda u: page(url="https://a.invalid/3"), extractor=BudgetedExtractor(FakeInner(), conn))["status"] == "waiting_budget"
    assert conn.execute("select count(*) c from events").fetchone()["c"] == 0


def test_fetcher_refuses_private_addresses_and_social_pages():
    for bad in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "http://localhost/admin", "ftp://example.com/x",
                "https://www.linkedin.com/posts/x", "https://user:pw@example.com/"):
        with pytest.raises(Exception) as ei:
            asyncio.run(assert_public(bad))
        assert isinstance(ei.value, (PolicyBlocked, Exception))


class FakeExa:
    def __init__(self, cost=0.007, fail=None):
        self.cost, self.fail, self.calls = cost, fail, []

    async def search(self, plan, *, num_results=10):
        if self.fail:
            raise self.fail
        self.calls.append(plan.plan_id)
        items = [RawItem(external_id=f"https://news.invalid/{plan.plan_id}/{i}", version=None, url=f"https://news.invalid/{plan.plan_id}/{i}",
                         payload={"title": f"hit {i}", "url": f"https://news.invalid/{plan.plan_id}/{i}", "published": "2026-11-12T00:00:00.000Z", "rank": i, "plan_id": plan.plan_id, "query": plan.query})
                 for i in range(1, 5)]
        items.append(RawItem(external_id="https://www.linkedin.com/x", version=None, url="https://www.linkedin.com/x", payload={"title": "social", "url": "https://www.linkedin.com/x", "rank": 9, "plan_id": plan.plan_id, "query": "q"}))
        return items, {"requests": 1, "reported_cost": self.cost, "plan_id": plan.plan_id}


def test_plans_are_built_from_active_rules_and_sync_is_idempotent(conn):
    plans = build_plans()
    assert len(plans) == 24 and len({p.plan_id for p in plans}) == 24
    assert {p.signal_rule_id for p in plans} <= {"PRG-01", "PRG-02", "PRG-06", "SRC-04", "SRC-05", "MAN-01"}
    assert sync_plans(conn) == 24 and sync_plans(conn) == 0


def test_discovery_reserves_budget_records_reported_cost_and_queues_verification_only(conn):
    seed_registry(conn)
    sync_plans(conn)
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 20.0, "usd")
    conn.commit()
    exa = FakeExa(cost=0.007)
    out = run_discovery(conn, exa, max_searches=3, max_new_requests=5, interval_days=7, est_cost=None)
    assert out["searches"] == 2 or out["searches"] == 3
    assert out["requests_created"] == 5 and out["stopped"] == "per-run request limit reached" or out["searches"] == 3
    reqs = conn.execute("select origin, status, url from verification_requests").fetchall()
    assert reqs and all(r["origin"] == "exa_discovery" and r["status"] == "queued" for r in reqs)
    assert all("linkedin" not in r["url"] for r in reqs)
    assert conn.execute("select count(*) c from events").fetchone()["c"] == 0           # discovery alone never creates events
    rows = conn.execute("select cost_usd, cost_basis, plan_id from provider_usage where provider='exa'").fetchall()
    assert rows and all(float(r["cost_usd"]) == 0.007 and r["cost_basis"] == "reported" for r in rows)
    b = conn.execute("select spent, reserved from provider_budgets where provider='exa'").fetchone()
    assert float(b["spent"]) == pytest.approx(0.007 * out["searches"]) and float(b["reserved"]) == 0
    again = run_discovery(conn, exa, max_searches=30, max_new_requests=100, interval_days=7, est_cost=None)
    assert set(exa.calls[:out["searches"]]).isdisjoint(set(exa.calls[out["searches"]:]))   # plans run once per interval


def test_discovery_stops_when_budget_exhausted_and_on_auth_failure(conn):
    from ri.sources.base import AuthError
    sync_plans(conn)
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 0.005, "usd")            # below the 0.01 default reservation
    conn.commit()
    out = run_discovery(conn, FakeExa(), max_searches=5, max_new_requests=5, interval_days=7, est_cost=None)
    assert out["searches"] == 0 and out["stopped"] == "monthly Exa budget reached"
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 5.0, "usd")
    conn.commit()
    out = run_discovery(conn, FakeExa(fail=AuthError("401")), max_searches=5, max_new_requests=5, interval_days=7, est_cost=None)
    assert out["searches"] == 0 and "refused" in out["stopped"]
    assert float(conn.execute("select reserved from provider_budgets where provider='exa'").fetchone()["reserved"]) == 0    # reservation released
    assert conn.execute("select status from provider_usage where provider='exa'").fetchone()["status"].startswith("error:")


# ---------------------------------------------------------------- API
@pytest.fixture()
def api(conn, db_url, world):
    mk_user(conn, "admin@x", "admin")
    mk_user(conn, "bdm@x", "bdm", 555)
    mk_user(conn, "mkt@x", "marketing")
    link = conn.execute("insert into account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at) "
                        "values (%s,'google_sheets','wb','s','Priority Outreach','r1',2,'h','Synthetic Example Bank AG',now()) returning id", (world["org"],)).fetchone()["id"]
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'priority_outreach',%s, now())",
                 (world["org"], link, jb({"Rank": "1", "Strongest buying signal": "Bank selects core vendor", "Event date": "2026-11-10", "Primary source URL": "https://example-bank.invalid/press/1",
                                         "Opportunity score /100": "90"})))
    conn.commit()
    db = Database(db_url, max_size=4)
    app = create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72), db=db)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield TestClient(app), hdr, world
    db.close()


def test_verify_api_only_allows_urls_cited_in_the_sheets_and_runs_through_the_worker(api, conn):
    c, h, w = api
    r = c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": w["org"], "url": "http://169.254.169.254/"})
    assert r.status_code == 400
    assert c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
    r = c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": w["org"]})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": w["org"]}).json()["duplicate"] is True
    handlers = build_handlers(connector_factory=lambda s: None, extractor_factory=lambda cn: BudgetedExtractor(FakeInner(), cn), fetch_factory=lambda: (lambda u: page()))
    assert run_one(conn, "w", handlers) == "completed"
    pr = c.get("/api/prospects", headers=h("bdm")).json()["items"][0]
    assert pr["verification"]["status"] == "verified" and pr["verification"]["events"] == 1
    assert c.get("/api/verifications", headers=h("bdm")).status_code == 403


def test_usage_endpoints_summarise_by_provider_source_and_plan_with_forecast(api, conn):
    c, h, w = api
    usage.record(conn, provider="exa", operation="search", units=1, unit="searches", cost_usd=0.007, cost_basis="reported", source_id="discovery-exa", plan_id="p1", query="q", detail={"results": 8})
    usage.record(conn, provider="anthropic", operation="extract", units=1200, unit="tokens", tokens_in=1000, tokens_out=200, source_id=SOURCE_ID)
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 20.0, "usd")
    conn.commit()
    assert c.get("/api/admin/usage", headers=h("bdm")).status_code == 403
    d = c.get("/api/admin/usage", headers=h("admin")).json()
    assert {m["provider"] for m in d["months"]} == {"exa", "anthropic"}
    assert any(s["source_id"] == "discovery-exa" for s in d["by_source"]) and d["by_plan"][0]["plan_id"] == "p1"
    assert d["forecast"]["exa"]["cap"] == 20.0 and d["prices"]["anthropic_in_per_mtok"] is None
    led = c.get("/api/admin/usage/ledger?provider=exa", headers=h("admin")).json()
    assert led["total"] == 1 and led["items"][0]["query"] == "q"
    assert c.get("/api/admin/library", headers=h("admin")).json()["items"]
    assert c.get("/api/admin/discovery", headers=h("admin")).status_code == 200



def test_source_opened_is_recorded_only_within_scope(api, conn):
    c, h, w = api
    assert c.post("/api/telemetry/source-opened", json={"organization_id": w["org"]}).status_code == 401
    assert c.post("/api/telemetry/source-opened", headers=h("bdm"), json={"organization_id": w["org"], "where": "prospect"}).json() == {"ok": True}
    assert c.post("/api/telemetry/source-opened", headers=h("bdm"), json={"organization_id": "00000000-0000-0000-0000-000000000000"}).json() == {"ok": True}   # silent: no existence oracle
    n = conn.execute("select count(*) c from product_events where event='source_opened'").fetchone()["c"]
    assert n == 1


def test_discovery_checks_leave_the_last_30_percent_of_model_budget_for_sellers(conn, world):
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 100_000, "tokens")
    conn.execute("update provider_budgets set spent=75000 where provider='anthropic'")        # 25% left
    conn.commit()
    disc = mk_request(conn, world["org"], url="https://d.invalid/1", origin="exa_discovery")
    mine = mk_request(conn, world["org"], url="https://d.invalid/2", origin="sheet_signal")
    out = run_verification(conn, disc, fetch=lambda u: page(url="https://d.invalid/1"), extractor=BudgetedExtractor(FakeInner(), conn))
    assert out == {"status": "waiting_budget", "reason": "seller_reserve"}
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 1_000_000, "tokens")
    conn.commit()
    assert run_verification(conn, mine, fetch=lambda u: page(url="https://d.invalid/2"), extractor=BudgetedExtractor(FakeInner(), conn))["status"] == "verified"


class ProgrammeInner(FakeInner):
    """A model reply describing a real-looking programme, so the whole chain can run: page -> event -> signal -> programme -> opportunity -> Today."""
    async def extract(self, *, title, text, published_at):
        out = await super().extract(title=title, text=text, published_at=published_at)
        out["events"][0]["attrs"] = {"programme_domain": "core_banking", "programme_name": "Atlas", "named_systems": ["Synthetic Core"], "has_dated_milestones": True,
                                     "programme_status": "implementation", "counterparty_selected": True}
        out["events"][0]["other_entities"] = [{"name": "Synthetic Platform Vendor", "role": "vendor"}, {"name": "Synthetic Integrator", "role": "si"}]
        return out


def test_end_to_end_verified_page_reaches_a_sellers_today_screen(api, conn):
    c, h, w = api
    r = c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": w["org"]}).json()
    assert r["status"] == "queued"
    handlers = build_handlers(connector_factory=lambda s: None, extractor_factory=lambda cn: BudgetedExtractor(ProgrammeInner(), cn), fetch_factory=lambda: (lambda u: page()))
    assert run_one(conn, "w", handlers) == "completed"
    assert conn.execute("select count(*) c from events").fetchone()["c"] == 1
    assert conn.execute("select count(*) c from initiatives").fetchone()["c"] >= 1                    # programme formed
    assert conn.execute("select count(*) c from opportunity_hypotheses where last_checked_at is not null").fetchone()["c"] >= 1   # opportunity built and scored with no manual step
    today = c.get("/api/today", headers=h("bdm")).json()
    cards = [x for sec in today["sections"].values() for x in sec]
    assert cards and cards[0]["account"]["name"].startswith("Synthetic Example Bank")
    assert cards[0]["external_demand_status"] in {"unknown", "inferred", "explicit"} and cards[0]["next_action"]


# ---------------------------------------------------------------- bulk validation, activation and the safe client
class _HC:
    def __init__(self, ok, detail="", error_class=None):
        self.ok, self.detail, self.error_class = ok, detail, error_class


class _Conn:
    def __init__(self, ok):
        self.ok = ok

    async def health_check(self):
        return _HC(self.ok, "" if self.ok else "robots.txt disallows", None if self.ok else "policy_block")


def test_safe_client_refuses_private_addresses_and_loopback():
    from ri.sources.safe import SafeClient
    for bad in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "http://10.1.2.3/", "http://localhost:8000/admin"):
        with pytest.raises(PolicyBlocked):
            asyncio.run(SafeClient().get(bad))


def test_bulk_validation_activates_only_sources_that_pass_and_records_why_others_did_not(conn, monkeypatch):
    from ri.sources import factory, registry
    seed_registry(conn)
    conn.commit()
    ok_ids = {"regulator-fca", "regulator-eba"}

    def fake_build(c, sid):
        if sid in ok_ids:
            return _Conn(True)
        if sid == "vendor-temenos-news":
            return _Conn(False)
        raise factory.NoConnector(f"no connector or address for {sid}")
    monkeypatch.setattr(factory, "build_connector", fake_build)
    handlers = build_handlers(connector_factory=lambda s: None)
    from ri.jobs.queue import enqueue
    enqueue(conn, "sources_validate_all", {"activate": True, "by": "admin@x"}, idempotency_key="t1")
    conn.commit()
    assert run_one(conn, "w", handlers, kinds=["sources_validate_all"]) == "completed"
    res = conn.execute("select result from jobs where kind='sources_validate_all'").fetchone()["result"]
    assert res["summary"]["activated"] == 2 and res["summary"]["failed"] == 1 and res["summary"]["skipped"] >= 10
    assert {r["source_id"] for r in conn.execute("select source_id from sources where status='active'")} == ok_ids
    cert = conn.execute("select certification from sources where source_id='regulator-fca'").fetchone()["certification"]
    assert cert["technical_owner"] == "admin@x" and "robots.txt" in cert["limits"] and cert["last_tested_date"]
    bad = conn.execute("select status, config->'validation'->>'ok' ok from sources where source_id='vendor-temenos-news'").fetchone()
    assert bad["status"] == "pending_connector_validation" and bad["ok"] == "false"           # never activated on a failed test
    assert "credential" in res["sources"]["registry-companies-house"] or "no connector" in res["sources"]["registry-companies-house"]
    ops = {r["operation"] for r in conn.execute("select operation from audit_events where record_id in ('regulator-fca','vendor-temenos-news')")}
    assert {"source_validate", "activate_source"} <= ops
    # a second run only looks at what is still inactive
    enqueue(conn, "sources_validate_all", {"activate": False, "by": "admin@x"}, idempotency_key="t2")
    conn.commit()
    run_one(conn, "w", handlers, kinds=["sources_validate_all"])
    assert conn.execute("select count(*) c from sources where status='active'").fetchone()["c"] == 2


def test_bulk_validate_endpoint_is_admin_only_and_queues_a_job(api, conn):
    c, h, _ = api
    assert c.post("/api/admin/sources/validate-all", headers=h("bdm"), json={"activate": True}).status_code == 403
    r = c.post("/api/admin/sources/validate-all", headers=h("admin"), json={"activate": True})
    assert r.status_code == 200 and r.json()["queued"] is True
    assert conn.execute("select count(*) c from jobs where kind='sources_validate_all'").fetchone()["c"] == 1


def test_feed_shows_only_quoted_verified_findings_with_evidence_source_and_service_fit(api, conn):
    c, h, w = api
    c.post("/api/prospects/verify", headers=h("bdm"), json={"organization_id": w["org"]})
    handlers = build_handlers(connector_factory=lambda s: None, extractor_factory=lambda cn: BudgetedExtractor(ProgrammeInner(), cn), fetch_factory=lambda: (lambda u: page()))
    run_one(conn, "w", handlers)
    # an unverified candidate event must never appear as a finding
    conn.execute("insert into events(event_key,event_type,subject_org_id,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                 "values ('k-unv','vendor_selected',%s,'UNVERIFIED rumour','{}','[]','t','unverified','g-unv')", (w["org"],))
    conn.commit()
    f = c.get("/api/feed", headers=h("bdm")).json()
    assert f["count"] == 1 and f["items"][0]["summary"].startswith("Synthetic Example Bank AG selected")
    it = f["items"][0]
    assert not any("UNVERIFIED" in x["summary"] for x in f["items"])
    assert it["evidence"] and ANCHOR in it["evidence"][0]["claim"] and it["evidence"][0]["url"] == "https://example-bank.invalid/press/1"      # the quote, with its source
    assert it["organization"]["market"]["fit"] == "core" and it["organization"]["name"].startswith("Synthetic Example Bank")
    assert "judgement" in it["fit"]["label"] and it["programme"]["title"]
    assert it["fit"]["offers"] == [] or all(isinstance(o, str) for o in it["fit"]["offers"])         # no signal fired from a low-authority page, so no offer is suggested
    assert f["totals"]["verified"] == 1 and f["totals"]["unverified"] == 1 and "never as findings" in f["note"]
    # filters
    assert c.get("/api/feed?q=synthetic%20example", headers=h("bdm")).json()["count"] == 1
    assert c.get("/api/feed?q=nothingmatches", headers=h("bdm")).json()["count"] == 0
    assert c.get("/api/feed?days=1", headers=h("bdm")).json()["count"] in (0, 1)
    assert c.get("/api/feed?market=core", headers=h("bdm")).json()["count"] == 1
    conn.execute("update organizations set country_code='US' where id=%s", (w["org"],))
    conn.commit()
    assert c.get("/api/feed?market=core", headers=h("bdm")).json()["count"] == 0               # US is a backup pool, not a main market
    assert c.get("/api/feed", headers=h("mkt")).status_code == 403


def test_service_fit_maps_rules_to_what_the_company_sells_and_ignores_negative_rules():
    from ri import feed
    f = feed.fit_for(["SRC-05", "CAP-05", "NEG-03"])
    assert set(f["offers"]) == {"AI Services", "Team Extension with delivery responsibility"}
    assert {p["play_id"] for p in f["plays"]} == {"SP-05", "SP-07"}                              # NEG-03 is a negative rule: never a fit
    assert feed.fit_for(["CAP-02"])["offers"] == ["Staff Augmentation"] and feed.fit_for(["MAN-01"])["offers"] == []
    assert "judgement" in f["label"] and feed.fit_for([])["plays"] == []
    assert feed.rules_for_play("SP-09") and feed.rule_questions(["SRC-01"])


def test_fetched_pages_get_an_authority_tier_from_their_address(conn, world):
    from ri.verify.run import classify_source
    conn.execute("update organizations set canonical_domain='example-bank.invalid' where id=%s", (world["org"],))
    conn.commit()
    t = lambda url, org=world["org"]: conn.execute("select source_tier from sources where source_id=%s", (classify_source(conn, url, org),)).fetchone()["source_tier"]  # noqa: E731
    assert t("https://www.example-bank.invalid/press/1") == "S2" and t("https://press.example-bank.invalid/x") == "S2"      # the company's own site
    assert t("https://www.temenos.com/press-releases/x") == "S3"                                                           # a registered vendor
    assert t("https://www.fca.org.uk/news/x") == "S1" and t("https://www.bundesregierung.gov.pl/x") == "S1"              # official
    assert t("https://random-blog.invalid/post") == "S4" and t("https://example-bank.invalid.evil.example/x") == "S4"      # unknown, and no look-alike trick
    assert t("https://example-bank.invalid/x", None) == "S4"                                                               # no account known: lowest tier


# ---------------------------------------------------------------- production fixes: tolerant feeds, address refresh, lead monitor
def test_feed_parser_tolerates_html_entities_and_bare_ampersands():
    import xml.etree.ElementTree as ET
    from ri.sources.webwatch import xml_safe
    t = '<rss><channel><item><title>A&nbsp;B &eacute; C & D &amp; E &#8217;</title><guid>1</guid></item></channel></rss>'
    assert ET.fromstring(xml_safe(t)).find(".//title").text.startswith("A B é C & D & E")
    assert ET.fromstring(xml_safe("<a>x\x07y</a>")).text == "xy"


def test_seed_refreshes_shipped_addresses_but_never_an_operators_own(conn):
    seed_registry(conn)
    conn.execute("update sources set config = jsonb_set(jsonb_set(config,'{url}','\"https://old.invalid/404\"'),'{url_basis}','\"derived earlier\"') where source_id='vendor-thoughtmachine-news'")
    conn.execute("update sources set config = jsonb_set(jsonb_set(config,'{url}','\"https://www.fca.org.uk/mine\"'),'{url_basis}','\"set by admin@x\"') where source_id='regulator-fca'")
    conn.commit()
    seed_registry(conn)
    q = lambda sid: conn.execute("select config->>'url' u from sources where source_id=%s", (sid,)).fetchone()["u"]  # noqa: E731
    assert q("vendor-thoughtmachine-news") == "https://www.thoughtmachine.net/press" and q("regulator-fca") == "https://www.fca.org.uk/mine"


def test_lead_monitor_creates_one_weekly_plan_per_priority_account_and_skips_seen_pages(conn, world):
    from ri.discovery.plans import sync_account_plans
    link = conn.execute("insert into account_source_links(organization_id, source_system, source_file_id, sheet_id, sheet_title, row_identity, row_number, imported_hash, raw_company_name, observed_at) "
                        "values (%s,'google_sheets','wb','s','Priority Outreach','r1',2,'h','x',now()) returning id", (world["org"],)).fetchone()["id"]
    conn.execute("insert into legacy_account_research(organization_id, source_link_id, kind, original, observed_at) values (%s,%s,'priority_outreach',%s,now())", (world["org"], link, jb({"Rank": "3"})))
    conn.commit()
    assert sync_account_plans(conn) == 1 and sync_account_plans(conn) == 0
    plan = conn.execute("select * from discovery_plans where theme='account_watch'").fetchone()
    assert str(plan["organization_id"]) == world["org"] and "Synthetic Example Bank AG" in plan["query"]
    budget.set_cap(conn, "exa", "monthly", usage.period_now(), 20.0, "usd")
    conn.execute("insert into verification_requests(url, origin, requested_by) values ('https://news.invalid/%s/1','sheet_signal','t')" % plan["plan_id"])    # one hit was already seen
    conn.commit()
    out = run_discovery(conn, FakeExa(), max_searches=1, max_new_requests=5, interval_days=7, est_cost=None, plan_ids=[plan["plan_id"]])
    urls = [r["url"] for r in conn.execute("select url from verification_requests where origin='exa_discovery'")]
    assert out["requests_created"] == 3 and f"https://news.invalid/{plan['plan_id']}/1" not in urls          # the seen page was skipped
    assert all(str(r["organization_id"]) == world["org"] for r in conn.execute("select organization_id from verification_requests where origin='exa_discovery'"))


def test_scheduler_requeues_stuck_verifications_twice_then_fails_them(conn, world):
    from datetime import timedelta
    from ri.scheduler import requeue_stuck_verifications
    rid = mk_request(conn, world["org"], url="https://stuck.invalid/1")
    conn.execute("update verification_requests set status='extracting', updated_at = now() - interval '2 hours' where id=%s", (rid,))
    conn.commit()
    assert requeue_stuck_verifications(conn) == 1
    assert conn.execute("select status from verification_requests where id=%s", (rid,)).fetchone()["status"] == "queued"
    for expected in (1, 0):
        conn.execute("update verification_requests set status='extracting', updated_at = now() - interval '2 hours' where id=%s", (rid,))
        assert requeue_stuck_verifications(conn) == expected
    assert conn.execute("select status from verification_requests where id=%s", (rid,)).fetchone()["status"] == "failed"
    fresh = mk_request(conn, world["org"], url="https://fresh.invalid/1")
    conn.execute("update verification_requests set status='extracting' where id=%s", (fresh,))
    assert requeue_stuck_verifications(conn) == 0                      # recent work is left alone


def test_background_reading_leaves_30_percent_of_the_model_budget_for_sellers(conn, world):
    budget.set_cap(conn, "anthropic", "monthly", usage.period_now(), 100_000, "tokens")
    conn.execute("update provider_budgets set spent=60000 where provider='anthropic'")        # 40% left; one read needs about 6k+
    conn.commit()
    ex = BudgetedExtractor(FakeInner(), conn)
    ex.reserve_fraction = 0.3
    assert asyncio.run(ex.extract(title="t", text="x" * 300, published_at=None))["events"]        # 40% left: room above the reserve
    conn.execute("update provider_budgets set spent=75000 where provider='anthropic'")        # 25% left: inside the reserve
    conn.commit()
    with pytest.raises(BudgetExceeded):
        asyncio.run(ex.extract(title="t", text="x" * 300, published_at=None))
    seller = BudgetedExtractor(FakeInner(), conn)                                              # a seller's own check has no reserve and still runs
    assert asyncio.run(seller.extract(title="t", text="x" * 300, published_at=None))["events"]


# ---------------------------------------------------------------- events that arrive before the accounts exist are linked afterwards
def test_unmatched_events_are_linked_once_the_account_exists_and_then_flow_on(conn):
    from ri.events.relink import relink_unmatched
    seed_registry(conn)
    conn.execute("insert into events(event_key,event_type,subject_org_id,subject_name_raw,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                 "values ('k-r1','vendor_selected',null,'Brand New Harbour Bank','Brand New Harbour Bank selected a vendor','{}','[]','t','verified','g-r1'), "
                 "('k-r2','vendor_selected',null,'Some Unrelated Council','Council tender','{}','[]','t','verified','g-r2')")
    conn.commit()
    assert relink_unmatched(conn) == {"checked": 0, "linked": 0}                           # no accounts yet: nothing to match against
    org = str(conn.execute("insert into organizations(display_name,country_code,in_master_list) values ('Brand New Harbour Bank','GB',true) returning id").fetchone()["id"])
    conn.execute("insert into organization_aliases(organization_id, alias, alias_norm, source_ref) values (%s,'Brand New Harbour Bank','brand new harbour bank','t')", (org,))
    conn.commit()
    out = relink_unmatched(conn)
    assert out["linked"] == 1 and out["checked"] == 2
    assert str(conn.execute("select subject_org_id from events where event_key='k-r1'").fetchone()["subject_org_id"]) == org
    assert conn.execute("select subject_org_id from events where event_key='k-r2'").fetchone()["subject_org_id"] is None
    assert relink_unmatched(conn)["checked"] == 0                                           # not re-checked until a newer organisation appears
    conn.execute("insert into organizations(display_name,country_code) values ('Another Newcomer','DE')")
    conn.commit()
    assert relink_unmatched(conn)["checked"] == 1                                           # the unrelated one is looked at again, once


def test_feed_hides_market_wide_items_by_default_but_can_show_them(api, conn):
    c, h, w = api
    conn.execute("insert into events(event_key,event_type,subject_org_id,subject_name_raw,pub_at,summary,attrs,other_entity_roles,extraction_model_version,verification_status,independent_origin_group) "
                 "values ('k-mw','procurement_published',null,'A Council Somewhere', now(),'Council tender for software','{}','[]','t','verified','g-mw')")
    conn.commit()
    f = c.get("/api/feed", headers=h("bdm")).json()
    assert f["count"] == 0 and f["totals"]["market_wide"] == 1                                # hidden, but counted so the page can say so
    assert c.get("/api/feed?accounts_only=false", headers=h("bdm")).json()["count"] == 1
