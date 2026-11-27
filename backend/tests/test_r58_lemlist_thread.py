"""Round 10: a Lemlist thread as separate emails, the quoted history cut in every common form, the thread kept on the person's page and on
the Conversations timeline, and the replies stored before this round cleaned again. Lemlist is a FAKE (no network); every name, address and
email is invented."""
from datetime import datetime, timedelta, timezone

import pytest
from test_r55_lemlist_bluedot import S as VAULT_SETTINGS
from test_r55_lemlist_bluedot import connect_lemlist, job
from test_r57_lemlist_replies import (  # noqa: F401 - world is a fixture
    REPLY_ID,
    SENT_ID,
    FakeLemlist,
    reply_activity,
    world,
)

from ri.connections import lemlist_sync, store
from ri.outbound import replies as rp
from ri.outbound.email_text import clean, extract
from ri.outbound.email_timeline import record_timeline
from ri.people import apply_lemlist_activity

T0 = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
CAMPAIGN, CAMPAIGN_NAME = "cam_Claims0001", "Claims platforms"
SENT2_ID = "act_SentToOla000002"
OUR_EMAIL_1 = ('<html><div>Hello Ola,</div><div>Nordic insurers are moving claims to new platforms. Would an extra Python team help yours?</div>'
               '<div>Tom</div><img src="https://track.example/open.gif"></html>')

OUTLOOK_EN = ("Hi Tom,\nyes, we are looking for two Python engineers from January.\nCan we talk on Tuesday?\n\nBest regards\nOla Nordmann\n\n"
              "From: Tom Field <tom@sender.example>\nSent: Monday, November 24, 2026 10:12 AM\nTo: Ola Nordmann <ola@fjordsure.example>\n"
              "Subject: Claims platform\n\nHello Ola,\nNordic insurers are moving claims to new platforms.")
OUTLOOK_ONE_LINE = ("Hi Tom, yes, let us talk on Tuesday. Ola ________________________________ From: Tom Field <tom@sender.example> "
                    "Sent: Monday, November 24, 2026 10:12 AM To: Ola Nordmann Subject: Claims platform Hello Ola, would an extra team help?")
GERMAN = ("Hallo Herr Field,\nwir melden uns im Januar.\n\nMit freundlichen Grüßen\nKlaus Beispiel\n\nVon: Tom Field <tom@sender.example>\n"
          "Gesendet: Montag, 24. November 2026 10:12\nAn: Klaus Beispiel <klaus@versicher.example>\nBetreff: Plattform\n\nGuten Tag Herr Beispiel,")
POLISH = ("Dzień dobry,\nproszę o kontakt w styczniu.\n\nPozdrawiam\nAnna Przykład\n\nOd: Tom Field <tom@sender.example>\n"
          "Wysłano: poniedziałek, 24 listopada 2026 10:12\nDo: Anna Przykład <anna@bank.example>\nTemat: Platforma szkód\n\nDzień dobry Pani Anno,")
OUTLOOK_HTML = ('<html><body><div class=WordSection1><p class=MsoNormal>Hi Tom,</p><p class=MsoNormal>Tuesday at 10:00 works.</p><p class=MsoNormal>Ola</p>'
                '<div><div style="border:none;border-top:solid #E1E1E1 1.0pt;padding:3.0pt 0cm 0cm 0cm"><p class=MsoNormal><b>Fra:</b> Tom Field<br>'
                '<b>Sendt:</b> 24. november 2026 10:12<br><b>Til:</b> Ola Nordmann<br><b>Emne:</b> Plattform</p></div></div><p>Hello Ola,</p></div></body></html>')
OWA_HTML = ('<div dir="ltr">Hi Tom, call me on Monday.</div><div id="appendonsend"></div><hr style="display:inline-block;width:98%">'
            '<div id="divRplyFwdMsg" dir="ltr"><b>From:</b> Tom Field<br><b>Sent:</b> Monday<br><b>To:</b> Ola<br><b>Subject:</b> x</div><div>Hello Ola</div>')


