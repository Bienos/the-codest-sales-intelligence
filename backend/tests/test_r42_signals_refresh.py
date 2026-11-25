"""Buying signals round 4: the pulse at the top, the family filter, the best-fit column, how fresh the signals are and "Refresh now"."""
from datetime import datetime, timedelta, timezone

import pytest

from test_r29_buying_signals import _names, event, world  # noqa: F401  (the world fixture: companies, events, people and an app)

NOW = datetime.now(timezone.utc)
SIGNAL_SOURCES = ["attention-finextra", "attention-sifted", "ats-greenhouse", "procurement-ted"]


def _switch_on(conn, ids, *, checked_minutes_ago=None):
    conn.execute("UPDATE sources SET status='active' WHERE source_id = ANY(%s)", (ids,))
    for sid in ids:
        last = checked_minutes_ago.get(sid) if isinstance(checked_minutes_ago, dict) else checked_minutes_ago
        at = NOW - timedelta(minutes=last) if last is not None else None
        conn.execute("INSERT INTO source_health(source_id, state, last_attempt_at, last_success_at) VALUES (%s,'healthy',%s,%s) "
                     "ON CONFLICT (source_id) DO UPDATE SET last_attempt_at=EXCLUDED.last_attempt_at, last_success_at=EXCLUDED.last_success_at",
                     (sid, at, at))


def _beat(conn, component, minutes_ago=1, detail='{"can": {"ai": true, "exa": true}}'):
    conn.execute("INSERT INTO system_heartbeats(component, at, detail) VALUES (%s, now() - make_interval(mins => %s), %s) "
                 "ON CONFLICT (component) DO UPDATE SET at=EXCLUDED.at, detail=EXCLUDED.detail", (component, minutes_ago, detail))


@pytest.fixture()
def live(world, conn):
    """Four free signal sources switched on, plus three that must never be touched: paid search, a regulator and an internal page fetcher."""
    _switch_on(conn, SIGNAL_SOURCES, checked_minutes_ago={"attention-finextra": 120, "attention-sifted": 10, "ats-greenhouse": 600, "procurement-ted": None})
    _switch_on(conn, ["discovery-exa", "regulator-fca", "verify-company-page"], checked_minutes_ago=600)
    _beat(conn, "worker")
    _beat(conn, "scheduler", 2)
    conn.commit()
    return world


# ---------------------------------------------------------------------------------------------------------------- the pulse and filters

def test_pulse_counts_families_weeks_and_the_strongest_new_signal(world, conn):
    c, h, o = world["c"], world["h"], world["o"]
    event(conn, o["kestrel"], "Kestrel Pay", "payments", days=2)       # Kestrel now has two kinds: the strongest company this week
    conn.commit()
    body = c.get("/api/buying-signals", headers=h("bdm")).json()
    pulse = body["pulse"]
    fam = {f["family"]: f for f in pulse["families"]}
    assert [f["family"] for f in pulse["families"]] == ["SRC", "CAP", "PRG", "MAN", "COM", "REL"]      # a fixed order, zeros included
    assert (fam["PRG"]["d7"], fam["PRG"]["d30"], fam["PRG"]["d90"]) == (2, 2, 2)      # Fjord (3 days) and Kestrel (2 days)
    assert (fam["COM"]["d7"], fam["COM"]["d30"], fam["COM"]["d90"]) == (0, 1, 1)      # Kestrel's funding: 10 and 40 days ago, one company
    assert fam["CAP"] == {"family": "CAP", "d7": 0, "d30": 0, "d90": 0}
    assert len(pulse["weeks"]) == 13 and pulse["weeks"][-1]["news"] == 2 and pulse["weeks"][-1]["companies"] == 2
    assert sum(w["news"] for w in pulse["weeks"]) == 4                  # Old News Bank (150 days) is outside the 90 days
    assert pulse["week"] == {"news": 2, "companies": 2}
    top = pulse["top"]
    assert top["window"] == 7 and top["organization"]["name"] == "Kestrel Pay" and top["company_strength"] == 3
    assert top["signal"]["rule_id"] == "PRG-06" and top["signal"]["age_days"] == 2

    # the pulse ignores the filters below it; the list follows them
    narrowed = c.get("/api/buying-signals?region=nordics&days=30", headers=h("bdm")).json()
    assert _names(narrowed) == ["Fjord Bank"]
    assert narrowed["pulse"]["families"] == pulse["families"] and narrowed["pulse"]["top"]["organization"]["name"] == "Kestrel Pay"
    assert "pulse" not in c.get("/api/buying-signals?pulse=false", headers=h("bdm")).json()


