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
