"""Each person's own Lemlist sync and Bluedot meeting webhook. Lemlist and Bluedot are FAKES (no network); all names and addresses are invented.
What matters: events are matched to our people by email (any case), nothing is stored for erased or suppressed people, repeats never duplicate,
the sync is incremental, a refused key needs attention with a plain message, the server-wide Lemlist tables are untouched, and Bluedot's unknown format
is read tolerantly (many shapes) while the raw deliveries are kept for matching the parser to the real thing."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ri import vault
from ri.connections import bluedot, lemlist_sync, store
from ri.db import q1, qa
from ri.jobs import queue
from ri.jobs.queue import JobError
from ri.outbound.lemlist import LemlistError

KEY = vault.new_key()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
LEM_KEY = "lem-key-ann-0123456789"


class S:                                                    # the only setting the sync reads
    vault_key = KEY


def mk_user(conn, email="ann@example.test"):
    return str(conn.execute("insert into users(email, display_name, role) values (%s,'Ann Example','bdm') returning id", (email,)).fetchone()["id"])


def mk_person(conn, org, name, email, *, suppressed=False, erased=False):
    pid = str(conn.execute("insert into people(full_name, work_email, suppressed, erased_at) values (%s,%s,%s,%s) returning id", (name, email, suppressed, NOW if erased else None)).fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, org))
    return pid


@pytest.fixture()
def world(conn):
    org = str(conn.execute("insert into organizations(display_name, canonical_domain) values ('Kestrel Pay Ltd','kestrel.example') returning id").fetchone()["id"])
    w = {"org": org, "ann": mk_user(conn), "bob": mk_user(conn, "bob@example.test"),
         "ada": mk_person(conn, org, "Ada Clarke", "Ada.Clarke@Kestrel.example"), "dan": mk_person(conn, org, "Dan Nowak", "dan@kestrel.example"),
         "gus": mk_person(conn, org, "Gus Suppressed", "gus@kestrel.example", suppressed=True), "hal": mk_person(conn, org, "Hal Erased", "hal@kestrel.example", erased=True),
         "ivy": mk_person(conn, org, "Ivy Optout", "ivy@kestrel.example")}
    conn.commit()
    return w


# ================================================================== Lemlist
def ev(kind, email, when, *, cid="cam_1", name="Payments rail Q4", lead="lea_1", **extra):
    return {"_id": f"act_{kind}_{email}_{when.timestamp()}", "type": kind, "leadEmail": email, "leadId": lead, "campaignId": cid, "campaignName": name, "createdAt": when.isoformat().replace("+00:00", "Z"), **extra}


class FakeLemlist:
    """Lemlist as one person's key sees it: campaigns, and per campaign a newest-first list of activities."""

    def __init__(self, campaigns, activity, *, via_req=False, fail=None):
        self.campaigns_data, self.activity, self.fail, self.calls = campaigns, activity, fail, []
        if via_req:
            self._req = lambda method, path, **kw: {"campaigns": campaigns, "pagination": {"page": 1}}

    def campaigns(self):
        if self.fail:
            raise self.fail
        return self.campaigns_data

    def activities(self, *, offset=0, limit=100, campaign_id=None):
        if self.fail:
            raise self.fail
        self.calls.append((campaign_id, offset, limit))
        return self.activity.get(campaign_id, [])[offset: offset + limit]


def connect_lemlist(conn, uid, key=LEM_KEY):
    store.connect(conn, KEY, uid, "lemlist", secret=key, label="Team Orchard")
    conn.commit()


def use(monkeypatch, fake):
    monkeypatch.setattr(lemlist_sync, "make_client", lambda key: fake)
    return fake


def job(uid, attempts=1, max_attempts=2):
    return {"payload": {"user_id": uid}, "attempts": attempts, "max_attempts": max_attempts}


def rows(conn, **where):
    sql, params = "SELECT * FROM contact_interactions", []
    if where:
        sql += " WHERE " + " AND ".join(f"{k}=%s" for k in where)
        params = list(where.values())
    return qa(conn, sql + " ORDER BY occurred_at, external_id", params)


