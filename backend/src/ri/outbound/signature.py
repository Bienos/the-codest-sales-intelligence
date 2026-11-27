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
from html.parser import HTMLParser
import re
import unicodedata
from collections import Counter
from typing import Any

from ..audit import audit
from ..db import Conn, q1, qa

LEMLIST_ADDS_SIGNATURE = False
MAX_SIGNATURE = 2000            # plain-text reading of a signature (a legal footer is longer than a sign-off)
MAX_LINES = 30
MAX_HTML = 12000                # the cleaned HTML kept for a formatted signature
MAX_SUGGESTED = 600             # a sign-off found in campaign emails is short; longer blocks are not trusted
FONT_MAX_PX = 12                # nothing in an email sent from the app is larger than this
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
    if not block or len(block) > 6 or any(len(x) > 90 for x in block) or len("\n".join(block)) > MAX_SUGGESTED:
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
    return q1(conn, "SELECT signature, signature_html, source, updated_at FROM user_email_signatures WHERE user_id=%s", (user_id,))


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
    """The signature under this person's replies: the one they saved (an empty one means none), else the suggestion. `html` is the cleaned
    formatted version (logo, links, small print) when they kept one, else None."""
    row = stored(conn, user_id)
    base = {"max": MAX_SIGNATURE, "lemlist_adds": LEMLIST_ADDS_SIGNATURE, "font_max_px": FONT_MAX_PX}
    if row is not None:
        src = row["source"] if row["source"] in ("gmail", "pasted") else "saved"
        return {**base, "text": row["signature"], "html": row["signature_html"] or None, "saved": True, "source": src, "updated_at": row["updated_at"], "from": None}
    return {**base, **suggestion(conn, user_id=user_id, email=email, person_id=person_id), "html": None, "saved": False, "updated_at": None}


def save(conn: Conn, *, user_id: str, actor: str, text: str | None, html: str | None = None) -> dict[str, Any]:
    """Keep this person's signature. Typed plain text (checked), or `html` pasted from their mail program (cleaned to safe tags, with its
    plain-text reading). Audited with its length only."""
    if html is not None and html.strip():
        rich = rich_from_html(html)
        s, h, source = rich["text"], rich["html"] or None, "pasted"
    else:
        s, h, source = validate(text), None, "saved"
    if not s:
        h = None                                                   # nothing readable: it is "no signature", on purpose
    conn.execute("INSERT INTO user_email_signatures(user_id, signature, signature_html, source, updated_by) VALUES (%s,%s,%s,%s,%s) "
                 "ON CONFLICT (user_id) DO UPDATE SET signature=EXCLUDED.signature, signature_html=EXCLUDED.signature_html, source=EXCLUDED.source, "
                 "updated_by=EXCLUDED.updated_by, updated_at=now()", (user_id, s, h, source, actor))
    audit(conn, actor=actor, operation="email_signature_saved", record_type="user", record_id=user_id, detail={"chars": len(s), "formatted": bool(h)})
    return for_user(conn, user_id=user_id, email=actor)


def html_for(info: dict[str, Any], footer: str | None) -> str:
    """The signature as the HTML that goes under the words: the person's formatted version when that is what `footer` came from, else the
    plain text as simple lines. Empty when there is nothing to add."""
    if not footer:
        return ""
    if info.get("html") and normalise(info.get("text")) == normalise(footer):
        return "<div><br></div>" + info["html"]
    return html(footer)


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
    """The signature as Gmail-style HTML: an empty line, then one <div> per line, everything escaped, nothing else (no images). The font comes
    from the block that wraps the whole email (ri.outbound.replies.email_html)."""
    lines = normalise(signature).split("\n")
    if not any(x.strip() for x in lines):
        return ""
    return "<div><br></div>" + "".join(f"<div>{htmllib.escape(ln)}</div>" if ln.strip() else "<div><br></div>" for ln in lines)


def full_text(body: str | None, signature: str | None) -> str:
    """The words and the signature as the email carries them (for recognising our own answer when Lemlist sends it back)."""
    return f"{body or ''}\n\n{signature}" if signature else (body or "")


