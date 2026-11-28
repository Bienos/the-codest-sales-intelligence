"""Pipedrive activities mirrored hourly into crm_activities (next step and last contact per deal), and the pipeline hygiene board built on
them. Names are invented."""
from datetime import datetime, timedelta, timezone

import httpx

from ri.crm import pipedrive as pd

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def act(i, deal, *, done=False, due="2026-12-02", time="14:30", typ="call", upd="2026-11-28T09:00:00Z", deleted=False, owner=555):
    return {"id": i, "type": typ, "subject": f"Call {i}", "owner_id": owner, "deal_id": deal, "org_id": 70, "person_id": 80, "done": done,
            "due_date": due, "due_time": time, "marked_as_done_time": "2026-11-27T10:00:00Z" if done else None, "update_time": upd,
            "is_deleted": deleted, "note": "Spoke to Anna about her private plans", "participants": [{"person_id": 80}]}


def reader(pages: dict[str, list], seen: list | None = None):
    def handler(req):
        tail = req.url.path.rsplit("/", 1)[-1]
        if seen is not None:
            seen.append((tail, dict(req.url.params)))
        if tail not in pages:
            return httpx.Response(404, json={"success": False})
        body = pages[tail]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json={"success": True, "data": body, "additional_data": {}})
    return pd.PipedriveReader("example", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_activities_are_mirrored_without_personal_text_and_only_changes_are_read_later(conn):
    seen: list = []
    r = reader({"activities": [act(1, 9), act(2, 9, done=True, typ="meeting", upd="2026-11-28T10:00:00Z"), act(3, None, due="2026-12-05", time="")]}, seen)
    assert pd.sync_activities(conn, r) == 3
    a1 = conn.execute("select * from crm_activities where crm_id=1").fetchone()
    assert (a1["crm_deal_id"], a1["crm_org_id"], a1["owner_id"], a1["done"], a1["type"]) == (9, 70, 555, False, "call")
    assert a1["due_at"] == datetime(2026, 12, 2, 14, 30, tzinfo=timezone.utc) and a1["done_at"] is None
    assert "note" not in a1["raw"] and "participants" not in a1["raw"]                   # nothing personal kept
    a2 = conn.execute("select done, done_at from crm_activities where crm_id=2").fetchone()
    assert a2["done"] and a2["done_at"] == datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
    assert conn.execute("select due_at from crm_activities where crm_id=3").fetchone()["due_at"] == datetime(2026, 12, 5, tzinfo=timezone.utc)
    assert "updated_since" not in seen[0][1]                                              # first run: everything

    seen.clear()
    r2 = reader({"activities": [act(1, 9, done=True, upd="2026-11-28T11:00:00Z"), act(3, None, deleted=True, upd="2026-11-28T11:05:00Z")]}, seen)
    assert pd.sync_activities(conn, r2) == 2
    assert seen[0][1]["updated_since"] == "2026-11-28T10:00:00Z"                          # only what changed since the newest seen
    assert conn.execute("select done from crm_activities where crm_id=1").fetchone()["done"] is True
    assert conn.execute("select count(*) n from crm_activities where crm_id=3").fetchone()["n"] == 0
    assert conn.execute("select watermark from sync_state where system='pipedrive' and entity='activities'").fetchone()["watermark"] == "2026-11-28T11:05:00Z"


def test_a_failing_activities_read_never_stops_the_deal_mirror(conn):
    r = reader({"stages": [], "organizations": [], "persons": [], "activities": RuntimeError("boom"),
                "deals": [{"id": 9, "title": "Deal 9", "stage_id": 7, "pipeline_id": 2, "status": "open", "value": 1000, "currency": "EUR",
                           "update_time": "2026-11-28T09:00:00Z", "owner_id": 555}]})
    out = pd.sync_incremental(conn, r)
    assert out["deals"] == 1 and "boom" in out["activities_error"]
    assert conn.execute("select count(*) n from crm_deals where crm_id=9").fetchone()["n"] == 1
    assert conn.execute("select count(*) n from sync_state where entity='activities'").fetchone()["n"] == 0   # read again next hour


# ------------------------------------------------------------------ hygiene board (Forecast, Data quality)
from ri.forecast import quality as fq   # noqa: E402

NAMES = ["Webform MQL", "DISCOVERY", "SQL", "Proposal/Shortlist", "Commercials/NDA"]


def open_deal(conn, i, *, owner=555, close=None, stage_index=2):
    conn.execute("insert into crm_deals(crm_id, title, stage_id, status, pipeline_id, value, currency, owner_id, expected_close_date) "
                 "values (%s,%s,12,'open',2,50000,'EUR',%s,%s)", (i, f"Deal {i}", owner, close))
    return {"crm_id": i, "title": f"Deal {i}", "stage_index": stage_index, "days_in_stage": 5.0, "value": 50000}


def activity(conn, i, deal, *, done, due=None, done_at=None):
    conn.execute("insert into crm_activities(crm_id, crm_deal_id, type, subject, done, due_at, done_at, owner_id) values (%s,%s,'call','x',%s,%s,%s,555)",
                 (i, deal, done, due, done_at))


def run_quality(conn, deals):
    return fq.quality(conn, deals, NAMES, [10, 20, 20, 30, 30], 2, NOW)


def test_hygiene_flags_past_close_dates_missing_steps_and_silence(conn):
    conn.execute("insert into crm_users(crm_id, name) values (555,'Ola'), (556,'Jan')")
    d = [open_deal(conn, 1, close=NOW.date() - timedelta(days=3)),       # close date passed
         open_deal(conn, 2, close=NOW.date() + timedelta(days=30)),      # next step booked, contact last week: clean
         open_deal(conn, 3, owner=556),                                    # only an overdue step, no contact for a month
         open_deal(conn, 4, owner=556)]                                    # nothing at all
    activity(conn, 10, 2, done=False, due=NOW + timedelta(days=2))
    activity(conn, 11, 2, done=True, done_at=NOW - timedelta(days=6))
    activity(conn, 12, 3, done=False, due=NOW - timedelta(days=9))
    activity(conn, 13, 3, done=True, done_at=NOW - timedelta(days=30))
    activity(conn, 14, 1, done=True, done_at=NOW - timedelta(days=2))
    activity(conn, 15, 1, done=False, due=NOW + timedelta(days=1))

    before = run_quality(conn, d)                                            # activities not read yet: only the close date is judged
    assert before["hygiene"]["activities_synced"] is False
    assert [c["key"] for c in before["hygiene"]["counts"]] == ["close_past"]
    assert {x["crm_id"]: x["hygiene"] for x in before["deals"]}.get(4, []) == []

    conn.execute("insert into sync_state(system, entity, watermark) values ('pipedrive','activities','2026-11-28T11:00:00Z')")
    q = run_quality(conn, d)
    flags = {x["crm_id"]: x["hygiene"] for x in q["deals"]}
    assert flags[1] == ["close_past"]
    assert 2 not in flags or flags[2] == []
    assert flags[3] == ["step_overdue", "no_contact"] and flags[4] == ["no_next_step", "no_contact"]
    by = {o["name"]: o for o in q["owners"]}
    assert (by["Ola"]["close_past"], by["Ola"]["no_next_step"], by["Ola"]["no_contact"]) == (1, 0, 0)
    assert (by["Jan"]["no_next_step"], by["Jan"]["step_overdue"], by["Jan"]["no_contact"]) == (1, 1, 2)
    assert {c["key"]: c["n"] for c in q["hygiene"]["counts"]} == {"close_past": 1, "no_next_step": 1, "step_overdue": 1, "no_contact": 2}
    one = next(x for x in q["deals"] if x["crm_id"] == 3)
    assert one["days_since_contact"] == 30 and one["owner_id"] == 556


def test_hygiene_links_to_pipedrive_when_the_domain_is_known(conn, monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_DOMAIN", "example")
    assert run_quality(conn, [open_deal(conn, 1)])["pipedrive_url"] == "https://example.pipedrive.com"
    monkeypatch.delenv("PIPEDRIVE_DOMAIN")
    assert run_quality(conn, [])["pipedrive_url"] is None
