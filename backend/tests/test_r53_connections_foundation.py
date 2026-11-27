"""Personal connections foundation: the vault seals credentials to their owner, the store never leaks them, syncs are queued once per hour per connection,
and what a connection brought can be deleted with it. No network; all names invented."""
from datetime import datetime, timedelta, timezone

import pytest

from ri import vault
from ri.connections import store
from ri.db import q1, qa

KEY = vault.new_key()
NOW = datetime(2026, 11, 27, 10, 30, tzinfo=timezone.utc)


def _user(conn, email="ann@example.test", active=True):
    return str(conn.execute("INSERT INTO users(email, display_name, role, active) VALUES (%s,'Ann Example','bdm',%s) RETURNING id", (email, active)).fetchone()["id"])


def test_the_vault_round_trips_and_binds_a_value_to_its_owner():
    blob = vault.seal(KEY, "refresh-token-123", "u1:gmail")
    assert b"refresh-token-123" not in blob and vault.unseal(KEY, blob, "u1:gmail") == "refresh-token-123"
    with pytest.raises(vault.VaultError):
        vault.unseal(KEY, blob, "u2:gmail")                      # copied to another user
    with pytest.raises(vault.VaultError):
        vault.unseal(vault.new_key(), blob, "u1:gmail")          # wrong key
    tampered = bytearray(blob); tampered[-1] ^= 1
    with pytest.raises(vault.VaultError):
        vault.unseal(KEY, bytes(tampered), "u1:gmail")
    assert vault.seal(KEY, "x", "a") != vault.seal(KEY, "x", "a")       # a fresh nonce every time


def test_without_a_key_nothing_can_be_stored():
    assert vault.available(None) is False and vault.available("short") is False and vault.available(KEY) is True
    with pytest.raises(vault.VaultError):
        vault.seal(None, "x", "a")
    with pytest.raises(vault.VaultError):
        vault.seal("not-32-bytes", "x", "a")


def test_a_connection_keeps_its_secret_sealed_and_the_api_view_never_shows_it(conn):
    uid = _user(conn)
    pub = store.connect(conn, KEY, uid, "gmail", secret="refresh-abc", label="ann@example.test", scopes="gmail.readonly")
    assert pub["status"] == "connected" and "secret_enc" not in pub and "cursor" not in pub and pub["settings"] == {"share_email_gist": False}
    raw = store.get(conn, uid, "gmail")
    assert raw["secret_enc"] and b"refresh-abc" not in bytes(raw["secret_enc"])
    assert store.secret(conn, KEY, uid, "gmail") == "refresh-abc"
    other = _user(conn, "bob@example.test")
    conn.execute("UPDATE user_connections SET user_id=%s WHERE user_id=%s", (other, uid))      # a row copied to another owner cannot be opened
    with pytest.raises(vault.VaultError):
        store.secret(conn, KEY, other, "gmail")


def test_connecting_again_renews_without_losing_settings_and_lists_every_provider(conn):
    uid = _user(conn)
    store.connect(conn, KEY, uid, "lemlist", secret="key-1", label="Team A", settings={"x": 1})
    again = store.connect(conn, KEY, uid, "lemlist", secret="key-2")
    assert again["account_label"] == "Team A" and again["settings"]["x"] == 1
    assert store.secret(conn, KEY, uid, "lemlist") == "key-2"
    assert [c["provider"] for c in store.list_public(conn, uid)] == ["gmail", "lemlist", "bluedot"]
    assert {c["provider"]: c["status"] for c in store.list_public(conn, uid)} == {"gmail": "disconnected", "lemlist": "connected", "bluedot": "disconnected"}


def test_an_email_stays_with_its_owner_unless_they_share_it(conn):
    uid = _user(conn)
    store.connect(conn, KEY, uid, "gmail", secret="r")
    assert store.email_visibility(store.get(conn, uid, "gmail")["settings"]) == "owner"
    store.update_settings(conn, uid, "gmail", {"share_email_gist": True})
    assert store.email_visibility(store.get(conn, uid, "gmail")["settings"]) == "team"


def test_interactions_are_repeatable_and_disconnecting_forgets_the_secret_and_can_delete_what_came(conn):
    uid = _user(conn)
    store.connect(conn, KEY, uid, "gmail", secret="r")
    kw = dict(user_id=uid, channel="email", external_id="m1", occurred_at=NOW, visibility="owner", direction="out", title="Hello", gist="First line")
    assert store.upsert_interaction(conn, **kw) is True
    assert store.upsert_interaction(conn, **{**kw, "title": "Hello again"}) is False                     # a repeat refreshes, never duplicates
    assert q1(conn, "SELECT title FROM contact_interactions WHERE external_id='m1'")["title"] == "Hello again"
    store.upsert_interaction(conn, user_id=uid, channel="meeting", external_id="b1", occurred_at=NOW, visibility="team")
    assert store.disconnect(conn, uid, "gmail", delete_data=False) == 0
    assert store.secret(conn, KEY, uid, "gmail") is None and store.get(conn, uid, "gmail")["status"] == "disconnected"
    assert q1(conn, "SELECT count(*) AS n FROM contact_interactions")["n"] == 2
    assert store.disconnect(conn, uid, "gmail", delete_data=True) == 1                                  # only that channel's rows go
    assert [r["channel"] for r in qa(conn, "SELECT channel FROM contact_interactions")] == ["meeting"]