def test_events_are_matched_to_our_people_by_email_in_any_case_and_stored_for_the_team(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    t = NOW - timedelta(days=3)
    fake = use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "Payments rail Q4"}], {"cam_1": [
        ev("emailsReplied", "ADA.CLARKE@kestrel.EXAMPLE", t + timedelta(hours=5)), ev("emailsOpened", "ada.clarke@kestrel.example", t + timedelta(hours=2)),
        ev("emailsSent", "ada.clarke@kestrel.example", t), ev("emailsSent", "stranger@elsewhere.example", t), ev("linkedinVisitDone", "ada.clarke@kestrel.example", t),
        ev("emailsClicked", "dan@kestrel.example", t + timedelta(hours=1), lead="lea_2"), ev("emailsBounced", "dan@kestrel.example", t + timedelta(hours=1, minutes=5), lead="lea_2")]}))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert out["campaigns"] == 1 and out["new"] == 5 and out["not_ours"] == 1 and "error" not in out
    got = rows(conn)
    assert len(got) == 5 and {str(r["user_id"]) for r in got} == {world["ann"]} and {r["channel"] for r in got} == {"lemlist"} and {r["visibility"] for r in got} == {"team"}
    assert {r["title"] for r in got} == {"Payments rail Q4"}
    assert sorted((r["gist"], r["direction"]) for r in got if str(r["person_id"]) == world["ada"]) == [("Opened", None), ("Replied", "in"), ("Sent", "out")]
    assert sorted(r["gist"] for r in got if str(r["person_id"]) == world["dan"]) == ["Bounced", "Clicked"]
    assert all(str(r["organization_id"]) == world["org"] for r in got)
    assert all(r["external_id"].startswith("cam_1:lea_") and r["external_id"].count(":") == 3 for r in got)          # campaign + lead + event + time
    assert len({r["external_id"] for r in got}) == 5 and fake.calls == [("cam_1", 0, 100)]
    assert LEM_KEY not in str(got)


def test_every_plain_event_name(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    kinds = {"emailsSent": "Sent", "emailsOpened": "Opened", "emailsClicked": "Clicked", "emailsReplied": "Replied", "emailsBounced": "Bounced", "emailsUnsubscribed": "Unsubscribed",
             "emailsInterested": "Marked interested", "emailsNotInterested": "Marked not interested", "linkedinSent": "LinkedIn message sent"}
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev(k, "dan@kestrel.example", NOW - timedelta(days=1, minutes=i)) for i, k in enumerate(kinds)]}))
    lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert sorted(r["gist"] for r in rows(conn)) == sorted(kinds.values())


def test_a_second_run_never_duplicates_and_reads_only_what_is_new(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    monkeypatch.setattr(lemlist_sync, "PAGE", 2)
    old = [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=20 + i), lead=f"l{i}") for i in range(5)]
    fake = use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": list(old)}))
    first = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert first["new"] == 5 and len(fake.calls) == 3                                                    # 2 + 2 + 1: the whole history
    cursor = store.get(conn, world["ann"], "lemlist")["cursor"]
    assert list(cursor["campaigns"]) == ["cam_1"] and cursor["campaigns"]["cam_1"] == old[0]["createdAt"].replace("Z", "+00:00")
    fake.calls.clear()
    again = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert again["new"] == 0 and len(rows(conn)) == 5 and len(fake.calls) == 1                         # newest page is all old news: it stops there
    fresh = ev("emailsReplied", "dan@kestrel.example", NOW - timedelta(hours=1), lead="l9")
    fake.activity["cam_1"].insert(0, fresh)
    fake.calls.clear()
    third = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert third["new"] == 1 and len(rows(conn)) == 6 and len(fake.calls) == 2                       # the new event, then the first page that is all old news: it stops there
    store_row = store.get(conn, world["ann"], "lemlist")
    assert store_row["status"] == "connected" and store_row["last_error"] is None and store_row["last_sync_at"] is not None


def test_each_campaign_keeps_its_own_cursor(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    a, b = ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=2), cid="cam_a"), ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=9), cid="cam_b")
    use(monkeypatch, FakeLemlist([{"_id": "cam_a", "name": "A"}, {"_id": "cam_b", "name": "B"}], {"cam_a": [a], "cam_b": [b]}))
    lemlist_sync.run(conn, job(world["ann"]), settings=S)
    cur = store.get(conn, world["ann"], "lemlist")["cursor"]["campaigns"]
    assert set(cur) == {"cam_a", "cam_b"} and cur["cam_a"] > cur["cam_b"]
    assert len(rows(conn)) == 2


def test_the_newer_campaign_list_shape_is_understood(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "Dict shape"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=1), name="")]}, via_req=True))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert out["campaigns"] == 1 and [r["title"] for r in rows(conn)] == ["Dict shape"]                  # the campaign list's name when the activity has none


