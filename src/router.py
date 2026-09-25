"""Query router: decides which NAFDAC pages to crawl for a given question,
so the user never has to pick start URLs by hand."""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote_plus

BASE = "https://nafdac.gov.ng"

# The live, current registration database (all categories, including herbals) — a separate
# subdomain from BASE. Its homepage renders its results table client-side via AJAX, so the
# crawler must type a search term in and wait; see crawler.search_greenbook().
GREENBOOK_URL = "https://greenbook.nafdac.gov.ng/"

# Each topic: keywords (matched as substrings of the lowercased query) -> pages to seed.
# "abs_urls" (optional) are fully-qualified URLs added as-is, not joined onto `base`.
SITE_MAP: List[Dict] = [
    {"topic": "alerts", "keywords": ["alert", "recall", "counterfeit", "fake", "falsified", "substandard", "warning", "poisoning", "seizure"],
     "urls": ["/category/recalls-and-alerts/", "/field-safety-alerts/"]},
    {"topic": "field_safety", "keywords": ["field safety", "fsn", "syringe", "condom", "medical device recall"],
     "urls": ["/field-safety-alerts/"]},
    {"topic": "withdrawn", "keywords": ["withdraw", "suspended", "cancelled", "canceled", "deregist", "delist", "no longer authori", "still authori", "market authorization", "market authorisation"],
     "urls": ["/our-services/market-authorization-withdrawal/"]},
    {"topic": "blacklist", "keywords": ["blacklist", "black list", "debarred", "non-compliant", "non compliant"],
     "urls": ["/blacklist-of-companies/"]},
    {"topic": "watchlist", "keywords": ["watchlist", "watch list", "watch-list"],
     "urls": ["/watchlist-of-companies-2/"]},
    {"topic": "herbal_products", "keywords": ["herbal", "herbs", "nutraceutical", "nutraceuticals"],
     "urls": ["/herbal-products-database/"], "abs_urls": [GREENBOOK_URL],
     "greenbook": True, "companions": ["alerts", "withdrawn"]},
    {"topic": "registered_products", "keywords": ["registered", "registration number", "reg no", "reg. no", "nrn", "greenbook", "green book", "product database", "is this product", "is this drug", "approved", "licensed", "licenced", "nafdac number", "nafdac no"],
     "urls": ["/productstable/"], "abs_urls": [GREENBOOK_URL], "greenbook": True, "companions": ["alerts", "withdrawn"]},
    {"topic": "registration_process", "keywords": ["how to register", "register a", "registration process", "how long", "timeline", "dossier", "application", "renew", "variation"],
     "urls": ["/our-services/product-registrationevaluation/", "/our-services/"]},
    {"topic": "fees", "keywords": ["tariff", "fee", "cost", "charge", "price", "payment", "how much"],
     "urls": ["/resources/nafdac-tariff", "/regulatory-resources/nafdac-tariff/"]},
    {"topic": "guidelines", "keywords": ["guideline", "requirement", "labelling", "labeling", "label", "standard", "notes to industry", "compliance"],
     "urls": ["/resources/guidelines/", "/our-services/notes-to-industry/"]},
    {"topic": "regulations", "keywords": ["regulation", "law", "act ", "gazette", "legal", "directive", "draft"],
     "urls": ["/resources/nafdac-regulations/", "/about-nafdac/nafdac-laws/", "/regulatory-resources/regulatory-directive/", "/draft-regulations/"]},
    {"topic": "clinical_trials", "keywords": ["clinical trial", "trial", "cro", "nhrec"],
     "urls": ["/clinical-trial-database/", "/drugs/application-for-clinical-trials/", "/our-services/clinical-trials-cro-eligibility-criteria/"]},
    {"topic": "inspection", "keywords": ["inspection", "gmp", "facility", "facilities", "foreign manufactur", "port", "clearance"],
     "urls": ["/inspection-classification-database/", "/our-services/ports-inspection/", "/regulatory-resources/foreign-gmp-plan/"]},
    {"topic": "exports", "keywords": ["export", "import"],
     "urls": ["/our-services/categories-of-exports/", "/our-services/ports-inspection/"]},
    {"topic": "traceability", "keywords": ["traceab", "authentic", "verify", "genuine", "scratch", "mas ", "track and trace"],
     "urls": ["/traceability/", "/our-services/pharmacovigilance-post-market-surveillance/mobile-authentication-service-mas/"],
     "greenbook": True, "companions": ["alerts", "withdrawn"]},
    {"topic": "pharmacovigilance", "keywords": ["adverse", "side effect", "reaction", "pharmacovigilance", "med safety", "report a", "post market", "surveillance"],
     "urls": ["/our-services/pharmacovigilance-post-market-surveillance/"]},
    {"topic": "news", "keywords": ["press", "news", "announce", "statement", "speech", "event", "address", "launch", "inaugurat", "conference", "training"],
     "urls": ["/category/press-release/", "/category/latest-news/", "/category/speeches/", "/category/events/", "/nafdac-newsroom/"]},
    {"topic": "contact", "keywords": ["contact", "phone", "email", "address of", "office", "state office", "complain", "enquir", "inquir", "hotline"],
     "urls": ["/about-nafdac/contact-nafdac/", "/about-nafdac/contact-nafdac/complaints-and-enquiries/"]},
    {"topic": "about", "keywords": ["who is", "director general", " dg", "management", "vision", "mission", "history", "founded", "established", "council", "strategic plan", "career", "working at", "job"],
     "urls": ["/about-nafdac/director-generals-page/", "/about-nafdac/nafdac-management/", "/about-nafdac/nafdac-organisation/", "/about-nafdac/nafdac-vision-and-mission/", "/about-nafdac/working-at-nafdac/"]},
    {"topic": "msme", "keywords": ["msme", "small business", "small and medium", "micro"],
     "urls": ["/our-services/micro-small-medium-enterprises-msme1/"]},
    {"topic": "bioequivalence", "keywords": ["bioequivalence", "bioavailability", "generic"],
     "urls": ["/bioequivalence/"]},
    {"topic": "drugs", "keywords": ["drug", "medicine", "pharma", "herbal", "tablet", "syrup", "antimalarial", "napar"],
     "urls": ["/drugs/"]},
    {"topic": "food", "keywords": ["food", "water", "beverage", "bread", "alcohol", "drink", "rutf", "fortif", "nutrition", "breastfeeding"],
     "urls": ["/food/"]},
    {"topic": "drugs", "keywords": ["drug", "medicine", "pharma", "tablet", "syrup", "antimalarial", "napar"],
     "urls": ["/drug/", "/drugs/"], "abs_urls": [GREENBOOK_URL],
     "greenbook": True,
     "companions": ["alerts", "withdrawn"]},
    {"topic": "cosmetics", "keywords": ["cosmetic", "toothpaste", "cream", "soap", "lotion", "medical device"],
     "urls": ["/cosmetics-medical-devices/"]},
    {"topic": "vaccines", "keywords": ["vaccine", "biological"],
     "urls": ["/vaccines-biologicals/"]},
    {"topic": "chemicals", "keywords": ["chemical", "detergent", "pesticide"],
     "urls": ["/chemicals/"]},
    {"topic": "veterinary", "keywords": ["veterinary", "animal", "livestock"],
     "urls": ["/veterinary/"]},
    {"topic": "narcotics", "keywords": ["narcotic", "controlled substance", "opioid", "tramadol", "codeine"],
     "urls": ["/narcotics/"]},
    {"topic": "faq", "keywords": ["faq", "frequently asked"],
     "urls": ["/frequently-asked-questions/"]}
]

