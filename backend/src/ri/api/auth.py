"""Authentication. OIDC (RS256 via JWKS) in staging/production; signed dev tokens only when env != production."""
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request

from ..access import Principal, principal_from_row
from ..config import Settings
from ..db import q1

SESSION_HOURS = 12
MAX_SESSION_DAYS = 7          # a session can be renewed, but never beyond this long after the person last signed in with Google
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


def session_key(s: Settings) -> str | None:
    secret = s.session_secret or s.dev_auth_secret
    return hmac.new(secret.encode(), b"ri-session-v1", hashlib.sha256).hexdigest() if secret else None


def issue_session(s: Settings, email: str, hours: int = SESSION_HOURS, auth_time: int | None = None) -> tuple[str, int]:
    """Our own signed session, so a person is not logged out every hour when the Google ID token expires. Still checked against the users table on every request.
    `auth_time` is when the person last proved who they are with Google; renewals carry it forward and stop after MAX_SESSION_DAYS."""
    key = session_key(s)
    if not key:
        raise HTTPException(503, "sessions are not configured: set RI_SESSION_SECRET")
    now = int(time.time())
    auth_time = auth_time or now
    if now - auth_time > MAX_SESSION_DAYS * 86400:
        raise HTTPException(401, "please sign in again")
    exp = min(now + hours * 3600, auth_time + MAX_SESSION_DAYS * 86400)
    return jwt.encode({"email": email, "typ": "session", "exp": exp, "auth_time": auth_time}, key, algorithm="HS256"), exp


class TokenVerifier:
    def __init__(self, settings: Settings, key_resolver: Callable[[str], Any] | None = None):
        self.s = settings
        self._resolver = key_resolver
        self._jwks = None

    def _key(self, token: str):
        if self._resolver:
            return self._resolver(token)
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(self.s.oidc_jwks_url)       # type: ignore[arg-type]
        return self._jwks.get_signing_key_from_jwt(token).key

    def session_claims(self, token: str) -> dict[str, Any] | None:
        key = session_key(self.s)
        if not key:
            return None
        try:
            claims = jwt.decode(token, key, algorithms=["HS256"], options={"require": ["exp", "email", "typ"]})
        except jwt.PyJWTError:
            return None
        if claims.get("typ") != "session":
            return None
        auth_time = int(claims.get("auth_time") or 0)
        if not auth_time or time.time() - auth_time > MAX_SESSION_DAYS * 86400:      # sessions from before the cap, or past it, end here
            return None
        return claims

    def _session_email(self, token: str) -> str | None:
        c = self.session_claims(token)
        return str(c["email"]).lower() if c else None

    def email_for(self, token: str) -> str:
        sess = self._session_email(token)
        if sess:
            dom = self.s.allowed_email_domain
            if dom and not sess.endswith("@" + dom):
                raise HTTPException(403, "email domain not allowed")
            return sess
        try:
            if self.s.auth_mode == "dev":
                if not self.s.dev_auth_allowed or not self.s.dev_auth_secret:
                    raise HTTPException(401, "dev auth unavailable")
                claims = jwt.decode(token, self.s.dev_auth_secret, algorithms=["HS256"], options={"require": ["exp", "email"]})
            else:
                issuers = [i.strip() for i in (self.s.oidc_issuer or "").split(",") if i.strip()]
                claims = jwt.decode(token, self._key(token), algorithms=["RS256"], audience=self.s.oidc_audience, issuer=issuers or None,
                                    options={"require": ["exp", "iss", "aud", "email"]})
                google = claims.get("iss") in GOOGLE_ISSUERS
                # Google always states whether it verified the address; other providers may omit it, but must not deny it
                if claims.get("email_verified") is False or (google and claims.get("email_verified") is not True):
                    raise HTTPException(401, "email not verified by the identity provider")
                dom = self.s.allowed_email_domain
                if google and dom and self.s.google_require_workspace and str(claims.get("hd") or "").lower() != dom:
                    # only accounts managed by the company's Google Workspace; a personal Google account registered on a work address is refused
                    raise HTTPException(403, "sign in with your company Google Workspace account")
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "invalid or expired sign-in, please sign in again") from exc
        email = str(claims["email"]).lower()
        dom = self.s.allowed_email_domain
        if dom and not email.endswith("@" + dom):
            raise HTTPException(403, "email domain not allowed")
        return email


def make_dev_token(secret: str, email: str, ttl: int = 3600) -> str:
    return jwt.encode({"email": email, "exp": int(time.time()) + ttl}, secret, algorithm="HS256")


INTERNAL_PREFIX = "ri-internal."
INTERNAL_TTL_SECONDS = 300


def issue_internal(key: bytes, email: str, via: str) -> str:
    """A short-lived token the assistant tools use to read the app's own API AS an already-authenticated person (same role, same row scope).
    Signed with a key that exists only in this process's memory: it cannot be minted outside the running server and never leaves it."""
    tok = jwt.encode({"email": email, "typ": "internal", "via": via, "exp": int(time.time()) + INTERNAL_TTL_SECONDS}, key, algorithm="HS256")
    return INTERNAL_PREFIX + tok


def internal_email(request: Request, token: str) -> str | None:
    key = getattr(request.app.state, "internal_key", None)
    if not key or not token.startswith(INTERNAL_PREFIX):
        return None
    try:
        claims = jwt.decode(token[len(INTERNAL_PREFIX):], key, algorithms=["HS256"], options={"require": ["exp", "email", "typ"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired internal token") from exc
    if claims.get("typ") != "internal":
        raise HTTPException(401, "invalid internal token")
    if request.method != "GET":          # the assistants only ever read: no change, no session swap, no export through this door
        raise HTTPException(403, "the assistant can only read")
    return str(claims["email"]).lower()


def principal_dep(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    email = internal_email(request, auth[7:]) or request.app.state.verifier.email_for(auth[7:])
    with request.app.state.db.tx() as conn:
        u = q1(conn, "SELECT * FROM users WHERE lower(email)=%s AND active", (email,))
        if not u:
            conn.execute("INSERT INTO audit_events(actor, operation, record_type, detail) VALUES (%s,'login_denied_unknown_user','user','{}')", (email,))
            raise HTTPException(403, "no active account for this identity")
        return principal_from_row(u)


def require(perm: str) -> Callable[[Principal], Principal]:
    def dep(p: Principal = Depends(principal_dep)) -> Principal:
        if not p.can(perm):
            raise HTTPException(403, f"missing permission {perm}")
        return p
    return dep
