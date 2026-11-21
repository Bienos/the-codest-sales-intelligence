"""Authentication. OIDC (RS256 via JWKS) in staging/production; signed dev tokens only when env != production."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Callable

import jwt
from fastapi import Depends, HTTPException, Request

from ..access import Principal, principal_from_row
from ..config import Settings
from ..db import q1


SESSION_HOURS = 12


def session_key(s: Settings) -> str | None:
    secret = s.session_secret or s.dev_auth_secret
    return hmac.new(secret.encode(), b"ri-session-v1", hashlib.sha256).hexdigest() if secret else None


def issue_session(s: Settings, email: str, hours: int = SESSION_HOURS) -> tuple[str, int]:
    """Our own signed session, so a person is not logged out every hour when the Google ID token expires. Still checked against the users table on every request."""
    key = session_key(s)
    if not key:
        raise HTTPException(503, "sessions are not configured: set RI_SESSION_SECRET")
    exp = int(time.time()) + hours * 3600
    return jwt.encode({"email": email, "typ": "session", "exp": exp}, key, algorithm="HS256"), exp


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

    def _session_email(self, token: str) -> str | None:
        key = session_key(self.s)
        if not key:
            return None
        try:
            claims = jwt.decode(token, key, algorithms=["HS256"], options={"require": ["exp", "email", "typ"]})
        except jwt.PyJWTError:
            return None
        return str(claims["email"]).lower() if claims.get("typ") == "session" else None

    def email_for(self, token: str) -> str:
        sess = self._session_email(token)
        if sess:
            dom = self.s.allowed_email_domain
            if dom and not sess.endswith("@" + dom):
                raise HTTPException(403, "email domain not allowed")
            return sess
        try:
            if self.s.auth_mode == "dev":
                if self.s.env == "production" or not self.s.dev_auth_secret:
                    raise HTTPException(401, "dev auth unavailable")
                claims = jwt.decode(token, self.s.dev_auth_secret, algorithms=["HS256"], options={"require": ["exp", "email"]})
            else:
                issuers = [i.strip() for i in (self.s.oidc_issuer or "").split(",") if i.strip()]
                claims = jwt.decode(token, self._key(token), algorithms=["RS256"], audience=self.s.oidc_audience, issuer=issuers or None,
                                    options={"require": ["exp", "iss", "aud", "email"]})
                if claims.get("email_verified") is False:
                    raise HTTPException(401, "email not verified by the identity provider")
        except jwt.PyJWTError as exc:
            raise HTTPException(401, f"invalid token: {type(exc).__name__}") from exc
        email = str(claims["email"]).lower()
        dom = self.s.allowed_email_domain
        if dom and not email.endswith("@" + dom):
            raise HTTPException(403, "email domain not allowed")
        return email


def make_dev_token(secret: str, email: str, ttl: int = 3600) -> str:
    return jwt.encode({"email": email, "exp": int(time.time()) + ttl}, secret, algorithm="HS256")


def principal_dep(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    email = request.app.state.verifier.email_for(auth[7:])
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
