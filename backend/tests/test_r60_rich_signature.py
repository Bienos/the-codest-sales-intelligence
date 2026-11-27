"""Round 12: the email goes out as a normal Gmail email (Arial, 12 px at most), and the signature can keep its formatting (a logo, links, a bold
line, small print) pasted from, or imported from, the person's Gmail. The server cleans every signature to a short list of safe tags.
Lemlist and Google are FAKES (no network); every name, address and link is invented."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_r57_lemlist_replies import (  # noqa: F401 - world is a fixture
    SECRET,
    _send,
    _store_reply,
    world,
)

from ri import vault
from ri.api.app import create_app
from ri.config import Settings
from ri.connections import gmail_api, gmail_sync, store
from ri.db import Database
from ri.outbound import replies as rp
from ri.outbound import signature as sig

FOOTER = '''<div dir="ltr"><div style="font-family:monospace;font-size:14px"><b>Tom Field</b></div>
<div style="font-size:small">Head of Sales &amp; AI Solutions</div>
<div><a href="https://www.google.com/url?q=https://www.linkedin.com/in/tomfield&amp;sa=D" target="_blank">linkedin.com/in/tomfield</a></div>
<div><a href="https://meetings.example/tom">Book a meeting</a></div>
<div><img src="https://lh3.googleusercontent.com/abc123" width="130" height="30" alt="Example Ltd"></div>
<div><b style="font-size:13px">See how the team can help <a href="https://example.com/venture" style="color:#7a00e6">here</a></b></div>
<div style="font-size:9px;color:#888888;line-height:1.3">If you would prefer not to hear from me again, just let me know. Sent on behalf of Example Ltd.</div></div>'''
HOSTILE = ('<script>alert(1)</script><img src="x" onerror="alert(2)"><a href="javascript:alert(3)">click</a><a href="jav&#x09;ascript:alert(4)">tab</a>'
           '<div style="position:fixed;color:red;background:url(javascript:x);font-size:30pt" onclick="alert(5)" class="x" id="y">big</div>'
           '<img src="data:image/png;base64,AAAA" width="100"><img src="http://plain.example/a.png"><img src="https://mail.google.com/mail/u/0?view=att">'
           '<iframe src="https://evil.example"></iframe><form action="https://evil.example"><input name="a"></form><!-- comment --><svg><script>x</script></svg>')


def sizes(html):
    return [int(x) for x in re.findall(r"font-size:(\d+)px", html)]


# ------------------------------------------------------------------------------------------------ the email itself
def test_the_email_is_a_normal_gmail_email_with_nothing_larger_than_12px():
    assert rp.EMAIL_SIZE_PX == 12 and sig.FONT_MAX_PX == 12
    html = rp.email_html(rp.to_html("\n\nHi Ola,\r\nTuesday works <b>fine</b>.\n\n\n\nTom\n\n"))
    assert html == ('<div dir="ltr" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;color:#222222">'
                    "<div>Hi Ola,</div><div>Tuesday works &lt;b&gt;fine&lt;/b&gt;.</div><div><br></div><div>Tom</div></div>")
    assert "<p>" not in html and sizes(html) == [12]


@pytest.mark.parametrize("css,expect", [("14px", "12px"), ("30pt", "12px"), ("9pt", "12px"), ("8px", "8px"), ("6px", "7px"), ("small", "12px"), ("x-small", "10px"),
                                        ("large", "12px"), ("11.5px", "12px"), ("1.5em", None), ("120%", None), ("calc(2px + 3px)", None)])
def test_font_sizes_are_capped_at_12px(css, expect):
    out = sig.rich_from_html(f'<div style="font-size:{css}">Tom</div>')["html"]
    assert (f"font-size:{expect}" in out) if expect else ("font-size" not in out)


def test_only_the_listed_style_properties_survive_a_declaration_list():
    out = sig.rich_from_html('<div style="font-size:12px;position:fixed;margin:-50px;color:#123456;font-family:Impact;background:red">Tom</div>')["html"]
    assert out == '<div style="font-size:12px;color:#123456">Tom</div>'


# ------------------------------------------------------------------------------------------------ the cleaner
def test_a_gmail_footer_keeps_its_logo_links_bold_and_small_print():
    r = sig.rich_from_html(FOOTER)
    h = r["html"]
    assert "<b>Tom Field</b>" in h and "font-family" not in h and "monospace" not in h        # the font is Arial from the email, not whatever Gmail had
    assert '<a href="https://www.linkedin.com/in/tomfield">linkedin.com/in/tomfield</a>' in h  # the redirect Gmail wraps around links is removed
    assert '<a href="https://meetings.example/tom">Book a meeting</a>' in h
    assert '<img src="https://lh3.googleusercontent.com/abc123" alt="Example Ltd" width="130" height="30"' in h
    assert '<a href="https://example.com/venture" style="color:#7a00e6">here</a>' in h
    assert 'style="font-size:9px;color:#888888;line-height:1.3"' in h                            # the small grey print stays small
    assert max(sizes(h)) <= 12 and r["dropped_images"] == 0
    assert r["text"].split("\n")[:2] == ["Tom Field", "Head of Sales & AI Solutions"]
    assert "Book a meeting (https://meetings.example/tom)" in r["text"] and "linkedin.com/in/tomfield" in r["text"] and "<" not in r["text"]
    assert r["text"].endswith("Sent on behalf of Example Ltd.")


def test_everything_unsafe_is_dropped_and_what_is_left_is_built_from_escaped_parts():
    r = sig.rich_from_html(HOSTILE)
    h = r["html"].lower()
    for bad in ("<script", "onerror", "onclick", "javascript", "<iframe", "<form", "<input", "<svg", "data:", "http://plain", "mail.google.com", "position", "url(",
                "class=", "id=", "comment", "evil.example"):
        assert bad not in h, bad
    assert "big" in r["text"] and "click" in r["text"] and "tab" in r["text"]                  # the words of dropped links and elements stay
    assert r["dropped_images"] == 4                                                             # x, data:, http:// and the Google-login picture
    assert sizes(h) == [12] and "color:red" in h


def test_links_and_pictures_follow_strict_rules():
    keep = sig.rich_from_html('<a href="mailto:tom@sender.example">mail me</a> <a href="tel:+48600000000">call</a> <a href=" HTTPS://x.example/a b ">w</a>')["html"]
    assert 'href="mailto:tom@sender.example"' in keep and 'href="tel:+48600000000"' in keep and 'href="HTTPS://x.example/ab"' in keep
    assert sig.rich_from_html('<a href="ftp://x.example">f</a><a href="//x.example">g</a><a href="/relative">h</a>')["html"].count("<a ") == 0
    # tracking pixels are not a loss; a picture without a size gets a safe maximum
    p = sig.rich_from_html('<img src="https://track.example/p.gif" width="1" height="1">Tom<img src="https://logo.example/l.png" alt="a&quot;b">')
    assert p["dropped_images"] == 0 and "track.example" not in p["html"] and 'max-width:300px' in p["html"] and 'alt="a&quot;b"' in p["html"]
    # table cells carry the size themselves (not every mail program passes it down)
    t = sig.rich_from_html("<table><tr><td>Tom</td><td style='font-size:10px'>x</td></tr></table>")["html"]
    assert t.count("font-size:12px") == 1 and "font-size:10px" in t and "Arial" in t


def test_broken_markup_is_closed_and_empty_or_oversize_input_is_handled():
    assert sig.rich_from_html("<div><b>Tom<i>Field")["html"] == "<div><b>Tom<i>Field</i></b></div>"
    assert sig.rich_from_html("Tom</b></div></span>")["html"] == "Tom"
    assert sig.rich_from_html("  ") == {"html": "", "text": "", "dropped_images": 0}
    assert sig.rich_from_html("<div><br></div><img src='https://x.example/a.png'>")["text"] == ""      # no words, so no signature
    with pytest.raises(sig.SignatureError, match="too long"):
        sig.rich_from_html("<div>" + "word " * 500 + "</div>")
    with pytest.raises(sig.SignatureError, match="too many lines"):
        sig.rich_from_html("".join(f"<div>line {i}</div>" for i in range(31)))
    # few words but a mountain of markup: refused as too large rather than cut in the middle of a tag
    with pytest.raises(sig.SignatureError, match="too large"):
        sig.rich_from_html("<div>" + "<span style='color:#888888'>a</span>" * 400 + "</div>")
    assert len(sig.rich_from_html("<div>" + "<span style='color:#888888'>a</span>" * 100 + "</div>")["html"]) < sig.MAX_HTML


# ------------------------------------------------------------------------------------------------ keeping and previewing it
def _put(world, **body):
    return world["on"].put("/api/me/email-signature", headers=world["h"]("bdm"), json=body)


def test_a_pasted_signature_is_cleaned_kept_with_its_formatting_and_audited_by_length(world, conn):
    r = _put(world, text="", html=FOOTER)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["saved"] is True and j["source"] == "pasted" and j["html"] and "linkedin.com/in/tomfield" in j["html"] and j["text"].startswith("Tom Field")
    assert max(sizes(j["html"])) <= 12 and "font_max_px" in j and j["max"] == 2000
    got = world["on"].get("/api/me/email-signature", headers=world["h"]("bdm")).json()
    assert got["html"] == j["html"] and got["text"] == j["text"]
    assert world["on"].get("/api/me/email-signature", headers=world["h"]("admin")).json()["html"] is None          # each person has their own
    a = conn.execute("select actor, detail from audit_events where operation='email_signature_saved'").fetchone()
    assert a["actor"] == "bdm@x" and a["detail"] == {"chars": len(j["text"]), "formatted": True} and "Tom Field" not in str(a["detail"])
    # typing plain text afterwards replaces the formatted version (the formatting is not silently kept under new words)
    t = _put(world, text="Tom Field\nThe Codest").json()
    assert t["html"] is None and t["text"] == "Tom Field\nThe Codest" and t["source"] == "saved"
    assert conn.execute("select signature_html from user_email_signatures").fetchone()["signature_html"] is None
    # hostile paste: cleaned, never refused for being hostile
    assert "<script" not in _put(world, text="", html=HOSTILE + "<div>Tom</div>").json()["html"].lower()
    # too long is refused in plain words, and nothing changes
    before = world["on"].get("/api/me/email-signature", headers=world["h"]("bdm")).json()
    bad = _put(world, text="", html="<div>" + "word " * 500 + "</div>")
    assert bad.status_code == 400 and "too long" in bad.json()["detail"]
    assert world["on"].get("/api/me/email-signature", headers=world["h"]("bdm")).json() == before


def test_preview_shows_the_cleaned_signature_without_saving_it_and_needs_sign_in(world, conn):
    r = world["on"].post("/api/me/email-signature/preview", headers=world["h"]("bdm"), json={"html": FOOTER + HOSTILE})
    assert r.status_code == 200
    j = r.json()
    assert "linkedin.com/in/tomfield" in j["html"] and "<script" not in j["html"] and j["dropped_images"] == 4 and j["text"].startswith("Tom Field")
    assert conn.execute("select count(*) n from user_email_signatures").fetchone()["n"] == 0
    assert world["on"].post("/api/me/email-signature/preview", json={"html": FOOTER}).status_code == 401
    assert world["on"].post("/api/me/email-signature/preview", headers=world["h"]("bdm"), json={"html": "word " * 600}).status_code == 400


# ------------------------------------------------------------------------------------------------ sending with it
def test_a_formatted_signature_goes_out_once_under_the_words_inside_the_12px_block(world, conn):
    _store_reply(conn)
    j = _put(world, text="", html=FOOTER).json()
    r = _send(world)
    assert r.status_code == 200, r.text
    [kw] = world["fake"].sent
    html = kw["message_html"]
    assert html == rp.email_html(rp.to_html("Hi Ola,\nTuesday at 10:00 works. <b>Speak then</b>.\n\nTom") + "<div><br></div>" + j["html"])
    assert html.startswith('<div dir="ltr" style="font-family:Arial,Helvetica,sans-serif;font-size:12px') and html.count('<a href="https://www.linkedin.com/in/tomfield"') == 1
    assert max(sizes(html)) <= 12 and "<script" not in html and 'src="https://lh3.googleusercontent.com/abc123"' in html
    row = conn.execute("select body_text, signature_text from lemlist_replies").fetchone()
    assert row["signature_text"] == j["text"] and "Tom Field" not in row["body_text"]
    audit = conn.execute("select detail from audit_events where operation='lemlist_reply_sent'").fetchone()["detail"]
    assert audit["signature_chars"] == len(j["text"]) and "tomfield" not in str(audit)
    # a second draft carries one signature again; words that already end with it get no second one
    r2 = _send(world, key="draft-0002-aaaa", text="Wednesday works.\n\n" + j["text"])
    assert r2.status_code == 200
    assert "<a href" not in world["fake"].sent[1]["message_html"] and "<div><br></div>" + j["html"] not in world["fake"].sent[1]["message_html"]


def test_without_a_formatted_version_the_plain_text_signature_is_set_in_the_same_block(world, conn):
    _store_reply(conn)
    _put(world, text="Tom Field\nThe Codest")
    assert _send(world).status_code == 200
    html = world["fake"].sent[0]["message_html"]
    assert html.endswith("<div><br></div><div>Tom Field</div><div>The Codest</div></div>") and sizes(html) == [12]


# ------------------------------------------------------------------------------------------------ from the person's own Gmail
KEY = vault.new_key()
CID = "client-id.apps.googleusercontent.com"


class FakeGmail:
    answer = ("tom@sender.example", FOOTER)
    error = None
    calls = 0

    def __init__(self, token, cid, secret):
        assert token == "refresh-token-1" and cid == CID
        FakeGmail.calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def send_as_signature(self):
        if FakeGmail.error:
            raise FakeGmail.error
        return FakeGmail.answer


@pytest.fixture()
def gworld(world, conn, db_url, monkeypatch):
    FakeGmail.answer, FakeGmail.error, FakeGmail.calls = ("tom@sender.example", FOOTER), None, 0
    monkeypatch.setattr(gmail_api, "GmailClient", FakeGmail)
    db = Database(db_url, max_size=3)
    base = dict(env="development", auth_mode="dev", dev_auth_secret=SECRET, outbound_enabled=True, lemlist_api_key="k", google_client_id=CID, google_client_secret="s" * 20,
                vault_key=KEY)
    mk = lambda **over: TestClient(create_app(Settings(**{**base, **over}), db=db))  # noqa: E731
    uid = str(conn.execute("select id from users where email='bdm@x'").fetchone()["id"])
    yield {**world, "g": mk(), "nokey": mk(vault_key=None), "uid": uid}
    db.close()


def _connect(conn, uid):
    store.connect(conn, KEY, uid, "gmail", secret="refresh-token-1", label="tom@sender.example")
    conn.commit()


def _import(w, who="bdm", client="g"):
    return w[client].post("/api/me/email-signature/import-gmail", headers=w["h"](who))


def test_import_needs_gmail_set_up_and_connected_and_says_so_plainly(gworld, conn):
    assert _import(gworld, client="on").status_code == 409 and "not set up on the server" in _import(gworld, client="on").json()["detail"]
    r = _import(gworld)
    assert r.status_code == 409 and "Connect your work Gmail first" in r.json()["detail"] and FakeGmail.calls == 0
    assert gworld["g"].post("/api/me/email-signature/import-gmail").status_code == 401


def test_import_takes_the_signature_of_my_gmail_with_its_formatting_and_audits_length_only(gworld, conn):
    _connect(conn, gworld["uid"])
    r = _import(gworld)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["imported"] is True and j["address"] == "tom@sender.example" and j["source"] == "gmail" and j["saved"] is True
    assert "linkedin.com/in/tomfield" in j["html"] and max(sizes(j["html"])) <= 12 and j["text"].startswith("Tom Field")
    a = conn.execute("select actor, detail from audit_events where operation='email_signature_imported'").fetchone()
    assert a["actor"] == "bdm@x" and a["detail"]["formatted"] is True and "Tom Field" not in str(a["detail"])
    assert gworld["g"].get("/api/me/email-signature", headers=gworld["h"]("bdm")).json()["source"] == "gmail"
    # the button replaces what was there; another person's signature is untouched
    assert gworld["g"].get("/api/me/email-signature", headers=gworld["h"]("admin")).json()["saved"] is False


def test_import_reports_a_refusal_an_empty_signature_and_a_too_long_one_without_changing_anything(gworld, conn):
    _connect(conn, gworld["uid"])
    gworld["g"].put("/api/me/email-signature", headers=gworld["h"]("bdm"), json={"text": "Kept by hand"})
    FakeGmail.error = gmail_api.GmailConfigError("Google did not allow reading this mailbox. Connect Gmail again and allow read access.", "insufficientPermissions")
    r = _import(gworld)
    assert r.status_code == 409 and "Google did not allow" in r.json()["detail"]
    FakeGmail.error = gmail_api.GmailUnavailable("down")
    assert _import(gworld).status_code == 503
    FakeGmail.error = None
    FakeGmail.answer = ("tom@sender.example", "")
    j = _import(gworld).json()
    assert j["imported"] is False and j["empty"] is True and j["text"] == "Kept by hand"
    FakeGmail.answer = ("tom@sender.example", "<div>" + "word " * 500 + "</div>")
    j = _import(gworld).json()
    assert j["imported"] is False and "too long" in j["error"] and j["text"] == "Kept by hand"


def test_connecting_gmail_takes_the_signature_once_and_never_replaces_a_kept_one(gworld, conn):
    uid = gworld["uid"]

    class Gm:
        def send_as_signature(self):
            return FakeGmail.answer
    gmail_sync._signature_once(conn, uid, "bdm@x", Gm())
    row = conn.execute("select signature, signature_html, source from user_email_signatures where user_id=%s", (uid,)).fetchone()
    assert row["source"] == "gmail" and row["signature_html"] and row["signature"].startswith("Tom Field")
    # a later run, or a signature kept by hand (even an empty one on purpose), is never replaced
    FakeGmail.answer = ("tom@sender.example", "<div>Someone Else</div>")
    gmail_sync._signature_once(conn, uid, "bdm@x", Gm())
    assert conn.execute("select signature from user_email_signatures where user_id=%s", (uid,)).fetchone()["signature"].startswith("Tom Field")
    conn.execute("update user_email_signatures set signature='', signature_html=NULL, source='saved' where user_id=%s", (uid,))
    conn.commit()
    gmail_sync._signature_once(conn, uid, "bdm@x", Gm())
    assert conn.execute("select signature from user_email_signatures where user_id=%s", (uid,)).fetchone()["signature"] == ""
    # Google refusing never fails the sync
    conn.execute("delete from user_email_signatures where user_id=%s", (uid,))
    conn.commit()

    class Refusing:
        def send_as_signature(self):
            raise gmail_api.GmailConfigError("no", "insufficientPermissions")
    gmail_sync._signature_once(conn, uid, "bdm@x", Refusing())
    assert conn.execute("select count(*) n from user_email_signatures").fetchone()["n"] == 0


def test_the_gmail_client_reads_the_default_send_as_address_and_nothing_else(monkeypatch):
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"sendAs": [{"sendAsEmail": "alias@example.com", "signature": "<div>alias</div>"},
                               {"sendAsEmail": "tom@example.com", "isPrimary": True, "signature": "<div>primary</div>"},
                               {"sendAsEmail": "default@example.com", "isDefault": True, "signature": "<div>default</div>"}]}

    gm = gmail_api.GmailClient("r", "cid", "secret")
    monkeypatch.setattr(gm, "_send", lambda method, url, **kw: (seen.append((method, url, kw.get("params"))), R())[1])
    assert gm.send_as_signature() == ("default@example.com", "<div>default</div>")
    assert seen == [("GET", "https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs", {"fields": "sendAs(sendAsEmail,isPrimary,isDefault,signature)"})]
    assert gmail_api.GMAIL_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"          # still read-only: nothing that sends, modifies or deletes


# ------------------------------------------------------------------------------------------------ the migration
def test_migration_0060_is_additive_safe_to_run_again_and_raises_the_text_limit(conn):
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0060_rich_signatures.sql").read_text()
    conn.execute(sql)
    conn.execute(sql)
    conn.commit()
    assert conn.execute("select 1 from information_schema.columns where table_name='user_email_signatures' and column_name='signature_html'").fetchone()
    uid = str(conn.execute("insert into users(email, display_name, role, active) values ('mig@x','Mig','bdm',true) returning id").fetchone()["id"])
    conn.execute("insert into user_email_signatures(user_id, signature, signature_html, updated_by) values (%s,%s,%s,'mig@x')", (uid, "x" * 1500, "<div>x</div>"))
    conn.commit()
    with pytest.raises(Exception):
        conn.execute("update user_email_signatures set signature=%s where user_id=%s", ("x" * 2001, uid))
    conn.rollback()