def test_a_sync_error_marks_the_connection_and_a_good_run_clears_it(conn):
    uid = _user(conn)
    store.connect(conn, KEY, uid, "gmail", secret="r")
    store.record_sync(conn, uid, "gmail", NOW, error="Google refused the token")
    assert store.get(conn, uid, "gmail")["status"] == "needs_attention"
    store.record_sync(conn, uid, "gmail", NOW, cursor={"after": "2026/11/27"})
    row = store.get(conn, uid, "gmail")
    assert row["status"] == "connected" and row["last_error"] is None and row["cursor"] == {"after": "2026/11/27"}


def test_syncs_are_queued_once_an_hour_for_connected_active_people_only(conn):
    ann, bob, cy, di = _user(conn, "a@example.test"), _user(conn, "b@example.test"), _user(conn, "c@example.test", active=False), _user(conn, "d@example.test")
    for u in (ann, bob, cy):
        store.connect(conn, KEY, u, "gmail", secret="r")
    store.connect(conn, KEY, ann, "lemlist", secret="k")
    store.connect(conn, KEY, di, "bluedot", webhook_token=store.new_webhook_token())                  # pushes by webhook: never polled
    store.disconnect(conn, bob, "gmail", delete_data=False)
    assert store.schedule_syncs(conn, NOW) == 2                                                          # ann: gmail + lemlist
    assert store.schedule_syncs(conn, NOW) == 0                                                          # same hour: nothing twice
    kinds = sorted(r["kind"] for r in qa(conn, "SELECT kind FROM jobs WHERE kind IN ('gmail_sync','lemlist_user_sync')"))
    assert kinds == ["gmail_sync", "lemlist_user_sync"]
    store.record_sync(conn, ann, "gmail", NOW)
    assert store.schedule_syncs(conn, NOW + timedelta(minutes=30)) == 0                                  # synced half an hour ago
    assert store.schedule_syncs(conn, NOW + timedelta(hours=2)) == 0                                     # the first jobs never ran (worker busy or down): no pile-up
    conn.execute("UPDATE jobs SET status='completed' WHERE kind IN ('gmail_sync','lemlist_user_sync')")
    assert store.schedule_syncs(conn, NOW + timedelta(hours=2)) == 2


def test_a_webhook_token_finds_its_owner_and_is_stored_only_as_a_hash(conn):
    uid = _user(conn)
    token = store.new_webhook_token()
    store.connect(conn, KEY, uid, "bluedot", webhook_token=token, label="Bluedot")
    assert str(store.by_webhook_token(conn, token)["user_id"]) == uid and store.by_webhook_token(conn, token + "x") is None
    assert token not in str(store.get(conn, uid, "bluedot"))
    store.disconnect(conn, uid, "bluedot", delete_data=False)
    assert store.by_webhook_token(conn, token) is None                                                   # a disconnected token stops working


def test_old_interactions_and_webhook_logs_are_trimmed(conn):
    uid = _user(conn)
    store.upsert_interaction(conn, user_id=uid, channel="email", external_id="old", occurred_at=NOW - timedelta(days=600), visibility="owner")
    store.upsert_interaction(conn, user_id=uid, channel="email", external_id="new", occurred_at=NOW - timedelta(days=30), visibility="owner")
    conn.execute("INSERT INTO connection_events(user_id, provider, received_at, payload) VALUES (%s,'bluedot',%s,'{}'::jsonb)", (uid, NOW - timedelta(days=40)))
    assert store.purge_old(conn, NOW) == 1
    assert [r["external_id"] for r in qa(conn, "SELECT external_id FROM contact_interactions")] == ["new"]
    assert q1(conn, "SELECT count(*) AS n FROM connection_events")["n"] == 0


def test_the_sync_lane_is_declared_and_runs_with_nothing_connected(conn):
    from ri import scheduler
    assert "connection_sync" in scheduler.LANE and scheduler.LANE["connection_sync"].can_pause
    out = scheduler.run_maintenance(conn, NOW)
    assert out.get("connection_sync", 0) == 0


def test_erasing_a_person_deletes_their_conversations_whatever_path_erased_them(conn):
    uid = _user(conn)
    pid = str(conn.execute("INSERT INTO people(full_name) VALUES ('Maja Example') RETURNING id").fetchone()["id"])
    other = str(conn.execute("INSERT INTO people(full_name) VALUES ('Jon Example') RETURNING id").fetchone()["id"])
    for i, p in enumerate((pid, pid, other)):
        store.upsert_interaction(conn, user_id=uid, channel="email", external_id=f"m{i}", occurred_at=NOW, visibility="owner", person_id=p)
    conn.execute("UPDATE people SET erased_at=now() WHERE id=%s", (pid,))
    assert [str(r["person_id"]) for r in qa(conn, "SELECT person_id FROM contact_interactions")] == [other]