def test_nobody_erased_suppressed_or_opted_out_gets_a_row(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    draft = str(conn.execute("insert into campaign_drafts(organization_id, requested_by, status, steps) values (%s,'x','approved','[]'::jsonb) returning id", (world["org"],)).fetchone()["id"])
    conn.execute("insert into outbound_enrolments(campaign_draft_id, organization_id, person_id, email, status) values (%s,%s,%s,'IVY@kestrel.example','unsubscribed')", (draft, world["org"], world["ivy"]))
    conn.commit()
    t = NOW - timedelta(days=1)
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", e, t, lead=e) for e in
        ("gus@kestrel.example", "hal@kestrel.example", "ivy@kestrel.example", "dan@kestrel.example")]}))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert out["new"] == 1 and [str(r["person_id"]) for r in rows(conn)] == [world["dan"]] and out["not_ours"] == 3


def test_an_old_event_beyond_the_retention_window_is_not_stored(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=30 * 19))]}))
    assert lemlist_sync.run(conn, job(world["ann"]), settings=S)["new"] == 0 and rows(conn) == []


def test_a_refused_key_needs_attention_with_a_plain_message_and_never_raises(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([], {}, fail=LemlistError("Lemlist refused the API key", 401)))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert out["error"] == "key refused"
    row = store.get(conn, world["ann"], "lemlist")
    assert row["status"] == "needs_attention" and row["last_error"] == lemlist_sync.MSG_REJECTED and LEM_KEY not in str(dict(row))
    assert "connect Lemlist again" in row["last_error"]


def test_a_temporary_problem_is_retried_and_shown_only_after_the_last_attempt(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([], {}, fail=LemlistError("Lemlist 503 after retries", 503)))
    with pytest.raises(JobError) as exc:
        lemlist_sync.run(conn, job(world["ann"], attempts=1, max_attempts=2), settings=S)
    assert exc.value.error_class == "transient_network" and LEM_KEY not in str(exc.value)
    assert store.get(conn, world["ann"], "lemlist")["status"] == "connected"                            # nothing alarming for a hiccup
    out = lemlist_sync.run(conn, job(world["ann"], attempts=2, max_attempts=2), settings=S)
    assert out["error"] == "temporary problem"
    row = store.get(conn, world["ann"], "lemlist")
    assert row["status"] == "needs_attention" and row["last_error"] == lemlist_sync.MSG_UNREACHABLE
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=1))]}))
    lemlist_sync.run(conn, job(world["ann"]), settings=S)                                              # the next good run clears it
    assert store.get(conn, world["ann"], "lemlist")["status"] == "connected" and store.get(conn, world["ann"], "lemlist")["last_error"] is None
    use(monkeypatch, FakeLemlist([], {}, fail=httpx.ConnectError("no route")))
    with pytest.raises(JobError):
        lemlist_sync.run(conn, job(world["ann"]), settings=S)


def test_a_vault_that_cannot_open_the_key_is_a_plain_message(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])

    class NoVault:
        vault_key = None
    use(monkeypatch, FakeLemlist([], {}))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=NoVault)
    assert "error" in out and store.get(conn, world["ann"], "lemlist")["last_error"] == lemlist_sync.MSG_VAULT


def test_a_disconnected_or_missing_connection_is_skipped_and_nothing_is_called(conn, world, monkeypatch):
    fake = use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW)]}))
    assert lemlist_sync.run(conn, job(world["ann"]), settings=S) == {"skipped": "not connected"}
    connect_lemlist(conn, world["ann"])
    store.disconnect(conn, world["ann"], "lemlist")
    conn.commit()
    assert lemlist_sync.run(conn, job(world["ann"]), settings=S) == {"skipped": "not connected"}
    assert lemlist_sync.run(conn, {"payload": {}}, settings=S) == {"skipped": "not connected"} and fake.calls == [] and rows(conn) == []