# ---------------------------------------------------------------------------------------------------- from the person's own Gmail
class _GmailSignatureText(HTMLParser):
    """A Gmail signature (HTML, as kept in Gmail's settings) as plain text: line breaks kept, links written out where the visible words do not
    already show the address, pictures, styles and scripts dropped."""
    _BREAK = {"div", "p", "br", "tr", "li", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "blockquote"}
    _SKIP = {"script", "style", "head", "title", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._href: str | None = None
        self._start = 0

    def _newline(self) -> None:
        while self.parts and self.parts[-1] != "\n" and not self.parts[-1].strip():
            self.parts.pop()                                         # spaces at the end of a line are not words
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "br":
            self.parts.append("\n")
        elif tag in self._BREAK:
            self._newline()
        elif tag in ("td", "th") and self.parts and self.parts[-1] != "\n":
            self.parts.append(" ")
        elif tag == "a":
            self._href = (dict(attrs).get("href") or "").strip() or None
            self._start = len(self.parts)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif self._skip:
            return
        elif tag == "a":
            shown = "".join(self.parts[self._start:]).strip()
            extra = _link_extra(shown, self._href)
            if extra:
                self.parts.append(f" ({extra})")
            self._href = None
        elif tag in self._BREAK:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        if not data.strip() and (not self.parts or self.parts[-1] == "\n"):
            return                                                   # spacing between tags at the start of a line
        self.parts.append(re.sub(r"[ \t\r\n\xa0]+", " ", data))


def _bare(url: str) -> str:
    return re.sub(r"^(?:https?://|mailto:|tel:)(?:www\.)?|[/\s]+$", "", (url or "").strip(), flags=re.I).lower()


def _link_extra(shown: str, href: str | None) -> str | None:
    """The address to write after a link's words, or None when the words already show it (or the link is not an address worth keeping)."""
    if not href or not shown or not re.match(r"^(?:https?://|mailto:|tel:)", href, re.I):
        return None
    if href.lower().startswith("http") and "google.com/url?" in href.lower():
        return None                                                  # a tracking redirect, not the real address
    a, b = _bare(href), _bare(shown)
    if not a or a in b or b in a or re.sub(r"[^\d+]", "", a) == re.sub(r"[^\d+]", "", b) and a.replace("+", "").isdigit():
        return None
    return re.sub(r"^mailto:|^tel:", "", href.strip(), flags=re.I)[:120]


def from_gmail_html(raw: str | None) -> tuple[str, bool]:
    """(plain text that fits the signature limits, whether it had to be shortened). Empty when the HTML holds no words."""
    if not (raw or "").strip():
        return "", False
    parser = _GmailSignatureText()
    try:
        parser.feed(raw or "")
        parser.close()
    except Exception:                                              # noqa: BLE001 - broken markup: keep what was read so far
        pass
    lines = [re.sub(r" {2,}", " ", ln).strip() for ln in "".join(parser.parts).split("\n")]
    text = normalise("\n".join(lines))
    if not text:
        return "", False
    cut = False
    rows = text.split("\n")
    if len(rows) > MAX_LINES:
        rows, cut = rows[:MAX_LINES], True
    while rows and len("\n".join(rows)) > MAX_SIGNATURE:
        rows.pop()
        cut = True
    return normalise("\n".join(rows)), cut


def import_from_gmail(conn: Conn, *, user_id: str, actor: str, client: Any, only_if_none: bool = False) -> dict[str, Any] | None:
    """Read the signature of the person's own Gmail account (its default send-as address) and keep it as theirs, with its formatting (logo, links,
    small print) cleaned to safe tags. `client` is a connected ri.connections.gmail_api.GmailClient. With `only_if_none` nothing is read when a
    signature is already kept (an empty one kept on purpose counts), so the automatic import after connecting Gmail never overwrites a choice.
    Audited with its length only."""
    if only_if_none and stored(conn, user_id) is not None:
        return None
    address, raw = client.send_as_signature()
    try:
        rich = rich_from_html(raw)
    except SignatureError as exc:
        return {"imported": False, "address": address, "empty": False, "error": str(exc)}
    if not rich["text"]:
        return {"imported": False, "address": address, "empty": True}
    sql = ("INSERT INTO user_email_signatures(user_id, signature, signature_html, source, updated_by) VALUES (%s,%s,%s,'gmail',%s) "
           + ("ON CONFLICT (user_id) DO NOTHING" if only_if_none else
              "ON CONFLICT (user_id) DO UPDATE SET signature=EXCLUDED.signature, signature_html=EXCLUDED.signature_html, source='gmail', "
              "updated_by=EXCLUDED.updated_by, updated_at=now()"))
    cur = conn.execute(sql, (user_id, rich["text"], rich["html"] or None, actor))
    if only_if_none and not cur.rowcount:
        return None
    audit(conn, actor=actor, operation="email_signature_imported", record_type="user", record_id=user_id,
          detail={"chars": len(rich["text"]), "formatted": bool(rich["html"]), "dropped_images": rich["dropped_images"], "from": "gmail"})
    return {"imported": True, "address": address, "empty": False, "dropped_images": rich["dropped_images"]}


# ---------------------------------------------------------------------------------------------------- a formatted signature, made safe
# What is kept of pasted or imported HTML: a short list of tags, links to http(s)/mailto/tel only, pictures from https addresses only, and a
# handful of style properties with checked values (font size never above FONT_MAX_PX). Everything else is dropped, so the output is built
# from escaped text and attributes this file writes itself, never passed through. Scripts, styles, forms, frames, event handlers, class names,
# comments and pictures that cannot be shown to the recipient (inline data, cid:, pages that need a Google login) are gone.
_RICH_TAGS = {"a", "b", "strong", "i", "em", "u", "s", "strike", "br", "hr", "p", "div", "span", "img", "table", "thead", "tbody", "tr", "td", "th",
              "ul", "ol", "li", "sub", "sup"}
_RICH_VOID = {"br", "hr", "img"}
_RICH_DROP = {"script", "style", "head", "title", "svg", "iframe", "object", "noscript", "template", "textarea", "select", "button", "math", "canvas",
              "audio", "video", "form", "xml", "o:p"}
_COLORS = {"black", "white", "gray", "grey", "silver", "red", "green", "blue", "navy", "purple", "teal", "maroon", "olive", "orange", "darkgray",
           "darkgrey", "dimgray", "dimgrey", "lightgray", "lightgrey", "gainsboro", "darkblue", "darkred", "darkgreen", "crimson", "magenta"}
_COLOR = re.compile(r"#[0-9a-f]{3}|#[0-9a-f]{6}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)")
_FONT_WORDS = {"xx-small": 9, "x-small": 10, "small": 12, "smaller": 11, "medium": 12, "large": 12, "x-large": 12, "xx-large": 12, "larger": 12}
_CELL_FONT = f"font-family:Arial,Helvetica,sans-serif;font-size:{FONT_MAX_PX}px"       # table cells do not inherit the size in every mail program


def _font_px(v: str) -> int | None:
    if v in _FONT_WORDS:
        return min(_FONT_WORDS[v], FONT_MAX_PX)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(px|pt)", v)
    if not m:
        return None
    px = float(m.group(1)) * (4 / 3 if m.group(2) == "pt" else 1)
    return int(round(min(max(px, 7), FONT_MAX_PX)))


def _clean_style(css: str | None) -> str:
    out: list[str] = []
    for decl in (css or "").split(";"):
        if ":" not in decl:
            continue
        k, v = (x.strip().lower() for x in decl.split(":", 1))
        v = v.replace("!important", "").strip()
        if k == "color" and (v in _COLORS or _COLOR.fullmatch(v)):
            out.append(f"color:{v}")
        elif k == "font-size" and (px := _font_px(v)):
            out.append(f"font-size:{px}px")
        elif k == "font-weight" and re.fullmatch(r"normal|bold|bolder|[1-9]00", v):
            out.append("font-weight:" + ("bold" if v in ("bold", "bolder") or (v.isdigit() and int(v) >= 600) else "normal"))
        elif k == "font-style" and v in ("italic", "normal"):
            out.append(f"font-style:{v}")
        elif k in ("text-decoration", "text-decoration-line") and v in ("underline", "none", "line-through"):
            out.append(f"text-decoration:{v}")
        elif k == "line-height" and (re.fullmatch(r"1(?:\.\d{1,2})?|2(?:\.0)?", v) or re.fullmatch(r"(?:1\d|2\d|30)px", v)):
            out.append(f"line-height:{v}")
    return ";".join(out)


def _unspace(v: str | None) -> str:
    return re.sub(r"[\x00-\x20\x7f\xa0]+", "", v or "")                 # "java\nscript:" must not slip through


def _safe_href(v: str | None) -> str | None:
    h = _unspace(v)
    if not h or len(h) > 600 or not re.match(r"^(?:https?|mailto|tel):", h, re.I):
        return None
    if re.match(r"^https?://(?:www\.)?google\.com/url\?", h, re.I):          # a redirect Gmail wraps around a link: keep the real address
        from urllib.parse import parse_qs, urlparse
        real = (parse_qs(urlparse(h).query).get("q") or parse_qs(urlparse(h).query).get("url") or [""])[0]
        return _safe_href(real) if real and not re.match(r"^https?://(?:www\.)?google\.com/url\?", real, re.I) else None
    return h


def _safe_src(v: str | None) -> str | None:
    s = _unspace(v)
    if not s or len(s) > 800 or not s.lower().startswith("https://"):
        return None
    if re.match(r"^https://mail\.google\.com/", s, re.I):                     # needs the owner's Google login: the recipient would see a broken picture
        return None
    return s


def _dim(v: str | None) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,4})(?:px)?\s*", v or "")
    return min(int(m.group(1)), 400) if m and int(m.group(1)) > 0 else None


