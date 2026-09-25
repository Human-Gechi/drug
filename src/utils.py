import re
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid"
}

TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def normalize_domain(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return parsed.netloc.lower()


def canonicalize_url(raw_url: str, base_url: str = "") -> str:
    if not raw_url:
        return ""
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""

    query_items = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_KEYS
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.params,
            urlencode(query_items, doseq=True),
            "",
        )
    )


def same_domain(url: str, allowed_domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in allowed_domains)


def clean_text(text: str) -> str:
    text = (text or "").replace("\u202f", " ").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    lines: List[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def tokenize(value: str) -> List[str]:
    return TOKEN_RE.findall((value or "").lower())


def _cell_links(cells: Iterable[Any], base_url: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for cell in cells:
        for a in cell.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            url = canonicalize_url(href, base_url)
            if url:
                out.append({"text": clean_text(a.get_text(" ", strip=True)).replace("\n", " "), "url": url})
    return out


def extract_tables(soup: BeautifulSoup, base_url: str = "") -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for table in soup.find_all("table")[:30]:
        headers = [clean_text(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        rows: List[Dict[str, Any]] = []
        row_links: List[List[Dict[str, str]]] = []  # parallel to rows

        for tr in table.find_all("tr")[:2000]:
            tr_cells = tr.find_all(["td", "th"])
            cells = [clean_text(td.get_text(" ", strip=True)) for td in tr_cells]
            if not cells:
                continue
            if headers and cells == headers:  # skip the header row itself
                continue
            if headers and len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
            else:
                rows.append({"cells": cells})
            row_links.append(_cell_links(tr_cells, base_url))

        if rows:
            tables.append({"headers": headers, "rows": rows, "row_links": row_links})

    return tables