"""Gmail connection (round 8): the sign-in, the client, and the sync. Google and Gmail are FAKES (httpx.MockTransport): no network, invented names and addresses."""
import asyncio
import base64
import dataclasses
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote_plus, urlparse

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from ri import vault
from ri.api import connections_gmail as cg
from ri.api.app import create_app
from ri.api.auth import make_dev_token
from ri.config import Settings
from ri.connections import gmail_api, gmail_sync, store
from ri.db import Database, q1, qa

SECRET = "x" * 32
KEY = vault.new_key()
CID = "client-id-123.apps.googleusercontent.com"
CLIENT_SECRET = "client-secret-xyz"
REFRESH = "RT-refresh-secret-1"
ACCESS = "AT-access-secret-1"
NOW = datetime(2026, 11, 27, 10, 30, tzinfo=timezone.utc)
OWN = "ann@thecodest.test"
SENSITIVE = (REFRESH, ACCESS, CLIENT_SECRET, "good-code")


def settings(**over) -> Settings:
    base = dict(env="development", auth_mode="dev", dev_auth_secret=SECRET, google_client_id=CID, google_client_secret=CLIENT_SECRET, vault_key=KEY,
                allowed_email_domain="thecodest.test")
    return Settings(**{**base, **over})


# ------------------------------------------------------------------ fake Google and Gmail
def _bare(header: str) -> list[str]:
    return [a.lower().split("+")[0] + "@" + a.lower().split("@")[1] if "+" in a else a.lower() for a in re.findall(r"[\w.+'-]+@[\w.-]+\.[a-zA-Z]{2,}", header)]


def msg(mid, frm, to, subject, snippet, days_ago=5, cc="", labels=("INBOX",), hours_ago=0):
    when = NOW - timedelta(days=days_ago, hours=hours_ago)
    return {"id": mid, "from": frm, "to": to, "cc": cc, "subject": subject, "snippet": snippet, "ms": int(when.timestamp() * 1000), "labels": list(labels)}


class FakeGoogle:
    def __init__(self):
        self.mailbox: list[dict] = []
        self.codes: dict[str, dict] = {}
        self.revoked = False                 # the refresh token was revoked at Google
        self.gone: set[str] = set()          # messages deleted between the search and the read
        self.malformed: set[str] = set()     # messages Gmail answers oddly for
        self.list_script: list[int] = []     # statuses to answer the next searches with, then behave
        self.list_always: int | None = None
        self.token_down = False
        self.revoke_status = 200
        self.userinfo: dict | None = None
        self.on_get = None
        self.lists: list[str] = []
        self.gets: list[tuple[str, list]] = []
        self.token_posts: list[dict] = []
        self.refreshes = 0
        self.revokes: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    @staticmethod
    def _err(status: int, reason: str, **kw) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": status, "message": "x", "errors": [{"reason": reason}]}}, **kw)

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        if u.host == "oauth2.googleapis.com" and u.path == "/token":
            if self.token_down:
                raise httpx.ConnectError("down")
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.token_posts.append(form)
            if form.get("grant_type") == "authorization_code":
                tokens = self.codes.get(form.get("code", ""))
                if not tokens or form.get("client_secret") != CLIENT_SECRET:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad Request"})
                return httpx.Response(200, json=tokens)
            if form.get("grant_type") == "refresh_token":
                self.refreshes += 1
                if self.revoked or form.get("refresh_token") != REFRESH:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
                return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3599, "token_type": "Bearer"})
        if u.host == "oauth2.googleapis.com" and u.path == "/revoke":
            self.revokes.append(parse_qs(req.content.decode()).get("token", [""])[0])
            return httpx.Response(self.revoke_status, json={})
        if u.host == "openidconnect.googleapis.com":
            if req.headers.get("authorization") != "Bearer AT-from-code" or not self.userinfo:
                return httpx.Response(401, json={})
            return httpx.Response(200, json=self.userinfo)
        if u.host == "gmail.googleapis.com":
            if req.headers.get("authorization") != f"Bearer {ACCESS}":
                return self._err(401, "authError")
            path = u.path
            if path == "/gmail/v1/users/me/messages":
                return self._list(u)
            if path.startswith("/gmail/v1/users/me/messages/"):
                return self._get(u)
        return httpx.Response(404, json={"error": "unknown"})

    def _list(self, u: httpx.URL) -> httpx.Response:
        q = u.params["q"]
        self.lists.append(q)
        if self.list_always:
            return self._err(self.list_always, "rateLimitExceeded" if self.list_always == 429 else "backendError")
        if self.list_script:
            status = self.list_script.pop(0)
            if status != 200:
                return self._err(status, "backendError")
        addrs = set(_bare(q))
        after = re.search(r"after:(\d{4})/(\d{2})/(\d{2})", q)
        floor = datetime(int(after[1]), int(after[2]), int(after[3]), tzinfo=timezone.utc).timestamp() * 1000 if after else 0
        hits = [m for m in self.mailbox if m["ms"] >= floor and addrs & set(_bare(m["from"] + " " + m["to"] + " " + m["cc"]))]
        hits.sort(key=lambda m: -m["ms"])
        size, off = int(u.params.get("maxResults", 100)), int(u.params.get("pageToken", 0))
        body: dict = {"resultSizeEstimate": len(hits)}
        if hits[off:off + size]:
            body["messages"] = [{"id": m["id"], "threadId": "t-" + m["id"]} for m in hits[off:off + size]]
        if off + size < len(hits):
            body["nextPageToken"] = str(off + size)
        return httpx.Response(200, json=body)

    def _get(self, u: httpx.URL) -> httpx.Response:
        mid = u.path.rsplit("/", 1)[1]
        self.gets.append((mid, u.params.multi_items()))
        if self.on_get:
            self.on_get(mid)
        if mid in self.gone:
            return self._err(404, "notFound")
        if mid in self.malformed:
            return httpx.Response(200, json={"unexpected": True})
        m = next(x for x in self.mailbox if x["id"] == mid)
        headers = [{"name": "From", "value": m["from"]}, {"name": "To", "value": m["to"]}, {"name": "Subject", "value": m["subject"]},
                   {"name": "Date", "value": "Mon, 1 Jan 2026 10:00:00 +0000"}]
        if m["cc"]:
            headers.append({"name": "Cc", "value": m["cc"]})
        return httpx.Response(200, json={"id": mid, "threadId": "t-" + mid, "labelIds": m["labels"], "snippet": m["snippet"], "internalDate": str(m["ms"]),
                                         "payload": {"headers": headers}})


