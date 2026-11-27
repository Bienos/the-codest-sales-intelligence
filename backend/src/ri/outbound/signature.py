"""The signature (footer) under a reply sent from the app.

What Lemlist does (developer.lemlist.com, read on 27 Nov 2026):
  * POST /inbox/email (https://developer.lemlist.com/api-reference/endpoints/inbox/send-email.md) sends `message` (HTML) as given. It has no
    signature field and says nothing about adding one;
  * a Lemlist user's signature (GET/PATCH /users/{userId}/signature) is "inserted by {{signature}} in emails sent as HTML": only where a
    template carries that variable (help.lemlist.com/en/articles/4452781). An answer written here carries no such variable.
So Lemlist adds no signature to a reply sent from here (LEMLIST_ADDS_SIGNATURE). Each person who uses the app keeps one plain-text signature
(table user_email_signatures, migration 0059), and send_reply appends it once, escaped, under the words they typed.

Until someone saves one, the app suggests it: the sign-off at the bottom of the campaign emails sent from the mailbox that answers (or from
the person's own mailbox), when it can be read reliably (the same closing block in two emails, or a closing line followed by the sender's
name), else their name, "The Codest" and the website. The suggestion is worked out the same way when the box is shown and when the email is
sent, so what the preview shows is what goes out."""
from __future__ import annotations

import html as htmllib
import re
import unicodedata
from collections import Counter
from typing import Any

from ..audit import audit
from ..db import Conn, q1, qa

LEMLIST_ADDS_SIGNATURE = False
MAX_SIGNATURE = 600
MAX_LINES = 10
COMPANY = "The Codest"
WEBSITE = "thecodest.co"
SAMPLE = 20                     # campaign emails of one mailbox read to find its sign-off
TAIL = 8                        # last lines of an email looked at

# A closing line: "Best regards," "Kind regards" "Thanks," "Mit freundlichen Grüßen" "Pozdrawiam" ... in our buyers' languages.
_CLOSING = re.compile(
    r"^(?:best(?: regards| wishes)?|kind regards|warm regards|warmest regards|regards|many thanks|thanks(?: again| a lot)?|thank you|cheers|all the best|"
    r"sincerely|yours(?: sincerely| truly)?|br|"
    r"mit freundlichen gr(?:ü|ue)(?:ß|ss)en|freundliche gr(?:ü|ue)(?:ß|ss)e|viele gr(?:ü|ue)(?:ß|ss)e|beste gr(?:ü|ue)(?:ß|ss)e|liebe gr(?:ü|ue)(?:ß|ss)e|lg|mfg|"
    r"pozdrawiam(?: serdecznie)?|z powa(?:ż|z)aniem|serdecznie pozdrawiam|"
    r"med v(?:ä|a)nliga h(?:ä|a)lsningar|v(?:ä|a)nliga h(?:ä|a)lsningar|mvh|med venlig hilsen|venlig hilsen|med vennlig hilsen|vennlig hilsen|hilsen|"
    r"cordialement|bien (?:à|a) vous|met vriendelijke groet(?:en)?|vriendelijke groet(?:en)?|groeten)[\s,.!–-]*$", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ﻿]")


class SignatureError(ValueError):
    pass


