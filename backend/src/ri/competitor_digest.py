"""Monday competitor digest: what the week's collection found and is waiting for review, what people confirmed, and how many deals named a
competitor. It goes to the Slack channel (SLACK_WEBHOOK_URL) and, when an email sender is configured (RI_SMTP_*), by email to everyone who may
review moves (competitors.manage). It holds public facts about competitors and counts only: never a deal name, an account or a person.
Each recipient gets it once per ISO week (notifications_sent); a quiet week sends nothing."""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from .access import principal_from_row
from .config import read_secret
from .db import Conn, jb, q1, qa
from .sheets.client import ConnectorBlocked

SEND_FROM_HOUR_UTC = 7            # Monday 07:00 UTC is 08:00 or 09:00 in Warsaw
MAX_LISTED = 8
TYPE_LABEL = {"ma_buy": "acquisition", "ma_sold": "sold", "funding": "funding", "leadership": "leadership", "offer": "new offer", "positioning": "positioning",
              "pricing": "pricing", "market": "new market", "client": "new client", "partnership": "partnership", "hiring": "hiring", "layoffs": "layoffs",
              "award": "award", "content": "content", "other": "other"}


class SmtpSink:
    """Plain-text email through an authenticated SMTP server (for Google Workspace: smtp.gmail.com:587 with an app password, or smtp-relay.gmail.com)."""
    channel = "email"

    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str, subject: str = "Competitor digest"):
        self.host, self.port, self.user, self.password, self.sender, self.subject = host, port, user, password, sender, subject

    def send(self, recipient: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, recipient, self.subject
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            if self.user and self.password:
                s.login(self.user, self.password)
            s.send_message(msg)


def email_sink() -> SmtpSink | None:
    host, sender = os.environ.get("RI_SMTP_HOST"), os.environ.get("RI_SMTP_FROM")
    if not host or not sender:
        return None
    return SmtpSink(host, int(os.environ.get("RI_SMTP_PORT") or 587), os.environ.get("RI_SMTP_USER") or None, read_secret("RI_SMTP_PASSWORD"), sender)


def build(conn: Conn, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=7)
    waiting = qa(conn, "SELECT c.name, m.move_type, m.headline, m.impact FROM competitor_moves m JOIN competitors c ON c.key=m.competitor_key "
                       "WHERE m.status='candidate' ORDER BY m.impact DESC, m.created_at DESC")
    confirmed = qa(conn, "SELECT c.name, m.move_type, m.headline, m.so_what, m.impact FROM competitor_moves m JOIN competitors c ON c.key=m.competitor_key "
                         "WHERE m.status='confirmed' AND m.reviewed_by <> 'seed' AND m.reviewed_at >= %s ORDER BY m.impact DESC, m.happened_on DESC", (since,))
    deals = qa(conn, "SELECT c.name, count(*) AS n FROM deal_competitors d JOIN competitors c ON c.key=d.competitor_key WHERE d.noted_at >= %s "
                     "GROUP BY c.name ORDER BY n DESC, c.name", (since,))
    failing = [r["check_name"] for r in qa(conn, "SELECT check_name FROM competitor_watch GROUP BY check_name HAVING bool_and(last_error IS NOT NULL) AND count(*) > 0")]
    return {"week": now.strftime("%G-W%V"), "waiting": waiting, "confirmed": confirmed, "deals": deals, "failing": failing,
            "quiet": not (waiting or confirmed or deals)}


def _move(m: dict[str, Any]) -> str:
    return f"- {m['name']} ({TYPE_LABEL.get(m['move_type'], m['move_type'])}): {m['headline']}"


def render(d: dict[str, Any], base_url: str) -> str:
    out = [f"Competitor digest, week {d['week']}", ""]
    if d["confirmed"]:
        out.append(f"Confirmed this week ({len(d['confirmed'])}):")
        for m in d["confirmed"][:MAX_LISTED]:
            out.append(_move(m) + (f"\n  So what: {m['so_what']}" if m.get("so_what") else ""))
        if len(d["confirmed"]) > MAX_LISTED:
            out.append(f"- and {len(d['confirmed']) - MAX_LISTED} more")
        out += [f"All moves: {base_url}/competitors/moves", ""]
    if d["waiting"]:
        out.append(f"Waiting for review ({len(d['waiting'])}). Nothing reaches the pages until someone confirms it:")
        out += [_move(m) for m in d["waiting"][:MAX_LISTED]]
        if len(d["waiting"]) > MAX_LISTED:
            out.append(f"- and {len(d['waiting']) - MAX_LISTED} more")
        out += [f"Review them (about 5 minutes): {base_url}/competitors/review", ""]
    if d["deals"]:
        out.append("Named on deals this week: " + ", ".join(f"{r['name']} ({r['n']})" for r in d["deals"]))
        out += [f"Head-to-head: {base_url}/competitors/head-to-head", ""]
    if d["failing"]:
        out.append("Automatic checks failing for every firm: " + ", ".join(d["failing"]) + ". Results may be missing; see the Review tab.")
    return "\n".join(out).rstrip() + "\n"


def recipients(conn: Conn) -> list[str]:
    return sorted(u["email"] for u in qa(conn, "SELECT * FROM users WHERE active") if principal_from_row(u).can("competitors.manage"))


def _deliver(conn: Conn, sink: Any, recipient: str, key: str, text: str, d: dict[str, Any]) -> str:
    if q1(conn, "SELECT 1 FROM notifications_sent WHERE dedup_key=%s", (key,)):
        return "duplicate"
    try:
        sink.send(recipient, text)
        status, err = "sent", None
    except Exception as exc:  # noqa: BLE001  (recorded, and retried by nobody: one failed week never floods the next)
        status, err = ("blocked" if isinstance(exc, ConnectorBlocked) else "failed"), f"{type(exc).__name__}: {exc}"[:200]
    conn.execute("INSERT INTO notifications_sent(dedup_key, recipient, kind, channel, payload_summary, status, error) VALUES (%s,%s,'competitor_digest',%s,%s,%s,%s) "
                 "ON CONFLICT (dedup_key) DO NOTHING",
                 (key, recipient, sink.channel, jb({"week": d["week"], "waiting": len(d["waiting"]), "confirmed": len(d["confirmed"])}), status, err))
    return status


def weekly_step(conn: Conn, *, base_url: str | None, slack: Any = None, email: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Called on every scheduler tick; does something only on Monday from 07:00 UTC, once per recipient per week."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() != 0 or now.hour < SEND_FROM_HOUR_UTC or not base_url or not (slack or email):
        return {}
    if not q1(conn, "SELECT 1 FROM competitors WHERE NOT is_us LIMIT 1"):
        return {}
    d = build(conn, now)
    if d["quiet"]:
        return {"competitor_digest": "quiet"}
    text = render(d, base_url.rstrip("/"))
    out: dict[str, int] = {}
    targets = ([(slack, "channel")] if slack else []) + ([(email, r) for r in recipients(conn)] if email else [])
    for sink, who in targets:
        status = _deliver(conn, sink, who, f"competitor_digest|{d['week']}|{sink.channel}|{who}", text, d)
        out[status] = out.get(status, 0) + 1
    return {f"competitor_digest_{k}": v for k, v in out.items()}