# ------------------------------------------------------------------------------------------------ cutting the quoted history
@pytest.mark.parametrize("raw,fmt,own,gone", [
    (OUTLOOK_EN, "text", "Can we talk on Tuesday?\n\nBest regards\nOla Nordmann", "Nordic insurers"),
    (OUTLOOK_ONE_LINE, "text", "Hi Tom, yes, let us talk on Tuesday. Ola", "would an extra team"),
    (GERMAN, "text", "wir melden uns im Januar.\n\nMit freundlichen Grüßen\nKlaus Beispiel", "Guten Tag"),
    (POLISH, "text", "proszę o kontakt w styczniu.\n\nPozdrawiam\nAnna Przykład", "Pani Anno"),
    ("Hej Tom,\nvi hörs i januari.\n\nFrån: Tom Field\nSkickat: den 24 november 2026 10:12\nTill: Sven\nÄmne: Plattform\n\nHej Sven,", "text", "vi hörs i januari.", "Hej Sven"),
    ("Bonjour Tom,\nrappelez-moi lundi.\n\nDe : Tom Field\nEnvoyé : lundi 24 novembre 2026\nÀ : Claire\nObjet : Plateforme\n\nBonjour Claire", "text",
     "rappelez-moi lundi.", "Bonjour Claire"),
    ("Hoi Tom,\nvolgende week.\n\nVan: Tom Field\nVerzonden: maandag 24 november 2026\nAan: Jan\nOnderwerp: Platform\n\nBeste Jan", "text", "volgende week.", "Beste Jan"),
    ("Thanks, not now.\n\n-----Original Message-----\nFrom: Tom Field\nSent: Monday\nTo: Ola\nSubject: x\n\nHello", "text", "Thanks, not now.", "Hello"),
    ("Danke, später.\n\n-----Ursprüngliche Nachricht-----\nVon: Tom Field\nGesendet: Montag\nAn: Klaus\nBetreff: x\n\nGuten Tag", "text", "Danke, später.", "Guten Tag"),
    ("Adding my colleague Piotr.\n\nBegin forwarded message:\n\nFrom: Tom Field\nSubject: Claims\nDate: 24 November 2026\nTo: Ola\n\nHello Ola", "text",
     "Adding my colleague Piotr.", "Hello Ola"),
    ("Yes please. Ola On Mon, 23 Nov 2026 at 09:12 Tom Field <tom@sender.example> wrote: > Hello Ola, would an extra team help?", "text", "Yes please. Ola", "extra team"),
    (OUTLOOK_HTML, "html", "Hi Tom,\nTuesday at 10:00 works.\nOla", "Hello Ola"),
    (OWA_HTML, "html", "Hi Tom, call me on Monday.", "Hello Ola"),
])
def test_quoted_history_is_cut_in_every_common_form(raw, fmt, own, gone):
    out = clean(raw, fmt)
    assert own in out and gone not in out
    assert not any(label in out for label in ("From:", "Von:", "Od:", "Sent:", "Gesendet:", "Wysłano:", "Original Message", "Fra:"))


def test_mobile_signature_stays_and_never_starts_a_new_email():
    assert clean("Sounds good, Tuesday 10:00.\n\nSent from my iPhone", "text") == "Sounds good, Tuesday 10:00.\n\nSent from my iPhone"
    assert clean("Gerne.\n\nVon meinem iPhone gesendet\n\nAm 24.11.2026 um 10:12 schrieb Tom Field <tom@sender.example>:\n> Hallo", "text") == \
        "Gerne.\n\nVon meinem iPhone gesendet"
    assert clean("Hei Tom, send mer info.\n\nSendt fra min iPhone\n\nFra: Tom Field\nSendt: 24. november 2026\nTil: Ola\nEmne: x", "text") == \
        "Hei Tom, send mer info.\n\nSendt fra min iPhone"


def test_an_email_that_only_quotes_keeps_a_readable_fallback():
    assert clean("> Hello Ola,\n> would an extra team help with the claims platform?\n> Tom", "text") == \
        "Hello Ola,\nwould an extra team help with the claims platform?\nTom"
    fwd = clean("From: Tom Field <tom@sender.example>\nSent: Monday\nTo: Ola\nSubject: Claims platform\n\nHello Ola, would an extra team help?", "text")
    assert fwd.startswith("From: Tom Field") and "would an extra team help?" in fwd        # nothing of their own: the whole email, never nothing
    # someone's own words that merely start with "From:" are not a mail header
    assert clean("From: next month we can start. Thanks!\nOla", "text") == "From: next month we can start. Thanks!\nOla"
    # a signature with a top border is not a quote
    assert clean('<div>Thanks, interested.</div><div style="border-top:1px solid #ccc">Ola Nordmann | Head of Claims IT</div>', "html") == \
        "Thanks, interested.\nOla Nordmann | Head of Claims IT"


