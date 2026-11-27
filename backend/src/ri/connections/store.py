"""Personal connections: the ONE place that reads and writes them, so credentials are sealed in a single spot.

A connection belongs to one user and one provider (gmail, lemlist, bluedot). What a sync brings goes into `contact_interactions`
(the shared database stays the master): each row names its owner and says who may see the detail:
  * 'owner' - only the person whose connection it came from (an email's subject and snippet, unless they chose to share them);
  * 'team'  - everyone (Lemlist campaign activity, meeting summaries). Others always see THAT a contact happened, with whom and when."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import vault
from ..db import Conn, jb, q1, qa
from ..jobs.queue import enqueue

PROVIDERS = ("gmail", "lemlist", "bluedot")
CHANNEL = {"gmail": "email", "lemlist": "lemlist", "bluedot": "meeting"}
SYNC_JOB = {"gmail": "gmail_sync", "lemlist": "lemlist_user_sync"}          # Bluedot pushes by webhook: nothing to poll
SYNC_EVERY_S = {"gmail": 3600, "lemlist": 3600}
DEFAULT_SETTINGS = {"share_email_gist": False}                             # an email's subject and snippet stay with their owner unless they share them
SECRET_FIELDS = ("secret_enc", "webhook_token_hash")


def aad(user_id: str, provider: str) -> str:
    return f"{user_id}:{provider}"


def get(conn: Conn, user_id: str, provider: str) -> dict[str, Any] | None:
    """The full row, secrets included: for sync jobs and this module only. API responses use `public`."""
    return q1(conn, "SELECT * FROM user_connections WHERE user_id=%s AND provider=%s", (user_id, provider))


def public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = {k: v for k, v in row.items() if k not in SECRET_FIELDS and k not in ("id", "cursor")}
    out["settings"] = {**DEFAULT_SETTINGS, **(row.get("settings") or {})}
    out["has_webhook"] = bool(row.get("webhook_token_hash"))
    return out


def list_public(conn: Conn, user_id: str) -> list[dict[str, Any]]:
    rows = {r["provider"]: r for r in qa(conn, "SELECT * FROM user_connections WHERE user_id=%s", (user_id,))}
    counts = {r["channel"]: int(r["n"]) for r in qa(conn, "SELECT channel, count(*) AS n FROM contact_interactions WHERE user_id=%s GROUP BY 1", (user_id,))}
    out = []
    for p in PROVIDERS:
        row = public(rows.get(p)) or {"provider": p, "status": "disconnected", "settings": dict(DEFAULT_SETTINGS), "has_webhook": False}
        row["stored"] = counts.get(CHANNEL[p], 0)
        out.append(row)
    return out


def connect(conn: Conn, vault_key: str | None, user_id: str, provider: str, *, secret: str | None = None, label: str | None = None, scopes: str | None = None,
            settings: dict[str, Any] | None = None, webhook_token: str | None = None) -> dict[str, Any]:
    """Create or renew a connection. The secret is sealed here; raises vault.VaultError when no key is configured."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider}")
    sealed = vault.seal(vault_key, secret, aad(user_id, provider)) if secret else None
    token_hash = hashlib.sha256(webhook_token.encode()).hexdigest() if webhook_token else None
    row = conn.execute(
        "INSERT INTO user_connections(user_id, provider, status, account_label, secret_enc, scopes, settings, webhook_token_hash) "
        "VALUES (%s,%s,'connected',%s,%s,%s,%s,%s) "
        "ON CONFLICT (user_id, provider) DO UPDATE SET status='connected', account_label=coalesce(EXCLUDED.account_label, user_connections.account_label), "
        "secret_enc=coalesce(EXCLUDED.secret_enc, user_connections.secret_enc), scopes=coalesce(EXCLUDED.scopes, user_connections.scopes), "
        "settings=user_connections.settings || EXCLUDED.settings, webhook_token_hash=coalesce(EXCLUDED.webhook_token_hash, user_connections.webhook_token_hash), "
        "last_error=NULL, disconnected_at=NULL, connected_at=now() RETURNING *",
        (user_id, provider, label, sealed, scopes, jb(settings or {}), token_hash)).fetchone()
    return public(row)  # type: ignore[return-value]


def new_webhook_token() -> str:
    return "bd_" + secrets.token_urlsafe(24)


def by_webhook_token(conn: Conn, token: str) -> dict[str, Any] | None:
    return q1(conn, "SELECT * FROM user_connections WHERE webhook_token_hash=%s AND status='connected'", (hashlib.sha256(token.encode()).hexdigest(),))


