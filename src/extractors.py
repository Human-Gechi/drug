import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.utils import canonicalize_url, clean_text, extract_tables

# Anchored to line starts so "Registration form:" or "Ingredients list" don't match.
GREENBOOK_PATTERNS = {
    "product_name": r"^\s*(?:product\s*name|name\s*of\s*product)\s*[:\-]\s*(.+)$",
    "registration_number": r"^\s*(?:nafdac\s*)?(?:registration\s*(?:number|no\.?|nº)|reg\.?\s*no\.?|nrn)\s*[:\-]\s*([A-Z0-9][A-Z0-9\/\-\.\(\)]*)",
    "manufacturer": r"^\s*(?:manufacturer|manufactured\s*by|applicant(?:\s*name)?)\s*[:\-]\s*(.+)$",
    "marketing_authorization_holder": r"^\s*(?:marketing\s*authori[sz]ation\s*holder|ma\s*holder)\s*[:\-]\s*(.+)$",
    "status": r"^\s*(?:registration\s*)?status\s*[:\-]\s*(.+)$",
    "active_ingredient": r"^\s*active\s*ingredients?\s*[:\-]\s*(.+)$",
    "dosage_form": r"^\s*dosage\s*form\s*[:\-]\s*(.+)$",
    "expiry": r"^\s*(?:expiry|expiration)(?:\s*date)?\s*[:\-]\s*(.+)$",
    "strength": r"^\s*strength(?:s)?\s*[:\-]\s*(.+)$",
    "approval_date": r"^\s*(?:approval\s*date|date\s*of\s*approval|registration\s*date|date\s*registered)\s*[:\-]\s*(.+)$",
    "product_category": r"^\s*(?:product\s*category|category)\s*[:\-]\s*(.+)$",
}

# Maps the raw regex field names above onto the flatter, user-facing names used in the
# "registrations" dataset/view (e.g. GREENBOOK_PATTERNS' "registration_number" -> "nrn").
# Where two raw fields map onto the same output name (manufacturer / MA holder), the first
# one present wins.
GREENBOOK_FIELD_MAP = {
    "product_name": "product_name",
    "registration_number": "nrn",
    "active_ingredient": "active_ingredients",
    "dosage_form": "form",
    "strength": "strengths",
    "manufacturer": "applicant_name",
    "marketing_authorization_holder": "applicant_name",
    "approval_date": "approval_date",
    "product_category": "product_category",
    "status": "status",
    "expiry": "expiry",
}


def map_greenbook_fields(gb: dict[str, Any]) -> dict[str, Any]:
    """Rename a raw greenbook record (regex field names) to the output-facing field names."""
    mapped: dict[str, Any] = {}
    for src_key, value in gb.items():
        if src_key in ("hint", "excerpt"):
            continue
        dest = GREENBOOK_FIELD_MAP.get(src_key, src_key)
        if dest not in mapped:
            mapped[dest] = value
    return mapped


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"(?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}}")
ALERT_HINT_RE = re.compile(
    r"alert|recall|notice|warning|advisory|FSN|withdraw(?:n|al)|seizure|banned|suspend",
    re.IGNORECASE,
)
ALERT_NO_RE = re.compile(
    r"(?:Public\s+Alert|Recall|Alert)\s*(?:No\.?|Number)?\s*:?\s*(\d+\s*/\s*\d{4})",
    re.IGNORECASE,
)
ONCLICK_URL_RE = re.compile(
    r"""(?:location(?:\.href)?\s*=|window\.open\(|open\()\s*['"]([^'"]+)['"]"""
)
SKIP_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".mp3",
    ".zip",
)


@dataclass
class PageDocument:
    url: str
    title: str
    text: str
    links: list[str]
    tables: list[dict[str, Any]]
    greenbook: dict[str, Any] | None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)

    def to_item(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "links": self.links,
            "tables": self.tables,
            "greenbook": self.greenbook,
            "alerts": self.alerts,
            "resources": self.resources,
        }