def test_the_html_original_is_kept_beside_the_words():
    b = extract({"text": OUTLOOK_EN, "html": OWA_HTML, "messagePreview": "Hi"})
    assert (b.text, b.fmt, b.raw, b.preview) == ("Hi Tom, call me on Monday.", "html", OWA_HTML, False)


# ------------------------------------------------------------------------------------------------ the thread, each email on its own
def _act(conn, aid, typ, at, **over):
    a = {"_id": aid, "type": typ, "createdAt": at.isoformat(), "leadEmail": "ola@fjordsure.example", "campaignId": CAMPAIGN, "campaignName": CAMPAIGN_NAME,
         "leadId": "lea_Ola000000001", "contactId": "ctc_Ola000000001", "teamId": "tea_Codest00001"}
    a.update(over)
    apply_lemlist_activity(conn, {k: v for k, v in a.items() if v is not None})


SENDER = {"sendUserId": "usr_Tom000000001", "sendUserEmail": "tom@sender.example", "sendUserName": "Tom Field", "sendUserMailboxId": "usm_Tom000000001"}


def _conversation(conn):
    """Two campaign emails (the second without words), opens and a click, and an Outlook reply quoting our first email."""
    _act(conn, SENT_ID, "emailsSent", T0, sequenceStep=0, subject="Claims platform", html=OUR_EMAIL_1, **SENDER)
    _act(conn, "act_Open1", "emailsOpened", T0 + timedelta(hours=3), sequenceStep=0)
    _act(conn, "act_Open2", "emailsOpened", T0 + timedelta(days=1), sequenceStep=0)
    _act(conn, SENT2_ID, "emailsSent", T0 + timedelta(days=3), sequenceStep=1, **SENDER)
    _act(conn, "act_Open3", "emailsOpened", T0 + timedelta(days=3, hours=6), sequenceStep=1)
    _act(conn, "act_Click1", "emailsClicked", T0 + timedelta(days=3, hours=6, minutes=5), sequenceStep=1)
    _act(conn, "act_Open4", "emailsOpened", T0 + timedelta(days=4))                           # no step: the last email sent before it
    _act(conn, REPLY_ID, "emailsReplied", T0 + timedelta(days=5), text=OUTLOOK_EN, subject="RE: Claims platform", **SENDER)
    conn.commit()


def test_the_thread_is_separate_emails_in_order_with_step_sender_and_opens(world, conn):
    _conversation(conn)
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    first, second, reply = t["items"]
    assert [i["kind"] for i in t["items"]] == ["sent", "sent", "reply"] and [i["id"] for i in t["items"]] == [SENT_ID, SENT2_ID, REPLY_ID]
    assert first["step"] == 1 and second["step"] == 2 and first["campaign"] == CAMPAIGN_NAME and first["via"] == "campaign"
    assert first["from"] == {"name": "Tom Field", "email": "tom@sender.example"} and first["to"] == {"name": "Ola Nordmann"}
    assert first["text"] == "Hello Ola,\nNordic insurers are moving claims to new platforms. Would an extra Python team help yours?\nTom"
    assert second["text"] is None                                                             # shown as a slim "Email 2 of the sequence" line
    assert first["engagement"]["opens"] == 2 and first["engagement"]["clicks"] == 0
    assert (second["engagement"]["opens"], second["engagement"]["clicks"]) == (2, 1)
    assert second["engagement"]["last_open"].startswith((T0 + timedelta(days=4)).date().isoformat())
    assert reply["newest"] is True and first["newest"] is False and reply["from"]["name"] == "Ola Nordmann"
    assert reply["text"] == "Hi Tom,\nyes, we are looking for two Python engineers from January.\nCan we talk on Tuesday?\n\nBest regards\nOla Nordmann"
    s = t["summary"]
    assert (s["sent"], s["opens"], s["clicks"], s["replies"], s["bounced"]) == (2, 4, 1, 1, False)
    assert s["first_sent"].startswith("2026-11-21") and s["last_reply"].startswith("2026-11-26")
    assert t["reply"]["activity_id"] == REPLY_ID


