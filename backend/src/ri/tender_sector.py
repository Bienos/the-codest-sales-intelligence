"""Who is buying: the buyer's sector for every open tender, so the list can show our ICP (banks first, then other financial services, then the
other ICP sectors) and keep public-sector notices out of the way.

Decided by rules first, in this order (the first that applies wins):
  1. named financial institutions and unmistakable patterns: central and development banks, financial supervisors and deposit guarantee
     schemes, exchanges and clearing houses, savings and cooperative banks (Sparkasse, Volksbank, Raiffeisen, sparebank...), named banks;
  2. public bodies that only look financial: social and statutory health insurance (Krankenkasse, ZUS, KRUS, NFZ, Försäkringskassan...),
     ministries of finance, tax and customs offices, finance departments of universities and cities;
  3. the linked account's segment on our own lists (Banking, Insurance, Payments...), when the notice is linked to a company;
  4. the word "bank" in any of our languages (blood, food, data and tissue banks excluded), insurers, pension funds, asset managers,
     payment and lending companies; with a public-sector word in the same name the rule is not sure;
  5. weaker hints (finance words, a fund, CPV 66 financial services, a company without a public or utility word in its name): not sure.
A notice the rules are not sure about is summarised, and the model's answer (summary "buyer_sector") decides its sector. A notice the rules
are sure is not ICP is never sent to the model. The rules are pure functions of the buyer name, the linked account and the CPV codes, and
RULES_VERSION is part of the stored basis, so changing a rule reclassifies every open notice on the next read."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RULES_VERSION = 1
SECTORS = ("banking", "financial_services", "other_icp", "not_icp")
SECTOR_RANK = {"banking": 0, "financial_services": 1, "other_icp": 2, "not_icp": 3}
ICP = ("banking", "financial_services", "other_icp")
# What kind of buyer, within the sector (shown as a tag on the card).
KINDS = {
    "banking": ("bank", "central_bank", "development_bank", "savings_bank", "cooperative_bank", "building_society"),
    "financial_services": ("insurance", "payments", "market_infrastructure", "asset_management", "pension", "lending", "financial_software", "financial_authority"),
    "other_icp": ("digital_commerce", "enterprise_software", "adtech_martech"),
    "not_icp": ("public_body", "social_insurance", "public_finance", "company"),
}
# The fixed set the model chooses from (ri.tender_summary), and where each answer lands.
MODEL_SECTORS = {
    "bank": ("banking", "bank"), "central_bank": ("banking", "central_bank"), "development_bank": ("banking", "development_bank"),
    "insurance": ("financial_services", "insurance"), "payments": ("financial_services", "payments"),
    "capital_markets": ("financial_services", "market_infrastructure"), "asset_management": ("financial_services", "asset_management"),
    "pension_fund": ("financial_services", "pension"), "lending": ("financial_services", "lending"),
    "financial_software": ("financial_services", "financial_software"), "financial_authority": ("financial_services", "financial_authority"),
    "digital_commerce": ("other_icp", "digital_commerce"), "enterprise_software": ("other_icp", "enterprise_software"),
    "adtech_martech": ("other_icp", "adtech_martech"), "public_sector": ("not_icp", "public_body"), "other": ("not_icp", "company"),
}
PUBLIC_KINDS = {"central_bank", "development_bank", "financial_authority"}


@dataclass(frozen=True)
class Sector:
    sector: str              # banking, financial_services, other_icp, not_icp
    kind: str                # see KINDS
    public: bool = False     # owned or run by the state (central and development banks, supervisors, public pension and insurance bodies)
    sure: bool = True        # False: the model decides when it summarises the notice
    source: str = "rules"    # rules, account, model
    why: str = ""            # the words or field that decided it

    def as_row(self) -> dict[str, Any]:
        return {"buyer_sector": self.sector, "buyer_kind": self.kind, "buyer_public": self.public, "sector_sure": self.sure,
                "sector_source": self.source, "sector_why": self.why[:120]}


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.I)


B = r"(?<!\w)"          # word start
E = r"(?![\w])"         # word end

# ---- 1. unmistakable financial institutions
CENTRAL_BANK = _rx(
    rf"{B}(central bank|zentralbank\w*|bundesbank|\w*nationalbank\w*|national bank of|narodowy bank polski|narodna banka|národná banka|národní banka|nemzeti bank|"
    r"banca na[tț]ional[aă]|banca d'italia|banco de españa|banco de portugal|banque de france|banque centrale|banque nationale|de nederlandsche bank|nationale bank|"
    r"bank of (england|greece|lithuania|latvia|finland|slovenia|estonia|albania)|riksbank\w*|norges bank|suomen pankki|eesti pank|latvijas banka|lietuvos bankas|"
    rf"banka slovenije|seðlabanki|bank for international settlements|българска народна банка|τράπεζα της ελλάδος|ceska narodni banka){E}")
DEVELOPMENT_BANK = _rx(
    rf"{B}(kfw|kreditanstalt für wiederaufbau|\w*förderbank|\w*foerderbank|\w*investitionsbank|nrw\.?bank|l-bank|\w*rentenbank|bank gospodarstwa krajowego|bgk|"
    r"european investment (bank|fund)|eib|ebrd|european bank for reconstruction|nordic investment bank|\w*development bank|entwicklungsbank|razvojna banka|"
    r"british business bank|national wealth fund|uk infrastructure bank|scottish national investment bank|caisse des d[ée]p[ôo]ts|bpifrance|cassa depositi|"
    r"instituto de cr[ée]dito oficial|finnvera|v[æa]kstfonden|polski fundusz rozwoju|austria wirtschaftsservice|kontrollbank|uk export finance|"
    rf"export credit\w*|exportkredit\w*|eksportkreditt\w*|eksfin|kuke|eifo|investeringsfonden for udviklingslande|swedfund|norfund|almi företagspartner){E}")
AUTHORITY = _rx(
    rf"{B}(bafin|bundesanstalt für finanzdienstleistungsaufsicht|finanzmarktaufsicht|fma|finma|financial conduct authority|fca|prudential regulation authority|"
    r"komisj\w* nadzoru finansowego|knf|finansinspektionen|finanstilsynet|finanssivalvonta|fjármálaeftirlit\w*|the pensions regulator|payment systems regulator|"
    r"financial ombudsman\w*|rzecznik\w* finansow\w*|financial services compensation scheme|fscs|bankowy fundusz gwarancyjny|bfg|bankenes sikringsfond|"
    r"einlagensicherung\w*|\w*sicherungsfonds?|deposit (guarantee|protection)\w*|garantiformuen|finansiel stabilitet|single resolution board|european banking authority|"
    r"eiopa|esma|european securities and markets|finanzagentur|debt management (office|agency)|national treasury management|riksgälden\w*|"
    rf"money and pensions service|financial supervis\w*|finanzaufsicht|dg fisma|resolution (authority|fund)|financial stability board){E}")
MARKET_INFRA = _rx(
    rf"{B}(\w*b[öo]rse|boerse\w*|stock exchange|securities exchange|gie[łl]d\w* papier\w*|towarowa gie[łl]da\w*|krajowy depozyt\w*|kdpw|krajowa izba rozliczeniowa|"
    r"clearstream|euroclear|euronext|nasdaq|lch|clearing house|central counterparty|central securities depository|verdipapirsentral\w*|vp securities|"
    rf"bankgirot|bankgirocentralen|pay\.uk|swift|six group|oslo b[øo]rs|nord pool|european energy exchange|london metal exchange|lseg){E}")
SAVINGS_BANK = _rx(rf"{B}(\w*sparkass\w*|sparebank\w*|sparbank\w*|sparekass\w*|caisse d'[ée]pargne|cassa di risparmio|caja de ahorros|s[äa]ästöpankki|savings bank){E}")
COOP_BANK = _rx(rf"{B}(\w*volksbank\w*|raiffeisen\w*|genossenschaftsbank|dz bank|bank\w* spółdzielcz\w*|spółdzielcz\w* bank\w*|osuuspankki|andelskasse\w*|"
                rf"cooperative bank|co-operative bank|credit union|spółdzielcza kasa oszczędnościowo\w*|skok){E}")
BUILDING_SOCIETY = _rx(rf"{B}(building society|\w*bausparkasse){E}")
NAMED_BANK = _rx(
    rf"{B}(girozentrale|pko bank polski|pko bp|bank pekao|pekao|mbank|santander bank|ing bank|alior bank|bnp paribas bank|bank millennium|credit agricole bank|bank ochrony środowiska|"
    r"handelsbanken|nordea|danske bank|dnb bank|swedbank|seb|landesbank\w*|helaba|bayernlb|nord/lb|lbbw|dekabank|deka|aareal|commerzbank|deutsche bank|"
    r"hypovereinsbank|unicredit|erste (bank|group)|bawag|bank austria|\w*kantonalbank|postbank|postfinance|ubs|credit suisse|julius b[äa]r|vontobel|"
    r"lloyds banking|barclays|hsbc|natwest|tsb bank|metro bank|starling bank|monzo|revolut|national savings and investments|ns&i|nationwide building society|"
    rf"op financial|op ryhmä|aktia|jyske bank|nykredit|sydbank|arion|íslandsbanki|landsbankinn){E}")
FIN_SOFTWARE = _rx(rf"{B}(finanz informatik|atruvia|fiducia|core banking|banking software|fintech\w*){E}")

# ---- 2. public bodies that only look financial
SOCIAL_INSURANCE = _rx(
    rf"{B}(\w*krankenkasse\w*|ersatzkasse\w*|bkk|aok|knappschaft\w*|\w*rentenversicherung\w*|\w*unfallversicherung\w*|unfallkasse\w*|berufsgenossenschaft\w*|"
    r"\w*sozialversicherung\w*|\w*pensionsversicherung\w*|gesundheitskasse|gesetzliche\w* kranken\w*|gkv\w*|försäkringskassan|pensionsmyndigheten|"
    r"statens tjänstepensionsverk|zakład ubezpieczeń społecznych|zus|kas\w* rolniczego ubezpieczenia|krus|narodow\w* fundusz\w* zdrowia|nfz|"
    r"arbeids- og velferds\w*|nav|kela|kansaneläkelaitos|udbetaling danmark|social security|national insurance|work and pensions|dwp|"
    r"assurance maladie|allocations familiales|caisse nationale|suva|ausgleichskasse\w*|familienkasse|arbeitslosen\w*|sociale verzekering\w*|"
    rf"uwv|ubezpiecze\w* społeczn\w*|social insurance|health insurance fund|nhs pensions){E}")
PUBLIC_FINANCE = _rx(
    rf"{B}((ministry|ministerium|ministerstw\w*|ministère|ministerio|ministarstvo|ministrstvo|ministerie|ministerstvo|departement\w*|department)\W+(\w+\W+){{0,3}}"
    r"(financ\w*|finanz\w*|finans\w*|funduszy)|\w*finanzministerium|finansų ministerij\w*|finansdepartement\w*|hm treasury|treasury department|"
    r"\w*finanzamt\w*|\w*finanzverwaltung|\w*finanzdirektion|landesamt für finanzen|bundesamt für finanzen|zentrale beschaffungsstelle der bundesfinanzverwaltung|"
    r"\w*finanzbehörde|senatsverwaltung für finanzen|inspection générale des finances|centrum informatyki resortu finansów|krajow\w* administracj\w* skarbow\w*|"
    r"izb\w* administracji skarbowej|\w*skarbow\w*|tax|taxes|hmrc|revenue|skatteverket|skatteetaten|skattestyrelsen|verohallinto|valstybinė mokesčių|"
    rf"customs|\w*zoll\w*|tullverket|muitin\w*|kammarkollegiet|ekonomistyrningsverket|dezernat finanzen|finanzabteilung|rechenzentrum der finanzverwaltung){E}")
NOT_A_FINANCE_BANK = _rx(
    rf"{B}((blood|food|tissue|milk|data|seed|gene|sperm|skin|eye|bone|image|time|land|bio|cord)\s?-?bank\w*|bank\w* (krwi|tkanek|żywności|mleka|komórek|danych)|"
    r"\w*datenbank\w*|\w*blutbank\w*|\w*bildbank\w*|\w*samenbank\w*|\w*genbank\w*|\w*biobank\w*|bankside|banksia|bank holiday|bankstown|fairbanks|"
    rf"south ?bank|north ?bank|west ?bank|banks? (road|street|lane)){E}")
QUALITY_ASSURANCE = _rx(rf"{B}(quality assurance|qualitätssicherung){E}")

# ---- 4. the generic words
BANK = _rx(rf"{B}(bank\w*|\w+bank(en|s)?|banca|banche|banque\w*|banco|banka|bankas|pank|pankki|τράπεζα|банка){E}")
INSURANCE = _rx(
    rf"{B}(\w*versicherung\w*|\w*versicherer\w*|\w*versicherungs\w*|insurance|insurer\w*|reinsur\w*|assurance\w*|assureur\w*|assicura\w*|aseguradora\w*|seguros|"
    rf"\w*ubezpiecze\w*|\w*f[öo]rs[äa]kring\w*|\w*forsikring\w*|\w*vakuutus\w*|verzekering\w*|lloyd's|mutuelle\w*|pzu|warta|ergo hestia){E}")
PENSION = _rx(
    rf"{B}(pension\w*|pensjon\w*|pensions?kass\w*|pensionsfonds|pensioenfonds|\w*versorgungswerk\w*|\w*versorgungskasse\w*|versorgungskammer|zusatzversorgung\w*|"
    r"versorgungsanstalt\w*|personalvorsorge|vorsorgestiftung|vorsorgeeinrichtung|superannuation|\w*eläke\w*|tillægspension|ap-fonden|ap7|"
    rf"fundusz\w* emerytaln\w*|powszechne towarzystwo emerytalne|pte|alecta|folksam|amf pension|caisse de retraite|caisse de prévoyance\w*|retraite\w*){E}")
PENSION_PROVIDER = _rx(rf"{B}(fund|funds|scheme|partnership|plan|trust|pool|foundation|\w*(fonds?|fonden|kasse|kassa|werk|kammer|anstalt|stiftung)|tillægspension|eläkeyhtiö|työeläke\w*){E}")
PAYMENTS = _rx(rf"{B}(payments?|zahlungsverkehr|zahlungsdienst\w*|p[łl]atno[śs]c\w*|e-money|card acquir\w*|visa europe|mastercard|klarna|adyen|worldline|nexi|nets|vipps|mobilepay|swish){E}")
LENDING = _rx(rf"{B}(leasing|factoring|kredit\w*|credit\w*|crédit|kredyt\w*|lending|lender\w*|loans?|hypothek\w*|mortgage\w*){E}")
ASSET_MGMT = _rx(
    rf"{B}(asset management|investment management|fund management|kapitalverwaltung\w*|kapitalanlage\w*|fondsgesellschaft|fondsmægler\w*|kapitalforvaltning|"
    rf"towarzystw\w* funduszy inwestycyjnych|tfi|wealth management|verm[öo]gensverwaltung\w*|private equity|folketrygdfondet|nbim){E}")
OTHER_ICP = _rx(rf"{B}(e-?commerce|online shop|webshop|marketplace|onlinehandel|handel internetowy|sklep internetowy|adtech|martech|advertising technology|softwarehouse|software house){E}")

# ---- 5. weaker hints
WEAK_FINANCE = _rx(rf"{B}(financ\w*|\w*finanz\w*|finans\w*|treasury|capital|invest\w*|fund|funds|fonds|fundusz\w*|rahasto\w*|kasse|kassa|kasa|caisse|exchange|clearing|securities|wertpapier\w*|bourse){E}")
COMPANY = _rx(rf"{B}(ag|gmbh|kg|kgaa|se|sa|s\.a\.|spa|s\.p\.a\.|srl|s\.l\.|nv|n\.v\.|bv|b\.v\.|ab|asa|as|a/s|aps|oy|oyj|plc|ltd|limited|llc|inc|"
                 rf"s\.r\.o\.|a\.s\.|d\.o\.o\.|d\.d\.|zrt\.?|sp\. z o\.o\.|spółka akcyjna|spółka z ograniczoną odpowiedzialnością|aktiengesellschaft){E}")
# Public-sector words (ministries, cities, agencies, hospitals, schools, police...) in the languages of our markets and the countries TED covers.
PUBLIC = _rx(
    rf"{B}(ministry|ministerium|\w*ministerium|ministerie|ministerstw\w*|minist[èe]re|ministerio|ministarstvo|ministrstvo|ministerstvo|ministeri\w*|departement\w*|"
    r"department (of|for)|home office|cabinet office|\w*council|municipal\w*|borough|city of|county|gemeinde\w*|\w*stadt|stadt\w*|landkreis|landratsamt|bezirk\w*|"
    r"kommun\w*|kommune|region\w*|landsting|gmina|powiat\w*|starostwo|miasto|miasta|województw\w*|urząd\w*|urzad\w*|nhs|hospital\w*|\w*krankenhaus\w*|\w*klinik\w*|"
    r"sjukhus\w*|sykehus\w*|szpital\w*|universit\w*|uniwersytet\w*|politechnik\w*|hochschule\w*|college|school\w*|schule\w*|szkoł\w*|akademi\w*|academy|"
    r"police|polizei\w*|polis\w*|policj\w*|fire (and rescue )?service|feuerwehr|bundesamt|landesamt|\w*amt|amt\w*|agency|agentur\w*|agencj\w*|aģentūra|agentūra|"
    r"\w*myndighet\w*|\w*verket|\w*etaten|\w*direktoratet|\w*styrelsen|\w*tilsynet|virasto|keskus|government|regierung|regering\w*|rząd\w*|parliament|bundestag|landtag|"
    r"embassy|armed forces|bundeswehr|defen[cs]e|army|navy|court|gericht\w*|sąd\w*|prokuratur\w*|staatsanwalt\w*|institut\w*|instytut\w*|museum|muzeum|library|biblioteka|"
    r"państwow\w*|krajow\w*|narodow\w*|staatlich\w*|statens|state|states|riigi|valstybės|federal|bundes\w*|freistaat|land berlin|kanton|canton\w*|république|republic|"
    r"aör|anstalt des öffentlichen rechts|körperschaft des öffentlichen rechts|zweckverband|stiftung|foundation|stichting|fundacja|fundació|fundación|"
    r"european (commission|parliament|union)|eu-lisa|\w*komission|commission|authority|behörde|\w*behörde|zakład\w*|inspektorat\w*|służb\w*|straż\w*|"
    r"\w*werke|stadtwerke\w*|energie\w*|energy|energi\w*|strom|power|elektr\w*|electric\w*|wasser\w*|water|vatten\w*|vann\w*|wodociąg\w*|gas|gaz\w*|netz\w*|grid|"
    r"bahn\w*|rail\w*|kolej\w*|pkp|transport\w*|transmission|verkehr\w*|traffic|\w*traffic|liikenne\w*|metro|tram\w*|airport|flughafen|lotnisk\w*|lennujaam|avinor|port of|"
    r"hafen\w*|hamn\w*|havn\w*|poczta|post|infra\w*|\w*infrastru\w*|abfall\w*|\w*avfall\w*|waste|odpad\w*|ceste|\w*avtocest\w*|autostrad\w*|kelių|\w*grid|"
    r"rundfunk|radio|televisi\w*|televizi\w*|broadcast\w*|nemocnic\w*|medyczn\w*|medical|\w*klinik\w*|laborator\w*|forschung\w*|research|helmholtz\w*|"
    r"wohnungs\w*|bostad\w*|\w*bostäder|housing|catering|consip|enedis|edf|enel|enexis|e\.on|eon|rwe|uniper|"
    r"theat\w*|orchest\w*|gesundheit\w*|health\w*|zdrow\w*|komunaln\w*|komunikacyjn\w*|mieszkaniow\w*|\w*netze?|plyn\w*|plinovod\w*|kommunal\w*|"
    r"ammattikorkeakoulu|\w*yrkeshögskola|\w*högskola|hogeschool|sairaala\w*|terveys\w*|plovidb\w*|šume|lasy|forst\w*|"
    rf"orlen|tauron|energa|enea|pge|vattenfall|fortum|ørsted|statkraft|equinor|asfinag|autobahn\w*|highways|motorway\w*){E}")
# fund-like names that are public bodies whatever else they say
PUBLIC_FUND = _rx(rf"{B}(fundusz\w* (ochrony|rehabilitacji|zdrowia|pracy|rozwoju regionalnego|polityki)|ministerstwo funduszy|noodfonds\w*|innovationsfonden|forskningsfond\w*|"
                  rf"lotteri\w*|lottery|kulturfond\w*|regionalfonds){E}")
CPV_FINANCE = re.compile(r"^66")
SEGMENT_SECTOR = [
    (re.compile(r"\b(bank\w*|banca|banque|building societ\w*|sparkass\w*|credit union)\b", re.I), ("banking", "bank")),
    (re.compile(r"insur\w*|reinsur\w*|versicher\w*", re.I), ("financial_services", "insurance")),
    (re.compile(r"payment\w*", re.I), ("financial_services", "payments")),
    (re.compile(r"capital market\w*|exchange\w*|clearing|securities|brokerage", re.I), ("financial_services", "market_infrastructure")),
    (re.compile(r"asset management|wealth|investment|fund\b|private equity", re.I), ("financial_services", "asset_management")),
    (re.compile(r"pension\w*", re.I), ("financial_services", "pension")),
    (re.compile(r"lending|lender\w*|leasing|mortgage\w*|credit\b|factoring", re.I), ("financial_services", "lending")),
    (re.compile(r"financial software|fintech|regtech|financial data|insurtech|wealthtech", re.I), ("financial_services", "financial_software")),
    (re.compile(r"financial services|finance|financial", re.I), ("financial_services", "lending")),
    (re.compile(r"commerce|retail|marketplace", re.I), ("other_icp", "digital_commerce")),
    (re.compile(r"adtech|martech|advertis\w*|marketing tech", re.I), ("other_icp", "adtech_martech")),
    (re.compile(r"enterprise software|procurement ?tech|saas|software", re.I), ("other_icp", "enterprise_software")),
]


def _hit(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text)
    return m.group(0).strip() if m else ""


def _segment(*values: str | None) -> tuple[str, str] | None:
    for v in values:
        if not v or not v.strip():
            continue
        for rx, out in SEGMENT_SECTOR:
            if rx.search(v):
                return out
    return None


def classify(name: str | None, *, segment: str | None = None, list_segment: str | None = None, list_eligibility: str | None = None,
             entity_type: str | None = None, cpv: list[str] | tuple[str, ...] | None = None, model_sector: str | None = None) -> Sector:
    """The buyer's sector by the rules above. `segment`, `list_segment`, `list_eligibility` and `entity_type` come from the linked account,
    `model_sector` from the summary (one of MODEL_SECTORS), used only where the rules are not sure."""
    n = re.sub(r"\s+", " ", name or "").strip()
    rule = _rules(n, segment=segment, list_segment=list_segment, list_eligibility=list_eligibility, entity_type=entity_type, cpv=cpv)
    if rule.sure or model_sector not in MODEL_SECTORS:
        return rule
    sector, kind = MODEL_SECTORS[model_sector]
    public = kind in PUBLIC_KINDS or (rule.public and sector == rule.sector)
    return Sector(sector, kind, public, True, "model", f"model: {model_sector}")


def _rules(n: str, *, segment: str | None, list_segment: str | None, list_eligibility: str | None, entity_type: str | None,
           cpv: list[str] | tuple[str, ...] | None) -> Sector:
    public_word = _hit(PUBLIC, n) or ("public body" if entity_type == "public_body" else "")
    # 1. unmistakable
    for rx, sector, kind, public in ((CENTRAL_BANK, "banking", "central_bank", True), (DEVELOPMENT_BANK, "banking", "development_bank", True),
                                     (AUTHORITY, "financial_services", "financial_authority", True),
                                     (MARKET_INFRA, "financial_services", "market_infrastructure", False)):
        if w := _hit(rx, n):
            return Sector(sector, kind, public, True, "rules", w)
    social = _hit(SOCIAL_INSURANCE, n)
    if not social:
        for rx, kind in ((NAMED_BANK, "bank"), (SAVINGS_BANK, "savings_bank"), (COOP_BANK, "cooperative_bank"), (BUILDING_SOCIETY, "building_society")):
            if w := _hit(rx, n):
                return Sector("banking", kind, False, True, "rules", w)
        if w := _hit(FIN_SOFTWARE, n):
            return Sector("financial_services", "financial_software", False, True, "rules", w)
    # 2. public bodies that only look financial
    if social:
        return Sector("not_icp", "social_insurance", True, True, "rules", social)
    if w := _hit(PUBLIC_FINANCE, n) or _hit(PUBLIC_FUND, n):
        return Sector("not_icp", "public_finance", True, True, "rules", w)
    # 3. the linked account on our lists
    seg = _segment(list_segment if list_segment and not re.fullmatch(r"(?i)enterprise|scale-?up|large bank|review", list_segment.strip()) else None, segment)
    if seg and entity_type != "public_body" and (list_eligibility or "").lower() != "out of scope":
        return Sector(seg[0], seg[1], False, True, "account", f"account segment: {segment or list_segment}")
    # 4. the generic words; a public-sector word next to them leaves the decision to the model
    unsure_public = bool(public_word)
    if (w := _hit(BANK, n)) and not _hit(NOT_A_FINANCE_BANK, n):
        return Sector("banking", "bank", unsure_public, not unsure_public, "rules", w)
    if (w := _hit(INSURANCE, n)) and not _hit(QUALITY_ASSURANCE, n):
        return Sector("financial_services", "insurance", unsure_public, not unsure_public, "rules", w)
    if w := _hit(PENSION, n):
        sure = bool(_hit(PENSION_PROVIDER, n)) or not unsure_public
        return Sector("financial_services", "pension", unsure_public, sure, "rules", w)
    for rx, kind in ((ASSET_MGMT, "asset_management"), (PAYMENTS, "payments"), (LENDING, "lending")):
        if w := _hit(rx, n):
            return Sector("financial_services", kind, unsure_public, not unsure_public, "rules", w)
    if w := _hit(OTHER_ICP, n):
        return Sector("other_icp", "enterprise_software" if "soft" in w.lower() else "digital_commerce", False, False, "rules", w)
    # 5. weaker hints: the model decides unless the name is plainly a public body
    if public_word:
        return Sector("not_icp", "public_body", True, True, "rules", public_word)
    if w := _hit(WEAK_FINANCE, n):
        return Sector("not_icp", "company", False, False, "rules", f"finance word: {w}")
    if any(CPV_FINANCE.match(str(c or "")) for c in (cpv or ())):
        return Sector("not_icp", "company", False, False, "rules", "CPV 66 financial services")
    if w := _hit(COMPANY, n):
        return Sector("not_icp", "company", False, False, "rules", f"company: {w}")
    return Sector("not_icp", "public_body", False, True, "rules", "no financial or company sign in the name")


# ------------------------------------------------------------------------------------------------ stored on the notice
# What the stored classification was made from: a changed name, linked account, CPV codes, model answer or rule version makes it due again.
BASIS_SQL = ("md5(concat_ws('|', %s::text, n.buyer_name, n.buyer_org_id::text, o.segment, o.list_icp_segment, o.list_eligibility, o.entity_type, "
             "array_to_string(n.service_codes, ','), n.summary->>'buyer_sector'))")


def classify_open(conn: Any, *, limit: int = 5000) -> int:
    """Classify every open notice whose stored sector is missing or out of date (normally none: one cheap query). Returns how many changed."""
    from .db import qa
    from .tenders import open_relevant_sql
    rel, args = open_relevant_sql("n")
    rows = qa(conn, f"""
        SELECT n.id, n.buyer_name, n.service_codes, n.summary->>'buyer_sector' AS model_sector,
               o.segment, o.list_icp_segment, o.list_eligibility, o.entity_type, {BASIS_SQL} AS basis
        FROM procurement_notices n LEFT JOIN organizations o ON o.id = n.buyer_org_id
        WHERE {rel} AND n.sector_basis IS DISTINCT FROM {BASIS_SQL}
        LIMIT %s""", [RULES_VERSION, *args, RULES_VERSION, limit])      # noqa: S608
    for r in rows:
        s = classify(r["buyer_name"], segment=r["segment"], list_segment=r["list_icp_segment"], list_eligibility=r["list_eligibility"],
                     entity_type=r["entity_type"], cpv=r["service_codes"], model_sector=r["model_sector"])
        row = s.as_row()
        conn.execute("UPDATE procurement_notices SET buyer_sector=%(buyer_sector)s, buyer_kind=%(buyer_kind)s, buyer_public=%(buyer_public)s, "
                     "sector_sure=%(sector_sure)s, sector_source=%(sector_source)s, sector_why=%(sector_why)s, sector_basis=%(basis)s WHERE id=%(id)s",
                     {**row, "basis": r["basis"], "id": r["id"]})
    return len(rows)
