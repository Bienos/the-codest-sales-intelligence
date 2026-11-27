"""Round 11: the signature (footer) under a reply sent from the app. Lemlist's POST /inbox/email adds none, so each person keeps one here; it
is suggested from the sign-off of our own campaign emails, appended once (escaped), shown in the thread, and sending stays once per draft.
Lemlist is a FAKE (no network); every name, address and email is invented."""
from datetime import timedelta
from pathlib import Path

from test_r57_lemlist_replies import (  # noqa: F401 - world is a fixture
    NOW,
    REPLY_ID,
    _send,
    _store_reply,
    reply_activity,
    world,
)

from ri.outbound import replies as rp
from ri.outbound import signature as sig
from ri.outbound.lemlist import LemlistError
from ri.people import apply_lemlist_activity

TEXT = "Hi Ola,\nTuesday at 10:00 works.\n\nTom"
OURS_1 = ("<div>Hello Ola,</div><div>Nordic insurers are moving claims to new platforms.</div><div>Would a short call help?</div>"
          "<div><br></div><div>Best regards,</div><div>Tom Field</div><div>Head of Sales, The Codest</div><div>thecodest.co</div>")
OURS_2 = "<div>Hello again Ola,</div><div>one more note on the claims platform.</div><div>Tom</div>"


def _put(world, text, who="bdm"):
    return world["on"].put("/api/me/email-signature", headers=world["h"](who), json={"text": text})


def _get(world, who="bdm", person=None):
    return world["on"].get("/api/me/email-signature" + (f"?person={person}" if person else ""), headers=world["h"](who)).json()


def _campaign_email(conn, aid, html, step, days_ago, **over):
    apply_lemlist_activity(conn, reply_activity(_id=aid, type="emailsSent", text=None, html=html, sequenceStep=step, subject="Claims platform",
                                                createdAt=(NOW - timedelta(days=days_ago)).isoformat(), **over))
    conn.commit()


# ------------------------------------------------------------------------------------------------ what Lemlist does
def test_lemlist_adds_no_signature_so_the_app_does():
    assert sig.LEMLIST_ADDS_SIGNATURE is False and sig.MAX_SIGNATURE == 600


# ------------------------------------------------------------------------------------------------ the suggestion
def test_sign_off_is_read_from_our_own_campaign_emails_only_when_reliable():
    one = "Hello Ola,\nwould a call help?\n\nBest regards,\nTom Field\nThe Codest"
    assert sig.detect([one], ["Tom Field"]) == "Tom Field\nThe Codest"
    # the same block at the end of two emails (Lemlist's {{signature}}) wins, without the closing line
    a = "Hello Ola,\nfirst note.\nKind regards\nTom Field\nHead of Sales\n+48 600 000 000"
    b = "Hello Kari,\nsecond note.\nThanks,\nTom Field\nHead of Sales\n+48 600 000 000"
    assert sig.detect([a, b], ["Tom Field"]) == "Tom Field\nHead of Sales\n+48 600 000 000"
    # German closing, first name only with a company line under it
    assert sig.detect(["Guten Tag,\nkurze Frage.\nMit freundlichen Grüßen\nTom\nThe Codest"], ["Tom Field"]) == "Tom\nThe Codest"
    # not reliable: no closing line, a closing with somebody else's name, a bare first name, or no name known
    assert sig.detect(["Hello Ola,\none last note.\nTom"], ["Tom Field"]) is None
    assert sig.detect(["Hello,\nBest regards,\nAnna Other\nThe Codest"], ["Tom Field"]) is None
    assert sig.detect(["Hello,\nBest,\nTom"], ["Tom Field"]) is None
    assert sig.detect([one], []) is None