def test_a_step_is_never_invented(world, conn):
    _act(conn, SENT_ID, "emailsSent", T0, subject="Claims platform", **SENDER)                # Lemlist named no step
    conn.commit()
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert t["items"][0]["step"] is None and t["summary"]["sent"] == 1


# ------------------------------------------------------------------------------------------------ the Conversations timeline
def _lemlist_rows(conn):
    return conn.execute("select * from contact_interactions where channel='lemlist' order by occurred_at, external_id").fetchall()


def test_every_email_is_on_the_timeline_once(world, conn):
    _conversation(conn)
    tom = str(conn.execute("insert into users(email, display_name, role) values ('tom@sender.example','Tom Field','bdm') returning id").fetchone()["id"])
    conn.commit()
    out = record_timeline(conn)
    assert out["new"] == 3
    rows = _lemlist_rows(conn)
    assert [(r["meta"]["type"], r["direction"], r["visibility"]) for r in rows] == [("emailsSent", "out", "team"), ("emailsSent", "out", "team"), ("emailsReplied", "in", "team")]
    assert all(str(r["user_id"]) == tom for r in rows)                                       # the colleague whose mailbox sent it
    assert rows[2]["gist"].startswith("Hi Tom,") and "From:" not in rows[2]["gist"] and rows[0]["title"] == "Claims platform"
    assert str(rows[0]["organization_id"]) == world["oid"] and rows[0]["meta"]["step"] == 1
    # again, and through the page: nothing new
    assert record_timeline(conn) == {"new": 0, "updated": 0, "no_owner": 0, "skipped": 0}
    world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm"))
    assert len(_lemlist_rows(conn)) == 3
    # the words change (a better cleaning, the inbox): the same row is refreshed, not added
    conn.execute("update lemlist_activities set body_text='Hi Tom, Tuesday works.', timeline_at=NULL where id=%s", (REPLY_ID,))
    conn.commit()
    assert record_timeline(conn)["updated"] == 1 and len(_lemlist_rows(conn)) == 3 and _lemlist_rows(conn)[2]["gist"] == "Hi Tom, Tuesday works."


def test_no_sender_among_us_falls_back_to_an_administrator(world, conn):
    _conversation(conn)
    record_timeline(conn)
    admin = conn.execute("select id from users where email='admin@x'").fetchone()["id"]
    assert {r["user_id"] for r in _lemlist_rows(conn)} == {admin}


def test_never_twice_with_the_personal_lemlist_sync(world, conn, monkeypatch):
    """Round 8's personal sync writes the same campaign events under its own key. Whichever runs first, each email is one row."""
    _conversation(conn)
    bdm = conn.execute("select id from users where email='bdm@x'").fetchone()["id"]
    # round 8 already wrote the reply (its key: campaign + lead + event + second)
    when = T0 + timedelta(days=5)
    store.upsert_interaction(conn, user_id=str(bdm), channel="lemlist", external_id=lemlist_sync.event_key(CAMPAIGN, "lea_Ola000000001", "emailsReplied", when),
                             occurred_at=when, visibility="team", person_id=world["pid"], direction="in", title=CAMPAIGN_NAME, gist="Replied",
                             meta={"campaign_id": CAMPAIGN, "type": "emailsReplied"})
    conn.commit()
    out = record_timeline(conn)
    assert (out["new"], out["updated"]) == (2, 1)
    replied = [r for r in _lemlist_rows(conn) if r["meta"]["type"] == "emailsReplied"]
    assert len(replied) == 1 and replied[0]["user_id"] == bdm and replied[0]["gist"].startswith("Hi Tom,") and replied[0]["meta"]["activity_id"] == REPLY_ID
    # round 8 runs again over the same campaign: the emails are not added twice, the completed row keeps its words; opens are round 8's own rows
    connect_lemlist(conn, str(bdm))
    feed = [{"_id": a["id"], "type": a["type"], "leadEmail": "ola@fjordsure.example", "leadId": "lea_Ola000000001", "campaignId": CAMPAIGN,
             "campaignName": CAMPAIGN_NAME, "createdAt": a["happened_at"].isoformat()}
            for a in conn.execute("select * from lemlist_activities order by happened_at desc").fetchall()]

    class Personal:
        def campaigns(self):
            return [{"_id": CAMPAIGN, "name": CAMPAIGN_NAME}]

        def activities(self, *, offset=0, limit=100, campaign_id=None):
            return feed[offset:offset + limit]
    monkeypatch.setattr(lemlist_sync, "make_client", lambda key: Personal())
    lemlist_sync.run(conn, job(str(bdm)), settings=VAULT_SETTINGS)
    emails = [r for r in _lemlist_rows(conn) if r["meta"]["type"] in ("emailsSent", "emailsReplied")]
    assert len(emails) == 3 and next(r for r in emails if r["meta"]["type"] == "emailsReplied")["gist"].startswith("Hi Tom,")
    assert sum(1 for r in _lemlist_rows(conn) if r["meta"]["type"] in ("emailsOpened", "emailsClicked")) == 5
    # and the shared writer after it: still one row per email
    conn.execute("update lemlist_activities set timeline_at=NULL")
    conn.commit()
    record_timeline(conn)
    assert len([r for r in _lemlist_rows(conn) if r["meta"]["type"] in ("emailsSent", "emailsReplied")]) == 3