def secret(conn: Conn, vault_key: str | None, user_id: str, provider: str) -> str | None:
    row = get(conn, user_id, provider)
    if not row or row["status"] == "disconnected" or not row.get("secret_enc"):
        return None
    return vault.unseal(vault_key, row["secret_enc"], aad(user_id, provider))


def update_settings(conn: Conn, user_id: str, provider: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute("UPDATE user_connections SET settings = settings || %s WHERE user_id=%s AND provider=%s RETURNING *", (jb(patch), user_id, provider)).fetchone()
    return public(row)


def record_sync(conn: Conn, user_id: str, provider: str, now: datetime, *, cursor: dict[str, Any] | None = None, error: str | None = None) -> None:
    """Remember where the sync stopped. An error marks the connection 'needs_attention' (a person must look), a good run clears it."""
    conn.execute("UPDATE user_connections SET last_sync_at=%s, cursor=coalesce(%s::jsonb, cursor), last_error=%s, "
                 "status=CASE WHEN %s::text IS NULL THEN 'connected' ELSE 'needs_attention' END WHERE user_id=%s AND provider=%s AND status<>'disconnected'",
                 (now, jb(cursor) if cursor is not None else None, (error or "")[:300] or None, error, user_id, provider))


def disconnect(conn: Conn, user_id: str, provider: str) -> None:
    """Forget the credentials at once, so nothing is read from this account any more. Everything the connection already brought STAYS in the
    database (the user's rule: nothing is ever deleted)."""
    conn.execute("UPDATE user_connections SET status='disconnected', secret_enc=NULL, webhook_token_hash=NULL, disconnected_at=now(), last_error=NULL, cursor='{}'::jsonb "
                 "WHERE user_id=%s AND provider=%s", (user_id, provider))


def email_visibility(settings: dict[str, Any] | None) -> str:
    return "team" if (settings or {}).get("share_email_gist") else "owner"


def upsert_interaction(conn: Conn, *, user_id: str, channel: str, external_id: str, occurred_at: datetime, visibility: str, person_id: str | None = None,
                       organization_id: str | None = None, direction: str | None = None, title: str | None = None, gist: str | None = None,
                       meta: dict[str, Any] | None = None) -> bool:
    """True when the row is new. Repeating a sync never duplicates; a later sync may refresh the text and the match. A Lemlist row that the
    shared Lemlist sync has completed with the email's words (its meta names the activity, see ri.outbound.email_timeline) is left as it is."""
    row = conn.execute(
        "INSERT INTO contact_interactions(user_id, channel, external_id, person_id, organization_id, direction, occurred_at, title, gist, visibility, meta) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, channel, external_id) DO UPDATE SET "
        "person_id=coalesce(EXCLUDED.person_id, contact_interactions.person_id), organization_id=coalesce(EXCLUDED.organization_id, contact_interactions.organization_id), "
        "title=EXCLUDED.title, gist=EXCLUDED.gist, visibility=EXCLUDED.visibility WHERE NOT (contact_interactions.meta ? 'activity_id') RETURNING (xmax = 0) AS inserted",
        (user_id, channel, external_id, person_id, organization_id, direction, occurred_at, (title or "")[:300] or None, (gist or "")[:600] or None,
         visibility, jb(meta or {}))).fetchone()
    return bool(row and row["inserted"])


def schedule_syncs(conn: Conn, now: datetime | None = None, limit: int = 40) -> int:
    """Queue one sync job per due, connected Gmail or Lemlist connection (the background lane runs them). Idempotent per connection per hour."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for provider, job_kind in SYNC_JOB.items():
        due = now - timedelta(seconds=SYNC_EVERY_S[provider])
        rows = qa(conn, "SELECT c.user_id FROM user_connections c JOIN users u ON u.id=c.user_id AND u.active "
                        "WHERE c.provider=%s AND c.status IN ('connected','needs_attention') AND c.secret_enc IS NOT NULL "
                        "AND (c.last_sync_at IS NULL OR c.last_sync_at < %s) "
                        "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind=%s AND j.payload->>'user_id' = c.user_id::text "      # an earlier sync still waiting or running: never pile up
                        "AND j.status IN ('scheduled','retryable_failed','claimed','fetching','parsing','verifying','committing')) "
                        "ORDER BY c.last_sync_at NULLS FIRST LIMIT %s", (provider, due, job_kind, limit))
        for r in rows:
            uid = str(r["user_id"])
            _, created = enqueue(conn, job_kind, {"user_id": uid}, idempotency_key=f"usersync:{provider}:{uid}:{now:%Y%m%d%H}", max_attempts=2, correlation_id="sched-connections")
            n += int(created)
    return n