MAX_TOPICS = 3

CATEGORY_KEYWORDS = {
    "herbal": "Herbals and Nutraceuticals",
    "nutraceutical": "Herbals and Nutraceuticals",
    "vaccine": "Vaccines and Biologics",
    "biologic": "Vaccines and Biologics",
    "drug": "Drugs",
    "medicine": "Drugs",
    "device": "Medical devices",
    "veterinary": "Veterinary",
    "disinfectant": "Disinfectants"
}


# Words that are part of the question, not of the product name.
_TERM_STRIP = {
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "can", "could", "would", "should", "will",
    "the", "a", "an", "of", "for", "to", "in", "on", "at", "by", "with", "from", "this", "that", "these", "those",
    "it", "its", "me", "my", "your", "our", "you", "please", "tell", "show", "check", "find", "see", "if", "whether",
    "any", "there", "which", "what", "who", "how", "has", "have", "had", "not", "still", "now", "currently", "still",
    "registered", "registration", "register", "approved", "approval", "approve", "licensed", "licenced", "license",
    "verify", "verified", "verification", "authentic", "authorised", "authorized", "genuine", "valid", "legit",
    "legitimate", "fake", "counterfeit", "original", "real", "safe", "status",
    "nafdac", "nafdacs", "greenbook", "green", "book", "reg", "no", "number", "nrn",
    "recall", "recalled", "withdrawn", "banned", "product", "products", "drug", "drugs", "medicine", "medicines"
}
_SPLIT_RE = re.compile(r"\s*(?:,|&|\band\b|\bor\b)\s*", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\./%+]*")
MAX_GREENBOOK_TERMS = 3


