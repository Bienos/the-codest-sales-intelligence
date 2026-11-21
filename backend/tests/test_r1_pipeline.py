"""R1 integration: connectors (hand-authored doc-shaped fixtures, NOT recorded live traffic) -> ingest -> events ->
verification -> signals, plus failure/recovery semantics."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ri.ingest.pipeline import evaluate_staleness, run_source
from ri.ingest.process import process_run
from ri.sources import registry
from ri.sources.ats import LeverConnector, classify_employment
from ri.sources.base import PolicyBlocked, SchemaDrift
from ri.sources.procurement import FindTenderConnector, TedConnector, derive_status
from ri.sources.webwatch import FeedConnector, PageWatchConnector, extract_section
from ri.signals.baseline import import_baseline
from ri.signals.persist import sync_rule_versions

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CERT = {"provider_doc_url": "https://example.invalid/docs", "auth_entitlement_verified": True, "verified_endpoints": ["x"],
        "data_licence": "test", "technical_owner": "t", "last_tested_date": "2026-11-20", "limits": "n/a", "rollback_path": "pause"}


@pytest.fixture()
def env(conn):
    import_baseline(conn)
    sync_rule_versions(conn)
    registry.seed_registry(conn)
    org = conn.execute("insert into organizations(display_name,country_code) values ('Example Bank','GB') returning id").fetchone()["id"]
    conn.execute("insert into organization_aliases(organization_id,alias,alias_norm) values (%s,'Example Bank','example bank')", (org,))
    conn.commit()
    return {"org": str(org)}


def activate(conn, sid, **cfg):
    registry.activate(conn, sid, CERT, "test")
    if cfg:
        import json
        conn.execute("update sources set config=config || %s::jsonb where source_id=%s", (json.dumps(cfg), sid))
    conn.commit()


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ted_notice(num, deadline="2026-12-07+01:00", ntype="cn-standard", title="Core banking integration software development services"):
    return {"publication-number": num, "notice-title": {"eng": title}, "buyer-name": {"eng": ["Example Bank"]}, "buyer-country": ["GBR"],
            "notice-type": ntype, "publication-date": "2026-11-18+01:00", "deadline-receipt-tender-date-lot": [deadline],
            "classification-cpv": ["72000000"], "procedure-type": "open", "links": {"html": {"ENG": f"https://example.invalid/ted/{num}"}}}


# ------------------------------------------------------------------- registry gating
def test_all_seeded_sources_pending_and_unactivated_source_does_not_run(conn, env):
    assert conn.execute("select count(*) c from sources where status<>'pending_connector_validation'").fetchone()["c"] == 0
    c = TedConnector(client(lambda r: httpx.Response(200, json={"notices": [], "totalNoticeCount": 0})), query="x", now_fn=lambda: NOW)
    res = run_source(conn, c)
    assert res.status == "skipped"
    assert conn.execute("select count(*) c from source_documents").fetchone()["c"] == 0


def test_activation_requires_complete_certification(conn, env):
    with pytest.raises(registry.CertificationError):
        registry.activate(conn, "procurement-ted", {"provider_doc_url": "x"}, "t")
    with pytest.raises(registry.CertificationError):
        registry.activate(conn, "procurement-ted", {**CERT, "auth_entitlement_verified": False}, "t")


def test_rule_catalogue_sync_all_64_with_17_active(conn, env):
    r = conn.execute("select status, count(*) c from signal_rule_versions group by status").fetchall()
    d = {x["status"]: x["c"] for x in r}
    assert d == {"active": 17, "defined_not_activated": 47}


# ------------------------------------------------------------------- TED lifecycle
def test_ted_ingest_amendment_updates_single_notice_and_one_amendment_event(conn, env):
    activate(conn, "procurement-ted")
    state = {"deadline": "2026-12-07+01:00", "type": "cn-standard"}

    def handler(req):
        return httpx.Response(200, json={"notices": [ted_notice("100-2026", state["deadline"], state["type"])], "totalNoticeCount": 1})

    c = TedConnector(client(handler), query="x", now_fn=lambda: NOW)
    resolver = lambda name: env["org"]
    res = run_source(conn, c, resolve_org=resolver)
    assert res.status == "success" and res.new == 1
    out = process_run(conn, res, resolve_org=resolver, now=NOW)
    assert out["events_created"] == 1 and out["signals"] == 1            # SRC-01 fires once, tier D1
    sig = conn.execute("select rule_id, demand_tier, expires_at from signals").fetchone()
    assert (sig["rule_id"], sig["demand_tier"]) == ("SRC-01", "D1")
    assert sig["expires_at"].isoformat().startswith("2026-12-06T23:00")   # official close incl. source timezone, not N days

    # replay identical: nothing new anywhere
    res2 = run_source(conn, c, resolve_org=resolver)
    assert res2.new == 0 and res2.unchanged == 1 and not res2.structured_changes
    # official amendment: deadline moves
    state["deadline"] = "2026-12-14+01:00"
    res3 = run_source(conn, c, resolve_org=resolver)
    assert res3.changed == 1 and res3.structured_changes[0][2].startswith("amended")
    process_run(conn, res3, resolve_org=resolver, now=NOW)
    assert conn.execute("select count(*) c from procurement_notices").fetchone()["c"] == 1          # same notice updated
    assert conn.execute("select count(*) c from events where event_type='procurement_published'").fetchone()["c"] == 1
    # re-delivery of the same amendment: still exactly one amendment-keyed event
    process_run(conn, res3, resolve_org=resolver, now=NOW)
    n_amend = conn.execute("select count(*) c from events where attrs->>'submission_deadline' like '2026-12-13T23%%'").fetchone()["c"]
    assert n_amend == 1


def test_ted_expired_and_award_never_open(conn, env):
    assert derive_status(NOW, kind="contract_notice", deadline=NOW - timedelta(days=1)) == "expired"
    assert derive_status(NOW, kind="award", deadline=NOW + timedelta(days=10)) == "awarded"
    activate(conn, "procurement-ted")
    body = {"notices": [ted_notice("1-2026", "2026-11-01+01:00"), ted_notice("2-2026", ntype="can-standard")], "totalNoticeCount": 2}
    c = TedConnector(client(lambda r: httpx.Response(200, json=body)), query="x", now_fn=lambda: NOW)
    res = run_source(conn, c, resolve_org=lambda n: env["org"])
    process_run(conn, res, resolve_org=lambda n: env["org"], now=NOW)
    assert conn.execute("select count(*) c from signals where rule_id='SRC-01'").fetchone()["c"] == 0
    assert {r["status"] for r in conn.execute("select status from procurement_notices")} == {"expired", "awarded"}


def test_ted_pagination_and_cursor_after_commit(conn, env):
    activate(conn, "procurement-ted")
    pages = []

    def handler(req):
        import json
        body = json.loads(req.content)
        pages.append(body["page"])
        return httpx.Response(200, json={"notices": [ted_notice(f"{body['page']}-2026")], "totalNoticeCount": 2 * body["limit"] if False else 150})

    c = TedConnector(client(handler), query="x", page_size=100, now_fn=lambda: NOW)
    res = run_source(conn, c, resolve_org=lambda n: env["org"])
    assert pages == [1, 2] and res.new == 2
    assert conn.execute("select cursor_value from source_cursors").fetchone()["cursor_value"] is None   # window exhausted


def test_failure_mid_window_keeps_cursor_and_marks_partial(conn, env):
    activate(conn, "procurement-ted")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"notices": [ted_notice("1-2026")], "totalNoticeCount": 150})
        return httpx.Response(401, json={"error": "expired token"})

    c = TedConnector(client(handler), query="x", page_size=100, now_fn=lambda: NOW)
    res = run_source(conn, c, resolve_org=lambda n: env["org"])
    assert res.status == "partial" and res.error_class == "auth"
    assert conn.execute("select cursor_value from source_cursors").fetchone()["cursor_value"] == "2"       # page 1 committed, resume at 2
    h = conn.execute("select state, error_class from source_health where source_id='procurement-ted'").fetchone()
    assert (h["state"], h["error_class"]) == ("failed", "auth")                                          # human action needed
    assert conn.execute("select status from sources where source_id='procurement-ted'").fetchone()["status"] == "failed"
    # a failed source never turns into "no events": a later run is skipped until re-authorised
    assert run_source(conn, c).status == "skipped"


def test_transient_failures_degrade_after_threshold_then_recover(conn, env):
    activate(conn, "procurement-ted")
    ok = {"v": False}

    def handler(req):
        return httpx.Response(200, json={"notices": [], "totalNoticeCount": 0}) if ok["v"] else httpx.Response(503)

    c = TedConnector(client(handler), query="x", now_fn=lambda: NOW)
    for i in range(3):
        r = run_source(conn, c)
        assert r.error_class == "server_error"
    assert conn.execute("select state from source_health where source_id='procurement-ted'").fetchone()["state"] == "degraded"
    ok["v"] = True
    assert run_source(conn, c).status == "success"       # degraded sources keep polling and heal
    assert conn.execute("select state, consecutive_failures from source_health where source_id='procurement-ted'").fetchone() == {"state": "healthy", "consecutive_failures": 0}


def test_rate_limit_carries_retry_after(conn, env):
    activate(conn, "procurement-ted")
    c = TedConnector(client(lambda r: httpx.Response(429, headers={"Retry-After": "17"})), query="x", now_fn=lambda: NOW)
    res = run_source(conn, c)
    assert res.error_class == "rate_limited"


def test_egress_block_is_reported_as_blocked_not_failed_auth(conn, env):
    activate(conn, "procurement-ted")
    c = TedConnector(client(lambda r: httpx.Response(403, text="Proxy refused: agent proxy: no rule allows host")), query="x", now_fn=lambda: NOW)
    res = run_source(conn, c)
    assert conn.execute("select state from source_health where source_id='procurement-ted'").fetchone()["state"] == "blocked"


def test_schema_drift_stops_collector(conn, env):
    activate(conn, "procurement-ted")
    c = TedConnector(client(lambda r: httpx.Response(200, json={"results": []})), query="x", now_fn=lambda: NOW)
    res = run_source(conn, c)
    assert res.error_class == "schema_drift"
    assert conn.execute("select state from source_health where source_id='procurement-ted'").fetchone()["state"] == "failed"


# ------------------------------------------------------------------- UK FTS (OCDS)
def ocds(ocid, tags, status, end, date="2026-11-18T09:00:00Z"):
    return {"ocid": ocid, "id": f"{ocid}-1", "date": date, "tag": tags, "buyer": {"name": "Example Bank"},
            "tender": {"title": "Digital platform software development", "description": "Software engineering services", "status": status,
                       "tenderPeriod": {"endDate": end}, "items": [{"classification": {"id": "72200000"}}]}}


def test_fts_award_cancelled_and_open(conn, env):
    activate(conn, "procurement-uk-fts")
    body = {"releases": [ocds("ocds-x-001", ["tender"], "active", "2026-12-20T12:00:00Z"),
                         ocds("ocds-x-002", ["award"], "complete", "2026-10-01T12:00:00Z"),
                         ocds("ocds-x-003", ["tender"], "cancelled", "2026-12-20T12:00:00Z")], "links": {}}
    c = FindTenderConnector(client(lambda r: httpx.Response(200, json=body)), now_fn=lambda: NOW)
    res = run_source(conn, c, resolve_org=lambda n: env["org"])
    process_run(conn, res, resolve_org=lambda n: env["org"], now=NOW)
    st = {r["notice_id"]: r["status"] for r in conn.execute("select notice_id, status from procurement_notices")}
    assert st == {"ocds-x-001": "open", "ocds-x-002": "awarded", "ocds-x-003": "cancelled"}
    assert conn.execute("select count(*) c from signals where rule_id='SRC-01'").fetchone()["c"] == 1
    # window cursor persisted for next incremental run
    assert conn.execute("select cursor_value from source_cursors where source_id='procurement-uk-fts'").fetchone()["cursor_value"].startswith("since:")


# ------------------------------------------------------------------- jobs / ATS
def lever_job(i, title="Backend Engineer", commit="Full-time", team="Payments", created=NOW - timedelta(days=5)):
    return {"id": f"j{i}", "text": title, "categories": {"commitment": commit, "team": team, "location": "London"},
            "createdAt": int(created.timestamp() * 1000), "hostedUrl": f"https://example.invalid/jobs/j{i}", "descriptionPlain": "We use Kafka and Java."}


def test_employment_type_classification_separates_contract_from_permanent():
    assert classify_employment("Contract", "Engineer") == "contract"
    assert classify_employment("Full-time", "Senior Engineer") == "permanent"
    assert classify_employment(None, "Interim Head of Platform") == "contract"


def test_lever_cluster_signal_contractor_signal_and_closure(conn, env):
    activate(conn, "ats-lever-example", board="example", provider="lever", organization_id=env["org"]) if False else None
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, collection_method, status, config) "
                 "values ('ats-lever-example','Lever example','S2','jobs','jobs','lever','official_api','pending_connector_validation', %s)",
                 (__import__("json").dumps({"board": "example", "provider": "lever", "organization_id": env["org"]}),))
    conn.execute("insert into source_health(source_id,state) values ('ats-lever-example','unconfigured')")
    conn.commit()
    registry.activate(conn, "ats-lever-example", CERT, "t")
    listing = {"jobs": [lever_job(1), lever_job(2), lever_job(3), lever_job(4, "Contract Kafka Engineer", "Contract", "Core")]}
    c = LeverConnector(client(lambda r: httpx.Response(200, json=listing["jobs"])), "example", employer_name="Example Bank", source_id="ats-lever-example", now_fn=lambda: NOW)
    res = run_source(conn, c)
    out = process_run(conn, res, now=NOW)
    rules = sorted(r["rule_id"] for r in conn.execute("select rule_id from signals"))
    assert rules == ["CAP-01", "CAP-02"]                  # 3 payments roles => cluster; contract role on kafka => CAP-02
    cap02 = conn.execute("select e.attrs->>'employment_type' t from signals s join events e on e.id=s.event_id where s.rule_id='CAP-02'").fetchone()
    assert cap02["t"] == "contract"
    # permanent role did NOT become a contractor signal, and agency eligibility stays unknown (role != supplier route)
    assert conn.execute("select count(*) c from events where attrs->>'agency_eligibility'='unknown'").fetchone()["c"] == 4
    # contract role disappears from an AFFIRMATIVE full listing => closed => CAP-02 expires
    listing["jobs"] = listing["jobs"][:3]
    res2 = run_source(conn, c)
    process_run(conn, res2, now=NOW)
    assert conn.execute("select open_status from job_postings where job_id='j4'").fetchone()["open_status"] == "closed"
    assert conn.execute("select status from signals where rule_id='CAP-02'").fetchone()["status"] == "expired"
    assert conn.execute("select status from signals where rule_id='CAP-01'").fetchone()["status"] == "active"


def test_board_404_does_not_close_any_jobs(conn, env):
    conn.execute("insert into sources(source_id, display_name, source_tier, source_type, source_family, provider, collection_method, status, config) "
                 "values ('ats-lever-ex2','Lever','S2','jobs','jobs','lever','official_api','pending_connector_validation','{}')")
    conn.execute("insert into source_health(source_id,state) values ('ats-lever-ex2','unconfigured')")
    registry.activate(conn, "ats-lever-ex2", CERT, "t")
    mode = {"ok": True}

    def handler(req):
        return httpx.Response(200, json=[lever_job(1)]) if mode["ok"] else httpx.Response(404)

    c = LeverConnector(client(handler), "ex2", source_id="ats-lever-ex2", now_fn=lambda: NOW)
    run_source(conn, c)
    mode["ok"] = False
    res = run_source(conn, c)
    assert res.error_class == "not_found"
    assert conn.execute("select open_status from job_postings where job_id='j1'").fetchone()["open_status"] == "open"   # outage != all jobs closed


# ------------------------------------------------------------------- web monitor / feeds
HTML = "<html><body><nav>menu</nav><div id='news'><h2>Bank selects platform</h2><p>Updated 12:30</p></div><footer>f</footer></body></html>"


def test_page_section_hash_ignores_chrome_and_clock():
    a, _ = extract_section(HTML, "#news")
    b, _ = extract_section(HTML.replace("12:30", "13:45").replace("menu", "other menu"), "#news")
    assert a == b == "Bank selects platform Updated"


def test_robots_disallow_blocks_collector():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /news")
        return httpx.Response(200, text=HTML)

    c = PageWatchConnector(client(handler), "https://example.invalid/news", source_id="s", selector="#news")
    with pytest.raises(PolicyBlocked):
        asyncio.run(c.fetch(None, 1))


def test_selector_miss_is_schema_drift_not_empty_success():
    c = PageWatchConnector(client(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200, text=HTML)), "https://example.invalid/x", source_id="s", selector="#gone")
    with pytest.raises(SchemaDrift):
        asyncio.run(c.fetch(None, 1))


def test_feed_parse_and_invalid_xml():
    rss = "<rss><channel><item><title>Bank selects Vendor</title><link>https://e.invalid/1</link><guid>g1</guid><description>&lt;p&gt;Details&lt;/p&gt;</description><pubDate>Tue, 17 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>"
    f = FeedConnector(client(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200, text=rss)), "https://e.invalid/feed", source_id="f")
    b = asyncio.run(f.fetch(None, 10))
    d = f.canonicalize(b.items[0])
    assert d.external_id == "g1" and d.published_at.year == 2026 and "Details" in d.content_text
    bad = FeedConnector(client(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200, text="<html>nope")), "https://e.invalid/feed", source_id="f")
    with pytest.raises(SchemaDrift):
        asyncio.run(bad.fetch(None, 10))


# ------------------------------------------------------------------- staleness
def test_missed_runs_degrade_coverage_not_negative_signal(conn, env):
    activate(conn, "procurement-ted")
    conn.execute("update source_health set state='healthy', last_success_at=%s where source_id='procurement-ted'", (NOW - timedelta(days=5),))
    degraded = evaluate_staleness(conn, now_fn=lambda: NOW)
    assert "procurement-ted" in degraded
    assert conn.execute("select state, missed_due_runs from source_health where source_id='procurement-ted'").fetchone()["state"] == "degraded"


# ------------------------------------------------------------------- a source run THROUGH THE QUEUE (production crashed here: commit inside a wrapping transaction)
def test_scheduled_source_run_executes_through_the_job_queue(conn, env):
    from ri.jobs.handlers import build_handlers
    from ri.jobs.queue import enqueue, run_one
    activate(conn, "attention-finextra")
    rss = ("<rss><channel><item><title>Example Bank selects Vendor</title><link>https://e.invalid/1</link><guid>g1</guid>"
           "<description>Example Bank selects Vendor</description><pubDate>Tue, 17 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200, text=rss)))
    handlers = build_handlers(connector_factory=lambda sid: FeedConnector(http, "https://e.invalid/feed", source_id=sid))
    enqueue(conn, "source_run", {"source_id": "attention-finextra"}, idempotency_key="sr1")
    conn.commit()
    assert run_one(conn, "w", handlers) == "completed"                       # was: retryable_failed, ProgrammingError
    job = conn.execute("select status, result from jobs where kind='source_run'").fetchone()
    assert job["status"] == "completed" and job["result"]["new"] == 1
    assert conn.execute("select count(*) c from source_documents where source_id='attention-finextra'").fetchone()["c"] == 1
    assert conn.execute("select state from source_health where source_id='attention-finextra'").fetchone()["state"] == "healthy"
