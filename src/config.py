import re
from dataclasses import dataclass
from typing import Any, Dict, List

from src.router import BASE, plan_query
from src.utils import normalize_domain


def _parse_start_urls(value: Any) -> List[str]:
    """Accept a comma/space separated string OR Apify's [{"url": ...}] list."""
    if not value:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\s]+", value)
    else:
        parts = [(u.get("url") if isinstance(u, dict) else u) for u in value]

    urls: List[str] = []
    for p in parts:
        p = str(p or "").strip()
        if not p:
            continue
        if "://" not in p:
            p = f"https://{p}"
        urls.append(p)
    return list(dict.fromkeys(urls))


@dataclass
class AppConfig:
    start_urls: List[str]
    allowed_domains: List[str]
    max_pages: int = 200
    crawl_depth: int = 2
    page_timeout_ms: int = 45_000
    query: str = ""
    ai_model: str = "openai/gpt-oss-120b"
    debug_html: bool = False
    greenbook_terms: List[str] = None  # product/ingredient terms to search on greenbook.nafdac.gov.ng

    @classmethod
    def from_input(cls, raw: Dict[str, Any]) -> "AppConfig":
        query = str(raw.get("query") or "").strip()
        auto_route = bool(raw.get("autoRoute", True))

        start_urls = _parse_start_urls(raw.get("startUrls"))
        routed = auto_route and bool(query)
        greenbook_terms: List[str] = []
        if routed:
            plan = plan_query(query)
            start_urls = list(dict.fromkeys(start_urls + plan.urls))
            greenbook_terms = plan.greenbook_terms
        if not start_urls:
            start_urls = [BASE + "/"]

        domains = [normalize_domain(u).removeprefix("www.") for u in start_urls]
        domains.append(normalize_domain(BASE))
        allowed_domains = list(dict.fromkeys(domains))

        # Routed seeds are already targeted, so default to a shallower, faster crawl.
        default_depth, default_pages = (1, 80) if routed else (2, 200)

        return cls(
            start_urls=start_urls,
            allowed_domains=allowed_domains,
            max_pages=int(raw.get("maxPages") or default_pages),
            crawl_depth=int(raw.get("crawlDepth") or default_depth),
            page_timeout_ms=int(raw.get("pageTimeoutMs") or 45_000),
            query=query,
            ai_model=str(raw.get("aiModel") or "openai/gpt-oss-120b").strip(),
            debug_html=bool(raw.get("debugHtml", False)),
            greenbook_terms=greenbook_terms,
        )