def test_one_persons_sync_touches_only_their_rows_and_never_the_server_wide_lemlist_tables(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    connect_lemlist(conn, world["bob"], key="lem-key-bob-0123456789")
    keys_seen = []
    use_fake = FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=1))]})
    monkeypatch.setattr(lemlist_sync, "make_client", lambda key: (keys_seen.append(key), use_fake)[1])
    lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert keys_seen == [LEM_KEY]                                                                          # Ann's own key, never Bob's or a server key
    assert {str(r["user_id"]) for r in rows(conn)} == {world["ann"]}
    bob = store.get(conn, world["bob"], "lemlist")
    assert bob["last_sync_at"] is None and bob["cursor"] == {}
    assert q1(conn, "SELECT count(*) AS n FROM lemlist_activities")["n"] == 0 and q1(conn, "SELECT count(*) AS n FROM sync_state")["n"] == 0
    assert q1(conn, "SELECT count(*) AS n FROM outbound_events")["n"] == 0


def test_a_run_that_runs_out_of_time_stops_cleanly_and_keeps_what_it_has(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "A"}, {"_id": "cam_2", "name": "B"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=1))],
                                                                                                    "cam_2": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=2), cid="cam_2")]}))
    clock = iter([0.0, 1.0, 999.0, 999.0, 999.0])
    monkeypatch.setattr(lemlist_sync.time, "monotonic", lambda: next(clock))
    out = lemlist_sync.run(conn, job(world["ann"]), settings=S)
    assert out["campaigns"] == 1 and out["out_of_time"] == 1 and len(rows(conn)) == 1
    assert list(store.get(conn, world["ann"], "lemlist")["cursor"]["campaigns"]) == ["cam_1"]              # cam_2 is read from the start next hour


def test_the_queue_runs_the_sync_end_to_end(conn, world, monkeypatch):
    connect_lemlist(conn, world["ann"])
    use(monkeypatch, FakeLemlist([{"_id": "cam_1", "name": "C"}], {"cam_1": [ev("emailsSent", "dan@kestrel.example", NOW - timedelta(days=1))]}))
    assert store.schedule_syncs(conn, NOW + timedelta(hours=2)) == 1
    conn.commit()

    def handler(c, j):
        return lemlist_sync.run(c, j, settings=S)
    handler.own_transactions = True
    assert queue.run_one(conn, "w1", {"lemlist_user_sync": handler}, ["lemlist_user_sync"]) == "completed"
    assert len(rows(conn)) == 1 and q1(conn, "SELECT result FROM jobs")["result"]["new"] == 1


# ================================================================== Bluedot: reading many shapes
ADA, DAN = "ada.clarke@kestrel.example", "dan@kestrel.example"
START = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

