from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


def first_text(v: Any, prefer: tuple[str, ...] = ("eng", "en")) -> str | None:
    """TED returns multilingual maps / lists; dig out one human string deterministically.

    Language keys are matched whatever their case: TED's `links.html` uses "ENG", "BUL"..., its `notice-title` uses "eng", "swe"..."""
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        for x in v:
            t = first_text(x, prefer)
            if t:
                return t
        return None
    if isinstance(v, dict):
        folded: dict[str, Any] = {}
        for k in v:
            folded.setdefault(str(k).lower(), k)
        for k in prefer:
            if k in v:
                return first_text(v[k], prefer)
            if k.lower() in folded:
                return first_text(v[folded[k.lower()]], prefer)
        for x in v.values():
            t = first_text(x, prefer)
            if t:
                return t
    return None


def parse_dt(s: Any) -> datetime | None:
    """ISO-8601 incl. TED style '2026-12-07+01:00' / '2026-12-07T12:00:00Z'. Naive values are treated as UTC and
    flagged by callers via deadline_tz; a bare date has no time-of-day (end of day is NOT assumed)."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})$", t)
    if m:
        t = f"{m.group(1)}T00:00:00{m.group(2)}"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def tz_of(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    m = re.search(r"([+-]\d{2}:\d{2}|Z)$", s.strip())
    return None if not m else ("+00:00" if m.group(1) == "Z" else m.group(1))


def excerpt(text: str | None, n: int = 400) -> str | None:
    if not text:
        return None
    t = re.sub(r"\s+", " ", text).strip()
    return t[:n]
