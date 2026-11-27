"""The connections API: every person reaches only their own connections, secrets are sealed and never echoed, Bluedot's webhook address is shown once,
and the settings switch moves the visibility of emails already stored. Lemlist and Google are FAKES (no network); all names and addresses are invented."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ri import vault
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.connections import lemlist_sync, store
from ri.db import Database, q1, qa
from ri.outbound.lemlist import LemlistError
from test_r4_crm_notify import mk_user

SECRET = "s" * 40
KEY = vault.new_key()
NOW = datetime(2026, 11, 27, 10, 30, tzinfo=timezone.utc)
LEMLIST_KEY = "lem-key-ann-0123456789"


def settings(**kw):
    return Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, approval_ttl_hours=72, **kw)


class FakeLemlist:
    """Answers like Lemlist for one key: the right key gets the team, anything else is refused."""
    calls: list[str] = []

    def __init__(self, key):
        self.key = key

    def team(self):
        FakeLemlist.calls.append(self.key)
        if self.key == "unreachable-key-000":
            raise LemlistError("Lemlist 503 after retries", 503)
        if self.key != LEMLIST_KEY and self.key != "lem-key-bob-0123456789":
            raise LemlistError("Lemlist refused the API key", 401)
        return {"_id": "tea_1", "name": "Team Orchard"}


@pytest.fixture()
def env(conn, db_url, monkeypatch):
    users = {"ann": mk_user(conn, "ann@x", "bdm", 555), "bob": mk_user(conn, "bob@x", "bdm", 666), "admin": mk_user(conn, "admin@x", "admin")}
    conn.commit()
    FakeLemlist.calls = []
    monkeypatch.setattr(lemlist_sync, "make_client", lambda key: FakeLemlist(key))
    monkeypatch.delenv("RI_PUBLIC_URL", raising=False)
    db = Database(db_url, max_size=3)
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"c": TestClient(create_app(settings(vault_key=KEY, google_client_id="gid", google_client_secret="gsecret"), db=db)),
           "novault": TestClient(create_app(settings(), db=db)), "h": hdr, "u": users, "db_url": db_url}
    db.close()


def _items(res):
    return {i["provider"]: i for i in res.json()["items"]}


# ------------------------------------------------------------------ the list
def test_the_list_has_the_three_providers_in_order_with_the_contract_fields(env):
    r = env["c"].get("/api/me/connections", headers=env["h"]("ann"))
    assert r.status_code == 200
    body = r.json()
    assert body["vault_ready"] is True and body["gmail_ready"] is True
    assert [i["provider"] for i in body["items"]] == ["gmail", "lemlist", "bluedot"]
    for i in body["items"]:
        assert set(i) == {"provider", "status", "account_label", "connected_at", "last_sync_at", "last_error", "settings", "has_webhook", "stored"}
        assert i["status"] == "disconnected" and i["settings"] == {"share_email_gist": False} and i["has_webhook"] is False and i["stored"] == 0


def test_readiness_follows_the_server_setup(env):
    body = env["novault"].get("/api/me/connections", headers=env["h"]("ann")).json()
    assert body["vault_ready"] is False and body["gmail_ready"] is False
    partial = TestClient(create_app(settings(vault_key=KEY), db=env["c"].app.state.db))
    body = partial.get("/api/me/connections", headers=env["h"]("ann")).json()
    assert body["vault_ready"] is True and body["gmail_ready"] is False               # no Google client yet


def test_every_route_needs_a_signed_in_person(env):
    c = env["c"]
    assert c.get("/api/me/connections").status_code == 401
    assert c.post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}).status_code == 401
    assert c.post("/api/me/connections/bluedot/token").status_code == 401
    assert c.get("/api/me/connections/bluedot/events").status_code == 401
    assert c.post("/api/me/connections/lemlist/disconnect", json={}).status_code == 401
    assert c.patch("/api/me/connections/gmail/settings", json={"share_email_gist": True}).status_code == 401
    assert c.post("/api/me/connections/lemlist/sync").status_code == 401


# ------------------------------------------------------------------ Lemlist
def test_without_a_vault_key_connecting_says_so_plainly(env):
    r = env["novault"].post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}, headers=env["h"]("ann"))
    assert r.status_code == 409 and "RI_VAULT_KEY" in r.json()["detail"] and FakeLemlist.calls == []          # nothing was even sent to Lemlist
    assert env["novault"].post("/api/me/connections/lemlist/sync", headers=env["h"]("ann")).status_code == 409


def test_a_wrong_or_missing_key_is_a_plain_400_and_stores_nothing(env, conn):
    h = env["h"]("ann")
    bad = env["c"].post("/api/me/connections/lemlist", json={"api_key": "not-the-right-key-1"}, headers=h)
    assert bad.status_code == 400 and "did not accept" in bad.json()["detail"] and "not-the-right-key-1" not in bad.text
    assert env["c"].post("/api/me/connections/lemlist", json={}, headers=h).status_code == 400
    assert env["c"].post("/api/me/connections/lemlist", json={"api_key": "has spaces in it 1234"}, headers=h).status_code == 400
    assert env["c"].post("/api/me/connections/lemlist", json={"api_key": ["x"]}, headers=h).status_code == 400
    assert env["c"].post("/api/me/connections/lemlist", json={"api_key": "unreachable-key-000"}, headers=h).status_code == 502
    assert q1(conn, "SELECT count(*) AS n FROM user_connections")["n"] == 0


def test_a_good_key_is_sealed_labelled_with_the_team_and_a_first_sync_is_queued(env, conn):
    r = env["c"].post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}, headers=env["h"]("ann"))
    assert r.status_code == 200
    out = r.json()
    assert out["provider"] == "lemlist" and out["status"] == "connected" and out["account_label"] == "Team Orchard" and out["stored"] == 0
    assert LEMLIST_KEY not in r.text
    row = store.get(conn, env["u"]["ann"], "lemlist")
    assert bytes(row["secret_enc"]) and LEMLIST_KEY.encode() not in bytes(row["secret_enc"]) and store.secret(conn, KEY, env["u"]["ann"], "lemlist") == LEMLIST_KEY
    job = q1(conn, "SELECT payload FROM jobs WHERE kind='lemlist_user_sync'")
    assert job["payload"] == {"user_id": env["u"]["ann"]}
    audit = qa(conn, "SELECT actor, operation, detail FROM audit_events WHERE operation='connection_connected'")
    assert [a["actor"] for a in audit] == ["ann@x"] and LEMLIST_KEY not in str(audit)
    listed = _items(env["c"].get("/api/me/connections", headers=env["h"]("ann")))["lemlist"]
    assert listed["status"] == "connected" and listed["account_label"] == "Team Orchard"


def test_connecting_twice_does_not_queue_a_second_sync_while_one_waits(env, conn):
    for _ in range(2):
        assert env["c"].post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}, headers=env["h"]("ann")).status_code == 200
    assert q1(conn, "SELECT count(*) AS n FROM jobs WHERE kind='lemlist_user_sync'")["n"] == 1


# ------------------------------------------------------------------ ownership
def test_a_person_never_reaches_somebody_elses_connection(env, conn):
    ann, bob = env["u"]["ann"], env["u"]["bob"]
    env["c"].post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}, headers=env["h"]("ann"))
    env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann"))
    store.upsert_interaction(conn, user_id=ann, channel="lemlist", external_id="e1", occurred_at=NOW, visibility="team")
    conn.commit()
    # Bob sees his own (empty) connections, not Ann's
    mine = _items(env["c"].get("/api/me/connections", headers=env["h"]("bob")))
    assert all(i["status"] == "disconnected" and i["stored"] == 0 and i["account_label"] is None for i in mine.values())
    # Bob "disconnecting Lemlist" touches Bob's row only; Ann's stays connected with her data
    assert env["c"].post("/api/me/connections/lemlist/disconnect", json={}, headers=env["h"]("bob")).json() == {"ok": True}
    assert store.get(conn, ann, "lemlist")["status"] == "connected" and q1(conn, "SELECT count(*) AS n FROM contact_interactions")["n"] == 1
    assert store.secret(conn, KEY, ann, "lemlist") == LEMLIST_KEY
    # Bob's events list never shows Ann's deliveries; his sync cannot start Ann's
    token = env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann")).json()["webhook_url"].rsplit("/", 1)[1]
    env["c"].post(f"/webhooks/bluedot/{token}", json={"title": "Ann's private call"})
    assert env["c"].get("/api/me/connections/bluedot/events", headers=env["h"]("bob")).json() == {"items": []}
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=env["h"]("bob")).status_code == 409
    # the user id cannot be smuggled in through the body either
    env["c"].post("/api/me/connections/lemlist/disconnect", json={"user_id": ann}, headers=env["h"]("bob"))
    assert store.get(conn, ann, "lemlist")["status"] == "connected"
    # an administrator has no way to read or change another person's connection through these routes
    assert all(i["status"] == "disconnected" for i in _items(env["c"].get("/api/me/connections", headers=env["h"]("admin"))).values())
    assert env["c"].get("/api/me/connections/bluedot/events", headers=env["h"]("admin")).json() == {"items": []}


def test_an_unknown_provider_is_not_found(env):
    h = env["h"]("ann")
    assert env["c"].post("/api/me/connections/slack/disconnect", json={}, headers=h).status_code == 404
    assert env["c"].patch("/api/me/connections/slack/settings", json={"share_email_gist": True}, headers=h).status_code == 404
    assert env["c"].post("/api/me/connections/slack/sync", headers=h).status_code == 404


# ------------------------------------------------------------------ Bluedot
def test_the_webhook_address_is_shown_once_and_only_its_hash_is_kept(env, conn):
    r = env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann"))
    assert r.status_code == 200
    url = r.json()["webhook_url"]
    assert url.startswith("http://testserver/webhooks/bluedot/bd_")
    token = url.rsplit("/", 1)[1]
    conn_view = r.json()["connection"]
    assert conn_view["provider"] == "bluedot" and conn_view["status"] == "connected" and conn_view["has_webhook"] is True and token not in str(conn_view)
    row = store.get(conn, env["u"]["ann"], "bluedot")
    assert token not in str(dict(row)) and len(row["webhook_token_hash"]) == 64
    assert token not in env["c"].get("/api/me/connections", headers=env["h"]("ann")).text           # the list never shows it again
    audit = qa(conn, "SELECT detail FROM audit_events WHERE operation='connection_connected'")
    assert token not in str(audit)


def test_the_address_uses_the_public_url_when_one_is_set_else_the_forwarded_host(env, monkeypatch):
    r = env["c"].post("/api/me/connections/bluedot/token", headers={**env["h"]("ann"), "X-Forwarded-Proto": "https", "X-Forwarded-Host": "intel.example.test"})
    assert r.json()["webhook_url"].startswith("https://intel.example.test/webhooks/bluedot/bd_")
    r = env["c"].post("/api/me/connections/bluedot/token", headers={**env["h"]("ann"), "X-Forwarded-Host": "evil host/<script>"})
    assert r.json()["webhook_url"].startswith("https://testserver/")                                  # a strange host header is ignored
    monkeypatch.setenv("RI_PUBLIC_URL", "https://app.example.test/")
    r = env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann"))
    assert r.json()["webhook_url"].startswith("https://app.example.test/webhooks/bluedot/bd_")


def test_bluedot_needs_no_vault_because_nothing_secret_is_sealed(env):
    r = env["novault"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann"))
    assert r.status_code == 200 and r.json()["connection"]["has_webhook"] is True


def test_a_new_address_stops_the_old_one_and_a_disconnected_one_is_not_found(env, conn):
    h = env["h"]("ann")
    old = env["c"].post("/api/me/connections/bluedot/token", headers=h).json()["webhook_url"].rsplit("/", 1)[1]
    new = env["c"].post("/api/me/connections/bluedot/token", headers=h).json()["webhook_url"].rsplit("/", 1)[1]
    assert old != new
    assert env["c"].post(f"/webhooks/bluedot/{old}", json={"title": "x"}).status_code == 404
    assert env["c"].post(f"/webhooks/bluedot/{new}", json={"title": "x"}).json() == {"ok": True}
    env["c"].post("/api/me/connections/bluedot/disconnect", json={}, headers=h)
    assert env["c"].post(f"/webhooks/bluedot/{new}", json={"title": "x"}).status_code == 404
    assert env["c"].post("/webhooks/bluedot/bd_never-issued-token-value", json={"title": "x"}).status_code == 404
    assert env["c"].post("/webhooks/bluedot/x", json={}).status_code == 404


def test_the_webhook_keeps_every_delivery_raw_and_never_fails_on_an_odd_one(env, conn):
    h = env["h"]("ann")
    token = env["c"].post("/api/me/connections/bluedot/token", headers=h).json()["webhook_url"].rsplit("/", 1)[1]
    url = f"/webhooks/bluedot/{token}"
    assert env["c"].post(url, json={"title": "Call", "attendees": ["nobody@elsewhere.test"]}).json() == {"ok": True}
    assert env["c"].post(url, content=b"this is { not json", headers={"Content-Type": "application/json"}).json() == {"ok": True}
    assert env["c"].post(url, content=b"", headers={"Content-Type": "application/json"}).json() == {"ok": True}
    assert env["c"].post(url, json=[1, 2, 3]).json() == {"ok": True}
    assert env["c"].post(url, json="just a string").json() == {"ok": True}
    assert env["c"].post(url, content=b'{"a": "\\u0000 nul", "b": NaN}', headers={"Content-Type": "application/json"}).json() == {"ok": True}
    events = env["c"].get("/api/me/connections/bluedot/events", headers=h).json()["items"]
    assert len(events) == 6 and all(set(e) == {"received_at", "handled", "note", "payload"} for e in events)
    assert all(e["handled"] is False for e in events)                           # nobody in our database matched any of them
    notes = [e["note"] for e in events]
    assert "no attendee matched a contact in our database" in notes and any("not JSON" in (n or "") for n in notes)
    assert events[-1]["payload"]["title"] == "Call" and events[0]["payload"]["b"] is None                      # newest first; NaN became null


def test_every_delivery_is_kept_whole_and_the_list_shows_the_newest_ten(env, conn):
    h = env["h"]("ann")
    token = env["c"].post("/api/me/connections/bluedot/token", headers=h).json()["webhook_url"].rsplit("/", 1)[1]
    for i in range(14):
        env["c"].post(f"/webhooks/bluedot/{token}", json={"title": f"Call {i}"})
    big = env["c"].post(f"/webhooks/bluedot/{token}", json={"title": "Long call", "transcript": "word " * 8000})
    assert big.json() == {"ok": True}
    events = env["c"].get("/api/me/connections/bluedot/events", headers=h).json()["items"]
    assert len(events) == 10                                                                       # the page lists the newest ten
    assert events[0]["payload"]["title"] == "Long call" and len(events[0]["payload"]["transcript"]) == 40000      # a full transcript stays whole
    assert q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 15                     # nothing is deleted


def test_a_body_sent_in_chunks_past_the_size_limit_is_kept_only_as_its_size(env, conn):
    token = env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann")).json()["webhook_url"].rsplit("/", 1)[1]
    r = env["c"].post(f"/webhooks/bluedot/{token}", content=(b"x" * 100_000 for _ in range(11)), headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    ev = env["c"].get("/api/me/connections/bluedot/events", headers=env["h"]("ann")).json()["items"][0]
    assert ev["payload"] == {"_too_large": True, "bytes": 1_100_000} and ev["handled"] is False and "too large" in ev["note"]
    assert env["c"].post(f"/webhooks/bluedot/{token}", content=b"x" * 1_100_000, headers={"Content-Type": "application/json"}).status_code == 413     # a declared size is refused up front


def test_a_matching_delivery_through_the_webhook_stores_a_team_visible_meeting(env, conn):
    ann = env["u"]["ann"]
    org = str(conn.execute("insert into organizations(display_name, canonical_domain) values ('Kestrel Pay Ltd','kestrel.example') returning id").fetchone()["id"])
    pid = str(conn.execute("insert into people(full_name, work_email) values ('Ada Clarke','ada@kestrel.example') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (pid, org))
    conn.commit()
    token = env["c"].post("/api/me/connections/bluedot/token", headers=env["h"]("ann")).json()["webhook_url"].rsplit("/", 1)[1]
    env["c"].post(f"/webhooks/bluedot/{token}", json={"id": "m-1", "title": "Payments review", "summary": "Agreed a pilot.", "attendees": ["ada@kestrel.example"]})
    row = q1(conn, "SELECT * FROM contact_interactions")
    assert str(row["user_id"]) == ann and row["channel"] == "meeting" and row["visibility"] == "team" and str(row["person_id"]) == pid and str(row["organization_id"]) == org
    ev = env["c"].get("/api/me/connections/bluedot/events", headers=env["h"]("ann")).json()["items"][0]
    assert ev["handled"] is True and env["c"].get("/api/me/connections", headers=env["h"]("ann")).json()["items"][2]["stored"] == 1


# ------------------------------------------------------------------ disconnect
def _seed_rows(conn, uid):
    for ch, ext in (("email", "m1"), ("email", "m2"), ("lemlist", "l1"), ("meeting", "b1:p")):
        store.upsert_interaction(conn, user_id=uid, channel=ch, external_id=ext, occurred_at=NOW, visibility="owner" if ch == "email" else "team")
    conn.commit()


def test_disconnecting_forgets_the_secret_and_keeps_the_data_unless_asked(env, conn):
    ann = env["u"]["ann"]
    env["c"].post("/api/me/connections/lemlist", json={"api_key": LEMLIST_KEY}, headers=env["h"]("ann"))
    _seed_rows(conn, ann)
    r = env["c"].post("/api/me/connections/lemlist/disconnect", json={}, headers=env["h"]("ann"))
    assert r.json() == {"ok": True}
    row = store.get(conn, ann, "lemlist")
    assert row["status"] == "disconnected" and row["secret_enc"] is None and store.secret(conn, KEY, ann, "lemlist") is None
    assert q1(conn, "SELECT count(*) AS n FROM contact_interactions")["n"] == 4
    r = env["c"].post("/api/me/connections/lemlist/disconnect", json={"delete_data": True}, headers=env["h"]("ann"))     # an old client asking to delete: ignored
    assert r.json() == {"ok": True}
    assert q1(conn, "SELECT count(*) AS n FROM contact_interactions")["n"] == 4                          # nothing is ever deleted
    ops = qa(conn, "SELECT operation, detail FROM audit_events WHERE operation='connection_disconnected' ORDER BY id")
    assert len(ops) == 2 and LEMLIST_KEY not in str(ops)
    assert env["c"].post("/api/me/connections/lemlist/disconnect", headers=env["h"]("ann")).json()["ok"] is True      # no body at all is fine, and repeating is harmless


def test_disconnecting_bluedot_keeps_the_raw_deliveries(env, conn):
    h = env["h"]("ann")
    token = env["c"].post("/api/me/connections/bluedot/token", headers=h).json()["webhook_url"].rsplit("/", 1)[1]
    env["c"].post(f"/webhooks/bluedot/{token}", json={"title": "x"})
    env["c"].post("/api/me/connections/bluedot/disconnect", json={"delete_data": True}, headers=h)
    assert len(env["c"].get("/api/me/connections/bluedot/events", headers=h).json()["items"]) == 1
    assert env["c"].post(f"/webhooks/bluedot/{token}", json={"title": "y"}).status_code == 404          # the old address stops working


def test_disconnecting_gmail_asks_google_to_revoke_first_and_still_works_when_it_cannot(env, conn, monkeypatch):
    ann = env["u"]["ann"]
    store.connect(conn, KEY, ann, "gmail", secret="refresh-token-ann", label="ann@gmail.example")
    conn.commit()
    asked = []
    fake = types.ModuleType("ri.connections.gmail_api")
    fake.revoke_token = lambda token: asked.append(token)
    monkeypatch.setitem(sys.modules, "ri.connections.gmail_api", fake)
    assert env["c"].post("/api/me/connections/gmail/disconnect", json={}, headers=env["h"]("ann")).json()["ok"] is True
    assert asked == ["refresh-token-ann"]
    assert store.get(conn, ann, "gmail")["secret_enc"] is None
    # Google cannot be reached: the secret is forgotten all the same, and nothing about the failure reaches the person
    store.connect(conn, KEY, ann, "gmail", secret="refresh-token-ann-2")
    conn.commit()

    def boom(token):
        raise RuntimeError("google down " + token)
    fake.revoke_token = boom
    r = env["c"].post("/api/me/connections/gmail/disconnect", json={}, headers=env["h"]("ann"))
    assert r.status_code == 200 and "refresh-token" not in r.text and store.get(conn, ann, "gmail")["status"] == "disconnected"


def test_disconnecting_gmail_works_when_the_gmail_helper_is_not_there(env, conn, monkeypatch):
    ann = env["u"]["ann"]
    store.connect(conn, KEY, ann, "gmail", secret="r")
    conn.commit()
    monkeypatch.setitem(sys.modules, "ri.connections.gmail_api", None)             # an import of it now raises ImportError
    assert env["c"].post("/api/me/connections/gmail/disconnect", json={}, headers=env["h"]("ann")).status_code == 200
    assert store.get(conn, ann, "gmail")["status"] == "disconnected"


# ------------------------------------------------------------------ settings
def test_the_sharing_switch_moves_the_visibility_of_emails_already_stored(env, conn):
    ann, bob = env["u"]["ann"], env["u"]["bob"]
    for u in (ann, bob):
        store.connect(conn, KEY, u, "gmail", secret="r")
        for i in range(3):
            store.upsert_interaction(conn, user_id=u, channel="email", external_id=f"{u}-{i}", occurred_at=NOW, visibility="owner", title="Subject", gist="Snippet")
        store.upsert_interaction(conn, user_id=u, channel="lemlist", external_id="l", occurred_at=NOW, visibility="team")
    conn.commit()
    vis = lambda u, ch: sorted({r["visibility"] for r in qa(conn, "SELECT visibility FROM contact_interactions WHERE user_id=%s AND channel=%s", (u, ch))})  # noqa: E731
    r = env["c"].patch("/api/me/connections/gmail/settings", json={"share_email_gist": True}, headers=env["h"]("ann"))
    assert r.status_code == 200 and r.json()["settings"] == {"share_email_gist": True} and r.json()["provider"] == "gmail" and r.json()["stored"] == 3
    assert vis(ann, "email") == ["team"] and vis(bob, "email") == ["owner"] and vis(ann, "lemlist") == ["team"]                 # Bob's rows and Ann's Lemlist rows untouched
    r = env["c"].patch("/api/me/connections/gmail/settings", json={"share_email_gist": False}, headers=env["h"]("ann"))
    assert r.json()["settings"] == {"share_email_gist": False} and vis(ann, "email") == ["owner"]
    ops = qa(conn, "SELECT actor, detail FROM audit_events WHERE operation='email_gist_sharing_changed' ORDER BY id")
    assert [(o["actor"], o["detail"]["share_email_gist"], o["detail"]["rows_changed"]) for o in ops] == [("ann@x", True, 3), ("ann@x", False, 3)]
    # a later sync stores new emails with the person's current choice (the store reads the setting)
    env["c"].patch("/api/me/connections/gmail/settings", json={"share_email_gist": True}, headers=env["h"]("ann"))
    assert store.email_visibility(store.get(conn, ann, "gmail")["settings"]) == "team"


def test_settings_are_only_for_gmail_and_only_for_a_connection_that_exists(env, conn):
    h = env["h"]("ann")
    assert env["c"].patch("/api/me/connections/lemlist/settings", json={"share_email_gist": True}, headers=h).status_code == 400
    assert env["c"].patch("/api/me/connections/gmail/settings", json={"share_email_gist": True}, headers=h).status_code == 404          # not connected
    store.connect(conn, KEY, env["u"]["ann"], "gmail", secret="r")
    conn.commit()
    assert env["c"].patch("/api/me/connections/gmail/settings", json={}, headers=h).status_code == 400
    assert env["c"].patch("/api/me/connections/gmail/settings", json={"share_email_gist": "maybe"}, headers=h).status_code == 422


# ------------------------------------------------------------------ sync now
class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def test_sync_now_queues_once_and_only_for_a_connected_lemlist_or_gmail(env, conn, monkeypatch):
    monkeypatch.setattr("ri.api.connections_api.datetime", _FrozenClock)         # the "same minute" must not depend on when the test runs
    h = env["h"]("ann")
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=h).status_code == 409                  # not connected
    assert env["c"].post("/api/me/connections/bluedot/sync", headers=h).status_code == 400
    store.connect(conn, KEY, env["u"]["ann"], "lemlist", secret=LEMLIST_KEY)
    store.connect(conn, KEY, env["u"]["ann"], "gmail", secret="r")
    conn.commit()
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=h).json() == {"queued": True}
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=h).json() == {"queued": False}         # one is already waiting
    assert env["c"].post("/api/me/connections/gmail/sync", headers=h).json() == {"queued": True}
    kinds = sorted(r["kind"] for r in qa(conn, "SELECT kind FROM jobs"))
    assert kinds == ["gmail_sync", "lemlist_user_sync"]
    conn.execute("UPDATE jobs SET status='completed'")
    conn.commit()
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=h).json() == {"queued": False}         # the same minute: the same job, never a second
    conn.execute("UPDATE jobs SET idempotency_key = idempotency_key || '-old'")
    conn.commit()
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=h).json() == {"queued": True}
    disconnected = env["c"].post("/api/me/connections/lemlist/disconnect", json={}, headers=h)
    assert disconnected.status_code == 200 and env["c"].post("/api/me/connections/lemlist/sync", headers=h).status_code == 409


def test_one_persons_pending_sync_does_not_block_anothers(env, conn):
    for who in ("ann", "bob"):
        store.connect(conn, KEY, env["u"][who], "lemlist", secret="k" * 12)
    conn.commit()
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=env["h"]("ann")).json() == {"queued": True}
    assert env["c"].post("/api/me/connections/lemlist/sync", headers=env["h"]("bob")).json() == {"queued": True}
    payloads = sorted(r["payload"]["user_id"] for r in qa(conn, "SELECT payload FROM jobs"))
    assert payloads == sorted([env["u"]["ann"], env["u"]["bob"]])
