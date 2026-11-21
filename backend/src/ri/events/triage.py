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
