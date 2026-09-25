"""Query router: decides which NAFDAC pages to crawl for a given question,
so the user never has to pick start URLs by hand."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from src.greenbook import GREENBOOK_URL, expand_terms

BASE = "https://nafdac.gov.ng"

# Alert / recall topics. When a query matches one of these, the Greenbook is
# not searched, because alerts and recalls are not Greenbook records.
ALERT_TOPICS = {"alerts", "field_safety", "withdrawn", "blacklist", "watchlist"}

SITE_MAP: list[dict] = [
    {
        "topic": "alerts",
        "keywords": [
            "alert",
            "recall",
            "counterfeit",
            "fake",
            "falsified",
            "substandard",
            "warning",
            "poisoning",
            "seizure",
        ],
        "urls": ["/category/recalls-and-alerts/", "/field-safety-alerts/"],
    },
    {
        "topic": "field_safety",
        "keywords": [
            "field safety",
            "fsn",
            "syringe",
            "condom",
            "medical device recall",
        ],
        "urls": ["/field-safety-alerts/"],
    },
    {
        "topic": "withdrawn",
        "keywords": [
            "withdraw",
            "suspended",
            "cancelled",
            "canceled",
            "deregist",
            "delist",
            "no longer authori",
            "still authori",
            "market authorization",
            "market authorisation",
        ],
        "urls": ["/our-services/market-authorization-withdrawal/"],
    },
    {
        "topic": "blacklist",
        "keywords": [
            "blacklist",
            "black list",
            "debarred",
            "non-compliant",
            "non compliant",
        ],
        "urls": ["/blacklist-of-companies/"],
    },
    {
        "topic": "watchlist",
        "keywords": ["watchlist", "watch list", "watch-list"],
        "urls": ["/watchlist-of-companies-2/"],
    },
    {
        "topic": "herbal_products",
        "keywords": ["herbal", "herbs", "nutraceutical", "nutraceuticals"],
        "urls": ["/herbal-products-database/"],
        "abs_urls": [GREENBOOK_URL],
        "greenbook": True,
    },
    {
        "topic": "registered_products",
        "keywords": [
            "registered",
            "registration number",
            "reg no",
            "reg. no",
            "nrn",
            "greenbook",
            "green book",
            "product database",
            "is this product",
            "is this drug",
            "approved",
            "licensed",
            "licenced",
            "nafdac number",
            "nafdac no",
        ],
        "urls": [],
        "abs_urls": [GREENBOOK_URL],
        "greenbook": True,
    },
    {
        "topic": "registration_process",
        "keywords": [
            "how to register",
            "register a",
            "registration process",
            "how long",
            "timeline",
            "dossier",
            "application",
            "renew",
            "variation",
        ],
        "urls": ["/our-services/product-registrationevaluation/", "/our-services/"],
    },
    {
        "topic": "fees",
        "keywords": ["tariff", "fee", "cost", "charge", "price", "payment", "how much"],
        "urls": ["/resources/nafdac-tariff", "/regulatory-resources/nafdac-tariff/"],
    },
    {
        "topic": "guidelines",
        "keywords": [
            "guideline",
            "requirement",
            "labelling",
            "labeling",
            "label",
            "standard",
            "notes to industry",
            "compliance",
        ],
        "urls": ["/resources/guidelines/", "/our-services/notes-to-industry/"],
    },
    {
        "topic": "regulations",
        "keywords": [
            "regulation",
            "law",
            "act ",
            "gazette",
            "legal",
            "directive",
            "draft",
        ],
        "urls": [
            "/resources/nafdac-regulations/",
            "/about-nafdac/nafdac-laws/",
            "/regulatory-resources/regulatory-directive/",
            "/draft-regulations/",
        ],
    },
    {
        "topic": "chemicals",
        "keywords": [
            "chemical",
            "chemicals",
            "pesticide",
            "pesticides",
            "detergent",
            "disinfectant",
            "disinfectants",
            "restricted",
            "prohibited",
            "banned substance",
            "controlled substance",
        ],
        "urls": ["/chemicals/"],
    },
    {
        "topic": "clinical_trials",
        "keywords": ["clinical trial", "trial", "cro", "nhrec"],
        "urls": [
            "/clinical-trial-database/",
            "/drugs/application-for-clinical-trials/",
            "/our-services/clinical-trials-cro-eligibility-criteria/",
        ],
    },
    {
        "topic": "inspection",
        "keywords": [
            "inspection",
            "gmp",
            "facility",
            "facilities",
            "foreign manufactur",
            "port",
            "clearance",
        ],
        "urls": [
            "/inspection-classification-database/",
            "/our-services/ports-inspection/",
            "/regulatory-resources/foreign-gmp-plan/",
        ],
    },
    {
        "topic": "exports",
        "keywords": ["export", "import"],
        "urls": [
            "/our-services/categories-of-exports/",
            "/our-services/ports-inspection/",
        ],
    },
    {
        "topic": "traceability",
        "keywords": [
            "traceab",
            "authentic",
            "verify",
            "genuine",
            "scratch",
            "mas ",
            "track and trace",
        ],
        "urls": [
            "/traceability/",
            "/our-services/pharmacovigilance-post-market-surveillance/mobile-authentication-service-mas/",
        ],
    },
    {
        "topic": "pharmacovigilance",
        "keywords": [
            "adverse",
            "side effect",
            "reaction",
            "pharmacovigilance",
            "med safety",
            "report a",
            "post market",
            "surveillance",
        ],
        "urls": ["/our-services/pharmacovigilance-post-market-surveillance/"],
    },
    {
        "topic": "news",
        "keywords": [
            "press",
            "news",
            "announce",
            "statement",
            "speech",
            "event",
            "address",
            "launch",
            "inaugurat",
            "conference",
            "training",
        ],
        "urls": [
            "/category/press-release/",
            "/category/latest-news/",
            "/category/speeches/",
            "/category/events/",
            "/nafdac-newsroom/",
        ],
    },
    {
        "topic": "contact",
        "keywords": [
            "contact",
            "phone",
            "email",
            "address of",
            "office",
            "state office",
            "complain",
            "enquir",
            "inquir",
            "hotline",
        ],
        "urls": [
            "/about-nafdac/contact-nafdac/",
            "/about-nafdac/contact-nafdac/complaints-and-enquiries/",
        ],
    },
    {
        "topic": "about",
        "keywords": [
            "who is",
            "director general",
            " dg",
            "management",
            "vision",
            "mission",
            "history",
            "founded",
            "established",
            "council",
            "strategic plan",
            "career",
            "working at",
            "job",
        ],
        "urls": [
            "/about-nafdac/director-generals-page/",
            "/about-nafdac/nafdac-management/",
            "/about-nafdac/nafdac-organisation/",
            "/about-nafdac/nafdac-vision-and-mission/",
            "/about-nafdac/working-at-nafdac/",
        ],
    },
    {
        "topic": "msme",
        "keywords": ["msme", "small business", "small and medium", "micro"],
        "urls": ["/our-services/micro-small-medium-enterprises-msme1/"],
    },
    {
        "topic": "bioequivalence",
        "keywords": ["bioequivalence", "bioavailability", "generic"],
        "urls": ["/bioequivalence/"],
    },
    {
        "topic": "food",
        "keywords": [
            "food",
            "water",
            "beverage",
            "bread",
            "alcohol",
            "drink",
            "rutf",
            "fortif",
            "nutrition",
            "breastfeeding",
        ],
        "urls": ["/food/"],
    },
    {
        "topic": "drugs",
        "keywords": [
            "drug",
            "medicine",
            "pharma",
            "tablet",
            "syrup",
            "antimalarial",
            "napar",
        ],
        "urls": ["/drug/", "/drugs/"],
        "abs_urls": [GREENBOOK_URL],
        "greenbook": True,
    },
    {
        "topic": "cosmetics",
        "keywords": [
            "cosmetic",
            "toothpaste",
            "cream",
            "soap",
            "lotion",
            "medical device",
        ],
        "urls": ["/cosmetics-medical-devices/"],
    },
    {
        "topic": "vaccines",
        "keywords": ["vaccine", "biological"],
        "urls": ["/vaccines-biologicals/"],
    },
    {
        "topic": "veterinary",
        "keywords": ["veterinary", "animal", "livestock"],
        "urls": ["/veterinary/"],
    },
    {
        "topic": "narcotics",
        "keywords": [
            "narcotic",
            "controlled substance",
            "opioid",
            "tramadol",
            "codeine",
        ],
        "urls": ["/narcotics/"],
    },
    {
        "topic": "faq",
        "keywords": ["faq", "frequently asked"],
        "urls": ["/frequently-asked-questions/"],
    },
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
    "disinfectant": "Disinfectants",
}

# Words that are part of the question, not the product name. Every token in a
# query is tested against this set before it can become a Greenbook search term.
_TERM_STRIP = {
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "am",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
    "have",
    "has",
    "had",
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "this",
    "that",
    "these",
    "those",
    "and",
    "or",
    "it",
    "its",
    "me",
    "my",
    "your",
    "our",
    "you",
    "any",
    "there",
    "which",
    "what",
    "who",
    "how",
    "when",
    "where",
    "why",
    "whether",
    "if",
    "please",
    "tell",
    "show",
    "check",
    "find",
    "see",
    "registered",
    "registration",
    "register",
    "approved",
    "approval",
    "approve",
    "licensed",
    "licenced",
    "license",
    "verify",
    "verified",
    "verification",
    "authentic",
    "authorised",
    "authorized",
    "genuine",
    "valid",
    "legit",
    "legitimate",
    "fake",
    "counterfeit",
    "original",
    "real",
    "safe",
    "status",
    "nafdac",
    "nafdacs",
    "greenbook",
    "green",
    "book",
    "reg",
    "no",
    "number",
    "nrn",
    "recall",
    "recalled",
    "withdrawn",
    "banned",
    "product",
    "products",
    "drug",
    "drugs",
    "medicine",
    "medicines",
    "kind",
    "kinds",
    "type",
    "types",
    "sort",
    "sorts",
    "form",
    "forms",
    "brand",
    "brands",
    "variant",
    "variants",
    "version",
    "versions",
    "option",
    "options",
    "example",
    "examples",
    "sample",
    "samples",
    "different",
    "various",
    "available",
    "some",
    "many",
    "much",
    "all",
    "every",
    "each",
    "both",
    "several",
    "few",
    "list",
    "lists",
    "listing",
    "enumerate",
    "last",
    "latest",
    "newest",
    "new",
    "recent",
    "current",
    "first",
    "oldest",
    "previous",
    "next",
    "today",
    "now",
    "item",
    "items",
    "thing",
    "things",
    "one",
    "two",
    "three",
    "website",
    "site",
    "page",
    "pages",
    "record",
    "records",
    "entry",
    "entries",
    "name",
    "names",
    "called",
    "known",
    "still",
    "currently",
    "yet",
    "also",
    "alert",
    "alerts",
    "recalls",
    "notice",
    "notices",
    "warning",
    "warnings",
    "fsn",
    "fsns",
    "withdraw",
    "withdrawal",
    "withdrawals",
    "ban",
    "blacklist",
    "blacklisted",
    "watchlist",
    "watchlisted",
    "suspended",
    "suspend",
    "cancelled",
    "canceled",
    "cancel",
    "deregistered",
    "delisted",
    "falsified",
    "substandard",
    "seizure",
    "poisoning",
    # Press / news vocabulary: not product names.
    "press",
    "release",
    "releases",
    "news",
    "newsroom",
    "article",
    "articles",
    "bulletin",
    "bulletins",
    "announcement",
    "announcements",
    "statement",
    "statements",
    "briefing",
    "briefings",
    "media",
    "parley",
    "speech",
    "speeches",
    "update",
    "updates",
    "communication",
    "communications",
    "publication",
    "publications",
    "report",
    "reports",
    # Category nouns that are never a product name on their own.
    "chemical",
    "chemicals",
    "restricted",
    "restriction",
    "restrictions",
    "prohibited",
    "controlled",
    "substance",
    "substances",
    "device",
    "devices",
    "cosmetic",
    "cosmetics",
    "vaccine",
    "vaccines",
    "biological",
    "biologicals",
    "food",
    "foods",
    "water",
    "beverage",
    "beverages",
    "veterinary",
    "narcotic",
    "narcotics",
    "herbal",
    "herbs",
    "section",
    "sections",
    "category",
    "categories",
    "class",
    "classes",
    "pesticide",
    "pesticides",
    "detergent",
    "detergents",
    "disinfectant",
    "disinfectants",
    # Words that describe the shape of the answer, not the topic.
    "document",
    "documents",
    "file",
    "files",
    "pdf",
    "pdfs",
    "download",
    "downloads",
    "attachment",
    "attachments",
    "guideline",
    "guidelines",
    "regulation",
    "regulations",
    "requirement",
    "requirements",
    "standard",
    "standards",
    "procedure",
    "procedures",
    "process",
    "processes",
    "rule",
    "rules",
    "law",
    "laws",
    "act",
    "acts",
    "policy",
    "policies",
    "circular",
    "circulars",
    "directive",
    "directives",
    "tariff",
    "tariffs",
    "fee",
    "fees",
}

_SPLIT_RE = re.compile(r"\s*(?:,|&|\band\b|\bor\b)\s*", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\./%+]*")
_PROPER_NOUN_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][A-Za-z0-9\-]{2,})\b")
MAX_GREENBOOK_TERMS = 3
MAX_TERM_WORDS = 3

# A product term made only of category words is not a product name. Used to
# drop terms like "chemicals restricted" before they reach the Greenbook.
_CATEGORY_ONLY = {
    "chemical",
    "chemicals",
    "restricted",
    "restriction",
    "restrictions",
    "banned",
    "prohibited",
    "controlled",
    "substance",
    "substances",
    "device",
    "devices",
    "cosmetic",
    "cosmetics",
    "vaccine",
    "vaccines",
    "biological",
    "biologicals",
    "food",
    "foods",
    "beverage",
    "beverages",
    "veterinary",
    "narcotic",
    "narcotics",
    "herbal",
    "herbs",
    "pesticide",
    "pesticides",
    "detergent",
    "detergents",
    "disinfectant",
    "disinfectants",
}


def _is_category_only(term: str) -> bool:
    words = [w.lower() for w in term.split()]
    return bool(words) and all(w in _CATEGORY_ONLY for w in words)


def detect_category(query: str) -> str | None:
    q = query.lower()
    for kw, cat in CATEGORY_KEYWORDS.items():
        if kw in q:
            return cat
    return None


def extract_product_terms(query: str) -> list[str]:
    """Product / brand / active-ingredient terms in a question.

    Deterministic: pure regex + dictionary lookup, no LLM. Brand names are
    translated to the active ingredient the Greenbook indexes under, so
    'Is Panadol approved?' searches 'Paracetamol'.

    Deliberately conservative: a term only survives if it contains at least
    one alphabetic word that is not a stopword or a generic query word, and
    the whole candidate is at most MAX_TERM_WORDS long."""
    q = query.lower()
    category_words = [kw for kw in CATEGORY_KEYWORDS if kw in q]
    extra = {"medical"} if "device" in category_words else set()

    terms: list[str] = []
    for part in _SPLIT_RE.split(query):
        kept = []
        for tok in _TOKEN_RE.findall(part):
            low = tok.lower().strip("./-")
            if low in _TERM_STRIP:
                continue
            if low in extra or any(low.startswith(c) for c in category_words):
                continue
            if not any(ch.isalpha() for ch in low):
                continue
            kept.append(tok)

        if not kept:
            continue
        if len(kept) > MAX_TERM_WORDS:
            continue
        term = " ".join(kept).strip(" .-/")
        if len(term) >= 3 and term.lower() not in [t.lower() for t in terms]:
            terms.append(term)

    if not terms:
        for m in _PROPER_NOUN_RE.finditer(query):
            word = m.group(1)
            low = word.lower()
            if low in _TERM_STRIP or low in extra:
                continue
            if low not in [t.lower() for t in terms]:
                terms.append(word)

    return expand_terms(terms)[:MAX_GREENBOOK_TERMS]


@dataclass
class RoutePlan:
    """Everything the router decides for a question."""

    urls: list[str]
    topics: list[str] = field(default_factory=list)
    greenbook_terms: list[str] = field(default_factory=list)
    greenbook_category: str | None = None

    @property
    def greenbook(self) -> bool:
        return bool(self.greenbook_terms)


def plan_query(query: str, base: str = BASE) -> RoutePlan:
    """Deterministically decide which NAFDAC pages to crawl for a question.

    Same query -> same URLs -> same Greenbook terms, every time. No LLM,
    no randomness, no dependence on phrasing."""
    q = f" {query.lower()} "
    scored = []
    for entry in SITE_MAP:
        score = sum(len(k) for k in entry["keywords"] if k in q)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda x: (-x[0], x[1]["topic"]))

    top = [entry for _, entry in scored[:MAX_TOPICS]]

    is_alert_query = any(e["topic"] in ALERT_TOPICS for e in top)

    if is_alert_query:
        terms: list[str] = []
    else:
        terms = [t for t in extract_product_terms(query) if not _is_category_only(t)]

    abs_urls: list[str] = []
    urls: list[str] = []
    for entry in top:
        urls.extend(base + path for path in entry["urls"])
        abs_urls.extend(entry.get("abs_urls", []))

    if terms:
        abs_urls.append(GREENBOOK_URL)

    urls = list(dict.fromkeys(abs_urls + urls))[:10]
    urls.append(f"{base}/?s={quote_plus(query)}")
    urls.append(base + "/")
    if not scored:
        urls.extend(
            base + p
            for p in (
                "/category/recalls-and-alerts/",
                "/frequently-asked-questions/",
                "/our-services/",
            )
        )

    seen = set()
    final_urls: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            final_urls.append(u)

    return RoutePlan(
        urls=final_urls,
        topics=[e["topic"] for e in top],
        greenbook_terms=terms,
        greenbook_category=detect_category(query) if terms else None,
    )


def route_query(query: str, base: str = BASE) -> list[str]:
    return plan_query(query, base).urls
