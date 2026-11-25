"""Deterministic brand and claims review of a draft sequence. Plain rules, no model: the same draft always gets the same verdict.

A draft passes only when it uses approved claims, names no blocked client or market, uses no avoid-list wording, states no number that is not in a verified source,
keeps to one small approved next step per email, and every email points at a verified fact."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[3] / "rules" / "brand_claims.yaml"
PLACEHOLDERS = {"{{firstName}}", "{{companyName}}"}


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def _words(rx_list: list[str]) -> re.Pattern:
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in rx_list) + r")(?![\w-])", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'")).strip().casefold()


def check_step(step: dict[str, Any], facts: dict[str, str], *, first: bool, placeholders: set[str] | frozenset[str] = frozenset(PLACEHOLDERS)) -> list[dict[str, str]]:
    r = rules()
    subject, body = str(step.get("subject") or ""), str(step.get("body") or "")
    text = f"{subject}\n{body}"
    out: list[dict[str, str]] = []

    def brk(code: str, detail: str) -> None:
        out.append({"code": code, "detail": detail})
    if not subject.strip() or not body.strip():
        brk("empty", "subject or body is empty")
        return out
    if "—" in text or "–" in text:
        brk("em_dash", "no em or en dashes")
    if "!" in text:
        brk("exclamation", "no exclamation marks")
    for m in _words(r["avoid_words"]).findall(text):
        brk("avoid_word", f"avoid-list word: {m}")
    low = _norm(text)
    for ph in r["avoid_phrases"]:
        if ph in low:
            brk("generated_copy_pattern", f"stock phrase: {ph}")
    for n in r["blocked_names"]:
        if re.search(rf"\b{re.escape(n)}\b", text, re.I):
            brk("blocked_name", f"{n} must never appear in outreach")
    if re.search(r"\b(USA|U\.S\.|United States)\b|\bUS\b", text) or "US market" in text:
        brk("blocked_market", "the US is never targeted or mentioned")
    if re.search(r"(?<!The )\bCodest\b", text):
        brk("brand_name", "write 'The Codest' with a capital T, never 'Codest' alone")
    if re.search(r"\b(ICP|funnel|buying committee|white space|audience group)\b", text, re.I):
        brk("internal_language", "internal strategy language must not appear in outreach")
    used = [str(c) for c in (step.get("claims_used") or [])]
    for c in used:
        if c not in r["usable_claims"]:
            brk("claim_not_usable", f"{c} is not cleared for use in a draft")
    if re.search(r"\btakes? responsibility\b", text, re.I) and "CLM-001" not in used:
        brk("claim_not_declared", "the responsibility promise is used but CLM-001 is not declared")
    for c in used:                      # the approved wording only: a paraphrase is a second, unapproved promise
        wording = r["usable_claims"].get(c)
        if wording and _norm(wording) not in low:
            brk("claim_reworded", f"{c} must appear in its approved wording: \"{wording}\"")
    # numbers: only those that appear in a verified source, or a year
    allowed = " ".join(facts.values())
    for num in re.findall(r"(?<![\w{])\d[\d.,]*%?", body + " " + subject):
        n = num.rstrip(".,")
        if re.fullmatch(r"20\d\d", n) or n in allowed:
            continue
        brk("number_not_in_source", f"{n} is not in any verified source")
    refs = [str(x) for x in (step.get("fact_refs") or [])]
    if not refs or any(x not in facts for x in refs):
        brk("no_verified_fact", "the email must point at a verified fact (fact_refs)")
    ctas = [c for c in r["ctas"] if _norm(c) in low]
    if len(ctas) != 1:
        brk("cta", "use exactly one approved next step" if ctas else "no approved next step found")
    for token in re.findall(r"\{\{[^}]*\}\}", text):
        if token not in placeholders:
            brk("placeholder", f"unknown placeholder {token}")
    if re.search(r"https?://|[\w.+-]+@[\w-]+\.[\w.]+", text):
        brk("link_or_address", "no links or email addresses in the draft")
    words = len(body.split())
    if words > (120 if first else 90):
        brk("too_long", f"{words} words; keep to {120 if first else 90}")
    for para in [p for p in body.split("\n\n") if p.strip()]:
        if len(re.findall(r"[.?!](?:\s|$)", para.strip())) > 2:
            brk("paragraph_long", "paragraphs are one or two sentences")
            break
    return out


def review_sequence(steps: list[dict[str, Any]], facts: dict[str, str], *, placeholders: set[str] | frozenset[str] = frozenset(PLACEHOLDERS)) -> dict[str, Any]:
    breaks = []
    for i, s in enumerate(steps):
        for b in check_step(s, facts, first=i == 0, placeholders=placeholders):
            breaks.append({**b, "step": i + 1})
    if not steps:
        breaks.append({"code": "empty", "detail": "no steps", "step": 0})
    return {"passed": not breaks, "breaks": breaks}