@pytest.fixture()
def fake(monkeypatch):
    g = FakeGoogle()
    waits: list[float] = []
    monkeypatch.setattr(gmail_api, "TRANSPORT", g.transport())
    monkeypatch.setattr(gmail_api, "PACE_S", 0)
    monkeypatch.setattr(gmail_api, "_sleep", waits.append)
    g.waits = waits                                  # type: ignore[attr-defined]
    return g


# ------------------------------------------------------------------ database helpers
def mk_user(conn, email=OWN, active=True) -> str:
    return str(conn.execute("INSERT INTO users(email, display_name, role, active) VALUES (%s,'Ann Example','bdm',%s) RETURNING id", (email, active)).fetchone()["id"])


def mk_org(conn, name, domain) -> str:
    return str(conn.execute("INSERT INTO organizations(display_name, canonical_domain) VALUES (%s,%s) RETURNING id", (name, domain)).fetchone()["id"])


def mk_person(conn, name, email, org=None, suppressed=False, erased=False) -> str:
    pid = str(conn.execute("INSERT INTO people(full_name, work_email, suppressed, erased_at) VALUES (%s,%s,%s,%s) RETURNING id",
                           (name, email, suppressed, NOW if erased else None)).fetchone()["id"])
    if org:
        conn.execute("INSERT INTO employment(person_id, organization_id, title) VALUES (%s,%s,'Head of IT')", (pid, org))
    return pid


def connect(conn, uid, label=OWN, **kw) -> None:
    store.connect(conn, KEY, uid, "gmail", secret=REFRESH, label=label, scopes=gmail_api.GMAIL_SCOPE, **kw)
    conn.commit()


def job(uid) -> dict:
    return {"id": "j1", "payload": {"user_id": uid}}


def sync(conn, uid, now=NOW, **kw) -> dict:
    return gmail_sync.run(conn, job(uid), settings=kw.pop("st", settings()), now=now, **kw)


@pytest.fixture()
def world(conn, fake):
    """Ann (a seller at thecodest.test) with Gmail connected, and the people she may have written to."""
    uid = mk_user(conn)
    bank, other = mk_org(conn, "Example Bank", "bank.example"), mk_org(conn, "Other Corp", "other.example")
    p = {"jane": mk_person(conn, "Jane Doe", "Jane.Doe@Bank.Example", bank), "marek": mk_person(conn, "Marek Nowak", "marek@bank.example", bank),
         "tom": mk_person(conn, "Tom Other", "tom@other.example"),                                   # no employment: the account comes from the address
         "colleague": mk_person(conn, "Cy Colleague", "cy@thecodest.test", bank),                    # her own company: never looked up
         "erased": mk_person(conn, "Gone Person", "gone@bank.example", bank, erased=True),
         "suppressed": mk_person(conn, "No Contact", "nope@bank.example", bank, suppressed=True)}
    connect(conn, uid)
    return {"uid": uid, "bank": bank, "other": other, "p": p}


def rows(conn, uid):
    return {r["external_id"]: r for r in qa(conn, "SELECT * FROM contact_interactions WHERE user_id=%s AND channel='email'", (uid,))}


