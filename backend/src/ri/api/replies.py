"""What people wrote back to our Lemlist campaigns, and answering them in the same thread.

  GET  /api/replies/config                        may this person send, and is sending switched on (for the reply box)
  GET  /api/people/{pid}/email-thread             every email on its own, oldest first (our campaign emails with step, sender and opens; their
                                                  replies; answers sent from here) and a summary; also writes them to the person's timeline
  POST /api/people/{pid}/email-thread/refresh     read that person's Lemlist conversation again (rate-limited, read-only towards Lemlist)
  POST /api/people/{pid}/email-thread/reply       answer their newest reply in its Lemlist thread (the sender's signature appended once)
  GET  /api/me/email-signature[?person=]          the signature under my replies (saved, or the suggestion; see ri.outbound.signature)
  PUT  /api/me/email-signature                    keep my signature (typed text, or HTML pasted from my mail program, cleaned to safe tags; '' means none)
  POST /api/me/email-signature/preview            what a pasted signature looks like once cleaned (nothing is saved)
  POST /api/me/email-signature/import-gmail       take the signature from my connected Gmail account (its default send-as address)

Reading needs `contacts.read` and the person's row scope (the person page's rules; an erased person is "not found"). Sending also needs
`draft.create` (the permission behind every outreach step), the outbound switch, and a person who wrote to us and is not on the
do-not-contact list. See ri.outbound.replies for the rules of a send."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ..access import Principal
from .. import vault
from ..audit import audit
from ..connections import gmail_api, store
from ..db import q1
from ..outbound import replies as rp
from ..outbound import signature as sig
from .auth import principal_dep, require
from .profiles import _visible_person

SEND_PERMISSION = "draft.create"


class _Fixed:
    """What Google already answered, handed to ri.outbound.signature.import_from_gmail so the answer is read once."""

    def __init__(self, address: str | None, raw: str):
        self._a, self._r = address, raw

    def send_as_signature(self) -> tuple[str | None, str]:
        return self._a, self._r


class SignatureIn(BaseModel):
    text: str = Field(default="", max_length=sig.MAX_SIGNATURE + 400)        # checked again (normalised, lines, plain text) by sig.validate
    html: str | None = Field(default=None, max_length=300_000)                # pasted from a mail program: cleaned to safe tags by sig.rich_from_html


class SignaturePreviewIn(BaseModel):
    html: str = Field(max_length=300_000)


class ReplyIn(BaseModel):
    activity_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")
    text: str = Field(min_length=1, max_length=rp.MAX_REPLY + 500)
    key: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")        # one per draft in the browser: a double click never sends twice


def _person(conn, p: Principal, pid: str) -> dict[str, Any]:
    v = _visible_person(conn, p, pid)                         # same row scope as the person page; erased is "not found"
    person = q1(conn, "SELECT id, full_name, suppressed, erased_at, work_email, photo_url FROM people WHERE id=%s AND erased_at IS NULL", (v["id"],))
    if not person:
        raise HTTPException(404, "not found")
    return person


def _config(request: Request, p: Principal) -> dict[str, Any]:
    s = request.app.state.settings
    return {"sending_on": bool(s.outbound_enabled), "lemlist_connected": bool(s.lemlist_api_key), "may_send": p.can(SEND_PERMISSION)}


def _thread(conn, request: Request, p: Principal, person: dict[str, Any]) -> dict[str, Any]:
    out = rp.thread(conn, person)
    return {"person": {"id": str(person["id"]), "name": person["full_name"], "do_not_contact": rp.do_not_contact(conn, person),
                       "email": (out.get("reply") or {}).get("to", {}).get("email") or person.get("work_email"), "photo_url": person.get("photo_url")},
            **out, **_config(request, p), "signature": sig.for_user(conn, user_id=p.user_id, email=p.email, person_id=str(person["id"]))}


def register_replies(app: FastAPI) -> None:
    def tx(request: Request):
        return request.app.state.db.tx()

    @app.get("/api/replies/config")
    def replies_config(request: Request, p: Principal = Depends(require("contacts.read"))):
        return _config(request, p)

    @app.get("/api/me/email-signature")
    def my_signature(request: Request, person: str | None = None, p: Principal = Depends(principal_dep)):
        """The signature under my replies. With `person`, a suggestion can come from the mailbox that answers that person (only when I may
        see them)."""
        with tx(request) as conn:
            pid = None
            if person and p.can("contacts.read"):
                try:
                    pid = str(_visible_person(conn, p, person)["id"])
                except HTTPException:
                    pid = None
            return sig.for_user(conn, user_id=p.user_id, email=p.email, person_id=pid)

    @app.put("/api/me/email-signature")
    def save_my_signature(body: SignatureIn, request: Request, p: Principal = Depends(principal_dep)):
        with tx(request) as conn:
            try:
                out = sig.save(conn, user_id=p.user_id, actor=p.email, text=body.text, html=body.html)
            except sig.SignatureError as exc:
                raise HTTPException(400, str(exc)) from exc
            return out

    @app.post("/api/me/email-signature/preview")
    def preview_my_signature(body: SignaturePreviewIn, p: Principal = Depends(principal_dep)):
        """What a pasted signature would look like once cleaned (nothing is saved): the safe HTML, its plain-text reading, and how many pictures
        could not be kept."""
        try:
            return sig.rich_from_html(body.html)
        except sig.SignatureError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/me/email-signature/import-gmail")
    def import_my_signature_from_gmail(request: Request, p: Principal = Depends(principal_dep)):
        """Read the signature of MY connected Gmail account (read-only towards Google) and keep it as my signature, replacing the one I had."""
        s = request.app.state.settings
        if not (vault.available(s.vault_key) and s.google_client_id and s.google_client_secret):
            raise HTTPException(409, "Gmail is not set up on the server yet. An administrator needs to finish the Google setup.")
        with tx(request) as conn:                                  # the token is read first and nothing stays open while Google answers
            try:
                token = store.secret(conn, s.vault_key, p.user_id, "gmail")
            except vault.VaultError:
                raise HTTPException(409, "We could not open the saved Gmail permission. Connect Gmail again.") from None
        if not token:
            raise HTTPException(409, "Connect your work Gmail first (see My connections), then import the signature.")
        try:
            with gmail_api.GmailClient(token, s.google_client_id, s.google_client_secret) as gm:
                address, raw = gm.send_as_signature()
        except (gmail_api.GmailAuthError, gmail_api.GmailConfigError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except gmail_api.GmailError as exc:
            raise HTTPException(503, "Google could not be reached just now. Try again in a minute.") from exc
        with tx(request) as conn:
            res = sig.import_from_gmail(conn, user_id=p.user_id, actor=p.email, client=_Fixed(address, raw)) or {}
            return {**sig.for_user(conn, user_id=p.user_id, email=p.email), **res}

    @app.get("/api/people/{pid}/email-thread")
    def email_thread(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        with tx(request) as conn:
            person = _person(conn, p, pid)
            rp.settle_person(conn, str(person["id"]))          # words cleaned by the latest version, every email on the timeline (local)
            out = _thread(conn, request, p, person)
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=str(person["id"]), detail={"view": "email_thread", "count": len(out["items"])})
            return out

    @app.post("/api/people/{pid}/email-thread/refresh")
    def email_thread_refresh(pid: str, request: Request, p: Principal = Depends(require("contacts.read"))):
        """Read the person's Lemlist conversation again, then answer with the thread. Without a Lemlist key it only answers with the thread."""
        with tx(request) as conn:
            person = _person(conn, p, pid)
            client = rp.make_client(request.app.state.settings)
            result = rp.refresh_person(conn, client, str(person["id"])) if client is not None else {"refreshed": False}
            rp.settle_person(conn, str(person["id"]))
            out = _thread(conn, request, p, person)
            audit(conn, actor=p.email, operation="pii_read", record_type="person", record_id=str(person["id"]), detail={"view": "email_thread_refresh", "count": len(out["items"])})
            return {**out, "refresh": result}

    @app.post("/api/people/{pid}/email-thread/reply")
    def email_thread_reply(pid: str, body: ReplyIn, request: Request, p: Principal = Depends(require("contacts.read"))):
        if not p.can(SEND_PERMISSION):
            raise HTTPException(403, "Your role cannot send emails from here.")
        err: rp.ReplyError | None = None
        with tx(request) as conn:
            person = _person(conn, p, pid)
            try:
                sent = rp.send_reply(conn, actor=p.email, user_id=p.user_id, person=person, activity_id=body.activity_id, text=body.text, key=body.key,
                                     settings=request.app.state.settings)
            except rp.ReplyError as exc:
                conn.commit()                                     # keep the audit line of a refusal or a failed attempt
                err = exc
            else:
                return {**sent, "thread": _thread(conn, request, p, person)}
        raise HTTPException(err.status, err.message)
