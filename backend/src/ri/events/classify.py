"""Deterministic relevance classifiers used by structured emitters (no AI needed for structured data)."""
from __future__ import annotations

import re

_ENG = re.compile(r"\b(engineer(ing)?|developer|devops|sre|site reliability|architect|qa|sdet|test automation|data scientist|"
                  r"machine learning|ml ?ops|platform|backend|front[- ]?end|full[- ]?stack|software|tech lead|cto|head of (engineering|platform|payments))\b", re.I)
_ENG_EXCL = re.compile(r"\b(sales engineer|solutions? engineer(ing)? (sales)|recruit|account (executive|manager)|hr |marketing|customer success|"
                       r"mechanical|electrical|civil|hardware engineer|facilities)\b", re.I)
_SVC_KW = re.compile(r"\b(software|development|digital|it consult|it services|information technology|application|platform|cloud|api|"
                     r"integration|migration|quality assurance|testing|artificial intelligence|machine learning|data platform|devops|"
                     r"cyber|payment|banking system|core banking|telematics|saas|web|mobile)\b", re.I)
_HARDWARE_ONLY = re.compile(r"\b(supply of (computer )?hardware|laptops?|servers?|printers?|furniture|vehicles?)\b", re.I)


def engineering_relevant(title: str) -> bool:
    return bool(_ENG.search(title)) and not _ENG_EXCL.search(title)


def services_relevant(title: str | None, scope: str | None, cpv: list[str]) -> bool:
    """Relevant software/IT services scope. Pure hardware supply (CPV 30*, 'supply of laptops') is not."""
    text = f"{title or ''} {scope or ''}"
    cpv_it = any(c.startswith(("72", "48", "73", "64216", "79")) and not c.startswith("79 ") for c in cpv if c)
    cpv_only_hw = bool(cpv) and all(c.startswith(("30", "32", "34", "39")) for c in cpv if c)
    if _HARDWARE_ONLY.search(text) and not _SVC_KW.search(re.sub(_HARDWARE_ONLY, "", text)):
        return False
    if cpv_only_hw and not _SVC_KW.search(text):
        return False
    return cpv_it or bool(_SVC_KW.search(text))
