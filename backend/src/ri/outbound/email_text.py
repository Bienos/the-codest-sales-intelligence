"""The words of one email, without the earlier emails it quotes.

A reply usually carries our own campaign email(s) underneath it. Shown as they come, several emails read as one block, so every email is cut
at the start of its quoted history and only its own words are kept. The history starts at the first of:
  * an HTML quote container: <blockquote>, Gmail's `gmail_quote`, Outlook's `divRplyFwdMsg` and `#appendonsend`, Outlook mobile's reference
    container, Thunderbird's forward container, and Outlook desktop's grey `border-top` separator (or an <hr>) when a mail header follows it;
  * a plain-text mail header of Outlook and most mail apps, in our buyers' languages: From/Sent/To/Subject, Von/Gesendet/An/Betreff,
    Od/Wysłano/Do/Temat, Från/Skickat/Till/Ämne, Fra/Sendt/Til/Emne, De/Envoyé/À/Objet, Van/Verzonden/Aan/Onderwerp (and the Date/Datum/Data
    variants), on separate lines or run together on one line;
  * "-----Original Message-----", "-----Ursprüngliche Nachricht-----" and their siblings, Gmail's "---------- Forwarded message ---------",
    Apple Mail's "Begin forwarded message:";
  * "On ... wrote:" and its translations (Am ... schrieb, W dniu ... napisał, Le ... a écrit, Den ... skrev, Op ... schreef);
  * a long underscore line (Outlook on the web), and lines quoted with ">".
A mobile signature ("Sent from my iPhone", "Von meinem iPhone gesendet") is not a mail header: it stays with the words it ends.
When nothing would be left (an email that only quotes, or answers inside the quote), the whole email is kept, with the ">" marks removed.

The email as Lemlist gave it is kept next to the words (`body_raw`), so a better cleaning can be run again later; CLEAN_VERSION says which
cleaning produced the stored words."""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

MAX_BODY = 8000                 # characters kept of one email's words (a long email is cut, with an ellipsis)
MAX_RAW = 200_000               # characters kept of the email as Lemlist gave it
CLEAN_VERSION = 2               # 1: round 9 (no Outlook plain-text headers); 2: this module

_QUOTE_CLASSES = ("gmail_quote", "gmail_attr", "yahoo_quoted", "moz-cite-prefix", "moz-forward-container", "protonmail_quote", "x_gmail_quote",
                  "outlookmessageheader", "ms-outlook-mobile-reference-message", "zmail_extra")
_QUOTE_IDS = ("divrplyfwdmsg", "x_divrplyfwdmsg", "appendonsend", "x_appendonsend", "mail-editor-reference-message-container",
              "x_mail-editor-reference-message-container")
_BLOCK = {"p", "div", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "hr"}
_SKIP = {"script", "style", "head", "title", "noscript"}
_SEPARATOR_STYLE = re.compile(r"border-top\s*:\s*(?:solid|[\d.]+\s*(?:pt|px)\s+solid)", re.I)