SHAPES = {
    "flat snake_case": ({"id": "m1", "title": "Pilot review", "summary": "Agreed a pilot.", "start_time": "2026-11-20T10:00:00Z", "attendees": [ADA, DAN], "url": "https://bd.example/m/1"},
                        dict(id="m1", title="Pilot review", summary="Agreed a pilot.", start=START, attendees=[ADA, DAN], url="https://bd.example/m/1")),
    "camelCase": ({"meetingId": "m2", "meetingTitle": "Pilot review", "aiSummary": "Agreed a pilot.", "startedAt": "2026-11-20T10:00:00.000Z", "participants": [{"name": "Ada", "email": "Ada.Clarke@Kestrel.example"}],
                   "shareUrl": "https://bd.example/m/2"},
                  dict(id="m2", title="Pilot review", summary="Agreed a pilot.", start=START, attendees=[ADA], url="https://bd.example/m/2")),
    "UPPER and dashed names": ({"Meeting-ID": "m3", "TITLE": "Pilot review", "SUMMARY": "Agreed a pilot.", "Start-Time": "2026-11-20T10:00:00+00:00", "ATTENDEES": [ADA]},
                               dict(id="m3", title="Pilot review", summary="Agreed a pilot.", start=START, attendees=[ADA], url=None)),
    "wrapped in an event envelope": ({"event": "meeting.completed", "id": "evt_77", "data": {"meeting": {"id": "m4", "title": "Pilot review", "summary": "Agreed a pilot.",
                                                                                                          "date": "2026-11-20T10:00:00Z", "attendees": [{"email": ADA}]}}},
                                     dict(id="m4", title="Pilot review", summary="Agreed a pilot.", start=START, attendees=[ADA], url=None)),
    "specific meeting id beats an event id": ({"id": "evt_1", "type": "recording.ready", "data": {"meeting_id": "m5", "title": "T", "attendees": [ADA]}}, dict(id="m5", title="T", attendees=[ADA])),
    "summary as a list of lines": ({"id": "m6", "summary": ["Agreed a pilot.", "Next call on Friday."], "attendees": [ADA]}, dict(summary="Agreed a pilot.; Next call on Friday.")),
    "summary as an object with text": ({"id": "m7", "summary": {"text": "Agreed a pilot."}, "attendees": [ADA]}, dict(summary="Agreed a pilot.")),
    "summary as an object with content": ({"id": "m7b", "ai_summary": {"content": "Agreed a pilot.", "model": "x"}, "attendees": [ADA]}, dict(summary="Agreed a pilot.")),
    "summary as sections": ({"id": "m7c", "summary": {"key_points": ["Pilot agreed"], "decisions": ["Start in January"]}, "attendees": [ADA]}, dict(summary="Pilot agreed; Start in January")),
    "summary as a list of objects": ({"id": "m7d", "notes": [{"text": "One."}, {"text": "Two."}], "attendees": [ADA]}, dict(summary="One.; Two.")),
    "summary under other names": ({"id": "m8", "tldr": "Short version.", "attendees": [ADA]}, dict(summary="Short version.")),
    "overview": ({"id": "m8b", "overview": "The overview.", "attendees": [ADA]}, dict(summary="The overview.")),
    "title from name": ({"id": "m9", "name": "Quarterly call", "attendees": [ADA]}, dict(title="Quarterly call")),
    "title from subject": ({"id": "m9b", "subject": "Subject line", "attendees": [ADA]}, dict(title="Subject line")),
    "attendees as Name <email> strings": ({"id": "m10", "attendees": ["Ada Clarke <ada.clarke@kestrel.example>", "Dan Nowak <DAN@kestrel.example>"]}, dict(attendees=[ADA, DAN])),
    "attendees as one comma text": ({"id": "m11", "attendees": "ada.clarke@kestrel.example, dan@kestrel.example; ada.clarke@kestrel.example"}, dict(attendees=[ADA, DAN])),
    "attendees in groups": ({"id": "m12", "guests": {"internal": [{"email": "me@us.example"}], "external": [{"email": ADA}]}}, dict(attendees=["me@us.example", ADA])),
    "attendees with a nested user": ({"id": "m13", "participants": [{"user": {"email": ADA}}, {"contact": {"emailAddress": DAN}}]}, dict(attendees=[ADA, DAN])),
    "attendees named email_addresses": ({"id": "m14", "participant_emails": [ADA, DAN]}, dict(attendees=[ADA, DAN])),
    "attendees keyed by email": ({"id": "m14b", "attendees": {ADA: {"status": "accepted"}}}, dict(attendees=[ADA])),
    "epoch seconds": ({"id": "m15", "attendees": [ADA], "started_at": 1795168800}, dict(start=START)),
    "epoch milliseconds": ({"id": "m16", "attendees": [ADA], "timestamp": 1795168800000}, dict(start=START)),
    "epoch as text": ({"id": "m17", "attendees": [ADA], "start": "1795168800"}, dict(start=START)),
    "a bare date": ({"id": "m18", "attendees": [ADA], "date": "2026-11-20"}, dict(start=datetime(2026, 11, 20, tzinfo=timezone.utc))),
    "a time zone offset": ({"id": "m19", "attendees": [ADA], "created_at": "2026-11-20T11:00:00+01:00"}, dict(start=START)),
    "a date object": ({"id": "m19b", "attendees": [ADA], "start": {"dateTime": "2026-11-20T10:00:00Z", "timeZone": "UTC"}}, dict(start=START)),
    "start_time wins over created_at": ({"id": "m20", "attendees": [ADA], "created_at": "2026-11-21T00:00:00Z", "start_time": "2026-11-20T10:00:00Z"}, dict(start=START)),
    "url under recording_url": ({"id": "m21", "attendees": [ADA], "recording_url": "https://bd.example/rec/21"}, dict(url="https://bd.example/rec/21")),
    "url under link": ({"id": "m22", "attendees": [ADA], "link": "http://bd.example/22"}, dict(url="http://bd.example/22")),
    "an unsafe link is dropped": ({"id": "m23", "attendees": [ADA], "url": "javascript:alert(1)", "share_url": "data:text/html,x"}, dict(url=None)),
    "a person's name is not the title": ({"id": "m24", "attendees": [ADA], "organizer": {"name": "Olive Organizer", "email": "olive@us.example"}, "host": {"name": "Hugo Host"}}, dict(title=None)),
    "the real title next to an organizer": ({"id": "m25", "title": "Real title", "organizer": {"name": "Olive Organizer"}, "attendees": [ADA]}, dict(title="Real title")),
    "meeting object beside an outer summary": ({"summary": "Outer summary.", "meeting": {"id": "m26", "title": "Inner", "attendees": [ADA], "start_time": "2026-11-20T10:00:00Z"}},
                                                dict(id="m26", title="Inner", summary="Outer summary.", start=START, attendees=[ADA])),
    "numeric ids": ({"meeting_id": 4711, "attendees": [ADA]}, dict(id="4711")),
    "colons in an id cannot break our key": ({"id": "a:b:c", "attendees": [ADA]}, dict(id="a-b-c")),
    "no attendees at all": ({"id": "m27", "title": "Lonely", "summary": "Nobody listed."}, dict(attendees=[], title="Lonely")),
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_the_parser_reads_this_shape(name):
    payload, want = SHAPES[name]
    got = bluedot.parse_meeting(bluedot.deliveries(payload)[0])
    for field, value in want.items():
        assert got[field] == value, f"{name}: {field}"


def test_a_long_summary_is_trimmed_to_600_characters_at_a_word():
    got = bluedot.parse_meeting({"id": "m", "summary": "word " * 400, "attendees": [ADA]})["summary"]
    assert len(got) <= 600 and got.endswith("…") and not got.endswith(" …")
    assert bluedot.trim_text("short") == "short" and bluedot.trim_text(None) is None and bluedot.trim_text("x" * 600) == "x" * 600


def test_batches_are_read_one_meeting_at_a_time():
    two = [{"id": "a", "title": "A", "attendees": [ADA]}, {"id": "b", "title": "B", "attendees": [DAN]}]
    assert [d["id"] for d in bluedot.deliveries(two)] == ["a", "b"]
    assert [d["id"] for d in bluedot.deliveries({"event": "batch", "data": two})] == ["a", "b"]
    assert [d["id"] for d in bluedot.deliveries({"meetings": two})] == ["a", "b"]
    assert bluedot.deliveries({"id": "x", "attendees": [ADA], "data": two}) == [{"id": "x", "attendees": [ADA], "data": two}]      # a meeting that merely has data inside it
    assert bluedot.deliveries("text") == [] and bluedot.deliveries(None) == [] and bluedot.deliveries(42) == [] and bluedot.deliveries([1, "x"]) == []


def test_a_huge_or_hostile_delivery_cannot_make_reading_slow():
    import time
    wide = {"id": "w", "attendees": [ADA], **{f"section{i}": {"title": f"T{i}", "summary": "s", "attendees": [f"x{i}@elsewhere.example"], "start": "2026-01-01"} for i in range(8000)}}
    t0 = time.perf_counter()
    got = bluedot.parse_meeting(bluedot.deliveries(wide)[0])
    assert time.perf_counter() - t0 < 2.0 and got["id"] == "w"
    many_keys = {f"k{i}": i for i in range(50000)}
    many_keys["attendees"] = [ADA]
    t0 = time.perf_counter()
    bluedot.parse_meeting(bluedot.deliveries(many_keys)[0])
    assert time.perf_counter() - t0 < 2.0


def test_clean_and_trim_make_any_json_storable():
    assert bluedot.clean({"a\x00b": "x\x00y", "n": float("nan"), "i": float("inf"), "l": [1, {"k": "\x00"}]}) == {"ab": "xy", "n": None, "i": None, "l": [1, {"k": ""}]}
    deep = cur = {}
    for _ in range(60):
        cur["x"] = {}
        cur = cur["x"]
    json.dumps(bluedot.clean(deep))
    small = {"title": "x"}
    assert bluedot.keep_raw(small) == small
    whole = {"title": "x", "transcript": "y" * (300 * 1024)}                       # a long meeting transcript is kept whole
    assert bluedot.keep_raw(whole) == whole
    big = bluedot.keep_raw({"title": "x", "transcript": "y" * (1100 * 1024)})
    assert big["_trimmed"] is True and big["top_level_keys"] == ["title", "transcript"] and big["original_bytes"] > 1024 * 1024 and "yyy" not in json.dumps(big)
    assert bluedot.keep_raw(["a"] * 300000)["_trimmed"] is True


# ================================================================== Bluedot: storing a delivery
def deliver(conn, uid, payload):
    out = bluedot.handle_delivery(conn, uid, payload)
    conn.commit()
    return out


def test_a_delivery_stores_one_team_visible_meeting_per_attendee_we_know(conn, world):
    payload = {"id": "m-100", "title": "Pilot review", "summary": "Agreed to scope a pilot.", "start_time": "2026-11-20T10:00:00Z", "url": "https://bd.example/m/100",
               "attendees": ["ADA.clarke@kestrel.example", "Dan Nowak <dan@kestrel.example>", "me@our-own-company.example", "stranger@elsewhere.example"]}
    out = deliver(conn, world["ann"], payload)
    assert out["handled"] is True and (out["matched"], out["created"], out["updated"], out["meetings"]) == (2, 2, 0, 1) and out["note"] is None
    got = rows(conn)
    assert sorted(r["external_id"] for r in got) == sorted([f"m-100:{world['ada']}", f"m-100:{world['dan']}"])
    for r in got:
        assert (str(r["user_id"]), r["channel"], r["visibility"], r["direction"]) == (world["ann"], "meeting", "team", "both")
        assert (r["title"], r["gist"], r["occurred_at"]) == ("Pilot review", "Agreed to scope a pilot.", START) and r["meta"] == {"url": "https://bd.example/m/100"}
        assert str(r["organization_id"]) == world["org"]
    ev_row = q1(conn, "SELECT handled, note FROM connection_events")
    assert ev_row["handled"] is True and ev_row["note"] is None


def test_a_delivery_nobody_matches_is_kept_raw_and_says_so(conn, world):
    out = deliver(conn, world["ann"], {"id": "m-200", "title": "Cold call", "attendees": ["stranger@elsewhere.example"]})
    assert out["handled"] is False and out["note"] == "no attendee matched a contact in our database" and rows(conn) == []
    ev_row = q1(conn, "SELECT handled, note, payload FROM connection_events")
    assert ev_row["handled"] is False and ev_row["note"] == out["note"] and ev_row["payload"]["id"] == "m-200"


def test_erased_and_suppressed_attendees_are_never_stored(conn, world):
    out = deliver(conn, world["ann"], {"id": "m-300", "attendees": ["gus@kestrel.example", "hal@kestrel.example"]})
    assert out["handled"] is False and out["note"] == "no attendee matched a contact in our database" and rows(conn) == []
    out = deliver(conn, world["ann"], {"id": "m-301", "attendees": ["gus@kestrel.example", DAN]})
    assert out["matched"] == 1 and [str(r["person_id"]) for r in rows(conn)] == [world["dan"]]


def test_a_delivery_with_no_attendee_list_says_so(conn, world):
    out = deliver(conn, world["ann"], {"id": "m-400", "title": "No list", "summary": "Hello"})
    assert out["handled"] is False and out["note"] == "no attendees were found in this delivery"


def test_a_repeated_delivery_never_duplicates_and_a_thinner_repeat_keeps_the_text(conn, world):
    full = {"id": "m-500", "title": "Pilot review", "summary": "Agreed a pilot.", "start_time": "2026-11-20T10:00:00Z", "attendees": [ADA]}
    assert deliver(conn, world["ann"], full)["created"] == 1
    again = deliver(conn, world["ann"], full)
    assert (again["created"], again["updated"], again["handled"]) == (0, 1, True) and len(rows(conn)) == 1
    deliver(conn, world["ann"], {"id": "m-500", "start_time": "2026-11-20T10:00:00Z", "attendees": [ADA]})              # a later delivery without the summary
    r = rows(conn)[0]
    assert len(rows(conn)) == 1 and r["title"] == "Pilot review" and r["gist"] == "Agreed a pilot."
    deliver(conn, world["ann"], {**full, "summary": "Agreed a pilot, kick-off in January."})                          # a fuller one refreshes the text
    assert rows(conn)[0]["gist"] == "Agreed a pilot, kick-off in January." and q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 4


def test_a_delivery_without_an_id_is_keyed_by_its_content_so_a_repeat_does_not_duplicate(conn, world):
    payload = {"title": "No id here", "summary": "Same words.", "attendees": [ADA], "start_time": "2026-11-20T10:00:00Z"}
    deliver(conn, world["ann"], payload)
    deliver(conn, world["ann"], dict(payload))
    assert len(rows(conn)) == 1 and rows(conn)[0]["external_id"].endswith(world["ada"]) and len(rows(conn)[0]["external_id"].split(":")[0]) == 20
    deliver(conn, world["ann"], {**payload, "summary": "Other words."})
    assert len(rows(conn)) == 2


def test_two_people_connecting_bluedot_keep_their_own_rows_for_the_same_meeting(conn, world):
    payload = {"id": "m-600", "title": "Shared call", "attendees": [ADA]}
    deliver(conn, world["ann"], payload)
    deliver(conn, world["bob"], payload)
    assert sorted(str(r["user_id"]) for r in rows(conn)) == sorted([world["ann"], world["bob"]])
    assert q1(conn, "SELECT count(*) AS n FROM connection_events WHERE user_id=%s", (world["bob"],))["n"] == 1


def test_a_missing_start_time_uses_the_arrival_time_and_says_so(conn, world):
    out = deliver(conn, world["ann"], {"id": "m-700", "attendees": [ADA]})
    assert out["handled"] is True and "no start time" in out["note"]
    assert abs((rows(conn)[0]["occurred_at"] - datetime.now(timezone.utc)).total_seconds()) < 120


def test_a_batch_delivery_is_read_meeting_by_meeting(conn, world):
    out = deliver(conn, world["ann"], {"data": [{"id": "b1", "title": "One", "attendees": [ADA]}, {"id": "b2", "title": "Two", "attendees": ["stranger@elsewhere.example"]},
                                                  {"id": "b3", "title": "Three", "attendees": [DAN]}]})
    assert out["handled"] is True and out["meetings"] == 3 and out["matched"] == 2 and sorted(r["title"] for r in rows(conn)) == ["One", "Three"]
    assert out["note"] is None or "no attendee matched" not in out["note"]
    assert q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 1                                         # the delivery is kept once, whole


@pytest.mark.parametrize("payload", ["just text", 42, None, [], {}, [1, 2], {"attendees": 5}, {"attendees": [None, 3, {}]}, {"a": {"b": {"c": {"d": {"attendees": [ADA]}}}}}])
def test_an_odd_delivery_never_raises_and_is_kept(conn, world, payload):
    out = deliver(conn, world["ann"], payload)
    assert out["handled"] is False and out["note"] and rows(conn) == []
    assert q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 1


def test_a_failure_while_reading_is_kept_raw_and_never_breaks_the_rest_of_the_transaction(conn, world, monkeypatch):
    conn.execute("INSERT INTO connection_events(user_id, provider, payload) VALUES (%s,'bluedot','{}'::jsonb)", (world["bob"],))      # earlier work in the same transaction
    monkeypatch.setattr(bluedot, "_store_meeting", lambda *a, **k: 1 / 0)
    out = bluedot.handle_delivery(conn, world["ann"], {"id": "x", "attendees": [ADA]})
    conn.commit()
    assert out["handled"] is False and "could not be read" in out["note"] and "ZeroDivisionError" in out["note"]
    assert q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 2


def test_every_raw_delivery_is_kept_and_the_newest_ten_are_listed(conn, world):
    for i in range(14):
        deliver(conn, world["ann"], {"id": f"k{i}", "title": f"Call {i}"})
    deliver(conn, world["bob"], {"id": "bob-1"})
    got = bluedot.recent_events(conn, world["ann"])
    assert [e["payload"]["id"] for e in got] == [f"k{i}" for i in range(13, 3, -1)]
    assert len(bluedot.recent_events(conn, world["bob"])) == 1
    assert q1(conn, "SELECT count(*) AS n FROM connection_events WHERE user_id=%s", (world["ann"],))["n"] == 14      # nothing is deleted
    assert all(set(e) == {"received_at", "handled", "note", "payload"} for e in got)


def test_a_huge_delivery_is_reduced_to_a_marker_but_still_read(conn, world):
    out = deliver(conn, world["ann"], {"id": "big", "title": "Huge", "attendees": [ADA], "transcript": "word " * 260000})
    assert out["handled"] is True and len(rows(conn)) == 1                                                            # the 20 KB limit is only for what we keep raw
    raw = q1(conn, "SELECT payload FROM connection_events")["payload"]
    assert raw["_trimmed"] is True and raw["original_bytes"] > 1024 * 1024 and "transcript" in raw["top_level_keys"]


def test_a_nul_character_cannot_break_storing(conn, world):
    out = deliver(conn, world["ann"], {"id": "nul\x00id", "title": "Bad\x00title", "attendees": [ADA]})
    assert out["handled"] is True and rows(conn)[0]["title"] == "Badtitle" and "\x00" not in rows(conn)[0]["external_id"]