# ------------------------------------------------------------------ the signed state
def test_state_is_signed_expires_and_names_the_person():
    s = settings()
    st = cg.make_state(s, "user-1", "https://ri.example.test/api/me/connections/gmail/callback", now=1000)
    claims = cg.read_state(s, st, now=1100)
    assert claims and claims["u"] == "user-1" and claims["r"].endswith("/gmail/callback") and claims["x"] == 1000 + 600
    assert cg.read_state(s, st, now=1000 + 601) is None                                   # 10 minutes, no more
    body, sig = st.split(".")
    assert cg.read_state(s, body + "." + sig[:-2] + ("AA" if not sig.endswith("AA") else "BB"), now=1100) is None      # a changed signature
    forged = base64.urlsafe_b64encode(json.dumps({**claims, "u": "user-2"}, separators=(",", ":")).encode()).decode().rstrip("=")
    assert cg.read_state(s, forged + "." + sig, now=1100) is None                         # a changed person
    assert cg.read_state(settings(dev_auth_secret="y" * 32), st, now=1100) is None          # another server secret
    for junk in ("", "abc", "a.b", "a.b.c", None, 5):
        assert cg.read_state(s, junk) is None                                              # type: ignore[arg-type]
    assert cg.read_state(s, jwt.encode({"email": OWN, "typ": "session", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")) is None   # a session token is no state


# ------------------------------------------------------------------ the API: start and callback
@pytest.fixture()
def app_world(conn, db_url, fake, monkeypatch):
    monkeypatch.delenv("RI_PUBLIC_URL", raising=False)
    uid = mk_user(conn)
    off = mk_user(conn, "off@thecodest.test", active=False)
    conn.commit()
    holder: dict = {"dbs": []}

    def build(**over):
        db = Database(db_url, max_size=4)
        holder["dbs"].append(db)
        st = settings(**over)
        return {"c": TestClient(create_app(st, db=db)), "st": st, "uid": uid, "off": off, "conn": conn, "fake": fake,
                "h": {"Authorization": f"Bearer {make_dev_token(SECRET, OWN)}"}}

    holder["build"] = build
    yield holder
    for db in holder["dbs"]:
        db.close()


def _tokens(address=OWN, scope=f"openid https://www.googleapis.com/auth/userinfo.email {gmail_api.GMAIL_SCOPE}", refresh=REFRESH, id_token=True):
    out = {"access_token": "AT-from-code", "expires_in": 3599, "scope": scope, "token_type": "Bearer"}
    if refresh:
        out["refresh_token"] = refresh
    if id_token:
        out["id_token"] = jwt.encode({"email": address, "email_verified": True, "aud": CID, "iss": "https://accounts.google.com"}, "irrelevant-key-of-32-bytes-or-more!!", algorithm="HS256")
    return out


CB = "/api/me/connections/gmail/callback"
REDIRECT = "http://testserver" + CB


def _err(resp) -> str:
    assert resp.status_code == 302, resp.text
    loc = resp.headers["location"]
    assert loc.startswith("/connections?error="), loc
    return unquote_plus(loc.split("error=", 1)[1])


def test_start_gives_the_consent_address_for_exactly_the_read_only_scope(app_world, monkeypatch):
    w = app_world["build"]()
    c, h = w["c"], w["h"]
    assert c.post("/api/me/connections/gmail/start").status_code == 401                    # needs a signed-in person
    url = c.post("/api/me/connections/gmail/start", headers=h).json()["url"]
    u, q = urlparse(url), parse_qs(urlparse(url).query)
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert q["scope"] == ["openid email https://www.googleapis.com/auth/gmail.readonly"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["response_type"] == ["code"] and q["client_id"] == [CID] and q["login_hint"] == [OWN]
    assert q["redirect_uri"] == [REDIRECT]
    claims = cg.read_state(w["st"], q["state"][0])
    assert claims and claims["u"] == w["uid"] and claims["r"] == REDIRECT
    assert CLIENT_SECRET not in url and REFRESH not in url
    # behind a proxy: the address the browser used
    url2 = c.post("/api/me/connections/gmail/start", headers={**h, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "ri.example.test"}).json()["url"]
    assert parse_qs(urlparse(url2).query)["redirect_uri"] == ["https://ri.example.test" + CB]
    monkeypatch.setenv("RI_PUBLIC_URL", "https://app.example.test/")                        # a configured address wins
    url3 = c.post("/api/me/connections/gmail/start", headers={**h, "X-Forwarded-Host": "evil.example"}).json()["url"]
    assert parse_qs(urlparse(url3).query)["redirect_uri"] == ["https://app.example.test" + CB]


@pytest.mark.parametrize("over,phrase", [({"vault_key": None}, "encryption key"), ({"google_client_secret": None}, "client secret"), ({"google_client_id": None}, "Google sign-in")])
def test_start_says_plainly_when_gmail_is_not_set_up(app_world, over, phrase):
    w = app_world["build"](**over)
    r = w["c"].post("/api/me/connections/gmail/start", headers=w["h"])
    assert r.status_code == 409 and phrase in r.json()["detail"] and "Traceback" not in r.text


def test_callback_stores_the_sealed_token_audits_queues_a_first_sync_and_leaks_nothing(app_world, conn):
    w = app_world["build"]()
    w["fake"].codes["good-code"] = _tokens()
    state = cg.make_state(w["st"], w["uid"], REDIRECT)
    r = w["c"].get(CB, params={"code": "good-code", "state": state}, follow_redirects=False)       # no bearer token: Google's browser redirect carries none
    assert r.status_code == 302 and r.headers["location"] == "/connections?connected=gmail"
    sent = w["fake"].token_posts[0]
    assert sent["grant_type"] == "authorization_code" and sent["redirect_uri"] == REDIRECT and sent["client_id"] == CID and sent["code"] == "good-code"
    row = store.get(conn, w["uid"], "gmail")
    assert row["status"] == "connected" and row["account_label"] == OWN and gmail_api.GMAIL_SCOPE in row["scopes"]
    assert REFRESH.encode() not in bytes(row["secret_enc"]) and store.secret(conn, KEY, w["uid"], "gmail") == REFRESH
    audit = q1(conn, "SELECT * FROM audit_events WHERE operation='connection_connected'")
    assert audit["actor"] == OWN and audit["record_id"] == f"{w['uid']}:gmail" and audit["detail"] == {"provider": "gmail", "account": OWN}
    queued = qa(conn, "SELECT kind, payload FROM jobs WHERE kind='gmail_sync'")
    assert len(queued) == 1 and queued[0]["payload"] == {"user_id": w["uid"]}
    everything = json.dumps([list(r.headers.items()), r.text])
    for table in ("audit_events", "user_connections", "jobs", "contact_interactions", "connection_events"):
        everything += "".join(x["t"] for x in qa(conn, f"SELECT row_to_json(x)::text AS t FROM {table} x"))
    listing = w["c"].get("/api/me/connections", headers=w["h"])
    if listing.status_code == 200:
        everything += listing.text
    assert not any(s in everything for s in SENSITIVE)


def test_callback_takes_the_address_from_google_userinfo_when_the_id_token_has_none(app_world, conn):
    w = app_world["build"]()
    w["fake"].codes["good-code"] = _tokens(id_token=False)
    w["fake"].userinfo = {"email": "Ann.Alias@TheCodest.test", "email_verified": True}
    r = w["c"].get(CB, params={"code": "good-code", "state": cg.make_state(w["st"], w["uid"], REDIRECT)}, follow_redirects=False)
    assert r.headers["location"] == "/connections?connected=gmail" and store.get(conn, w["uid"], "gmail")["account_label"] == "ann.alias@thecodest.test"


def _nothing_stored(conn, w):
    assert store.get(conn, w["uid"], "gmail") is None
    assert q1(conn, "SELECT count(*) AS n FROM jobs WHERE kind='gmail_sync'")["n"] == 0


def test_callback_when_the_person_says_no(app_world, conn):
    w = app_world["build"]()
    r = w["c"].get(CB, params={"error": "access_denied", "state": cg.make_state(w["st"], w["uid"], REDIRECT)}, follow_redirects=False)
    assert _err(r) == cg.MSG_DENIED
    assert w["fake"].token_posts == []
    _nothing_stored(conn, w)


def test_callback_refuses_a_state_that_is_forged_expired_or_missing(app_world, conn):
    w = app_world["build"]()
    w["fake"].codes["good-code"] = _tokens()
    good = cg.make_state(w["st"], w["uid"], REDIRECT)
    for bad in (good[:-3] + "xyz", cg.make_state(w["st"], w["uid"], REDIRECT, now=time.time() - 700), "", "garbage",
                cg.make_state(settings(dev_auth_secret="y" * 32), w["uid"], REDIRECT)):
        r = w["c"].get(CB, params={"code": "good-code", "state": bad}, follow_redirects=False)
        assert _err(r) == cg.MSG_LINK
    assert _err(w["c"].get(CB, params={"code": "good-code"}, follow_redirects=False)) == cg.MSG_LINK
    assert w["fake"].token_posts == []                                                     # Google was never asked, the code was not used
    _nothing_stored(conn, w)


def test_callback_for_someone_who_is_no_longer_active(app_world, conn):
    w = app_world["build"]()
    w["fake"].codes["good-code"] = _tokens()
    r = w["c"].get(CB, params={"code": "good-code", "state": cg.make_state(w["st"], w["off"], REDIRECT)}, follow_redirects=False)
    assert _err(r) == cg.MSG_NO_USER and w["fake"].token_posts == []


@pytest.mark.parametrize("over,msg_", [({"vault_key": None}, cg.MSG_VAULT), ({"google_client_secret": None}, cg.MSG_SECRET)])
def test_callback_when_the_server_is_not_set_up_does_not_use_the_code(app_world, conn, over, msg_):
    w = app_world["build"](**over)
    w["fake"].codes["good-code"] = _tokens()
    r = w["c"].get(CB, params={"code": "good-code", "state": cg.make_state(w["st"], w["uid"], REDIRECT)}, follow_redirects=False)
    assert _err(r) == msg_ and w["fake"].token_posts == []
    _nothing_stored(conn, w)


def test_callback_explains_each_way_google_can_say_no(app_world, conn):
    w = app_world["build"]()
    f = w["fake"]
    state = lambda: cg.make_state(w["st"], w["uid"], REDIRECT)       # noqa: E731
    get = lambda code: w["c"].get(CB, params={"code": code, "state": state()}, follow_redirects=False)       # noqa: E731
    assert _err(get("unknown-code")) == cg.MSG_REFUSED                                       # Google refuses the code
    f.codes["no-refresh"] = _tokens(refresh=None)
    assert _err(get("no-refresh")) == cg.MSG_NO_REFRESH
    f.codes["no-gmail"] = _tokens(scope="openid https://www.googleapis.com/auth/userinfo.email")          # the person unticked the Gmail box
    assert _err(get("no-gmail")) == cg.MSG_NO_SCOPE
    f.codes["no-address"] = _tokens(id_token=False)
    assert _err(get("no-address")) == cg.MSG_NO_ADDRESS
    assert _err(w["c"].get(CB, params={"state": state()}, follow_redirects=False)) == cg.MSG_GOOGLE_STOPPED          # no code at all
    f.token_down = True
    assert _err(get("whatever")) == cg.MSG_UNREACHABLE
    _nothing_stored(conn, w)


def test_callback_reports_a_wrong_google_client_plainly(app_world, conn, monkeypatch):
    w = app_world["build"](google_client_secret="the-wrong-secret")
    w["fake"].codes["good-code"] = _tokens()
    w["fake"].token_posts.clear()
    r = w["c"].get(CB, params={"code": "good-code", "state": cg.make_state(w["st"], w["uid"], REDIRECT)}, follow_redirects=False)
    assert _err(r) == cg.MSG_REFUSED and "the-wrong-secret" not in r.headers["location"]


# ------------------------------------------------------------------ revoke
def test_revoke_is_best_effort_and_never_raises(fake, conn, monkeypatch):
    assert gmail_api.revoke_token(REFRESH) is True and fake.revokes == [REFRESH]
    assert asyncio.run(gmail_api.revoke_token_async(REFRESH)) is True and fake.revokes == [REFRESH, REFRESH]
    fake.revoke_status = 400
    assert gmail_api.revoke_token(REFRESH) is False
    assert gmail_api.revoke_token("") is False

    def down(req):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(gmail_api, "TRANSPORT", httpx.MockTransport(down))
    assert gmail_api.revoke_token(REFRESH) is False and asyncio.run(gmail_api.revoke_token_async(REFRESH)) is False


def test_revoke_for_uses_the_stored_token_and_stops_once_disconnected(fake, conn):
    uid = mk_user(conn)
    connect(conn, uid)
    assert gmail_sync.revoke_for(conn, KEY, uid) is True and fake.revokes == [REFRESH]
    assert gmail_sync.revoke_for(conn, vault.new_key(), uid) is False               # a wrong key cannot open it: no crash
    store.disconnect(conn, uid, "gmail")
    assert gmail_sync.revoke_for(conn, KEY, uid) is False


# ------------------------------------------------------------------ the client
def test_message_metadata_is_read_tolerantly():
    m = gmail_api.parse_metadata({"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"], "snippet": "Thanks for the &#39;proposal&#39; &amp;  the call",
                                  "internalDate": str(int(NOW.timestamp() * 1000)),
                                  "payload": {"headers": [{"name": "From", "value": '"Doe, Jane" <Jane.Doe@Bank.Example>'},
                                                          {"name": "To", "value": "ann@thecodest.test, Marek <marek@bank.example>"},
                                                          {"name": "Subject", "value": "=?UTF-8?B?V2l0YWphxIc=?=   today"}]}})
    assert m.sender == ("jane.doe@bank.example",) and m.to == ("ann@thecodest.test", "marek@bank.example") and m.cc == ()
    assert m.subject == "Witajać today" and m.snippet == "Thanks for the 'proposal' & the call" and m.occurred_at == NOW and m.labels == ("INBOX", "UNREAD")
    with pytest.raises(ValueError):
        gmail_api.parse_metadata({"nothing": 1})
    assert gmail_api.parse_metadata({"id": "m2", "payload": {"headers": [{"name": "Date", "value": "Mon, 2 Nov 2026 09:00:00 +0100"}]}}).occurred_at.hour == 8


def test_the_client_refreshes_retries_and_names_what_went_wrong(fake, monkeypatch):
    fake.mailbox = [msg("m1", "jane@bank.example", OWN, "Hi", "Hello")]
    with gmail_api.GmailClient(REFRESH, CID, CLIENT_SECRET) as gm:
        assert "RT-" not in repr(gm) and CLIENT_SECRET not in repr(gm)
        fake.list_script = [503, 429]
        ids, more = gm.list_message_ids("from:(jane@bank.example) after:2026/08/01")
        assert ids == ["m1"] and more is False and fake.waits == [1.0, 2.0]                 # 503 then 429, then success after backing off
        assert fake.refreshes == 1                                                           # one token refresh served all the calls
        meta = gm.get_metadata("m1")
        assert meta.subject == "Hi" and fake.refreshes == 1
    with gmail_api.GmailClient(REFRESH, CID, CLIENT_SECRET) as gm:
        fake.list_always = 429
        fake.waits.clear()
        with pytest.raises(gmail_api.GmailRateLimited):
            gm.list_message_ids("x")
        assert fake.waits == [1.0, 2.0, 4.0]
        fake.list_always = 500
        with pytest.raises(gmail_api.GmailUnavailable):
            gm.list_message_ids("x")
    fake.revoked = True
    with gmail_api.GmailClient(REFRESH, CID, CLIENT_SECRET) as gm, pytest.raises(gmail_api.GmailAuthError) as exc:
        gm.list_message_ids("x")
    assert not any(s in str(exc.value) for s in SENSITIVE) and exc.value.reason == "invalid_grant"
    with gmail_api.GmailClient("another-token", CID, CLIENT_SECRET) as gm, pytest.raises(gmail_api.GmailAuthError):
        gm.list_message_ids("x")


# ------------------------------------------------------------------ the sync
def test_addresses_are_compared_the_way_people_write_them():
    k = gmail_sync.match_key
    assert k("Jane.Doe@Bank.Example") == k("jane.doe@bank.example") == k("jane.doe+news@bank.example") == "jane.doe@bank.example"
    assert k("first.last@gmail.com") == k("FirstLast+x@googlemail.com") == "firstlast@gmail.com"
    assert k("nope") is None and k("a@b@c") is None and k("+tag@x.example") is None and k(None) is None
    assert gmail_sync.query_form("Jane+news@Bank.Example") == "jane@bank.example"
    for unsafe in ("x@y.example) OR in:anywhere (a@b.example", "a b@x.example", 'a"b@x.example', "a:b@x.example", "-a@x.example", "a@b"):
        assert gmail_sync.query_form(unsafe) is None


def test_first_sync_stores_the_gist_of_conversations_with_people_we_know(conn, fake, world):
    uid, p = world["uid"], world["p"]
    fake.mailbox = [
        msg("m-in", '"Doe, Jane" <jane.doe@bank.example>', OWN, "Re: Dev team", "Thanks for the &#39;proposal&#39; &amp; the call", days_ago=10),
        msg("m-out", OWN, "JANE.DOE@bank.example", "Proposal for a team", "Hi Jane, attached is the proposal", days_ago=12, labels=("SENT",)),
        msg("m-both", "Cy Colleague <cy@thecodest.test>", "jane.doe@bank.example", "Intro", "Meet Ann", days_ago=3, cc=OWN),
        msg("m-plus", "jane.doe+newsletter@bank.example", OWN, "Weekly", "News", days_ago=2),
        msg("m-tom", "tom@other.example", OWN, "Quote", "Prices attached", days_ago=20),
        msg("m-marek", OWN, "marek@bank.example, gone@bank.example", "Call", "Shall we talk?", days_ago=7, labels=("SENT",)),
        msg("m-old", "jane.doe@bank.example", OWN, "Ancient", "From last year", days_ago=200),                   # before the 90 days
        msg("m-draft", OWN, "jane.doe@bank.example", "Unsent", "draft text", days_ago=1, labels=("DRAFT",)),
        msg("m-stranger", "stranger@elsewhere.example", OWN, "Spam", "Buy now", days_ago=1),                  # nobody we know
    ]
    out = sync(conn, uid)
    assert out["contacts_checked"] == 3 and out["more"] is False and out["new_interactions"] == 6 and out["messages_seen"] == 7
    got = rows(conn, uid)
    assert set(got) == {"m-in", "m-out", "m-both", "m-plus", "m-tom", "m-marek"}
    assert {k: v["direction"] for k, v in got.items()} == {"m-in": "in", "m-out": "out", "m-both": "both", "m-plus": "in", "m-tom": "in", "m-marek": "out"}
    assert str(got["m-in"]["person_id"]) == p["jane"] and str(got["m-in"]["organization_id"]) == world["bank"]
    assert str(got["m-out"]["person_id"]) == p["jane"]                                                       # case does not matter
    assert str(got["m-plus"]["person_id"]) == p["jane"]                                                      # neither does a +tag
    assert str(got["m-marek"]["person_id"]) == p["marek"]                                                    # the erased person on the same message is ignored
    assert str(got["m-tom"]["person_id"]) == p["tom"] and str(got["m-tom"]["organization_id"]) == world["other"]       # the account found from the address
    assert got["m-in"]["title"] == "Re: Dev team" and got["m-in"]["gist"] == "Thanks for the 'proposal' & the call" and got["m-in"]["occurred_at"] == NOW - timedelta(days=10)
    assert all(r["visibility"] == "owner" for r in got.values())                                            # the gist stays with its owner by default
    assert got["m-in"]["meta"] == {"thread": "t-m-in"}
    # the very first look goes back 90 days, and the cursor remembers the pass
    assert all("after:2026/08/29" in q for q in fake.lists) and len(fake.lists) == 1
    cur = store.get(conn, uid, "gmail")["cursor"]
    assert cur["account"] == OWN and cur["synced_through"] == NOW.isoformat() and "pass_start" not in cur
    assert store.get(conn, uid, "gmail")["last_sync_at"] == NOW and store.get(conn, uid, "gmail")["status"] == "connected"


def test_colleagues_erased_suppressed_and_unsubscribed_people_are_never_looked_up(conn, fake, world):
    uid, bank = world["uid"], world["bank"]
    mk_person(conn, "Bounced Bob", "bob@bank.example", bank)
    mk_person(conn, "Optout Olga", "olga@bank.example", bank)
    mk_person(conn, "Mirror Mia", "mia@bank.example", bank)
    mk_person(conn, "Evil Eve", "eve@x.example) OR in:anywhere (a@b.example", bank)
    mk_person(conn, "Own Ann", OWN, bank)                                                      # the seller herself
    pid = {r["work_email"]: str(r["id"]) for r in qa(conn, "SELECT id, work_email FROM people")}
    conn.execute("INSERT INTO crm_persons(crm_id, person_id, name, suppressed) VALUES (1, %s, 'Mirror Mia', true)", (pid["mia@bank.example"],))      # suppressed in the CRM mirror
    did = conn.execute("INSERT INTO campaign_drafts(organization_id, requested_by, status) VALUES (%s,'x','approved') RETURNING id", (bank,)).fetchone()["id"]
    for who, status in (("bob@bank.example", "bounced"), ("OLGA@bank.example", "unsubscribed")):                                               # blocked by a campaign
        conn.execute("INSERT INTO outbound_enrolments(campaign_draft_id, organization_id, email, status) VALUES (%s,%s,%s,%s)", (did, bank, who, status))
    conn.commit()
    fake.mailbox = [msg("m-colleague", "cy@thecodest.test", OWN, "Lunch", "Noon?"), msg("m-erased", "gone@bank.example", OWN, "Hello", "Hi"),
                    msg("m-supp", "nope@bank.example", OWN, "Hello", "Hi"), msg("m-jane", "jane.doe@bank.example", OWN, "Real", "Real contact")]
    out = sync(conn, uid)
    allq = " ".join(fake.lists).lower()
    for never in ("cy@thecodest", "gone@bank", "nope@bank", "mia@bank", "in:anywhere", OWN):
        assert never not in allq, never
    assert "jane.doe@bank.example" in allq and set(rows(conn, uid)) == {"m-jane"} and out["contacts_checked"] == 3          # jane, marek, tom
    assert not [m for m in fake.gets if m[0] in ("m-colleague", "m-erased", "m-supp")]


def test_visibility_is_owner_only_unless_the_person_shares_the_gist(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "A private line")]
    sync(conn, uid)
    assert rows(conn, uid)["m1"]["visibility"] == "owner"
    store.update_settings(conn, uid, "gmail", {"share_email_gist": True})
    fake.mailbox.append(msg("m2", "jane.doe@bank.example", OWN, "Hello again", "Shared line", days_ago=1))
    sync(conn, uid, now=NOW + timedelta(hours=1))
    assert rows(conn, uid)["m2"]["visibility"] == "team"                                    # new mail follows the switch (the switch route flips the older rows)


def test_a_repeat_sync_adds_nothing_twice_and_does_not_read_known_messages_again(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "Line one", days_ago=0), msg("m2", OWN, "marek@bank.example", "Hi", "Line two", days_ago=0, labels=("SENT",))]
    assert sync(conn, uid)["new_interactions"] == 2 and len(fake.gets) == 2
    again = sync(conn, uid, now=NOW + timedelta(hours=1))
    assert again["new_interactions"] == 0 and again["messages_seen"] == 2 and again["already_stored"] == 2
    assert len(fake.gets) == 2 and len(rows(conn, uid)) == 2                                # not read again, not duplicated


def test_later_runs_start_a_day_before_the_last_pass_and_pick_up_new_mail(conn, fake, world, monkeypatch):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "Line one", days_ago=40)]
    sync(conn, uid)
    fake.mailbox.append(msg("m-new", "jane.doe@bank.example", OWN, "Newer", "Just in", days_ago=0, hours_ago=-1))          # arrives after the first pass
    fake.lists.clear()
    later = sync(conn, uid, now=NOW + timedelta(hours=2))
    assert all("after:2026/11/26" in q for q in fake.lists)                                  # the safety day: the day before the last pass began
    assert later["new_interactions"] == 1 and later["deep"] is False and set(rows(conn, uid)) == {"m1", "m-new"}
    fake.lists.clear()
    week = sync(conn, uid, now=NOW + timedelta(days=8))                                      # a weekly full look-back picks up contacts added after the first pass
    assert week["deep"] is True and all("after:2026/09/" in q or "after:2026/10/" in q for q in fake.lists)


