import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def normalize_domain(value: str) -> str:
    """Return the lowercased network location of a URL or bare domain."""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return parsed.netloc.lower()


def canonicalize_url(raw_url: str, base_url: str = "") -> str:
    """Join a possibly relative URL against a base, strip tracking parameters,
    drop fragments, and lowercase the host. Returns an empty string for
    anything that is not http/https."""
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
    """True if the URL's host equals or is a subdomain of one of the allowed
    domains."""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in allowed_domains)


def clean_text(text: str) -> str:
    """Normalize whitespace, strip zero-width characters, and drop empty lines."""
    text = (
        (text or "")
        .replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def tokenize(value: str) -> list[str]:
    """Lowercase alphanumeric tokenizer. 'Is Panadol approved?' -> ['is', 'panadol', 'approved']."""
    return TOKEN_RE.findall((value or "").lower())


def _cell_links(cells: Iterable[Any], base_url: str) -> list[dict[str, str]]:
    """Links found inside a table row's cells, with their visible text."""
    out: list[dict[str, str]] = []
    for cell in cells:
        for a in cell.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            url = canonicalize_url(href, base_url)
            if url:
                out.append(
                    {
                        "text": clean_text(a.get_text(" ", strip=True)).replace(
                            "\n", " "
                        ),
                        "url": url,
                    }
                )
    return out


def extract_tables(soup: BeautifulSoup, base_url: str = "") -> list[dict[str, Any]]:
    """Extract every table on the page as {headers, rows, row_links}.

    If a row's cell count matches the header count, the row is a dict keyed by
    header names. Otherwise the row is {"cells": [...]} in source order.
    row_links is a parallel list: for each row, the links found in its cells.
    """
    tables: list[dict[str, Any]] = []
    for table in soup.find_all("table")[:30]:
        headers = [
            clean_text(th.get_text(" ", strip=True)) for th in table.find_all("th")
        ]
        rows: list[dict[str, Any]] = []
        row_links: list[list[dict[str, str]]] = []

        for tr in table.find_all("tr")[:2000]:
            tr_cells = tr.find_all(["td", "th"])
            cells = [clean_text(td.get_text(" ", strip=True)) for td in tr_cells]
            if not cells:
                continue
            if headers and cells == headers:  # header row itself; skip it
                continue
            if headers and len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
            else:
                rows.append({"cells": cells})
            row_links.append(_cell_links(tr_cells, base_url))

        if rows:
            tables.append({"headers": headers, "rows": rows, "row_links": row_links})

    return tables
