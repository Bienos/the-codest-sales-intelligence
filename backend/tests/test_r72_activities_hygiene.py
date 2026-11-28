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