def test_a_contact_added_later_gets_their_history_at_the_next_weekly_pass(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m-late", "late@bank.example", OWN, "Old thread", "From a while back", days_ago=30)]
    sync(conn, uid)
    assert rows(conn, uid) == {}
    mk_person(conn, "Late Larry", "late@bank.example", world["bank"])
    conn.commit()
    assert sync(conn, uid, now=NOW + timedelta(hours=3))["new_interactions"] == 0           # an ordinary pass only looks at the last day or two
    assert sync(conn, uid, now=NOW + timedelta(days=8))["new_interactions"] == 1


def test_big_contact_lists_are_worked_through_over_several_runs(conn, fake, world, monkeypatch):
    uid = world["uid"]
    for p in qa(conn, "SELECT id FROM people"):
        conn.execute("UPDATE people SET suppressed=true WHERE id=%s", (p["id"],))
    for i in range(10):
        mk_person(conn, f"Contact {i}", f"c{i:02d}@bank.example", world["bank"])
        fake.mailbox.append(msg(f"m{i:02d}", f"c{i:02d}@bank.example", OWN, f"Subject {i}", "x", days_ago=i + 1))
    conn.commit()
    monkeypatch.setattr(gmail_sync, "GROUP_SIZE", 2)
    monkeypatch.setattr(gmail_sync, "MAX_CONTACTS_PER_RUN", 4)
    r1 = sync(conn, uid)
    assert r1["more"] is True and r1["contacts_checked"] == 4 and r1["pass_complete"] is False and len(fake.lists) == 2
    cur = store.get(conn, uid, "gmail")["cursor"]
    assert cur["last"] == "c03@bank.example" and cur["pass_start"] == NOW.isoformat() and "synced_through" in cur
    r2 = sync(conn, uid, now=NOW + timedelta(hours=1))
    assert r2["more"] is True and r2["contacts_checked"] == 4 and r2["deep"] is True              # the same pass, the same 90-day window
    assert "after:2026/08/29" in fake.lists[-1]
    r3 = sync(conn, uid, now=NOW + timedelta(hours=2))
    assert r3["more"] is False and r3["contacts_checked"] == 2 and r3["pass_complete"] is True
    assert len(rows(conn, uid)) == 10
    cur = store.get(conn, uid, "gmail")["cursor"]
    assert cur["synced_through"] == NOW.isoformat() and "pass_start" not in cur                # the pass is dated by when it began
    fake.lists.clear()
    r4 = sync(conn, uid, now=NOW + timedelta(hours=3))                                          # the next pass begins, a day before the last one began
    assert r4["deep"] is False and all("after:2026/11/26" in q for q in fake.lists)


def test_messages_are_batched_into_few_searches(conn, fake, world):
    uid = world["uid"]
    for i in range(12):
        mk_person(conn, f"Batch {i}", f"b{i:02d}@bank.example", world["bank"])
    conn.commit()
    out = sync(conn, uid)
    assert out["contacts_known"] == 15 and len(fake.lists) == 2                                  # 15 addresses, ten to a search
    assert fake.lists[0].count("@") == 3 * 10 and fake.lists[0].startswith("(from:(") and " OR to:(" in fake.lists[0] and " OR cc:(" in fake.lists[0]


def test_no_body_and_no_attachment_is_ever_asked_for(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "Line one"), msg("m2", OWN, "marek@bank.example", "Hi", "Line two", labels=("SENT",))]
    sync(conn, uid)
    assert len(fake.gets) == 2
    for _mid, params in fake.gets:
        p = dict(params)
        assert p["format"] == "metadata" and sorted(v for k, v in params if k == "metadataHeaders") == ["Cc", "Date", "From", "Subject", "To"]
        assert "raw" not in p.values() and "full" not in p.values()
    assert {k for k in rows(conn, uid)["m1"] if k in ("body", "html", "text", "raw")} == set()
    assert all(r["gist"] in ("Line one", "Line two") and r["meta"].keys() <= {"thread"} for r in rows(conn, uid).values())


