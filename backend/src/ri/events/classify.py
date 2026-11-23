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


# software and IT services codes; 723xx (data entry, data processing, digitisation) is clerical work, not engineering
_IT_CPV = ("72000000", "722", "724", "725", "726", "727", "728", "48", "73")
# real software work in the languages of the markets: development, implementation, integration, migration, building a system
_WORK = re.compile(r"(software development|development of|implementation|integration|migration|programming|custom software|bespoke|build(ing)? (a|the|an)? ?(system|platform|application)|"
                   r"wdro[zż]eni\w* (systemu|oprogramowania|aplikacji|platformy|rozwi[aą]zania)|integracj\w*|migracj\w*|budow\w* system\w*|rozbudow\w* system\w*|opracowani\w* (systemu|oprogramowania|aplikacji)|oprogramowani\w* dedykowan\w*|aplikacj\w*|"
                   r"entwicklung|implementierung|programmierung|systemintegration|utveckling|implementering|kehitt[aä]\w*|"
                   r"modernis\w*|moderniz\w*|digitalis\w*|digitaliz\w*|data platform|api|cloud)", re.I)
# goods, not services: equipment, furniture, teaching aids, printers, leasing
_GOODS = re.compile(r"(dostaw\w* (fabrycznie nowych )?(sprz[eę]tu|pomocy|mebli|wyposa[zż]eni\w*|urz[aą]dze\w*|drukar\w*)|pomocy dydaktycznych|mebl\w*|klimatyzac\w*|drukar\w*|dzier[zż]aw\w*|"
                    r"sprz[eę]t\w* (komputerow\w*|sportow\w*|medyczn\w*)|lieferung von|beschaffung von (hardware|ger[aä]ten)|supply of (computer )?(hardware|equipment)|delivery of equipment|furniture|laptops?|printers?|"
                    r"mro[zź]ni\w*|serwis pogwarancyjn\w*)", re.I)
# buying or renewing a product licence or vendor support is not engineering work for a partner
_LICENCE_ONLY = re.compile(r"(licencj\w*|licen[cs]e\w*|subskrypcj\w*|subscription|maintenance agreement|wsparci\w* techniczn\w*|technical support for|renewal)", re.I)


def services_relevant(title: str | None, scope: str | None, cpv: list[str]) -> bool:
    """Is this real software or IT services work (the kind The Codest does), not goods or a product licence?
    A generic software code buried in a long list of goods codes does not count: the MAIN code must be IT, or most codes, or the title must name the work."""
    text = f"{title or ''} {scope or ''}"
    codes = [str(c)[:8] for c in (cpv or []) if c]
    it = [c for c in codes if c.startswith(_IT_CPV)]
    primary_it = bool(codes) and codes[0].startswith(_IT_CPV)
    share = len(it) / len(codes) if codes else 0.0
    work = bool(_WORK.search(text))
    if _GOODS.search(text) and not work:
        return False
    if _LICENCE_ONLY.search(text) and not work:
        return False
    if codes and not it and not work:
        return False
    return work or primary_it or share > 0.5