def test_quiet_week_shows_the_strongest_of_the_month(world, conn):
    c, h, ev = world["c"], world["h"], world["ev"]
    top = c.get("/api/buying-signals", headers=h("bdm")).json()["pulse"]["top"]
    assert top["window"] == 7 and top["organization"]["name"] == "Fjord Bank"         # 3 days ago
    conn.execute("UPDATE events SET event_at = now() - interval '12 days', pub_at = now() - interval '12 days' WHERE id=%s", (ev["fjord"],))
    conn.commit()
    pulse = c.get("/api/buying-signals", headers=h("bdm")).json()["pulse"]
    assert pulse["week"] == {"news": 0, "companies": 0}
    assert pulse["top"]["window"] == 30 and pulse["top"]["organization"]["name"] == "Fjord Bank"      # stronger than Kestrel's funding round
    # another seller's account never reaches this seller's pulse; an administrator sees it (scope follows ownership, as everywhere)
    assert c.get("/api/buying-signals", headers=h("bdm")).json()["pulse"]["companies"] == 2
    assert c.get("/api/buying-signals", headers=h("admin")).json()["pulse"]["companies"] == 3


def test_family_filter_keeps_kind_counts(world, conn):
    c, h, o = world["c"], world["h"], world["o"]
    event(conn, o["kestrel"], "Kestrel Pay", "payments", days=20)
    conn.commit()
    body = c.get("/api/buying-signals?family=COM", headers=h("bdm")).json()
    assert _names(body) == ["Kestrel Pay"] and body["family"] == "COM"
    assert [s["rule_id"][:3] for s in body["companies"][0]["signals"]] == ["COM", "PRG"]       # the picked family first
    assert {k["rule_id"]: k["companies"] for k in body["kinds"]} == {"PRG-06": 2, "COM-01": 1}
    assert body["companies"][0]["families"] == ["PRG", "COM"]
    assert c.get("/api/buying-signals?family=XYZ", headers=h("bdm")).status_code == 422


def test_best_fit_column_is_optional_and_never_breaks_the_list(world, monkeypatch):
    c, h, o = world["c"], world["h"], world["o"]
    import ri.solutions as solutions
    monkeypatch.setattr(solutions, "best_fit_many", lambda conn, ids: {o["kestrel"]: {"service": "team_extension", "label": "Team extension", "score": 72,
                                                                                         "level": "strong", "why": "Raised a Series B (2 Nov)"}})
    body = c.get("/api/buying-signals", headers=h("bdm")).json()
    fits = {co["organization"]["name"]: co["organization"]["best_fit"] for co in body["companies"]}
    assert fits["Kestrel Pay"] == {"key": "team_extension", "name": "Team extension", "reason": "Raised a Series B (2 Nov)", "level": "strong", "score": 72}
    assert fits["Fjord Bank"] is None and body["best_fit"] is True

    def broken(conn, ids):
        conn.execute("SELECT * FROM no_such_table")
    monkeypatch.setattr(solutions, "best_fit_many", broken)
    body = c.get("/api/buying-signals", headers=h("bdm")).json()      # the failing query is rolled back to a savepoint; the list still loads
    assert set(_names(body)) == {"Kestrel Pay", "Fjord Bank"} and body["best_fit"] is False


# ---------------------------------------------------------------------------------------------------------------- freshness