def extract_greenbook(text: str, tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    source_text = text or ""
    table_lines: list[str] = []

    for table in tables:
        if len(table.get("rows", [])) > 3:
            continue
        for row in table.get("rows", []):
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if key == "cells":
                    continue
                table_lines.append(f"{key}: {value}")

    if table_lines:
        source_text += "\n" + "\n".join(table_lines)

    matched: dict[str, Any] = {}
    for fld, pattern in GREENBOOK_PATTERNS.items():
        match = re.search(pattern, source_text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            matched[fld] = clean_text(match.group(1))[:400]

    if len(matched) >= 2 and (
        "product_name" in matched or "registration_number" in matched
    ):
        return matched

    lower = source_text.lower()
    if "green book" in lower or "greenbook" in lower:
        return {
            "hint": "possible greenbook page",
            "excerpt": clean_text(source_text[:1200]),
        }

    return None


def extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Links from <a>, <area>, iframes/embeds/objects, data-* attributes,
    onclick handlers, and any element with a data-url-like attribute."""
    found: list[str] = []

    for el in soup.find_all(True):
        candidates: list[str | None] = []

        if el.name in ("a", "area"):
            candidates.append(el.get("href"))
        elif el.name in ("iframe", "embed", "source"):
            candidates.append(el.get("src"))
        elif el.name == "object":
            candidates.append(el.get("data"))

        for attr in ("data-href", "data-url", "data-link", "data-file", "data-pdf"):
            candidates.append(el.get(attr))

        onclick = el.get("onclick")
        if onclick:
            m = ONCLICK_URL_RE.search(onclick)
            if m:
                candidates.append(m.group(1))

        for href in candidates:
            href = (href or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absolute = canonicalize_url(href, base_url)
            if not absolute or urlparse(absolute).path.lower().endswith(SKIP_EXT):
                continue
            found.append(absolute)

    return list(dict.fromkeys(found))


DOC_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv")


def extract_resources(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Documents (PDF/Word/Excel) and embedded content (iframes, embeds).

    Reads the URL from any of href / data-href / data-url / data-link /
    onclick / src / data, because NAFDAC's card widgets often place the PDF
    URL on a wrapping <div> or <button> rather than on a plain <a href>."""
    found: list[dict[str, Any]] = []
    seen = set()

    for el in soup.find_all(
        ["a", "iframe", "embed", "object", "button", "div", "span"]
    ):
        if el.find_parent(["nav", "footer", "aside"]):
            continue

        raw_candidates: list[str] = []
        for attr in ("href", "data-href", "data-url", "data-link", "src", "data"):
            v = el.get(attr)
            if v:
                raw_candidates.append(v)
        onclick = el.get("onclick")
        if onclick:
            m = ONCLICK_URL_RE.search(onclick)
            if m:
                raw_candidates.append(m.group(1))

        for raw in raw_candidates:
            raw = (raw or "").strip()
            if not raw or raw.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            url = canonicalize_url(raw, base_url)
            if not url or url in seen:
                continue

            path = urlparse(url).path.lower()

            if path.endswith(DOC_EXT):
                kind = path.rsplit(".", 1)[-1]
                text = clean_text(el.get_text(" ", strip=True)).replace("\n", " ")
                text = (
                    text
                    or el.get("title")
                    or el.get("aria-label")
                    or path.rsplit("/", 1)[-1]
                    or url
                )
                seen.add(url)
                found.append({"text": text[:200], "url": url, "type": kind})
                continue

            if el.name in ("iframe", "embed", "object") and (
                el.get("src") or el.get("data")
            ):
                text = (
                    el.get("title")
                    or el.get("aria-label")
                    or path.rsplit("/", 1)[-1]
                    or url
                )
                seen.add(url)
                found.append({"text": text[:200], "url": url, "type": "embed"})

    return found


def extract_alerts(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Turn alert/recall/notice cards (date + title + link) into structured records."""
    alerts: list[dict[str, Any]] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True)).replace("\n", " ")
        if len(title) < 8 or not ALERT_HINT_RE.search(title):
            continue
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = canonicalize_url(href, base_url)
        if not url:
            continue
        if "/category/" in urlparse(url).path:
            continue
        if a.find_parent(["nav", "footer", "aside"]):
            continue
        if a.find_parent("header") and not a.find_parent("article"):
            continue

        date = None
        in_title = DATE_RE.search(title)
        if in_title:
            date = in_title.group(0)
            title = re.sub(r"\s+", " ", DATE_RE.sub("", title)).strip(" |-–:")
        else:
            node = a.parent
            for _ in range(5):
                if node is None or node.name in ("body", "html"):
                    break
                dates = DATE_RE.findall(node.get_text(" ", strip=True))
                if len(dates) == 1:
                    date = dates[0]
                    break
                if len(dates) > 1:
                    break
                node = node.parent

        key = (url, title)
        if key in seen:
            continue
        seen.add(key)

        m = ALERT_NO_RE.search(title)
        card = a.find_parent("article")
        excerpt = (
            clean_text(card.get_text(" ", strip=True)).replace("\n", " ")[:300]
            if card
            else ""
        )
        alerts.append(
            {
                "date": date,
                "alert_no": re.sub(r"\s+", "", m.group(1)) if m else None,
                "title": title,
                "url": url,
                "excerpt": excerpt,
            }
        )
    return alerts


def extract_page_document(url: str, html: str) -> PageDocument:
    soup = BeautifulSoup(html, "lxml")

    canonical = soup.find(
        "link", rel=lambda x: x and "canonical" in " ".join(x).lower()
    )
    if canonical and canonical.get("href"):
        url = canonicalize_url(canonical["href"], url) or url

    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""

    links = extract_links(soup, url)
    alerts = extract_alerts(soup, url)
    tables = extract_tables(soup, url)
    resources = extract_resources(soup, url)

    container = None
    for sel in ("main", ".entry-content", "#content", "#primary"):
        container = soup.select_one(sel)
        if container:
            break
    container = container or soup.body or soup
    for tag in container.select(
        "script, style, noscript, svg, nav, footer, aside, "
        ".menu, .sidebar, .widget, .breadcrumb, .ticker, .marquee"
    ):
        tag.decompose()

    text = clean_text(container.get_text("\n", strip=True))
    greenbook = extract_greenbook(text, tables)

    return PageDocument(
        url=url,
        title=title,
        text=text,
        links=links,
        tables=tables,
        greenbook=greenbook,
        alerts=alerts,
        resources=resources,
    )


def merge_documents(base: PageDocument, others: list[PageDocument]) -> PageDocument:
    """Fold extra snapshots (after tab clicks, iframes, ...) into the base document."""
    seen_alerts = {(a["url"], a["title"]) for a in base.alerts}
    known_lines = set(base.text.splitlines())

    for d in others:
        base.links = list(dict.fromkeys(base.links + d.links))

        for a in d.alerts:
            key = (a["url"], a["title"])
            if key not in seen_alerts:
                seen_alerts.add(key)
                base.alerts.append(a)

        new_lines = [ln for ln in d.text.splitlines() if ln not in known_lines]
        if new_lines:
            known_lines.update(new_lines)
            base.text += "\n" + "\n".join(new_lines)

        for t in d.tables:
            if t not in base.tables:
                base.tables.append(t)

        known_res = {r["url"] for r in base.resources}
        for r in d.resources:
            if r["url"] not in known_res:
                known_res.add(r["url"])
                base.resources.append(r)

    base.greenbook = extract_greenbook(base.text, base.tables) or base.greenbook
    return base