class _Text(HTMLParser):
    """HTML to plain text: block elements become line breaks, links keep their words, and everything from the first quote container on is
    dropped. Outlook desktop's separator (a div with a grey top border) and <hr> are only remembered as places where the history MAY start:
    the text decides (a mail header must follow)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.size = 0
        self.skip = 0
        self.done = False
        self.marks: list[int] = []

    def _add(self, s: str) -> None:
        self.parts.append(s)
        self.size += len(s)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        cls, ident = a.get("class", "").lower(), a.get("id", "").lower()
        if tag == "blockquote" or any(c in cls for c in _QUOTE_CLASSES) or ident in _QUOTE_IDS:
            self.done = True
            return
        if tag == "hr" or (tag in ("div", "p") and _SEPARATOR_STYLE.search(a.get("style", ""))):
            self._edge()
            self.marks.append(self.size)
        if tag in _SKIP:
            self.skip += 1
        elif tag == "br":
            self._add("\n")
        elif tag in _BLOCK:
            self._edge()

    def _edge(self) -> None:
        """A block starts or ends: begin a new line, but never add an empty one (Gmail writes one <div> per line)."""
        if self.parts and not self.parts[-1].endswith("\n"):
            self._add("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag == "br":
            self._add("\n")
        elif tag == "hr":
            self._edge()
            self.marks.append(self.size)

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self._edge()

    def handle_data(self, data: str) -> None:
        if not self.done and not self.skip and data:
            self._add(data if data.strip() or not self.parts or not self.parts[-1].endswith("\n") else data.strip(" \t\n"))


# A mail header after a separator: three "Label:" starts within a few lines, whatever the language.
_LABEL_LINE = re.compile(r"^[ \t*]*[^\W\d_][^\W\d_ ]{0,14}[ \t]?\*?[ \t]*:", re.M)


def _header_follows(text: str) -> bool:
    head = text.lstrip()[:700]
    return bool(re.match(r"[ \t*]*[^\W\d_][^\W\d_ ]{0,14}[ \t]?\*?[ \t]*:", head)) and len(_LABEL_LINE.findall(head)) >= 3


def html_to_text(value: str) -> str:
    p = _Text()
    try:
        p.feed(value)
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML: fall back to removing the tags
        return htmllib.unescape(re.sub(r"<[^>]+>", " ", value))
    text = "".join(p.parts)
    for m in p.marks:                                  # Outlook desktop's separator, or an <hr>, followed by a mail header
        if m < len(text) and text[:m].strip() and _header_follows(text[m:]):
            return text[:m]
    return text


# ------------------------------------------------------------------------------------------------ plain text
_SP = r"[ \t ]"


def _label(words: str) -> str:
    """A header label ("From:", "*Von:*", "De :"), not glued to a word before it. Case-sensitive: mail apps write them capitalised."""
    return rf"(?<![\w@.]){_SP}*\*?(?:{words}){_SP}?\*?{_SP}*:\*?"


_FROM = "From|FROM|Von|VON|Od|OD|Från|FRÅN|Fra|FRA|De|DE|Van|VAN"
_REST = ("Sent|SENT|Date|DATE|Gesendet|GESENDET|Datum|Wysłano|Wysłane|Data|Skickat|Sendt|Dato|Envoyé|Verzonden|Verstuurd"
         "|To|TO|An|AN|Do|DO|Till|Til|À|Aan|Cc|CC|DW|Kopie|Kopia|Kopia do"
         "|Subject|SUBJECT|Betreff|BETREFF|Temat|TEMAT|Ämne|Emne|Objet|Onderwerp")
# "From: Tom <tom@x>  Sent: Monday ...  To: Ola  Subject: ..." on separate lines or on one: the From label and two more labels close behind it.
_HEADER = re.compile(_label(_FROM) + r"[^\S\n]*\S.{0,320}?" + _label(_REST) + r".{0,320}?" + _label(_REST), re.S)

_WRAP = r"[^\n]{0,250}(?:\n[^\n]{0,250}){0,2}?"
_HISTORY = [
    _HEADER,
    # "On Mon, 23 Nov 2026 at 09:12 Tom <tom@x> wrote:" at the start of a line (possibly wrapped over two or three lines) ...
    re.compile(r"^[ \t>]*On\s" + _WRAP + r"\bwrote:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*W\s+dniu\s" + _WRAP + r"(?:napisał|napisała|napisał\(a\)|pisze)[^\n]{0,20}:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*(?:Dnia|Wiadomość napisana przez)\s" + _WRAP + r"(?:napisał|napisała|napisał\(a\)|w dniu)[^\n]{0,60}:?[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*Am\s" + _WRAP + r"\bschrieb[^\n]{0,120}:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*Le\s" + _WRAP + r"a\s+écrit\s*:[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t>]*(?:Den|Op)\s" + _WRAP + r"\b(?:skrev|schreef)[^\n]{0,120}:[ \t]*$", re.I | re.M),
    # ... or run into the words on one line (a date must be in it, so "On Tuesday we wrote:" in someone's own words is not cut)
    re.compile(r"(?<![\w])On\s(?=[^\n]{0,60}\d)[^\n]{4,300}?\swrote:"),
    re.compile(r"(?<![\w])Am\s(?=[^\n]{0,40}\d)[^\n]{4,300}?\sschrieb[^\n:]{0,120}:"),
    re.compile(r"(?<![\w])W\s+dniu\s(?=[^\n]{0,40}\d)[^\n]{4,300}?\s(?:napisał|napisała|napisał\(a\)|pisze)[^\n:]{0,20}:"),
    re.compile(r"(?<![\w])Le\s(?=[^\n]{0,40}\d)[^\n]{4,300}?\sa\s+écrit\s*:"),
    re.compile(r"(?<![\w])(?:Den|Op)\s(?=[^\n]{0,40}\d)[^\n]{4,300}?\s(?:skrev|schreef)[^\n:]{0,120}:"),
    re.compile(r"-{2,}\s*(?:Original Message|Ursprüngliche Nachricht|Wiadomość oryginalna|Oryginalna wiadomość|Message d'origine|Opprinnelig melding"
               r"|Ursprungligt meddelande|Oorspronkelijk bericht|Oprindelig meddelelse|Original meddelelse|Forwarded message|Weitergeleitete Nachricht"
               r"|Wiadomość przekazana dalej|Message transféré|Vidarebefordrat meddelande|Videresendt melding|Videresendt meddelelse|Doorgestuurd bericht)\s*-{2,}",
               re.I),
    re.compile(r"(?<![\w])(?:Begin forwarded message|Anfang der weitergeleiteten Nachricht|Początek przekazanej wiadomości|Début du message réexpédié"
               r"|Begin doorgestuurd bericht|Vidarebefordrat brev|Videresendt melding|Videresendt besked)\s*:", re.I),
    re.compile(r"^[ \t]*_{20,}[ \t]*$", re.M),
]
_TRAIL = re.compile(r"(?:\s*(?:_{3,}|-{3,}|={3,}))+\s*\Z")
_IMAGE = re.compile(r"\[(?:image|cid|obraz)[^\]]{0,200}\]", re.I)


def history_start(text: str) -> int:
    """Where the quoted history begins (len(text) when there is none)."""
    return min((m.start() for rx in _HISTORY if (m := rx.search(text))), default=len(text))


def _unquote(text: str) -> str:
    return "\n".join(re.sub(r"^(?:[ \t]*>)+[ \t]?", "", line) for line in text.split("\n"))


def strip_history(text: str) -> str:
    """Cut an email at its quoted history and drop any left-over quoted lines. When nothing would be left (the email only quotes, or answers
    inside the quote), keep the whole email with the ">" marks removed rather than show nothing."""
    cut = history_start(text)
    head = "\n".join(line for line in text[:cut].split("\n") if not line.lstrip().startswith(">"))
    head = _TRAIL.sub("", head)
    return head if head.strip() else _unquote(text)


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("​", "").replace("﻿", "")


def tidy(text: str) -> str:
    t = _IMAGE.sub("", _normal(text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in t.split("\n")]
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return t if len(t) <= MAX_BODY else t[:MAX_BODY].rstrip() + "…"


def looks_html(value: str) -> bool:
    return bool(re.search(r"<(?:html|body|div|p|br|span|blockquote|a|table|img)\b", value, re.I))


def clean(raw: str, fmt: str) -> str:
    """The email's own words from the email as Lemlist gave it ('html' or 'text')."""
    text = html_to_text(raw) if fmt == "html" else raw
    return tidy(strip_history(_normal(text)))


