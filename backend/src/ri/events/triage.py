from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[3] / "rules" / "relevance.yaml"


@lru_cache(maxsize=1)
def _rx() -> tuple[re.Pattern, re.Pattern]:
    cfg = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    def build(words):
        return re.compile("|".join(r"\b" + re.escape(w) if len(w) > 3 else r"\b" + re.escape(w) + r"\b" for w in words), re.I)
    return build(cfg["topics"]), build(cfg["developments"])


def looks_relevant(title: str | None, text: str | None) -> bool:
    """True when the title or the opening of the text mentions a topic and a kind of development."""
    t, d = _rx()
    blob = f"{title or ''} {(text or '')[:700]}"
    return bool(t.search(blob) and d.search(blob))


def relevance_version() -> str:
    import hashlib
    return hashlib.sha256(PATH.read_bytes()).hexdigest()[:10]


def retriage(conn, limit: int = 500, days: int = 21) -> int:
    """Items skipped under an older version of the word list are checked once against the current one; the newly relevant ones are queued for the model."""
    v = relevance_version()
    n = 0
    rows = conn.execute("SELECT id, title, coalesce(content_text, excerpt) AS body FROM source_documents WHERE raw_meta->>'triage'='skipped_not_relevant' "
                        "AND coalesce(raw_meta->>'triage_v','') <> %s AND content_text IS NOT NULL AND fetched_at > now() - make_interval(days => %s) LIMIT %s", (v, days, limit)).fetchall()
    for r in rows:
        if looks_relevant(r["title"], r["body"]):
            conn.execute("UPDATE source_documents SET raw_meta = (raw_meta - 'triage' - 'triage_v') || '{\"pending_extraction\": true}'::jsonb WHERE id=%s", (r["id"],))
            n += 1
        else:
            conn.execute("UPDATE source_documents SET raw_meta = raw_meta || jsonb_build_object('triage_v', %s::text) WHERE id=%s", (v, r["id"]))
    return n