def test_freshness_reads_the_real_schedule(live, conn):
    c, h = live["c"], live["h"]
    f = c.get("/api/signals/freshness", headers=h("bdm")).json()
    assert f["sources"]["watched"] == 4                              # paid search, regulators and internal fetchers are not signal sources
    assert f["sources"]["checked_24h"] == 3 and f["sources"]["every_hours"] == [24, 48]
    assert f["automatic"] is True and f["worker"] is True and f["running"] is False
    last = datetime.fromisoformat(f["last_checked_at"])
    assert abs((last - (NOW - timedelta(minutes=10))).total_seconds()) < 5
    # procurement-ted has never been tried, so it is due now: the next check is the scheduler's next tick (2 minutes ago + 5 minutes)
    nxt = datetime.fromisoformat(f["next_check_at"])
    assert timedelta(minutes=2) < nxt - datetime.now(timezone.utc) < timedelta(minutes=3, seconds=30)
    assert f["tick_minutes"] == 5 and f["due_next"] >= 1
    assert f["can_refresh"] is True and f["blocked"] is None and f["cooldown_until"] is None and f["ai_reading"] == "off"

    # nothing due for a day: the next check is the first tick after the earliest source falls due
    conn.execute("UPDATE source_health SET last_attempt_at = now() - interval '1 hour' WHERE source_id = ANY(%s)", (SIGNAL_SOURCES,))
    conn.commit()
    f = c.get("/api/signals/freshness", headers=h("bdm")).json()
    nxt = datetime.fromisoformat(f["next_check_at"]) - datetime.now(timezone.utc)
    assert timedelta(hours=22, minutes=55) < nxt < timedelta(hours=23, minutes=6)

    # a silent scheduler means no automatic checks: no next time is promised
    _beat(conn, "scheduler", 45)
    conn.commit()
    f = c.get("/api/signals/freshness", headers=h("bdm")).json()
    assert f["automatic"] is False and f["next_check_at"] is None


def test_freshness_explains_why_refresh_is_not_possible(world, conn):
    c, h = world["c"], world["h"]
    f = c.get("/api/signals/freshness", headers=h("bdm")).json()
    assert f["blocked"] == "no_sources" and f["sources"]["watched"] == 0 and f["last_checked_at"] is None
    r = c.post("/api/signals/refresh", headers=h("bdm"))
    assert r.status_code == 409 and r.json()["code"] == "no_sources"

    _switch_on(conn, SIGNAL_SOURCES, checked_minutes_ago=300)
    conn.commit()
    assert c.get("/api/signals/freshness", headers=h("bdm")).json()["blocked"] == "worker"       # no worker heartbeat yet
    r = c.post("/api/signals/refresh", headers=h("bdm"))
    assert r.status_code == 409 and r.json()["code"] == "worker"
    assert conn.execute("SELECT count(*) n FROM jobs").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) n FROM audit_events WHERE operation='signals_refresh'").fetchone()["n"] == 0   # no cooldown started

    _beat(conn, "worker", 40)          # silent for 40 minutes: still no
    conn.commit()
    assert c.get("/api/signals/freshness", headers=h("bdm")).json()["blocked"] == "worker"


def test_ai_reading_state_follows_the_budget(live, conn):
    c, h = live["c"], live["h"]
    from ri import usage
    conn.execute("INSERT INTO provider_budgets(provider, kind, period, cap, unit, spent) VALUES ('anthropic','monthly',%s,1000,'tokens',500)", (usage.period_now(),))
    conn.commit()
    assert c.get("/api/signals/freshness", headers=h("bdm")).json()["ai_reading"] == "on"
    conn.execute("UPDATE provider_budgets SET spent=750 WHERE provider='anthropic'")       # background reading stops at 70% of the cap
    conn.commit()
    assert c.get("/api/signals/freshness", headers=h("bdm")).json()["ai_reading"] == "paused"
    _beat(conn, "worker", 1, '{"can": {"ai": false, "exa": false}}')
    conn.commit()
    assert c.get("/api/signals/freshness", headers=h("bdm")).json()["ai_reading"] == "off"


# ---------------------------------------------------------------------------------------------------------------- refresh now

