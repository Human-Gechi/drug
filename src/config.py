import re
from dataclasses import dataclass
from typing import Any

from src.router import BASE, plan_query
from src.utils import normalize_domain


def _parse_start_urls(value: Any) -> list[str]:
    """Accept a comma/space separated string OR Apify's [{"url": ...}] list."""
    if not value:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\s]+", value)
    else:
        parts = [(u.get("url") if isinstance(u, dict) else u) for u in value]

    urls: list[str] = []
    for p in parts:
        p = str(p or "").strip()
        if not p:
            continue
        if "://" not in p:
            p = f"https://{p}"
        urls.append(p)
    return list(dict.fromkeys(urls))


def _as_positive_int(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


@dataclass
class AppConfig:
    start_urls: list[str]
    allowed_domains: list[str]
    max_pages: int = 200
    crawl_depth: int = 2
    page_timeout_ms: int = 20_000
    query: str = ""
    ai_model: str = "openai/gpt-oss-120b"
    debug_html: bool = False
    greenbook_terms: list[str] = None
    max_results: int | None = None
    default_max_results: int | None = None

    @classmethod
    def from_input(cls, raw: dict[str, Any]) -> "AppConfig":
        query = str(raw.get("query") or "").strip()
        auto_route = bool(raw.get("autoRoute", True))

        start_urls = _parse_start_urls(raw.get("startUrls"))
        routed = auto_route and bool(query)
        greenbook_terms: list[str] = []
        if routed:
            plan = plan_query(query)
            start_urls = list(dict.fromkeys(start_urls + plan.urls))
            greenbook_terms = plan.greenbook_terms
        if not start_urls:
            start_urls = [BASE + "/"]

        domains = [normalize_domain(u).removeprefix("www.") for u in start_urls]
        domains.append(normalize_domain(BASE))
        allowed_domains = list(dict.fromkeys(domains))

        default_depth, default_pages = (2, 60) if routed else (2, 150)

        return cls(
            start_urls=start_urls,
            allowed_domains=allowed_domains,
            max_pages=int(raw.get("maxPages") or default_pages),
            crawl_depth=int(raw.get("crawlDepth") or default_depth),
            page_timeout_ms=int(raw.get("pageTimeoutMs") or 20_000),
            query=query,
            ai_model="openai/gpt-oss-120b",
            debug_html=bool(raw.get("debugHtml", False)),
            greenbook_terms=greenbook_terms,
            max_results=_as_positive_int(raw.get("maxResults")),
            default_max_results=_as_positive_int(raw.get("defaultMaxResults")),
        )