def test_suggestion_comes_from_the_mailbox_that_answers_else_name_company_website(world, conn):
    _store_reply(conn)
    assert _get(world) == {"text": "bdm\nThe Codest\nthecodest.co", "source": "default", "from": None, "saved": False, "updated_at": None,
                           "max": 600, "lemlist_adds": False}
    _campaign_email(conn, "act_SentToOla000001", OURS_1, 0, 5)
    _campaign_email(conn, "act_SentToOla000002", OURS_2, 1, 2)
    s = _get(world, person=world["pid"])
    assert (s["text"], s["source"], s["from"], s["saved"]) == ("Tom Field\nHead of Sales, The Codest\nthecodest.co", "campaign", "tom@sender.example", False)
    # a person I may not see gives no context (and no error)
    assert _get(world, person=world["other"])["source"] == "default"
    # the thread carries the same signature, so the preview shows what will be sent
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert t["signature"]["text"] == s["text"] and t["person"]["email"] == "ola@fjordsure.example" and t["reply"]["to"]["email"] == "ola@fjordsure.example"


# ------------------------------------------------------------------------------------------------ keeping it
def test_signature_is_kept_per_person_as_plain_text_and_audited_by_length(world, conn):
    r = _put(world, "  Tom Field <CTO>\r\nThe Codest & co\n\n\n\n+48 600 000 000  ")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Tom Field <CTO>\nThe Codest & co\n\n+48 600 000 000" and r.json()["saved"] is True and r.json()["source"] == "saved"
    assert _get(world)["text"] == "Tom Field <CTO>\nThe Codest & co\n\n+48 600 000 000"
    assert _get(world, who="admin")["saved"] is False                         # each person has their own
    a = conn.execute("select actor, detail from audit_events where operation='email_signature_saved'").fetchone()
    assert a["actor"] == "bdm@x" and a["detail"] == {"chars": len("Tom Field <CTO>\nThe Codest & co\n\n+48 600 000 000")}
    assert _put(world, "x" * 601).status_code == 400
    assert _put(world, "\n".join(["line"] * 11)).status_code == 400
    assert "plain text" in _put(world, 'Tom <img src="https://track.example/p.gif">').json()["detail"]
    # saving again replaces it; an empty one means "no signature" and is not replaced by a suggestion
    assert _put(world, "").json() | {"updated_at": None} == {"text": "", "saved": True, "source": "saved", "from": None, "updated_at": None,
                                                           "max": 600, "lemlist_adds": False}
    assert conn.execute("select count(*) n from user_email_signatures").fetchone()["n"] == 1


# ------------------------------------------------------------------------------------------------ sending with it
def test_signature_is_appended_once_escaped_and_shown_in_the_thread(world, conn):
    _store_reply(conn)
    _put(world, "Tom Field <CTO>\nThe Codest & co")
    r = _send(world, text=TEXT)
    assert r.status_code == 200, r.text
    [kw] = world["fake"].sent
    assert kw["message_html"] == "<p>Hi Ola,<br>Tuesday at 10:00 works.</p><p>Tom</p><p>Tom Field &lt;CTO&gt;<br>The Codest &amp; co</p>"
    assert kw["message_html"].count("Tom Field") == 1 and "<img" not in kw["message_html"]
    row = conn.execute("select body_text, signature_text from lemlist_replies").fetchone()
    assert (row["body_text"], row["signature_text"]) == (TEXT, "Tom Field <CTO>\nThe Codest & co")
    out = r.json()
    assert out["from"] == "tom@sender.example" and out["sent_at"] is not None
    [mine] = [i for i in out["thread"]["items"] if i["kind"] == "answer"]
    assert mine["text"] == TEXT and mine["signature"] == "Tom Field <CTO>\nThe Codest & co" and mine["from"]["email"] == "tom@sender.example"
    audit = conn.execute("select detail from audit_events where operation='lemlist_reply_sent'").fetchone()["detail"]
    assert audit["chars"] == len(TEXT) and audit["signature_chars"] == len("Tom Field <CTO>\nThe Codest & co") and "Codest" not in str(audit)
    # a double click (same draft) sends nothing more, and the signature is not added again
    again = _send(world, text=TEXT)
    assert again.json()["repeat"] is True and len(world["fake"].sent) == 1 and again.json()["from"] == "tom@sender.example"
    # Lemlist sends our answer back (words + signature, no campaign): the thread still shows it once, from our record
    apply_lemlist_activity(conn, reply_activity(_id="act_OurAnswerBack01", type="emailsSent", campaignId=None, campaignName=None,
                                                text=sig.full_text(TEXT, "Tom Field <CTO>\nThe Codest & co"), createdAt=NOW.isoformat()))
    conn.commit()
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    assert [i["kind"] for i in t["items"]].count("answer") == 1 and not any(i["id"] == "act_OurAnswerBack01" for i in t["items"])