def test_refresh_queues_only_free_signal_sources_and_respects_the_cooldown(live, conn):
    c, h = live["c"], live["h"]
    # a scheduled run already waiting for ats-greenhouse is not jumped or doubled
    from ri.jobs.queue import enqueue
    enqueue(conn, "source_run", {"source_id": "ats-greenhouse"}, idempotency_key="source_run:ats-greenhouse:sched", correlation_id="sched-1")
    conn.commit()

    r = c.post("/api/signals/refresh", headers=h("bdm"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 2 and body["skipped"] == {"recent": 1, "queued": 1, "limit": 0}
    jobs = conn.execute("SELECT kind, payload->>'source_id' AS sid, correlation_id, max_attempts FROM jobs WHERE correlation_id LIKE 'signals-refresh:%%' ORDER BY 2").fetchall()
    assert [(j["kind"], j["sid"]) for j in jobs] == [("source_run", "attention-finextra"), ("source_run", "procurement-ted")]
    assert all(j["max_attempts"] == 2 for j in jobs)
    # never paid search, regulators, internal fetchers, nor any other kind of job (no Exa, AI research or Apollo)
    assert {r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM jobs").fetchall()} == {"source_run"}
    assert not conn.execute("SELECT 1 FROM jobs WHERE payload->>'source_id' IN ('discovery-exa','regulator-fca','verify-company-page')").fetchall()
    audit = conn.execute("SELECT actor, detail FROM audit_events WHERE operation='signals_refresh'").fetchall()
    assert len(audit) == 1 and audit[0]["actor"] == "bdm@x" and audit[0]["detail"]["queued"] == 2

    f = body["freshness"]
    assert f["running"] is True and f["manual"]["running"] is True and f["manual"]["jobs"] == 2 and f["manual"]["done"] == 0
    assert f["manual"]["by_me"] is True and f["blocked"] == "running" and f["poll_seconds"] == 5

    # a second press while it runs, or from someone else, is refused and queues nothing
    r = c.post("/api/signals/refresh", headers=h("admin"))
    assert r.status_code == 409 and r.json()["code"] == "running"

    # once the worker has done the jobs, the wait is explained by the cooldown
    conn.execute("UPDATE jobs SET status='completed' WHERE correlation_id LIKE 'signals-refresh:%%'")
    conn.commit()
    f = c.get("/api/signals/freshness", headers=h("admin")).json()
    assert f["manual"]["running"] is False and f["manual"]["done"] == 2 and f["manual"]["by_me"] is False
    assert f["blocked"] == "cooldown" and f["cooldown_until"] and f["can_refresh"] is False
    r = c.post("/api/signals/refresh", headers=h("admin"))
    assert r.status_code == 429 and r.json()["code"] == "cooldown" and 0 < int(r.headers["Retry-After"]) <= 15 * 60
    assert conn.execute("SELECT count(*) n FROM jobs WHERE correlation_id LIKE 'signals-refresh:%%'").fetchone()["n"] == 2


def test_refresh_after_the_cooldown_and_with_nothing_to_do(live, conn):
    c, h = live["c"], live["h"]
    conn.execute("INSERT INTO audit_events(at, actor, operation, record_type, record_id, detail) VALUES "
                 "(now() - interval '16 minutes', 'x@x', 'signals_refresh', 'buying_signals', 'refresh', '{\"correlation_id\": \"signals-refresh:old\", \"queued\": 1}')")
    conn.execute("UPDATE source_health SET last_attempt_at = now() - interval '5 minutes' WHERE source_id = ANY(%s)", (SIGNAL_SOURCES,))
    conn.execute("INSERT INTO source_health(source_id, state, last_attempt_at) VALUES ('procurement-ted','healthy', now() - interval '5 minutes') "
                 "ON CONFLICT (source_id) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at")
    conn.commit()
    f = c.get("/api/signals/freshness", headers=h("bdm")).json()
    assert f["cooldown_until"] is None and f["can_refresh"] is True

    # everything was checked five minutes ago: nothing to queue, and the cooldown does not start
    r = c.post("/api/signals/refresh", headers=h("bdm"))
    assert r.status_code == 200 and r.json()["queued"] == 0 and r.json()["skipped"]["recent"] == 4
    assert conn.execute("SELECT count(*) n FROM audit_events WHERE operation='signals_refresh'").fetchone()["n"] == 1


def test_refresh_needs_permission(live):
    c, h = live["c"], live["h"]
    assert c.post("/api/signals/refresh", headers=h("mkt")).status_code == 403
    assert c.get("/api/signals/freshness", headers=h("mkt")).status_code == 403


def test_refresh_permission_is_reported(live, conn):
    from test_r4_crm_notify import mk_user
    mk_user(conn, "dx@x", "delivery_expert")
    conn.commit()
    c, h = live["c"], live["h"]
    f = c.get("/api/signals/freshness", headers=h("dx")).json()      # may read the page, may not press the button
    assert f["blocked"] == "permission" and f["can_refresh"] is False
    assert c.post("/api/signals/refresh", headers=h("dx")).status_code == 403


def test_tick_follows_the_scheduler_when_it_says(monkeypatch):
    from ri import scheduler
    from ri.api.refresh import tick_seconds
    assert tick_seconds() == 300
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 600, raising=False)
    assert tick_seconds() == 600