def test_an_answer_from_here_is_on_the_timeline_once_and_not_again_when_read_back(world, conn):
    _conversation(conn)
    r = world["on"].post(f"/api/people/{world['pid']}/email-thread/reply", headers=world["h"]("bdm"),
                         json={"activity_id": REPLY_ID, "text": "Hi Ola,\nTuesday at 10:00 works.\n\nTom", "key": "draft-r58-0001"})
    assert r.status_code == 200, r.text
    answers = [x for x in _lemlist_rows(conn) if x["meta"]["type"] == "answer"]
    assert len(answers) == 1 and answers[0]["direction"] == "out" and answers[0]["gist"].startswith("Hi Ola,")
    assert answers[0]["user_id"] == conn.execute("select id from users where email='bdm@x'").fetchone()["id"]
    # Lemlist's inbox later shows our answer as a sent email: one row still, and one card in the thread
    _act(conn, "act_OurAnswerBack1", "emailsSent", datetime.now(timezone.utc), campaignId=None, campaignName=None, text="Hi Ola,\nTuesday at 10:00 works.\n\nTom", **SENDER)
    conn.commit()
    record_timeline(conn)
    assert len([x for x in _lemlist_rows(conn) if x["direction"] == "out" and "Tuesday at 10:00" in (x["gist"] or "")]) == 1
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert [i["kind"] for i in t["items"]][-1] == "answer" and sum(1 for i in t["items"] if "Tuesday at 10:00" in (i["text"] or "")) == 1


# ------------------------------------------------------------------------------------------------ cleaning the stored replies again
def _as_stored_in_round_9(conn):
    """A reply stored before this round: words with our campaign email under an Outlook header, no original kept."""
    _act(conn, REPLY_ID, "emailsReplied", T0 + timedelta(days=5), subject="RE: Claims platform", **SENDER)
    conn.execute("update lemlist_activities set body_text=%s, body_source='activity', body_raw=NULL, body_raw_format=NULL, body_clean_version=NULL, "
                 "body_text_original=NULL, timeline_at=NULL where id=%s", (OUTLOOK_EN, REPLY_ID))
    conn.commit()


