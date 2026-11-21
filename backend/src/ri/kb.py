"""Knowledge base: two internal documents (ICP map, sales field guide) stored privately in the repository and served only through
short-lived signed links, never as public static files. Also provides keyword search over their text."""
from __future__ import annotations

import hashlib
import html
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
DOCS: dict[str, dict[str, str]] = {
    "icp": {"title": "ICP and buying-persona map", "file": "icp.html", "blurb": "Who we target, the buying committee, and what to ask each role.",
            "source": "https://claude.ai/artifact/DBEktr9wwdNxnfnoWvRcQC"},
    "field-guide": {"title": "Sales field guide", "file": "field-guide.html", "blurb": "What we sell, the proof we may use, and what not to say.",
                    "source": "https://claude.ai/artifact/Baujgg6mde8V2eLJK9nMJ2"},
}
LINK_TTL_S = 300
KB_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
          "img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; "
          "sandbox allow-scripts allow-popups allow-modals allow-downloads")     # opaque origin: the document can never read the app's tokens


def _key(database_url: str) -> str:
    return hashlib.sha256(("kb-link:" + database_url).encode()).hexdigest()


def sign_link(database_url: str, doc_id: str, user_id: str, now: float | None = None) -> str:
    now = now or time.time()
    return jwt.encode({"doc": doc_id, "uid": user_id, "exp": int(now) + LINK_TTL_S}, _key(database_url), algorithm="HS256")


def verify_link(database_url: str, token: str, doc_id: str) -> bool:
    try:
        c = jwt.decode(token, _key(database_url), algorithms=["HS256"], options={"require": ["exp", "doc"]})
    except jwt.PyJWTError:
        return False
    return c.get("doc") == doc_id


def available(doc_id: str) -> bool:
    """Documents live outside git until the owner decides where internal material may be stored; a missing file is a normal state."""
    return doc_id in DOCS and (KB_DIR / DOCS[doc_id]["file"]).is_file()


def read_doc(doc_id: str) -> str:
    return (KB_DIR / DOCS[doc_id]["file"]).read_text(encoding="utf-8")


_STR = re.compile(r"""(?:"((?:[^"\\\n]|\\.){30,})"|'((?:[^'\\\n]|\\.){30,})')""")


@lru_cache(maxsize=1)
def chunks() -> tuple[tuple[str, str], ...]:
    """(doc_id, text) pieces: visible text plus the long string literals the pages render from (their content lives in script data)."""
    out: list[tuple[str, str]] = []
    for did in DOCS:
        if not available(did):
            continue
        raw = read_doc(did)
        seen: set[str] = set()
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
        pieces = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()]
        for sc in re.findall(r"(?is)<script\b[^>]*>(.*?)</script>", raw):
            for m in _STR.finditer(sc):
                t = (m.group(1) or m.group(2)).encode().decode("unicode_escape", errors="ignore") if "\\" in (m.group(1) or m.group(2)) else (m.group(1) or m.group(2))
                if re.search(r"[A-Za-z]{3,} [A-Za-z]{3,}", t) and not t.startswith(("M", "<", "http")):
                    pieces.append(t)
        for t in pieces:
            for sent in re.split(r"(?<=[.!?])\s+", t):
                s = sent.strip()
                if len(s) >= 25 and s not in seen:
                    seen.add(s)
                    out.append((did, s[:400]))
    return tuple(out)


def search(tokens: list[str], limit: int = 8) -> list[dict[str, Any]]:
    toks = [t.casefold() for t in tokens if t]
    if not toks:
        return []
    hits = []
    for did, text in chunks():
        low = text.casefold()
        if all(t in low for t in toks):
            hits.append((sum(low.count(t) for t in toks) + (2 if low.startswith(toks[0]) else 0), did, text))
    hits.sort(key=lambda h: -h[0])
    return [{"doc": d, "doc_title": DOCS[d]["title"], "text": t} for _, d, t in hits[:limit]]