def test_never_doubled_when_the_words_already_end_with_it_or_lemlist_adds_one(world, conn, monkeypatch):
    _store_reply(conn)
    _put(world, "Tom Field\nThe Codest")
    r = _send(world, text="Hi Ola,\nTuesday works.\n\nTom Field\nThe Codest", key="draft-0001-sign")
    assert r.status_code == 200, r.text
    assert world["fake"].sent[0]["message_html"] == "<p>Hi Ola,<br>Tuesday works.</p><p>Tom Field<br>The Codest</p>"
    assert conn.execute("select signature_text from lemlist_replies").fetchone()["signature_text"] is None
    # if Lemlist ever adds the sender's own signature, ours is not added on top
    monkeypatch.setattr(sig, "LEMLIST_ADDS_SIGNATURE", True)
    assert _get(world)["lemlist_adds"] is True
    r = _send(world, text="Wednesday works too.", key="draft-0002-sign")
    assert r.status_code == 200 and world["fake"].sent[1]["message_html"] == "<p>Wednesday works too.</p>"


def test_an_empty_signature_sends_the_words_alone(world, conn):
    _store_reply(conn)
    _put(world, "")
    assert _send(world, text=TEXT).status_code == 200
    assert world["fake"].sent[0]["message_html"] == "<p>Hi Ola,<br>Tuesday at 10:00 works.</p><p>Tom</p>"


def test_a_definite_refusal_then_the_same_draft_again_carries_one_signature(world, conn):
    _store_reply(conn)
    _put(world, "Tom Field")
    world["fake"].send_error = LemlistError("Lemlist 429 after retries", 429)
    assert _send(world, text=TEXT).status_code == 409
    world["fake"].send_error = None
    assert _send(world, text=TEXT).json()["status"] == "sent"
    assert [k["message_html"].count("<p>Tom Field</p>") for k in world["fake"].sent] == [1, 1]
    assert conn.execute("select count(*) n from lemlist_replies").fetchone()["n"] == 1


# ------------------------------------------------------------------------------------------------ the thread
def test_campaign_emails_carry_their_step_of_the_sequence(world, conn):
    _store_reply(conn)
    _campaign_email(conn, "act_SentToOla000001", OURS_1, 0, 5)
    _campaign_email(conn, "act_SentToOla000002", OURS_2, 1, 2)
    # someone else in the same campaign already had the third email: the sequence has (at least) three
    apply_lemlist_activity(conn, reply_activity(_id="act_SentToKari0003", type="emailsSent", text="Hello Kari", sequenceStep=2, leadEmail="kari@else.example",
                                                contactId="ctc_Kari00000001", leadId="lea_Kari00000001"))
    conn.commit()
    t = world["on"].get(f"/api/people/{world['pid']}/email-thread", headers=world["h"]("bdm")).json()
    sent = [(i["step"], i["steps"]) for i in t["items"] if i["kind"] == "sent"]
    assert sent == [(1, 3), (2, 3)] and rp._step_totals(conn, []) == {}


def test_migration_0059_is_additive_and_safe_to_run_again(conn):
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0059_email_signatures.sql").read_text()
    conn.execute(sql)
    conn.execute(sql)
    conn.commit()
    cols = {r["column_name"] for r in conn.execute("select column_name from information_schema.columns where table_name='user_email_signatures'")}
    assert cols == {"user_id", "signature", "source", "updated_by", "updated_at"}
    assert conn.execute("select 1 from information_schema.columns where table_name='lemlist_replies' and column_name='signature_text'").fetchone()