def normalise(text: str | None) -> str:
    """Plain text as it is kept: no control characters, no trailing spaces, at most one blank line in a row, at most MAX_LINES lines."""
    s = unicodedata.normalize("NFC", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    s = _CTRL.sub("", s.replace("\t", " "))
    lines = [ln.rstrip() for ln in s.split("\n")]
    out: list[str] = []
    for ln in lines:
        if not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def validate(text: str | None) -> str:
    s = normalise(text)
    if len(s) > MAX_SIGNATURE:
        raise SignatureError(f"The signature is too long. Keep it under {MAX_SIGNATURE} characters.")
    if s.count("\n") + 1 > MAX_LINES and s:
        raise SignatureError(f"The signature has too many lines. Keep it to {MAX_LINES} lines.")
    if re.search(r"<\s*(?:img|script|style|iframe)\b", s, re.I):
        raise SignatureError("The signature is plain text: pictures and code cannot be added.")
    return s


def default(name: str | None) -> str:
    n = (name or "").strip()
    return "\n".join(x for x in (n, COMPANY, WEBSITE) if x)


def _key(s: str) -> str:
    return re.sub(r"[^\w]+", " ", s.casefold()).strip()


def _mentions(line: str, names: list[str]) -> str | None:
    """'full' when the line holds a full name, 'first' when only a first name, else None."""
    k = f" {_key(line)} "
    for n in names:
        nk = _key(n)
        if nk and " " in nk and f" {nk} " in k:
            return "full"
    for n in names:
        first = _key(n).split(" ")[0] if _key(n) else ""
        if len(first) >= 2 and f" {first} " in k:
            return "first"
    return None


def _plausible(block: list[str], names: list[str]) -> bool:
    """A sign-off block: short lines, the first one naming the sender (the full name, or the first name with more lines under it)."""
    if not block or len(block) > 6 or any(len(x) > 90 for x in block) or len("\n".join(block)) > MAX_SIGNATURE:
        return False
    if any(_CLOSING.match(x.strip()) for x in block) or block[0].rstrip().endswith(("?", ":")):
        return False
    m = _mentions(block[0], names)
    return m == "full" or (m == "first" and len(block) >= 2)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


def detect(texts: list[str], names: list[str]) -> str | None:
    """The sign-off at the bottom of these campaign emails (newest first), or None when it cannot be read reliably:
      1. the same closing block at the end of two or more emails (Lemlist's {{signature}} gives identical lines), a closing line removed;
      2. otherwise the lines after the last closing line ("Best regards,") of an email, when they start with the sender's name.
    The most common candidate wins; between equals, the newest."""
    names = [n for n in names if n and n.strip()]
    if not names:
        return None
    emails = [_lines(t)[-TAIL:] for t in texts if t]
    emails = [e for e in emails if len(e) >= 2]
    shared: Counter[tuple[str, ...]] = Counter()
    for size in range(6, 0, -1):
        c = Counter(tuple(e[-size:]) for e in emails if len(e) > size)
        for block, n in c.items():
            if n < 2:
                continue
            b = list(block)
            while b and _CLOSING.match(b[0]):
                b = b[1:]
            if _plausible(b, names):
                shared[tuple(b)] = max(shared[tuple(b)], n)
        if shared:
            break
    if shared:
        return "\n".join(max(shared.items(), key=lambda kv: (kv[1], len(kv[0])))[0])
    found: Counter[tuple[str, ...]] = Counter()
    first_seen: dict[tuple[str, ...], int] = {}
    for i, e in enumerate(emails):
        idx = max((j for j, ln in enumerate(e) if _CLOSING.match(ln)), default=None)
        if idx is None:
            continue
        b = tuple(e[idx + 1:])
        if _plausible(list(b), names):
            found[b] += 1
            first_seen.setdefault(b, i)
    if not found:
        return None
    return "\n".join(max(found, key=lambda b: (found[b], -first_seen[b])))


def _campaign_texts(conn: Conn, sender_email: str) -> list[str]:
    return [r["body_text"] for r in qa(conn, "SELECT body_text FROM lemlist_activities WHERE type='emailsSent' AND campaign_id IS NOT NULL AND body_text IS NOT NULL "
                                             "AND body_source IS DISTINCT FROM 'preview' AND lower(send_user_email)=lower(%s) "
                                             "ORDER BY happened_at DESC NULLS LAST LIMIT %s", (sender_email, SAMPLE))]


def _thread_sender(conn: Conn, person_id: str) -> dict[str, Any] | None:
    """The mailbox that answers this person: the one named on their newest reply, else on our newest campaign email to them."""
    return q1(conn, "SELECT send_user_email, send_user_name FROM lemlist_activities WHERE person_id=%s AND type IN ('emailsReplied','emailsSent') "
                    "AND send_user_email IS NOT NULL ORDER BY (type='emailsReplied') DESC, happened_at DESC NULLS LAST LIMIT 1", (person_id,))


def stored(conn: Conn, user_id: str) -> dict[str, Any] | None:
    return q1(conn, "SELECT signature, source, updated_at FROM user_email_signatures WHERE user_id=%s", (user_id,))


def suggestion(conn: Conn, *, user_id: str, email: str, person_id: str | None = None) -> dict[str, Any]:
    """What to propose before the person has saved a signature: {text, source: campaign|default, from}."""
    me = q1(conn, "SELECT display_name FROM users WHERE id=%s", (user_id,)) or {}
    my_name = (me.get("display_name") or "").strip()
    tried: list[tuple[str, str | None]] = [(email, my_name)]
    if person_id:
        s = _thread_sender(conn, person_id)
        if s and s["send_user_email"] and s["send_user_email"].lower() != (email or "").lower():
            tried.append((s["send_user_email"], s["send_user_name"]))
    for addr, name in tried:
        texts = _campaign_texts(conn, addr)
        found = detect(texts, [x for x in (name, my_name) if x]) if texts else None
        if found:
            return {"text": found, "source": "campaign", "from": addr}
    return {"text": default(my_name or (email or "").split("@")[0]), "source": "default", "from": None}


def for_user(conn: Conn, *, user_id: str, email: str, person_id: str | None = None) -> dict[str, Any]:
    """The signature under this person's replies: the one they saved (an empty one means none), else the suggestion."""
    row = stored(conn, user_id)
    base = {"max": MAX_SIGNATURE, "lemlist_adds": LEMLIST_ADDS_SIGNATURE}
    if row is not None:
        return {**base, "text": row["signature"], "saved": True, "source": "saved", "updated_at": row["updated_at"], "from": None}
    return {**base, **suggestion(conn, user_id=user_id, email=email, person_id=person_id), "saved": False, "updated_at": None}


def save(conn: Conn, *, user_id: str, actor: str, text: str | None) -> dict[str, Any]:
    """Keep this person's signature (plain text, checked). Audited with its length only."""
    s = validate(text)
    conn.execute("INSERT INTO user_email_signatures(user_id, signature, source, updated_by) VALUES (%s,%s,'saved',%s) "
                 "ON CONFLICT (user_id) DO UPDATE SET signature=EXCLUDED.signature, source='saved', updated_by=EXCLUDED.updated_by, updated_at=now()",
                 (user_id, s, actor))
    audit(conn, actor=actor, operation="email_signature_saved", record_type="user", record_id=user_id, detail={"chars": len(s)})
    return for_user(conn, user_id=user_id, email=actor)


def to_add(body: str, signature: str | None) -> str | None:
    """The signature to append under these words, or None: none set, Lemlist adds its own, or the words already end with it (typed or
    pasted by hand), so it is never there twice."""
    sig = normalise(signature)
    if not sig or LEMLIST_ADDS_SIGNATURE:
        return None
    if _key(body).endswith(_key(sig)):
        return None
    return sig


def html(signature: str) -> str:
    """The signature as the simple HTML Lemlist sends: one paragraph, line breaks kept, everything escaped, nothing else (no images)."""
    lines = [htmllib.escape(ln) for ln in normalise(signature).split("\n")]
    return f"<p>{'<br>'.join(lines)}</p>" if any(x.strip() for x in lines) else ""


def full_text(body: str | None, signature: str | None) -> str:
    """The words and the signature as the email carries them (for recognising our own answer when Lemlist sends it back)."""
    return f"{body or ''}\n\n{signature}" if signature else (body or "")
