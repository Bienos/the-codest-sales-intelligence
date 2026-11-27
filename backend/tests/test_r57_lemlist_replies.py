"""What people wrote back to our Lemlist campaigns, and answering them in the same thread. Lemlist is a fake: nothing here touches the network.
Every name and address is invented."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_r4_crm_notify import mk_user
from test_r29_buying_signals import company

from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.db import Database
from ri.outbound import replies as rp
from ri.outbound.lemlist import LemlistError
from ri.people import apply_lemlist_activity

SECRET = "r" * 40
NOW = datetime.now(timezone.utc)
REPLY_ID = "act_ReplyFromOla0001"
SENT_ID = "act_SentToOla000001"

REPLY_TEXT = ("Hi Tom,\nthanks for the note. Yes, we are looking for two Python engineers from January.\nCan we talk on Tuesday?\n\nOla\n\n"
              "On Mon, 23 Nov 2026 at 09:12 Tom Field <tom@sender.example>\nwrote:\n\n> Hello Ola,\n> would an extra team help with the claims platform?\n")
REPLY_HTML = ('<div dir="ltr">Hi Tom,<br>thanks for the note. Yes, we are looking for two Python engineers from January.<br>Can we talk on Tuesday?<br><br>Ola</div>'
              '<br><div class="gmail_quote"><div class="gmail_attr">On Mon, 23 Nov 2026 Tom Field wrote:</div><blockquote>Hello Ola, would an extra team help?</blockquote></div>')
SENT_HTML = '<html><div>Hello Ola,</div><div>would an extra team help with the claims platform?</div><img src="https://track.example/open.gif"></html>'


def reply_activity(**over):
    """An emailsReplied activity as Lemlist documents it (webhook payload / GET /activities v2), with invented values."""
    a = {"_id": REPLY_ID, "type": "emailsReplied", "createdAt": (NOW - timedelta(hours=3)).isoformat(), "leadEmail": "ola@fjordsure.example",
         "campaignId": "cam_Claims0001", "campaignName": "Claims platforms", "teamId": "tea_Codest00001", "leadId": "lea_Ola000000001",
         "contactId": "ctc_Ola000000001", "sendUserId": "usr_Tom000000001", "sendUserEmail": "tom@sender.example", "sendUserName": "Tom Field",
         "sendUserMailboxId": "usm_Tom000000001", "subject": "Re: Claims platform", "messageId": "<m1@mail.example>", "text": REPLY_TEXT}
    a.update(over)
    return {k: v for k, v in a.items() if v is not None}


class FakeLemlist:
    """Answers the inbox and activity routes from fixed payloads, and records every send."""

    def __init__(self, *, feed=None, thread=None, send_error=None):
        self.feed, self.thread, self.send_error = feed or [], thread or {}, send_error
        self.sent, self.calls = [], []

    def reply_activities(self, *, offset=0, limit=100):
        self.calls.append(("feed", offset))
        return self.feed[offset:offset + limit]

    def contact_messages(self, contact_id, *, limit=50):
        self.calls.append(("inbox", contact_id))
        return self.thread.get(contact_id, [])

    def leads_by_email(self, email):
        self.calls.append(("lead", email))
        return []

    def send_email(self, **kw):
        self.calls.append(("send",))
        self.sent.append(kw)
        if self.send_error:
            raise self.send_error
        return {"ok": True, "contactId": kw.get("contact_id")}


@pytest.fixture()
def world(conn, db_url, monkeypatch):
    mk_user(conn, "bdm@x", "bdm", 555)
    mk_user(conn, "admin@x", "admin")
    mk_user(conn, "analyst@x", "research_analyst")
    mk_user(conn, "other@x", "bdm", 777)
    oid = company(conn, "Fjordsure Insurance", country="NO", segment="Insurance", priority="P1", score=90)
    pid = str(conn.execute("insert into people(full_name, work_email, email_verification_status) values ('Ola Nordmann','ola@fjordsure.example','Verified') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'Head of Claims IT')", (pid, oid))
    owned = company(conn, "Owned Elsewhere Bank", owner=999, priority="P1")
    other = str(conn.execute("insert into people(full_name, work_email) values ('Kari Owned','kari@owned.example') returning id").fetchone()["id"])
    conn.execute("insert into employment(person_id, organization_id, title) values (%s,%s,'CTO')", (other, owned))
    conn.commit()
    fake = FakeLemlist()
    monkeypatch.setattr(rp, "make_client", lambda settings: fake)
    db = Database(db_url, max_size=3)
    apps = {on: TestClient(create_app(Settings(env="development", auth_mode="dev", dev_auth_secret=SECRET, outbound_enabled=on, lemlist_api_key="test-key"), db=db))
            for on in (False, True)}
    hdr = lambda who: {"Authorization": f"Bearer {make_dev_token(SECRET, who + '@x')}"}  # noqa: E731
    yield {"off": apps[False], "on": apps[True], "h": hdr, "pid": pid, "oid": oid, "other": other, "fake": fake}
    db.close()


def _store_reply(conn, **over):
    apply_lemlist_activity(conn, reply_activity(**over))
    conn.commit()


def _send(world, who="bdm", app="on", key="draft-0001-aaaa", text="Hi Ola,\nTuesday at 10:00 works. <b>Speak then</b>.\n\nTom", activity=REPLY_ID, pid=None):
    return world[app].post(f"/api/people/{pid or world['pid']}/email-thread/reply", headers=world["h"](who), json={"activity_id": activity, "text": text, "key": key})


# ------------------------------------------------------------------------------------------------ reading the words
def test_quoted_history_is_cut_from_text_and_html():
    text, preview = rp.extract_body({"text": REPLY_TEXT})
    assert not preview and text.endswith("Ola") and "Can we talk on Tuesday?" in text and "wrote" not in text and ">" not in text
    html_text, _ = rp.extract_body({"message": REPLY_HTML})
    assert html_text == text
    pl, _ = rp.extract_body({"text": "Dzień dobry, chętnie porozmawiam.\n\nW dniu 23.11.2026 o 09:12 Tom Field <tom@sender.example> napisał(a):\n> Dzień dobry"})
    assert pl == "Dzień dobry, chętnie porozmawiam."
    only, is_preview = rp.extract_body({"messagePreview": "Yes, let us talk"})
    assert only == "Yes, let us talk" and is_preview
    long, _ = rp.extract_body({"text": "x" * (rp.MAX_BODY + 50)})
    assert len(long) == rp.MAX_BODY + 1 and long.endswith("…")


def test_the_activity_feed_brings_the_words_and_the_thread_ids(world, conn):
    _store_reply(conn)
    r = conn.execute("select * from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()
    assert r["body_source"] == "activity" and r["body_text"].startswith("Hi Tom,") and "would an extra team" not in r["body_text"]
    assert (r["send_user_id"], r["send_user_email"], r["send_user_mailbox_id"], r["lemlist_contact_id"], r["lemlist_team_id"]) == \
        ("usr_Tom000000001", "tom@sender.example", "usm_Tom000000001", "ctc_Ola000000001", "tea_Codest00001")
    # the same activity again with only a preview never replaces the full words
    apply_lemlist_activity(conn, reply_activity(text=None, messagePreview="Hi Tom"))
    conn.commit()
    assert conn.execute("select body_source, body_text from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_source"] == "activity"


def test_hourly_sync_fills_missing_words_from_the_inbox_thread(world, conn):
    # stored earlier by the feed with no words and no ids (as the nine replies in production are)
    _store_reply(conn, text=None, contactId=None, sendUserId=None, sendUserEmail=None, sendUserMailboxId=None, teamId=None, leadId=None, subject=None, messageId=None)
    fake = FakeLemlist(feed=[reply_activity(text=None)],                                     # the replies feed names the contact, but carries no words
                       thread={"ctc_Ola000000001": [
                           {"_id": SENT_ID, "type": "emailsSent", "leadEmail": "ola@fjordsure.example", "campaignId": "cam_Claims0001", "campaignName": "Claims platforms",
                            "createdAt": (NOW - timedelta(days=2)).isoformat(), "subject": "Claims platform", "message": SENT_HTML, "sendUserId": "usr_Tom000000001",
                            "sendUserEmail": "tom@sender.example", "sendUserMailboxId": "usm_Tom000000001", "contactId": "ctc_Ola000000001", "teamId": "tea_Codest00001"},
                           {"_id": REPLY_ID, "type": "emailsReplied", "leadEmail": "ola@fjordsure.example", "fromEmail": "ola@fjordsure.example", "message": REPLY_HTML,
                            "subject": "Re: Claims platform", "contactId": "ctc_Ola000000001"}]})
    out = rp.fill_reply_bodies(conn, fake)
    assert out["asked"] == 1 and out["threads"] == 1 and ("inbox", "ctc_Ola000000001") in fake.calls
    rows = {r["id"]: r for r in conn.execute("select * from lemlist_activities where person_id=%s", (world["pid"],)).fetchall()}
    assert rows[REPLY_ID]["body_source"] == "inbox" and rows[REPLY_ID]["body_text"].startswith("Hi Tom,") and "extra team" not in rows[REPLY_ID]["body_text"]
    assert rows[REPLY_ID]["send_user_mailbox_id"] == "usm_Tom000000001" and rows[REPLY_ID]["body_attempts"] == 1
    assert rows[SENT_ID]["body_text"] == "Hello Ola,\nwould an extra team help with the claims platform?"     # our own campaign email, for the thread
    # nothing left to ask: a second run calls Lemlist no more
    fake.calls.clear()
    assert rp.fill_reply_bodies(conn, fake) == {"asked": 0} and fake.calls == []


def test_lemlist_down_never_breaks_the_hourly_sync(world, conn):
    _store_reply(conn, text=None)

    class Down(FakeLemlist):
        def reply_activities(self, **kw):
            raise LemlistError("Lemlist 503 after retries", 503)
    out = rp.fill_reply_bodies(conn, Down())
    assert out["asked"] == 1 and "error" in out
    assert conn.execute("select body_attempts from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_attempts"] == 0


# ------------------------------------------------------------------------------------------------ showing them
def test_today_shows_what_they_wrote(world, conn):
    _store_reply(conn)
    b = world["on"].get("/api/today/brief", headers=world["h"]("bdm")).json()
    item = next(a for a in b["actions"] if a["kind"] == "reply")
    rep = item["reply"]
    assert item["person"]["name"] == "Ola Nordmann" and rep["campaign"] == "Claims platforms" and rep["activity_id"] == REPLY_ID
    assert rep["text"].startswith("Hi Tom,") and rep["text_state"] == "full" and rep["blocked"] is None
    assert rep["sender"] == {"name": "Tom Field", "email": "tom@sender.example"}
    assert rep["lemlist_url"] == "https://app.lemlist.com/teams/tea_Codest00001/inbox/list/myConversations/contacts/ctc_Ola000000001"


def test_a_reply_without_words_says_so_plainly(world, conn):
    _store_reply(conn, text=None, teamId=None)
    rep = next(a for a in world["on"].get("/api/today/brief", headers=world["h"]("bdm")).json()["actions"] if a["kind"] == "reply")["reply"]
    assert rep["text"] is None and rep["text_state"] == "pending" and rep["lemlist_url"] is None
    conn.execute("update lemlist_activities set body_attempts=%s where id=%s", (rp.MAX_ATTEMPTS, REPLY_ID))
    conn.commit()
    rep = next(a for a in world["on"].get("/api/today/brief", headers=world["h"]("bdm")).json()["actions"] if a["kind"] == "reply")["reply"]
    assert rep["text_state"] == "missing"


def test_thread_on_the_person_page_and_on_demand_refresh(world, conn, monkeypatch):
    _store_reply(conn, text=None)
    fake = FakeLemlist(thread={"ctc_Ola000000001": [
        {"_id": SENT_ID, "type": "emailsSent", "leadEmail": "ola@fjordsure.example", "campaignId": "cam_Claims0001", "campaignName": "Claims platforms",
         "createdAt": (NOW - timedelta(days=2)).isoformat(), "subject": "Claims platform", "message": SENT_HTML},
        {"_id": REPLY_ID, "type": "emailsReplied", "leadEmail": "ola@fjordsure.example", "message": REPLY_HTML}]})
    monkeypatch.setattr(rp, "make_client", lambda settings: fake)
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert [i["kind"] for i in t["items"]] == ["reply"] and t["items"][0]["text"] is None and t["sending_on"] is True and t["may_send"] is True
    t = world["on"].post(f"/api/people/{world['pid']}/email-thread/refresh", headers=world["h"]("bdm")).json()
    assert t["refresh"]["refreshed"] is True
    assert [i["kind"] for i in t["items"]] == ["sent", "reply"] and t["items"][1]["text"].startswith("Hi Tom,") and t["reply"]["activity_id"] == REPLY_ID
    # opening it again at once does not ask Lemlist again
    fake.calls.clear()
    assert world["on"].post(f"/api/people/{world['pid']}/email-thread/refresh", headers=world["h"]("bdm")).json()["refresh"] == {"refreshed": False}
    assert fake.calls == []


# ------------------------------------------------------------------------------------------------ answering
def test_send_is_refused_while_the_switch_is_off(world, conn):
    _store_reply(conn)
    r = _send(world, app="off")
    assert r.status_code == 403 and r.json()["detail"] == "Sending is switched off by the administrator. Nothing was sent."
    assert world["fake"].sent == [] and conn.execute("select count(*) n from lemlist_replies").fetchone()["n"] == 0
    a = conn.execute("select actor, detail from audit_events where operation='lemlist_reply_refused'").fetchone()
    assert a["actor"] == "bdm@x" and a["detail"]["reason"] == "switched_off"
    t = world["off"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert t["sending_on"] is False                       # the box is shown, Send is disabled with the plain reason


def test_send_threads_from_the_campaign_mailbox_once_per_draft_and_is_audited(world, conn):
    _store_reply(conn)
    r = _send(world)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["repeat"] is False
    [kw] = world["fake"].sent
    assert kw["reply_to_activity_id"] == REPLY_ID and kw["contact_id"] == "ctc_Ola000000001" and kw["lead_id"] is None
    assert (kw["send_user_id"], kw["send_user_email"], kw["send_user_mailbox_id"]) == ("usr_Tom000000001", "tom@sender.example", "usm_Tom000000001")
    assert kw["message_html"] == "<p>Hi Ola,<br>Tuesday at 10:00 works. &lt;b&gt;Speak then&lt;/b&gt;.</p><p>Tom</p>"
    # a double click (same draft key) never sends twice
    again = _send(world)
    assert again.status_code == 200 and again.json()["repeat"] is True and len(world["fake"].sent) == 1
    # the same words from a second tab (another key) are not sent twice either
    twin = _send(world, key="draft-0002-bbbb")
    assert twin.status_code == 409 and len(world["fake"].sent) == 1
    audit = conn.execute("select actor, detail from audit_events where operation='lemlist_reply_sent'").fetchall()
    assert len(audit) == 1 and audit[0]["actor"] == "bdm@x" and audit[0]["detail"]["chars"] == len("Hi Ola,\nTuesday at 10:00 works. <b>Speak then</b>.\n\nTom")
    assert "Tuesday" not in str(audit[0]["detail"])        # the words never go into the audit trail
    thread = r.json()["thread"]
    mine = [i for i in thread["items"] if i["kind"] == "answer"]
    assert len(mine) == 1 and mine[0]["status"] == "sent" and mine[0]["text"].startswith("Hi Ola,") and thread["reply"]["answered"]["status"] == "sent"


def test_unclear_lemlist_answer_is_never_sent_again_with_the_same_draft(world, conn):
    _store_reply(conn)
    world["fake"].send_error = LemlistError("Lemlist 502", 502)
    r = _send(world)
    assert r.status_code == 502 and "did not confirm" in r.json()["detail"]
    assert _send(world).status_code == 409 and len(world["fake"].sent) == 1
    assert conn.execute("select status from lemlist_replies").fetchone()["status"] == "unknown"


def test_a_definite_refusal_can_be_sent_again_with_the_same_draft(world, conn):
    _store_reply(conn)
    world["fake"].send_error = LemlistError('Lemlist 400: {"error":"Alias not found"}', 400)
    r = _send(world)
    assert r.status_code == 409 and "sender address" in r.json()["detail"] and "Nothing was sent" in r.json()["detail"]
    world["fake"].send_error = None
    assert _send(world).json()["status"] == "sent" and len(world["fake"].sent) == 2
    assert conn.execute("select count(*) n from lemlist_replies").fetchone()["n"] == 1


def test_suppressed_or_unsubscribed_people_are_never_sent_to(world, conn):
    _store_reply(conn)
    conn.execute("update people set suppressed=true, suppression_reason='asked to stop' where id=%s", (world["pid"],))
    conn.commit()
    r = _send(world)
    assert r.status_code == 409 and "do-not-contact" in r.json()["detail"] and world["fake"].sent == []
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert t["reply"]["blocked"] == "do_not_contact" and t["person"]["do_not_contact"] is True
    conn.execute("update people set erased_at=now() where id=%s", (world["pid"],))
    conn.commit()
    assert _send(world).status_code == 404 and world["fake"].sent == []


def test_only_someone_who_wrote_to_us_and_only_from_a_known_mailbox(world, conn):
    _store_reply(conn)
    apply_lemlist_activity(conn, reply_activity(_id=SENT_ID, type="emailsSent", text="Hello Ola"))
    conn.commit()
    assert _send(world, activity=SENT_ID).status_code == 404                     # our own email is not a reply
    assert _send(world, activity=REPLY_ID, pid=world["other"]).status_code == 404  # a reply belongs to its own person (and Kari is not visible)
    # no mailbox anywhere in what Lemlist told us: refused, never a guessed sender
    conn.execute("update lemlist_activities set send_user_mailbox_id=NULL where person_id=%s", (world["pid"],))
    conn.commit()
    r = _send(world)
    assert r.status_code == 409 and "which mailbox" in r.json()["detail"] and world["fake"].sent == []
    # the reply lacks it but our campaign email to the same person in the same campaign names it: that mailbox answers
    conn.execute("update lemlist_activities set send_user_mailbox_id='usm_Tom000000001' where id=%s", (SENT_ID,))
    conn.commit()
    assert _send(world).json()["status"] == "sent" and world["fake"].sent[0]["send_user_mailbox_id"] == "usm_Tom000000001"


def test_permissions_hold(world, conn):
    _store_reply(conn)
    assert world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("analyst")).status_code == 403
    assert _send(world, who="analyst").status_code == 403
    assert world["on"].get(f"/api/people/{world['other']}/email-thread", headers=world["h"]("bdm")).status_code == 404   # another seller's account
    assert world["on"].get("/api/replies/config", headers=world["h"]("bdm")).json() == {"sending_on": True, "lemlist_connected": True, "may_send": True}
    assert world["fake"].sent == []
    assert _send(world, who="admin").json()["status"] == "sent"


def test_the_hourly_lemlist_job_also_fetches_missing_words(world, conn, monkeypatch):
    """The existing hourly `lemlist_sync` job: the activity feed first, then the words of replies that came without them."""
    from ri.jobs.handlers import build_handlers
    from ri.outbound import lemlist as lemlist_mod

    class Feed(FakeLemlist):
        def activities(self, *, offset=0, limit=100, campaign_id=None):
            return [reply_activity(text=None)] if offset == 0 else []
    fake = Feed(feed=[reply_activity(text=None)], thread={"ctc_Ola000000001": [{"_id": REPLY_ID, "type": "emailsReplied", "leadEmail": "ola@fjordsure.example", "message": REPLY_HTML}]})
    monkeypatch.setattr(lemlist_mod, "LemlistClient", lambda key: fake)
    monkeypatch.setenv("LEMLIST_API_KEY", "test-key")
    out = build_handlers(connector_factory=lambda sid: None)["lemlist_sync"](conn, {"payload": {}})
    assert out["new"] == 1 and out["reply_text"]["threads"] == 1
    assert conn.execute("select body_text from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_text"].startswith("Hi Tom,")