@dataclass
class Body:
    text: str | None            # the email's own words
    preview: bool               # only Lemlist's one-line preview came
    raw: str | None = None      # the email as Lemlist gave it (None for a preview)
    fmt: str | None = None      # 'html' | 'text'


def extract(a: dict[str, Any]) -> Body:
    """The words of an activity or inbox message. HTML is preferred (its quote containers mark the history best), then the plain text;
    when only Lemlist's one-line `messagePreview` is there, it is returned as a preview."""
    found: list[tuple[str, str]] = []
    for key in ("html", "message", "body", "text"):
        v = a.get(key)
        if isinstance(v, str) and v.strip():
            found.append((v, "html" if key == "html" or (key != "text" and looks_html(v)) else "text"))
    found.sort(key=lambda x: x[1] != "html")                    # stable: html first, then the rest in key order
    for raw, fmt in found:
        raw = raw[:MAX_RAW]
        t = clean(raw, fmt)
        if t:
            return Body(t, False, raw, fmt)
    v = a.get("messagePreview")
    if isinstance(v, str) and v.strip():
        return Body(tidy(v), True)
    return Body(None, False)


def extract_body(a: dict[str, Any]) -> tuple[str | None, bool]:
    """(plain text, is_preview): the round 9 interface, kept for callers."""
    b = extract(a)
    return b.text, b.preview
