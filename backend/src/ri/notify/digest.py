"""Daily digest + urgent alerts (spec 09 §8, 13 §7). Dedup key = recipient+hypothesis+material fingerprint+period.
Content is deliberately minimal and safe for chat: NO contact details, NO CRM notes, NO personal data - just a signed deep link."""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from ..access import principal_from_row
from ..db import Conn, jb, q1, qa
from ..readmodel import today
from ..sheets.client import ConnectorBlocked


class Sink(Protocol):
    channel: str
    def send(self, recipient: str, text: str) -> None: ...


class ConsoleSink:
    channel = "console"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, recipient, text):
        self.sent.append((recipient, text))


class SlackWebhookSink:
    channel = "slack"

    def __init__(self, url: str | None, http: httpx.Client | None = None):
        if not url:
            raise ConnectorBlocked("SLACK_WEBHOOK_URL not set: Slack notifications are blocked (digest stays available in-app)")
        self.url, self.http = url, http or httpx.Client(timeout=10)

    def send(self, recipient, text):
        r = self.http.post(self.url, json={"text": text})
        r.raise_for_status()


def sign_link(secret: str, path: str, ttl_s: int = 7 * 86400, now: float | None = None) -> str:
    exp = int((now or time.time()) + ttl_s)
    sig = hmac.new(secret.encode(), f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{path}?exp={exp}&sig={sig}"


def verify_link(secret: str, path: str, exp: int, sig: str, now: float | None = None) -> bool:
    if exp < (now or time.time()):
        return False
    good = hmac.new(secret.encode(), f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)


def _line(c: dict[str, Any], base: str, secret: str) -> str:
    link = base + sign_link(secret, f"/opportunities/{c['hypothesis_id']}")
    a = c["next_action"] or {}
    due = a.get("due_at") or a.get("trigger_condition") or ""
    tag = "EXISTING" if c["account"]["in_master_list"] else "NEW"
    return f"[{c['priority'].upper()}][{tag}] {c['account']['name']} - {c['initiative']['title']} | route: {c['route']['type']} ({c['route']['access_status']}) | next: {a.get('action_type', 'n/a')} {str(due)[:10]} | {link}"


def send_digest(conn: Conn, sink: Sink, *, base_url: str, secret: str, period: str | None = None, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    period = period or now.strftime("%Y-%m-%d")
    stats = {"sent": 0, "skipped_duplicate": 0, "empty": 0}
    for u in qa(conn, "SELECT * FROM users WHERE active AND role IN ('bdm','sales_manager')"):
        p = principal_from_row(u)
        t = today(conn, p, now)
        fresh = []
        for sec in t["sections"].values():
            for c in sec:
                key = f"{u['email']}|{c['hypothesis_id']}|{c['fingerprint']}|digest"
                if q1(conn, "SELECT 1 FROM notifications_sent WHERE dedup_key=%s", (key,)):
                    stats["skipped_duplicate"] += 1
                    continue
                fresh.append((key, c))
        if not fresh:
            stats["empty"] += 1
            continue
        text = f"Revenue Intelligence - {period}: {len(fresh)} material change(s)\n" + "\n".join(_line(c, base_url, secret) for _, c in fresh)
        if t["coverage"]["unhealthy"]:
            text += f"\nCoverage warning: {len(t['coverage']['unhealthy'])} source(s) degraded - absence of items is not confirmation."
        try:
            sink.send(u["email"], text)
            status, err = "sent", None
        except Exception as exc:  # noqa: BLE001
            status, err = ("blocked" if isinstance(exc, ConnectorBlocked) else "failed"), str(exc)[:200]
        for key, c in fresh:
            conn.execute("INSERT INTO notifications_sent(dedup_key, recipient, kind, channel, hypothesis_id, payload_summary, status, error) VALUES (%s,%s,'daily_digest',%s,%s,%s,%s,%s) "
                         "ON CONFLICT (dedup_key) DO NOTHING", (key, u["email"], sink.channel, c["hypothesis_id"], jb({"priority": c["priority"], "account": c["account"]["name"]}), status, err))
        if status == "sent":
            stats["sent"] += 1
    return stats


def send_urgent(conn: Conn, sink: Sink, hyp: dict[str, Any], *, recipient: str, base_url: str, secret: str, fp: str) -> bool:
    """Critical tender alert: immediate, de-duplicated, and subsequently excluded from the daily digest via the same key space."""
    key = f"{recipient}|{hyp['hypothesis_id']}|{fp}|digest"            # same key as the digest => digest will not repeat it
    if q1(conn, "SELECT 1 FROM notifications_sent WHERE dedup_key=%s", (key,)):
        return False
    sink.send(recipient, "URGENT " + _line(hyp, base_url, secret))
    conn.execute("INSERT INTO notifications_sent(dedup_key, recipient, kind, channel, hypothesis_id, status) VALUES (%s,%s,'urgent_tender',%s,%s,'sent')", (key, recipient, sink.channel, hyp["hypothesis_id"]))
    return True