class _RichClean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.skip = 0
        self.dropped_images = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag in _RICH_DROP:
                self.skip += 1
            return
        if tag in _RICH_DROP:
            self.skip = 1
            return
        if tag not in _RICH_TAGS:
            return                                                   # the tag goes, its words stay
        a = {k.lower(): v for k, v in attrs}
        style = _clean_style(a.get("style"))
        if tag == "img":
            src = _safe_src(a.get("src"))
            w, h = _dim(a.get("width")), _dim(a.get("height"))
            if not src or (w is not None and w <= 2) or (h is not None and h <= 2):
                self.dropped_images += 0 if (w is not None and w <= 2) or (h is not None and h <= 2) else 1      # tracking pixels are not a loss
                return
            size = (f' width="{w}"' if w else "") + (f' height="{h}"' if h else "")
            alt = htmllib.escape((a.get("alt") or "").strip()[:120], quote=True)
            self.out.append(f'<img src="{htmllib.escape(src, quote=True)}" alt="{alt}"{size} style="border:0;{"" if w else "max-width:300px;"}height:auto">')
            return
        if tag in _RICH_VOID:
            self.out.append(f"<{tag}>")
            return
        if tag == "a":
            href = _safe_href(a.get("href"))
            if not href:
                self.stack.append((tag, False))
                return
            self.out.append(f'<a href="{htmllib.escape(href, quote=True)}"' + (f' style="{style}"' if style else "") + ">")
            self.stack.append((tag, True))
            return
        if tag in ("td", "th") and "font-size" not in style:
            style = f"{style};{_CELL_FONT}" if style else _CELL_FONT
        self.out.append(f"<{tag}" + (f' style="{style}"' if style else "") + ">")
        self.stack.append((tag, True))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _RICH_VOID and tag not in _RICH_DROP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in _RICH_DROP:
                self.skip -= 1
            return
        if tag not in _RICH_TAGS or tag in _RICH_VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    t, emitted = self.stack.pop()
                    if emitted:
                        self.out.append(f"</{t}>")
                return

    def handle_data(self, data: str) -> None:
        if not self.skip and data:
            self.out.append(htmllib.escape(re.sub(r"[ \t\r\n\xa0]+", " ", data), quote=False))

    def result(self) -> str:
        while self.stack:
            t, emitted = self.stack.pop()
            if emitted:
                self.out.append(f"</{t}>")
        return "".join(self.out)


