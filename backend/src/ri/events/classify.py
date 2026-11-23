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


# CPV codes for engineering SERVICES we sell: programming, custom development, systems analysis, implementation, configuration, web and app development.
# Not support desks (72253, 72261, 726xx), maintenance (72267), licence supply (72268), data entry (723xx), networks, hardware or software packages (48).
_ENG_CPV = ("7221", "7222", "7223", "7224", "722540", "72260000", "72262", "72263", "72265", "72266", "72413", "7242")
_UMBRELLA_CPV = ("72000000", "72200000", "72500000")
_HW_CPV = ("30", "31", "32", "35", "38", "4882", "45")   # computers, electrical, network, security and test equipment, servers, construction
# a software object: what is being built, implemented or integrated
_OBJ = (r"(software|system|systems|platform|application|applications|app|apps|website|web ?site|web|portal|digital|data|cloud|api|apis|crm|erp|cms|"
        r"database|e-?service|online service|it service|mobile)")
_WORK = re.compile(
    r"\b(develop\w*|build\w*|implement\w*|moderni[sz]\w*|migrat\w*|integrat\w*|re-?platform\w*|re-?build\w*|re-?design\w*)\b(\W+\w+){0,4}?\W+" + _OBJ + r"\b|"
    r"\b" + _OBJ + r"\W+(development|engineering|implementation|integration|migration|modernisation|modernization|delivery partner|build)\b|"
    r"\b(custom|bespoke) (software|application|system)|\bagile (delivery|development)\b|\bdigital (development|delivery|transformation) partner|"
    r"\b(wdro[zż]\w*|budow\w*|rozbudow\w*|modernizac\w*|modyfikac\w*|opracowani\w*|wykonani\w*|rozw[oó]j\w*|integracj\w*|migracj\w*)\W+(\w+\W+){0,2}?"
    r"(systemu|system\w*|oprogramowani\w*|aplikacj\w*|platform\w*|portalu|serwisu internetowego|rozwi[aą]zani\w* informatyczn\w*|danych)\b|"
    r"\boprogramowani\w* dedykowan\w*|\bus[lł]ug\w* programistyczn\w*|"
    r"\b(softwareentwicklung|anwendungsentwicklung|systemintegration|programmierung|implementierung|digitalisierung\w*)\b|\bentwicklung (einer |eines |der |des )?(software|anwendung|plattform|app|system)|"
    r"\b(systemutveckling|mjukvaruutveckling|programvaruutveckling|systemutvikling|programvareutvikling|ohjelmistokehit\w*|j[aä]rjestelm[aä]kehit\w*)\b|\butveckling av (system|plattform|app|tj[aä]nst)",
    re.I)
# goods, not services: equipment, servers, furniture, teaching aids, printers, leasing
_GOODS = re.compile(r"(dostaw\w* (fabrycznie nowych )?(sprz[eę]tu|pomocy|mebli|wyposa[zż]eni\w*|urz[aą]dze\w*|drukar\w*|serwer\w*|macierz\w*|infrastruktur\w*)|pomocy dydaktycznych|mebl\w*|klimatyzac\w*|drukar\w*|dzier[zż]aw\w*|"
                    r"sprz[eę]t\w* (komputerow\w*|sportow\w*|medyczn\w*)|\bserwer\w*|\bmacierz\w*|infrastruktur\w* serwerow\w*|lieferung von|beschaffung von (hardware|ger[aä]ten)|supply of (computer )?(hardware|equipment)|delivery of equipment|furniture|laptops?|printers?|\bservers?\b|"
                    r"mro[zź]ni\w*|serwis pogwarancyjn\w*|access points?|kontroler\w* sieci|prze[lł][aą]czni\w*|\brout(er|ery)\b|wifi|wi-fi)", re.I)
# buying, renewing or getting access to a product, or a vendor's support desk, is not engineering work for a partner
_LICENCE_ONLY = re.compile(r"(us[lł]ug\w* serwisow\w*|nadz[oó]r\w* autorsk\w*|serwis\w* (oprogramowania|system\w*)|licencj\w*|licen[cs]e\w*|lizenz\w*|verl[aä]ngerung|wartungs\w*|opiek\w* serwisow\w*|przed[lł]u[zż]eni\w*|subskrypcj\w*|subscription|maintenance agreement|wsparci\w* techniczn\w*|asyst\w* techniczn\w*|technical support for|renewal|"
                           r"dost[eę]p\w* do (programu|oprogramowania|systemu|platformy)|\bzakup (systemu|oprogramowania|licencji)|security operations cent\w*|\busług\w* soc\b|\bsoc\b)", re.I)
_NAMES_SYSTEM = re.compile(r"(\b(erp|crm|cms|ict|core banking|platform\w*|software|application\w*|oprogramowani\w*|aplikacj\w*|sovellu\w*|konsult\w*|consultants?|developers?|"
                           r"entwicklung\w*|utvikl\w*|utveckl\w*|kehit\w*)\b|system|j[aä]rjestelm|konsult|webb)", re.I)
# running someone's estate is not building software: hosting, operations, managed services, monitoring
_OPS = re.compile(r"\b(it operations|operation and maintenance|operations and maintenance|managed (kubernetes|services?)|hosting|serverdrift|drift(s|en)?\b|betrieb\w*|"
                  r"service desk|helpdesk|monitoring|utrzymani\w* infrastruktur\w*)", re.I)
# a software-package code alone means buying a product; only clearly custom work turns it into engineering
_CUSTOM = re.compile(r"\b(custom|bespoke|dedykowan\w*|indywidualn\w*|entwicklung\w*|digitalisierung\w*|utveckling|kehitt\w*|software development)\b", re.I)
# TED summaries read "Country – CPV label – title"; the label ('IT services: consulting, software development, ...') says nothing about this tender
_TED_PREFIX = re.compile(r"^\s*[A-Z][A-Za-z .'-]{2,30} – [^–]{3,140}? – ")


def services_relevant(title: str | None, scope: str | None, cpv: list[str]) -> bool:
    """Is this real software engineering work (the kind The Codest does), not goods, a product licence, a support desk or a non-IT 'development'?
    Needs either an engineering-services code that is not lost among goods codes, or wording that pairs a build/implement verb with a software object."""
    text = _TED_PREFIX.sub("", f"{title or ''} {scope or ''}".strip(), count=1)
    codes = [str(c)[:8] for c in (cpv or []) if c]
    work = bool(_WORK.search(text))
    if _OPS.search(text) and not work:
        return False
    if _LICENCE_ONLY.search(text) and not work:
        return False
    if _GOODS.search(text) and not work:
        return False
    if not codes:
        return work
    eng = [c for c in codes if c.startswith(_ENG_CPV)]
    hw = [c for c in codes if c.startswith(_HW_CPV)]
    if all(c.startswith("48") for c in codes) and not _CUSTOM.search(text):
        return False
    if len(hw) * 2 >= len(codes) and not work:
        return False
    if eng:
        if len(eng) * 3 < len(codes) and hw and not work:     # one implementation code inside a bundle of equipment
            return False
        return True
    if work:
        return True
    return any(c.startswith(_UMBRELLA_CPV) for c in codes) and bool(_NAMES_SYSTEM.search(text))
