"""Deal stage history (capture from sync and webhooks, backfill from Pipedrive's deal flow), funnel measurements, the forecast simulation
and its API."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.crm import pipedrive as pd
from ri.db import Database
from ri.forecast import engine, service
from ri.forecast.history import load_funnel, measure
from test_r4_crm_notify import mk_user

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
STAGES = [(10, "Webform MQL", 1), (11, "Marketing Nurturing", 2), (7, "DISCOVERY", 3), (12, "SQL", 4), (13, "Proposal/Shortlist", 5),
          (14, "Commercials/NDA", 6), (15, "Active Delivery", 7)]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def deal(i, stage, status="open", add=None, moved=None, won=None, lost=None, value=100000, upd=None):
    return {"id": i, "title": f"Deal {i}", "org_id": None, "owner_id": 555, "stage_id": stage, "pipeline_id": 2, "status": status, "value": value, "currency": "EUR",
            "add_time": iso(add) if add else None, "stage_change_time": iso(moved) if moved else None, "won_time": iso(won) if won else None,
            "lost_time": iso(lost) if lost else None, "update_time": iso(upd or moved or add or NOW), "lost_reason": "Budget" if lost else None}


def seed_stages(conn):
    for sid, name, order in STAGES:
        conn.execute("insert into crm_stages(crm_id, pipeline_id, name, order_nr) values (%s,2,%s,%s)", (sid, name, order))


def rows(conn, deal_id):
    return [(r["from_stage_id"], r["to_stage_id"], r["from_status"], r["to_status"], r["source"]) for r in
            conn.execute("select * from deal_stage_transitions where crm_deal_id=%s order by at, id", (deal_id,)).fetchall()]


# ------------------------------------------------------------------ capture
def test_sync_and_webhooks_log_every_stage_and_status_change_once(conn):
    seed_stages(conn)
    t0 = NOW - timedelta(days=60)
    pd.upsert_deal(conn, deal(1, 10, add=t0))                                                     # never moved: entered at creation
    pd.upsert_deal(conn, deal(1, 10, add=t0))                                                     # same copy again: nothing new
    pd.upsert_deal(conn, deal(1, 7, add=t0, moved=t0 + timedelta(days=5)))                        # hourly sync sees a move
    pd.upsert_deal(conn, deal(1, 14, "won", add=t0, moved=t0 + timedelta(days=30), won=t0 + timedelta(days=40)), source="webhook")
    assert rows(conn, 1) == [(None, 10, None, "open", "created"), (10, 7, "open", "open", "sync"), (7, 14, "open", "open", "webhook"), (14, 14, "open", "won", "webhook")]
    d = conn.execute("select stage_name, add_time, won_time, lost_reason from crm_deals where crm_id=1").fetchone()
    assert d["stage_name"] == "Commercials/NDA" and d["add_time"] == t0.replace(microsecond=0) and d["won_time"] is not None

    pd.upsert_deal(conn, deal(2, 12, add=t0, moved=t0 + timedelta(days=9)))                       # met mid-way: occupancy, not an entry we saw
    assert rows(conn, 2) == [(None, 12, None, "open", "first_seen")]
    pd.upsert_deal(conn, deal(3, 7, "lost", add=t0, moved=t0 + timedelta(days=3), lost=t0 + timedelta(days=20)))
    assert rows(conn, 3) == [(None, 7, None, "open", "first_seen"), (7, 7, "open", "lost", "first_seen")]


def test_existing_mirror_rows_get_a_first_entry_and_partial_webhooks_keep_dates(conn):
    conn.execute("insert into crm_deals(crm_id, title, stage_id, status) values (5, 'Old deal', 7, 'open')")     # mirrored before history existed
    pd.upsert_deal(conn, deal(5, 7, add=NOW - timedelta(days=10)))
    assert rows(conn, 5) == [(None, 7, None, "open", "created")]
    pd.upsert_deal(conn, {**deal(5, 12, moved=NOW), "add_time": None}, source="webhook")
    assert conn.execute("select add_time from crm_deals where crm_id=5").fetchone()["add_time"] is not None


def test_webhook_inbox_logs_deal_moves(conn):
    pd.upsert_deal(conn, deal(8, 10, add=NOW - timedelta(days=3)))
    pd.receive_webhook(conn, {"meta": {"entity": "deal", "action": "change", "webhook_id": "d8"}, "data": deal(8, 7, add=NOW - timedelta(days=3), moved=NOW)})
    assert pd.process_inbox(conn) == {"processed": 1, "quarantined": 0}
    assert rows(conn, 8)[-1] == (10, 7, "open", "open", "webhook")


def test_sync_reads_the_stage_list_and_names_deals(conn):
    pages = {"stages": [[{"id": 7, "pipeline_id": 2, "name": "DISCOVERY", "order_nr": 3}]], "organizations": [[]], "persons": [[]],
             "deals": [[deal(9, 7, add=NOW - timedelta(days=2))]]}

    def handler(req):
        return httpx.Response(200, json={"success": True, "data": pages[req.url.path.rsplit("/", 1)[-1]][0], "additional_data": {}})
    out = pd.sync_incremental(conn, pd.PipedriveReader("example", "tok", http=httpx.Client(transport=httpx.MockTransport(handler))))
    assert out["stages"] == 1 and out["deals"] == 1
    assert conn.execute("select stage_name from crm_deals where crm_id=9").fetchone()["stage_name"] == "DISCOVERY"


# ------------------------------------------------------------------ backfill
def flow_item(i, key, old, new, at):
    return {"object": "dealChange", "timestamp": at.strftime("%Y-%m-%d %H:%M:%S"),
            "data": {"id": i, "field_key": key, "old_value": old, "new_value": new, "log_time": at.strftime("%Y-%m-%d %H:%M:%S")}}


def test_backfill_reads_paged_flow_and_is_idempotent(conn):
    t0 = NOW - timedelta(days=90)
    conn.execute("insert into crm_deals(crm_id, title, stage_id, status, pipeline_id, value, currency, add_time) values (20,'Atlas',14,'won',2,50000,'EUR',%s)", (t0,))
    flow = [flow_item(1, "stage_id", "10", "7", t0 + timedelta(days=4)), {"object": "activity", "data": {}},
            flow_item(2, "title", "a", "b", t0 + timedelta(days=5)), flow_item(3, "stage_id", "7", "14", t0 + timedelta(days=30)),
            flow_item(4, "status", "open", "won", t0 + timedelta(days=45))]
    calls = []

    def handler(req):
        assert req.url.path == "/api/v1/deals/20/flow"
        start = int(req.url.params["start"])
        calls.append(start)
        page = flow[start:start + 2]
        more = start + 2 < len(flow)
        return httpx.Response(200, json={"success": True, "data": page, "additional_data": {"pagination": {"more_items_in_collection": more, "next_start": start + 2}}})
    reader = pd.PipedriveReader("example", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = pd.backfill_deal_history(conn, reader)
    assert out == {"deals": 1, "transitions": 4, "remaining": 0} and calls == [0, 2, 4]
    assert rows(conn, 20) == [(None, 10, None, "open", "backfill"), (10, 7, "open", "open", "backfill"), (7, 14, "open", "open", "backfill"),
                              (14, 14, "open", "won", "backfill")]
    assert pd.backfill_deal_history(conn, reader) == {"deals": 0, "transitions": 0, "remaining": 0}


def test_hourly_crm_step_backfills_history_and_survives_a_backfill_failure(conn, monkeypatch):
    from ri import scheduler
    monkeypatch.setattr(pd, "sync_incremental", lambda c, r: {"deals": 0})
    monkeypatch.setattr(pd, "process_inbox", lambda c: {"processed": 0})
    calls = []
    monkeypatch.setattr(pd, "backfill_deal_history", lambda c, r, limit: calls.append(limit) or {"deals": 3, "transitions": 9, "remaining": 0})
    st = Settings(pipedrive_api_token="t", pipedrive_domain="d")
    out = scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 10, 1, tzinfo=timezone.utc))
    assert out["deal_history_backfill"]["deals"] == 3 and calls == [100]

    def boom(c, r, limit):
        raise pd.CrmError("Pipedrive 500 after retries")
    monkeypatch.setattr(pd, "backfill_deal_history", boom)
    out = scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 11, 1, tzinfo=timezone.utc))
    assert out["crm_sync"] == {"deals": 0} and "Pipedrive 500" in out["deal_history_backfill_error"]


# ------------------------------------------------------------------ measurement
def add_history(conn, deal_id, steps, status, value=100000, stage=None):
    """steps: [(stage_id, days_after_start)], then optional ('won'|'lost', day). Written as backfilled history."""
    t0 = NOW - timedelta(days=100)
    conn.execute("insert into crm_deals(crm_id, title, stage_id, status, pipeline_id, value, currency, add_time, won_time, history_backfilled_at) "
                 "values (%s,%s,%s,%s,2,%s,'EUR',%s,%s,now())",
                 (deal_id, f"Deal {deal_id}", stage or steps[-1][0], status, value, t0, t0 + timedelta(days=steps[-1][1]) if status == "won" else None))
    rows_, prev = [], None
    for sid, day in steps:
        rows_.append((prev, sid, None if prev is None else "open", "open", t0 + timedelta(days=day), "backfill"))
        prev = sid
    if status in ("won", "lost"):
        rows_.append((prev, prev, "open", status, t0 + timedelta(days=steps[-1][1] + 5), "backfill"))
    pd._add_transitions(conn, deal_id, rows_, pipeline_id=2, value=value, currency="EUR")


@pytest.fixture()
def history(conn):
    seed_stages(conn)
    add_history(conn, 1, [(10, 0), (7, 10), (12, 30), (13, 50), (14, 70)], "won", value=200000)      # all the way
    add_history(conn, 2, [(10, 0), (7, 6)], "lost")                                                    # lost in Discovery
    add_history(conn, 3, [(7, 0), (12, 20)], "open")                                                   # outbound: starts in Discovery, sits in SQL
    add_history(conn, 4, [(10, 0), (11, 8)], "open")                                                   # parked in Marketing Nurturing
    return load_funnel(conn, 2, ["Marketing Nurturing"], ["Active Delivery"])


def test_funnel_rates_count_entries_losses_and_skips(conn, history):
    f = history
    assert [s.name for s in f.stages] == ["Webform MQL", "DISCOVERY", "SQL", "Proposal/Shortlist", "Commercials/NDA"] and f.parking_ids == {11} and f.won_ids == {15}
    h = measure(conn, f, now=NOW)
    got = [(s.passed, s.lost, s.still_open) for s in h.stats]
    assert got == [(2, 1, 0), (2, 1, 0), (1, 0, 1), (1, 0, 0), (1, 0, 0)]     # MQL: 2 moved on, 1 parked; Discovery: deal 3 entered there
    assert h.stats[0].days == [10.0, 6.0] and h.stats[1].median_days() == 20.0
    assert h.entries_by_stage[:2] == [3, 1]
    assert [(o["crm_id"], o["stage_index"], round(o["days_in_stage"])) for o in h.open_deals] == [(3, 2, 80)]
    assert h.parked["n"] == 1 and h.won_values == [200000.0]


# ------------------------------------------------------------------ engine
def run(inp, today=date(2027, 1, 5), year=2027, runs=400):
    cal = engine.Calendar(today, year)
    dr = engine.Draws(runs, len(inp.open_stage), len(inp.conv), len(inp.streams), len(cal.months), max(l for l, _ in inp.streams) * 1.3)
    return engine.simulate(inp, cal, dr)


def test_engine_closes_a_sure_deal_in_the_right_quarter_and_nothing_when_rates_are_zero():
    sure = engine.Inputs(conv=[1, 1], days=[2, 2], open_stage=[1], open_value=[100000], open_age=[0], streams=[(0.0, 0)], deal_value=1)
    res = run(sure)
    assert res["wins"] == pytest.approx(1.0) and res["buckets"][1]["open"] == pytest.approx(100000, rel=0.1)
    none = engine.Inputs(conv=[0, 0], days=[2, 2], open_stage=[0], open_value=[100000], open_age=[0], streams=[(5.0, 0)], deal_value=100000)
    assert run(none)["mean"] == 0


def test_engine_whatifs_move_in_the_right_direction_without_noise():
    base = engine.Inputs(conv=[0.6, 0.5, 0.6], days=[10, 20, 20], open_stage=[0, 1, 2], open_value=[1e5, 1e5, 1e5], open_age=[5, 5, 40],
                         streams=[(3.0, 1), (2.0, 0)], deal_value=1e5)
    cal = engine.Calendar(date(2026, 11, 27), 2027)
    dr = engine.Draws(500, 3, 3, 2, len(cal.months), 3.0 * 1.3)
    m = engine.simulate(base, cal, dr)["mean"]
    better = engine.simulate(service._with(base, conv=[0.6, 0.6, 0.6]), cal, dr, detail=False)["mean"]
    more = engine.simulate(service._with(base, streams=[(3.6, 1), (2.0, 0)]), cal, dr, detail=False)["mean"]
    assert better > m and more > m
    assert engine.simulate(base, cal, dr)["mean"] == m                       # same draws, same answer
    assert len(cal.months) == 13 and cal.edges[0] == (date(2027, 1, 1) - date(2026, 11, 27)).days


# ------------------------------------------------------------------ service
def test_build_without_pipedrive_uses_placeholders_and_daily_step_runs_once(conn):
    s = Settings()
    p = service.build(conn, s, now=NOW, use_cache=False)
    assert p["stages_source"] == "placeholder" and p["year"] == 2027 and p["result"]["year"]["p50"] >= 0
    assert any("No Pipedrive stages" in w for w in p["health"]["warnings"])
    out = service.daily_step(conn, s, now=NOW)
    assert out["forecast_logged"] is True
    assert service.daily_step(conn, s, now=NOW + timedelta(hours=1)) == {}
    assert len(service.forecast_log(conn, 2027)) == 1


def test_build_blends_measured_rates_with_priors(conn, history):
    s = Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7)
    p = service.build(conn, s, now=NOW, use_cache=False)
    st = p["inputs"]["stages"]
    assert p["stages_source"] == "pipedrive" and st[0]["measured"]["conv"] == pytest.approx(200 / 3)
    assert st[0]["used"]["conv"] == pytest.approx((0.55 * 20 + 2) / 23 * 100)          # 20 deals' worth of prior + 3 decided deals
    assert p["inputs"]["outbound_entry"]["index"] == 1 and p["pipeline"]["deals"] == 1 and p["pipeline"]["parked"]["n"] == 1
    assert p["sensitivity"] and {x["key"] for x in p["sensitivity"]} >= {"reply_rate", "deal_value", "days"}
    conn.execute("insert into crm_deals(crm_id, title, stage_id, status) values (99,'snap',12,'open')")
    assert service.snapshot_pipeline(conn, NOW.date()) == 3 and service.snapshot_pipeline(conn, NOW.date()) == 0     # every open deal, parked ones too


def test_outbound_actuals_count_leads_once_and_ignore_opens(conn):
    for i, (typ, email) in enumerate([("emailsSent", "a@x"), ("emailsSent", "a@x"), ("emailsSent", "b@x"), ("emailsOpened", "b@x"), ("emailsReplied", "b@x"),
                                      ("emailsInterested", "b@x"), ("emailsBounced", "c@x"), ("emailsSent", "c@x")]):
        conn.execute("insert into lemlist_activities(id, type, email, campaign_id, campaign_name, happened_at) values (%s,%s,%s,'cam1','DACH Procurement', now() - interval '3 days')",
                     (f"act{i}", typ, email))
    o = service.outbound_actuals(conn)
    assert (o["contacted"], o["replied"], o["interested"], o["bounced"], o["sends"]) == (3, 1, 1, 1, 4)
    assert o["campaigns"][0]["name"] == "DACH Procurement" and o["campaigns"][0]["leads"] == 3


# ------------------------------------------------------------------ API
SECRET = "x" * 32


@pytest.fixture()
def api(conn, db_url):
    mk_user(conn, "mgr@x", "sales_manager", 700)
    mk_user(conn, "bdm@x", "bdm", 555)
    conn.commit()
    db = Database(db_url, max_size=4)
    c = TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET), db=db))
    yield c, (lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"})
    db.close()


def test_forecast_api_is_for_managers_and_changes_are_validated_and_audited(api, conn):
    c, h = api
    assert c.get("/api/forecast", headers=h("bdm")).status_code == 403
    r = c.get("/api/forecast", headers=h("mgr"))
    assert r.status_code == 200 and r.json()["result"]["chance_of_target"] is None
    assert c.put("/api/forecast/settings", json={"target": 1}, headers=h("bdm")).status_code == 403
    assert c.put("/api/forecast/settings", json={"prior_conv": {"7": 150}}, headers=h("mgr")).status_code == 422
    r = c.put("/api/forecast/settings", json={"target": 900000, "leads_per_month": 500, "prior_conv": {"-1": 70}}, headers=h("mgr"))
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == 900000 and body["result"]["chance_of_target"] is not None and body["inputs"]["stages"][0]["prior"]["conv"] == 70
    assert body["inputs"]["leads_per_month"]["used"] == 500
    r = c.put("/api/forecast/settings", json={"clear": ["leads_per_month"]}, headers=h("mgr"))
    assert r.json()["inputs"]["leads_per_month"]["setting"] is None
    assert conn.execute("select count(*) n from audit_events where operation='forecast_settings_changed'").fetchone()["n"] == 2
    assert conn.execute("select count(*) n from forecast_log where trigger='settings_saved'").fetchone()["n"] == 2


# ------------------------------------------------------------------ webhooks, real-pipeline details
def test_each_webhook_event_is_kept_and_deleted_deals_leave_the_mirror(conn):
    def ev(action, eid, ts, data):
        return {"meta": {"entity": "deal", "action": action, "entity_id": str(eid), "webhook_id": "hook-1", "timestamp": ts}, "data": data}
    pd.upsert_deal(conn, deal(30, 10, add=NOW - timedelta(days=5)))
    a = pd.receive_webhook(conn, ev("change", 30, "2026-11-27T10:00:00Z", deal(30, 7, add=NOW - timedelta(days=5), moved=NOW)))
    b = pd.receive_webhook(conn, ev("change", 30, "2026-11-27T11:00:00Z", deal(30, 12, add=NOW - timedelta(days=5), moved=NOW + timedelta(hours=1))))
    assert a[1] and b[1] and a[0] != b[0]                               # same webhook, two events: both kept (webhook_id is not an event id)
    assert pd.receive_webhook(conn, ev("change", 30, "2026-11-27T11:00:00Z", {}))[1] is False      # Pipedrive's retry of the same event
    gone = {"meta": {"entity": "deal", "action": "delete", "entity_id": "30", "webhook_id": "hook-1", "timestamp": "2026-11-27T12:00:00Z"},
            "data": None, "previous": {"id": 30, "title": "Deal 30"}}
    pd.receive_webhook(conn, gone)
    assert pd.process_inbox(conn) == {"processed": 3, "quarantined": 0}
    assert conn.execute("select count(*) n from crm_deals where crm_id=30").fetchone()["n"] == 0
    assert [r[1] for r in rows(conn, 30)] == [10, 7, 12]                  # history stays: it is append-only


def test_app_registers_its_deal_webhook_once(conn):
    hooks, posted = [], []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"success": True, "data": hooks})
        body = __import__("json").loads(req.content)
        posted.append(body)
        hooks.append({**body, "id": 77, "is_active": True})
        return httpx.Response(201, json={"success": True, "data": {"id": 77}})
    reader = pd.PipedriveReader("thecodestltd", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert pd.ensure_deal_webhook(reader, "https://ri.example/", "pw") == {"webhook": "created", "id": 77}
    assert pd.ensure_deal_webhook(reader, "https://ri.example", "pw") == {"webhook": "present", "id": 77}
    assert posted == [{"subscription_url": "https://ri.example/webhooks/pipedrive", "event_action": "*", "event_object": "deal", "version": "2.0",
                       "http_auth_user": "pipedrive", "http_auth_password": "pw"}]


def test_webhook_password_and_public_url_come_from_existing_settings(monkeypatch):
    for k in ("PIPEDRIVE_WEBHOOK_SECRET", "RI_PUBLIC_URL", "RI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RI_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://ri-api.example/")
    a, b = Settings.from_env(require_auth=False), Settings.from_env(require_auth=False)
    assert a.pipedrive_webhook_secret and a.pipedrive_webhook_secret == b.pipedrive_webhook_secret and a.public_url == "https://ri-api.example"
    monkeypatch.setenv("PIPEDRIVE_WEBHOOK_SECRET", "explicit")
    assert Settings.from_env(require_auth=False).pipedrive_webhook_secret == "explicit"


def test_real_pipeline_parks_passive_converts_currencies_and_values_empty_deals(conn):
    for sid, name, order in [(59, "Marketing Nurturing ", 1), (58, "Webform MQL", 2), (7, "DISCOVERY", 3), (8, "SQL", 4), (9, "Proposal/Shortlist", 5),
                             (23, "Commercials/NDA", 6), (42, "Active Delivery", 7), (27, "On-Hold", 8), (11, "Passive", 9)]:
        conn.execute("insert into crm_stages(crm_id, pipeline_id, name, order_nr) values (%s,2,%s,%s)", (sid, name, order))
    pd.upsert_deal(conn, {**deal(40, 8, add=NOW - timedelta(days=20)), "value": 100000, "currency": "PLN"})
    pd.upsert_deal(conn, {**deal(41, 7, add=NOW - timedelta(days=10)), "value": 0})
    pd.upsert_deal(conn, deal(42, 11, add=NOW - timedelta(days=300)))
    p = service.build(conn, Settings(pipedrive_pipeline_id=2, pipedrive_stage_id=7), now=NOW, use_cache=False)
    assert [st["name"] for st in p["inputs"]["stages"]] == ["Webform MQL", "DISCOVERY", "SQL", "Proposal/Shortlist", "Commercials/NDA"]
    st = {s_["name"]: s_ for s_ in p["inputs"]["stages"]}
    assert st["SQL"]["open"]["value"] == pytest.approx(23500)                 # 100k PLN at 0.235
    assert st["DISCOVERY"]["open"]["value"] == pytest.approx(120000)          # no value in Pipedrive: the average deal
    assert p["pipeline"]["without_value"] == 1 and p["pipeline"]["parked"]["n"] == 1 and p["currency"] == "EUR"
    assert any("no value in Pipedrive" in w for w in p["health"]["warnings"])


def test_hourly_step_registers_the_webhook_when_the_app_knows_its_address(conn, monkeypatch):
    from ri import scheduler
    monkeypatch.setattr(pd, "sync_incremental", lambda c, r: {"deals": 0})
    monkeypatch.setattr(pd, "process_inbox", lambda c: {"processed": 0})
    monkeypatch.setattr(pd, "backfill_deal_history", lambda c, r, limit: {"deals": 0, "transitions": 0, "remaining": 0})
    seen = []
    monkeypatch.setattr(pd, "ensure_deal_webhook", lambda r, url, pw: seen.append((url, pw)) or {"webhook": "created", "id": 5})
    st = Settings(pipedrive_api_token="t", pipedrive_domain="d", pipedrive_webhook_secret="pw", public_url="https://ri.example")
    assert scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 10, 1, tzinfo=timezone.utc))["pipedrive_webhook"]["id"] == 5
    assert seen == [("https://ri.example", "pw")]
    assert "pipedrive_webhook" not in scheduler.crm_sync_step(conn, Settings(pipedrive_api_token="t", pipedrive_domain="d"), datetime(2026, 11, 27, 11, 1, tzinfo=timezone.utc))


def test_first_sync_runs_at_once_and_history_catches_up_every_tick(conn, monkeypatch):
    from ri import scheduler
    synced, backfills = [], []
    monkeypatch.setattr(pd, "sync_incremental", lambda c, r: synced.append(1) or c.execute(
        "insert into sync_state(system, entity, watermark) values ('pipedrive','deals','x') on conflict do nothing") and {"deals": 1})
    monkeypatch.setattr(pd, "process_inbox", lambda c: {"processed": 0})
    monkeypatch.setattr(pd, "backfill_deal_history", lambda c, r, limit: backfills.append(1) or {"deals": 1, "transitions": 2, "remaining": 1})
    st = Settings(pipedrive_api_token="t", pipedrive_domain="d")
    out = scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 18, 20, tzinfo=timezone.utc))    # never synced: runs now, not at 19:00
    assert "crm_sync" in out and synced == [1]
    conn.execute("insert into crm_deals(crm_id, title, status) values (500, 'Waiting for history', 'open')")
    out = scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 18, 25, tzinfo=timezone.utc))    # mid-hour: history only
    assert "crm_sync" not in out and out["deal_history_backfill"]["deals"] == 1 and synced == [1] and len(backfills) == 2
    conn.execute("update crm_deals set history_backfilled_at=now()")
    assert scheduler.crm_sync_step(conn, st, datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)) == {}    # nothing to catch up: waits for the hour


def test_secrets_are_read_from_render_secret_files_and_trimmed(monkeypatch, tmp_path):
    from ri import config
    monkeypatch.setattr(config, "SECRET_FILES_DIR", str(tmp_path))
    monkeypatch.delenv("PIPEDRIVE_API_TOKEN", raising=False)
    monkeypatch.delenv("PIPEDRIVE_API_TOKEN_FILE", raising=False)
    assert config.read_secret("PIPEDRIVE_API_TOKEN") is None
    (tmp_path / "PIPEDRIVE_API_TOKEN").write_text("from-file\n")
    assert config.read_secret("PIPEDRIVE_API_TOKEN") == "from-file"
    monkeypatch.setenv("PIPEDRIVE_API_TOKEN", "  from-env \n")
    assert config.read_secret("PIPEDRIVE_API_TOKEN") == "from-env"


def test_forecast_reports_where_its_data_comes_from_and_can_recalculate_now(api, conn):
    c, h = api
    pd.upsert_deal(conn, deal(60, 10, add=NOW - timedelta(days=3)))
    conn.execute("update crm_deals set history_backfilled_at=now() where crm_id=60")
    conn.commit()
    r = c.get("/api/forecast?fresh=true", headers=h("mgr")).json()
    src = r["sources"]
    assert src["history"] == {"deals": 1, "read": 1, "changes": 1} and src["pipedrive"]["last_sync"] and src["webhook"]["waiting"] == 0
    assert r["can"] == {"manage": True, "sync": True}