def detect_category(query: str) -> Optional[str]:
    q = query.lower()
    for kw, cat in CATEGORY_KEYWORDS.items():
        if kw in q:
            return cat
    return None


def extract_product_terms(query: str) -> List[str]:
    """Product / active ingredient / NRN terms in a question, e.g.
    'Is Paracetamol 500mg registered with NAFDAC?' -> ['Paracetamol 500mg'];
    'Are paracetamol and ibuprofen approved?' -> ['paracetamol', 'ibuprofen']."""
    q = query.lower()
    category_words = [kw for kw in CATEGORY_KEYWORDS if kw in q]
    extra = {"medical"} if "device" in category_words else set()

    terms: List[str] = []
    for part in _SPLIT_RE.split(query):
        kept = []
        for tok in _TOKEN_RE.findall(part):
            low = tok.lower().strip("./-")
            if low in _TERM_STRIP or low in extra or any(low.startswith(c) for c in category_words):
                continue
            kept.append(tok)
        term = " ".join(kept).strip(" .-/")
        if len(term) >= 3 and term.lower() not in [t.lower() for t in terms]:
            terms.append(term)
    return terms[:MAX_GREENBOOK_TERMS]


@dataclass
class RoutePlan:
    """Everything the router decides for a question."""
    urls: List[str]                                   # NAFDAC pages to crawl
    topics: List[str] = field(default_factory=list)   # matched topics, best first
    greenbook_terms: List[str] = field(default_factory=list)
    greenbook_category: Optional[str] = None

    @property
    def greenbook(self) -> bool:
        return bool(self.greenbook_terms)


def plan_query(query: str, base: str = BASE) -> RoutePlan:
    """Decide which NAFDAC pages to crawl, and whether the Greenbook database must be searched too."""
    q = f" {query.lower()} "
    scored = []
    for entry in SITE_MAP:
        score = sum(len(k) for k in entry["keywords"] if k in q)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)

    top = [entry for _, entry in scored[:MAX_TOPICS]]
    terms = extract_product_terms(query) if any(e.get("greenbook") for _, e in scored) else []

    abs_urls: List[str] = []
    urls: List[str] = []
    for entry in top:
        urls.extend(base + path for path in entry["urls"])
        abs_urls.extend(entry.get("abs_urls", []))

    # Registration questions also check the "recalled / withdrawn?" side, and always
    # get the live Greenbook seeded even if the matched topic didn't already include it.
    if terms:
        abs_urls.append(GREENBOOK_URL)
        by_topic = {e["topic"]: e for e in SITE_MAP}
        for _, entry in scored:
            for name in entry.get("companions", []):
                urls.extend(base + path for path in by_topic[name]["urls"])

    # abs_urls should go first so they survive the 10-URL cap below.
    urls = list(dict.fromkeys(abs_urls + urls))[:10]
    # Universal fallbacks: the site's own search, then the homepage (latest alerts/news live there).
    urls.append(f"{base}/?s={quote_plus(query)}")
    urls.append(base + "/")
    if not scored:
        urls.extend(base + p for p in ("/category/recalls-and-alerts/", "/frequently-asked-questions/", "/our-services/"))

    return RoutePlan(
        urls=list(dict.fromkeys(urls)),
        topics=[e["topic"] for e in top],
        greenbook_terms=terms,
        greenbook_category=detect_category(query) if terms else None,
    )


def route_query(query: str, base: str = BASE) -> List[str]:
    """Seed URLs for the query (kept for callers that only need the pages)."""
    return plan_query(query, base).urls