def _plain_text(html_text: str) -> str:
    parser = _GmailSignatureText()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:                                              # noqa: BLE001
        pass
    return normalise("\n".join(re.sub(r" {2,}", " ", ln).strip() for ln in "".join(parser.parts).split("\n")))


def rich_from_html(raw: str | None) -> dict[str, Any]:
    """{html, text, dropped_images}: the signature HTML cleaned to safe tags, and its plain-text reading. Raises SignatureError when it is too
    long to keep. Empty html and text when the markup holds no words."""
    if not (raw or "").strip():
        return {"html": "", "text": "", "dropped_images": 0}
    p = _RichClean()
    try:
        p.feed((raw or "")[:400_000])
        p.close()
    except Exception:                                              # noqa: BLE001 - broken markup: keep what was read
        pass
    cleaned = re.sub(r"\s*(<(?:div|p)>(?:<br>)?</(?:div|p)>|<br>)\s*$", "", p.result()).strip()
    text = _plain_text(cleaned)
    if not text:
        return {"html": "", "text": "", "dropped_images": p.dropped_images}
    if len(text) > MAX_SIGNATURE:
        raise SignatureError(f"The signature is too long. Keep it under {MAX_SIGNATURE} characters.")
    if text.count("\n") + 1 > MAX_LINES:
        raise SignatureError(f"The signature has too many lines. Keep it to {MAX_LINES} lines.")
    if len(cleaned) > MAX_HTML:
        raise SignatureError("The signature's formatting is too large to keep. Paste a simpler version.")
    return {"html": cleaned, "text": text, "dropped_images": p.dropped_images}