def test_the_backfill_cleans_again_is_idempotent_and_never_deletes(world, conn):
    _as_stored_in_round_9(conn)
    before = conn.execute("select count(*) n from lemlist_activities").fetchone()["n"]
    out = rp.reclean(conn)                                                       # local: no Lemlist call
    assert (out["checked"], out["changed"]) == (1, 1)
    r = conn.execute("select * from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()
    assert r["body_text"].endswith("Best regards\nOla Nordmann") and "From:" not in r["body_text"]
    assert r["body_text_original"] == OUTLOOK_EN and r["body_clean_version"] == rp.CLEAN_VERSION   # the old words are kept
    assert rp.reclean(conn) == {"checked": 0, "changed": 0}                      # a second run changes nothing
    # Today shows only their own words
    rep = next(a for a in world["on"].get("/api/today/brief", headers=world["h"]("bdm")).json()["actions"] if a["kind"] == "reply")["reply"]
    assert rep["text"] == r["body_text"]
    # with Lemlist: the conversation is read again once, and the email as Lemlist gave it is kept
    fake = FakeLemlist(thread={"ctc_Ola000000001": [{"_id": REPLY_ID, "type": "emailsReplied", "leadEmail": "ola@fjordsure.example", "message": OWA_HTML,
                                                     "contactId": "ctc_Ola000000001", "sequenceStep": 0}]})
    out = rp.reclean(conn, fake)
    assert out["threads"] == 1 and fake.calls == [("inbox", "ctc_Ola000000001")]
    r = conn.execute("select * from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()
    assert r["body_raw"] == OWA_HTML and r["body_raw_format"] == "html" and r["raw_refetched_at"] is not None and r["body_text"] == "Hi Tom, call me on Monday."
    assert r["body_text_original"] == OUTLOOK_EN
    fake.calls.clear()
    assert rp.reclean(conn, fake) == {"checked": 0, "changed": 0, "threads": 0} and fake.calls == []      # once only
    assert conn.execute("select count(*) n from lemlist_activities").fetchone()["n"] == before
    # a later cleaning version re-derives from the kept original, and again changes nothing on a second run
    conn.execute("update lemlist_activities set body_clean_version=1")
    conn.commit()
    assert rp.reclean(conn)["changed"] == 0 and conn.execute("select body_text from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_text"] == \
        "Hi Tom, call me on Monday."


def test_opening_the_page_cleans_that_persons_replies_at_once(world, conn):
    _as_stored_in_round_9(conn)
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert "From:" not in t["items"][0]["text"] and t["items"][0]["text"].startswith("Hi Tom,")
    assert len(_lemlist_rows(conn)) == 1


def test_the_hourly_job_cleans_again_and_fills_the_timeline(world, conn, monkeypatch):
    from ri.jobs.handlers import build_handlers
    from ri.outbound import lemlist as lemlist_mod
    _as_stored_in_round_9(conn)

    class Feed(FakeLemlist):
        def activities(self, *, offset=0, limit=100, campaign_id=None):
            return []
    fake = Feed(thread={"ctc_Ola000000001": [{"_id": REPLY_ID, "type": "emailsReplied", "leadEmail": "ola@fjordsure.example", "message": OUTLOOK_HTML}]})
    monkeypatch.setattr(lemlist_mod, "LemlistClient", lambda key: fake)
    monkeypatch.setenv("LEMLIST_API_KEY", "test-key")
    out = build_handlers(connector_factory=lambda sid: None)["lemlist_sync"](conn, {"payload": {}})
    assert out["reclean"]["changed"] == 1 and out["reclean"]["threads"] == 1 and out["timeline"]["new"] == 1
    assert conn.execute("select body_text from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_text"] == "Hi Tom,\nTuesday at 10:00 works.\nOla"
    again = build_handlers(connector_factory=lambda sid: None)["lemlist_sync"](conn, {"payload": {}})
    assert again["reclean"] == {"checked": 0, "changed": 0, "threads": 0} and again["timeline"]["new"] == 0 and len(_lemlist_rows(conn)) == 1


def test_erased_or_suppressed_people_get_no_timeline_row(world, conn):
    _conversation(conn)
    conn.execute("update people set suppressed=true where id=%s", (world["pid"],))
    conn.commit()
    assert record_timeline(conn)["new"] == 0 and _lemlist_rows(conn) == []
    assert reply_activity()["_id"] == REPLY_ID                                   # (the round 9 fixtures are the ones reused here)


def test_today_shows_only_their_own_words_even_before_the_hourly_run(world, conn):
    _as_stored_in_round_9(conn)
    rep = next(a for a in world["on"].get("/api/today/brief", headers=world["h"]("bdm")).json()["actions"] if a["kind"] == "reply")["reply"]
    assert rep["text"].startswith("Hi Tom,") and rep["text"].endswith("Ola Nordmann") and "From:" not in rep["text"]
    # shown clean, stored as it was until the hourly run (or opening the page) cleans it for good
    assert conn.execute("select body_text from lemlist_activities where id=%s", (REPLY_ID,)).fetchone()["body_text"] == OUTLOOK_EN