def test_one_bad_message_never_stops_the_rest(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m-gone", "jane.doe@bank.example", OWN, "Deleted", "x"), msg("m-odd", "jane.doe@bank.example", OWN, "Odd", "x", days_ago=2),
                    msg("m-ok", "jane.doe@bank.example", OWN, "Fine", "Good line", days_ago=3), msg("m-nul", "jane.doe@bank.example", OWN, "Has\x00nul", "snip\x00pet", days_ago=4)]
    fake.gone, fake.malformed = {"m-gone"}, {"m-odd"}
    out = sync(conn, uid)
    assert set(rows(conn, uid)) == {"m-ok", "m-nul"} and out["skipped_messages"] == 2 and out["new_interactions"] == 2
    assert rows(conn, uid)["m-nul"]["title"] == "Hasnul"                                        # a character the database refuses is dropped, the message kept
    assert store.get(conn, uid, "gmail")["status"] == "connected"


def test_when_google_no_longer_accepts_the_connection_the_person_is_asked_to_connect_again(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x")]
    fake.revoked = True
    out = sync(conn, uid)
    row = store.get(conn, uid, "gmail")
    assert out["needs_attention"] is True and row["status"] == "needs_attention" and "Connect Gmail again" in row["last_error"]
    assert not any(s in row["last_error"] for s in SENSITIVE) and fake.lists == [] and rows(conn, uid) == {}
    fake.revoked = False                                                                        # connecting again fixes it
    connect(conn, uid)
    assert sync(conn, uid, now=NOW + timedelta(hours=1))["new_interactions"] == 1 and store.get(conn, uid, "gmail")["status"] == "connected"


def test_an_api_that_is_switched_off_or_a_wrong_client_needs_attention_with_a_plain_message(conn, fake, world):
    uid = world["uid"]
    out = sync(conn, uid, st=settings(google_client_secret=None))
    assert out["needs_attention"] and "client id or secret" in store.get(conn, uid, "gmail")["last_error"]
    out = sync(conn, uid, st=settings(vault_key=None))
    assert out["needs_attention"] and "encryption key" in store.get(conn, uid, "gmail")["last_error"]
    out = sync(conn, uid, st=settings(vault_key=vault.new_key()))                              # another key cannot open the stored token
    assert out["needs_attention"] and "Connect Gmail again" in store.get(conn, uid, "gmail")["last_error"]


def test_rate_limits_stop_the_run_and_the_next_run_carries_on(conn, fake, world, monkeypatch):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x")]
    fake.list_always = 429
    out = sync(conn, uid)
    assert out["more"] is True and out["stopped"] == "Google asked us to slow down" and out["new_interactions"] == 0 and out["contacts_checked"] == 0
    assert fake.waits == [1.0, 2.0, 4.0] and len(fake.lists) == 4                               # waited, tried again, then stopped
    row = store.get(conn, uid, "gmail")
    assert row["status"] == "connected" and row["last_error"] is None and row["cursor"]["pass_start"] == NOW.isoformat()      # not an error: just not finished
    fake.list_always = None
    out = sync(conn, uid, now=NOW + timedelta(hours=1))
    assert out["more"] is False and out["new_interactions"] == 1 and out["deep"] is True       # it carried on with the same pass


def test_a_message_limit_stops_a_group_midway_and_resumes_without_rereading(conn, fake, world, monkeypatch):
    uid = world["uid"]
    fake.mailbox = [msg(f"m{i}", "jane.doe@bank.example", OWN, f"S{i}", "x", days_ago=i + 1) for i in range(5)]
    monkeypatch.setattr(gmail_sync, "MAX_MESSAGES_PER_RUN", 3)
    out = sync(conn, uid)
    assert out["more"] is True and out["stopped"] == "message limit" and out["new_interactions"] == 3 and len(fake.gets) == 3 and out["contacts_checked"] == 0
    out = sync(conn, uid, now=NOW + timedelta(hours=1))
    assert out["new_interactions"] == 2 and len(fake.gets) == 5 and out["more"] is False and len(rows(conn, uid)) == 5


def test_a_connection_removed_during_a_run_stores_nothing_more(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x")]
    def other_session_disconnects(mid):
        store.disconnect(conn, uid, "gmail")
        conn.commit()
    fake.on_get = other_session_disconnects
    out = sync(conn, uid)
    assert out["stopped"] == "disconnected" and rows(conn, uid) == {} and store.get(conn, uid, "gmail")["status"] == "disconnected"


def test_nothing_to_do_without_a_live_connection(conn, fake, world):
    uid = world["uid"]
    store.disconnect(conn, uid, "gmail")
    assert sync(conn, uid)["skipped"] == "not connected" and fake.lists == [] and fake.refreshes == 0
    assert gmail_sync.run(conn, {"payload": {}}, settings=settings())["skipped"]
    assert gmail_sync.run(conn, {"payload": {"user_id": "not-a-uuid"}}, settings=settings())["skipped"]
    off = mk_user(conn, "off@thecodest.test", active=False)
    connect(conn, off)
    assert sync(conn, off)["skipped"] == "not connected"


def test_a_different_google_account_starts_the_look_back_afresh(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x", days_ago=60)]
    sync(conn, uid)
    store.record_sync(conn, uid, "gmail", NOW, cursor={**store.get(conn, uid, "gmail")["cursor"], "synced_through": (NOW - timedelta(days=1)).isoformat(),
                                                          "deep_at": (NOW - timedelta(hours=1)).isoformat()})
    connect(conn, uid, label="ann.other@thecodest.test")                                       # connected another account
    fake.lists.clear()
    assert sync(conn, uid, now=NOW + timedelta(hours=1))["deep"] is True and "after:2026/08/29" in fake.lists[0]


def test_nothing_secret_or_private_reaches_the_log(conn, fake, world, caplog):
    uid = world["uid"]
    caplog.set_level(logging.DEBUG)
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Confidential renewal terms", "Our private snippet text"), msg("m2", "x@y.example", OWN, "Other", "x")]
    fake.revoked = False
    sync(conn, uid)
    fake.revoked = True
    sync(conn, uid, now=NOW + timedelta(hours=1))
    gmail_api.revoke_token(REFRESH)
    text = caplog.text
    assert "googleapis.com" not in text and "bank.example" not in text and "bank%2Eexample" not in text            # not even httpx's own request lines
    assert not any(s in text for s in SENSITIVE) and "Confidential renewal terms" not in text and "Our private snippet" not in text and "jane.doe@bank.example" not in text


def test_the_token_is_never_in_any_stored_row(conn, fake, world):
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x")]
    sync(conn, uid)
    blob = "".join(x["t"] for x in qa(conn, "SELECT row_to_json(x)::text AS t FROM user_connections x")) + "".join(x["t"] for x in qa(conn, "SELECT row_to_json(x)::text AS t FROM contact_interactions x"))
    assert REFRESH not in blob and ACCESS not in blob


def test_a_first_sync_can_be_queued_once_per_minute_and_never_piles_up(conn, world):
    uid = world["uid"]
    assert gmail_sync.queue_sync(conn, uid, now=NOW) is True
    assert gmail_sync.queue_sync(conn, uid, now=NOW) is False                                  # the same minute, and one still waiting
    job_row = q1(conn, "SELECT kind, payload, max_attempts FROM jobs")
    assert job_row == {"kind": "gmail_sync", "payload": {"user_id": uid}, "max_attempts": 2}
    conn.execute("UPDATE jobs SET status='completed'")
    assert gmail_sync.queue_sync(conn, uid, now=NOW + timedelta(minutes=5)) is True


def test_the_job_handler_runs_this_module(conn, fake, world, monkeypatch):
    """The scheduler's job handler calls run(conn, job, settings=...) with settings read from the environment."""
    from ri.jobs import handlers
    uid = world["uid"]
    fake.mailbox = [msg("m1", "jane.doe@bank.example", OWN, "Hello", "x")]
    for k, v in {"RI_GOOGLE_CLIENT_ID": CID, "RI_GOOGLE_CLIENT_SECRET": CLIENT_SECRET, "RI_VAULT_KEY": KEY, "RI_ENV": "development", "RI_AUTH_MODE": "dev",
                 "RI_DEV_AUTH_SECRET": SECRET}.items():
        monkeypatch.setenv(k, v)
    fn = handlers._admin_ops_jobs()["gmail_sync"]
    out = fn(conn, job(uid))
    assert out["new_interactions"] == 1 and fn.own_transactions is True
