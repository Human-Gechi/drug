import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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
    "expiry": r"^\s*(?:expiry|expiration)(?:\s*date)?\s*[:\-]\s*(.+)$"
}

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"(?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}}")
ALERT_HINT_RE = re.compile(r"alert|recall|notice|warning|advisory|FSN|withdraw(?:n|al)|seizure|banned|suspend", re.I)
ALERT_NO_RE = re.compile(
    r"(?:Public\s+Alert|Recall|Alert)\s*(?:No\.?|Number)?\s*:?\s*(\d+\s*/\s*\d{4})", re.I
)
ONCLICK_URL_RE = re.compile(r"""(?:location(?:\.href)?\s*=|window\.open\(|open\()\s*['"]([^'"]+)['"]""")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
            ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".zip")


@dataclass
class PageDocument:
    url: str
    title: str
    text: str
    links: List[str]
    tables: List[Dict[str, Any]]
    greenbook: Optional[Dict[str, Any]]
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)  # PDFs, documents, iframes/embeds

    def to_item(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "links": self.links,
            "tables": self.tables,
            "greenbook": self.greenbook,
            "alerts": self.alerts,
            "resources": self.resources
        }


def extract_greenbook(text: str, tables: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    source_text = text or ""
    table_lines: List[str] = []

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

    matched: Dict[str, Any] = {}
    for fld, pattern in GREENBOOK_PATTERNS.items():
        match = re.search(pattern, source_text, flags=re.I | re.M)
        if match:
            matched[fld] = clean_text(match.group(1))[:400]

    if len(matched) >= 2 and ("product_name" in matched or "registration_number" in matched):
        return matched

    lower = source_text.lower()
    if "green book" in lower or "greenbook" in lower:
        return {"hint": "possible greenbook page", "excerpt": clean_text(source_text[:1200])}

    return None


def extract_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Links from <a>, <area>, iframes/embeds/objects, data-href attrs and onclick handlers."""
    found: List[str] = []
    for el in soup.find_all(True):
        candidates: List[Optional[str]] = []
        if el.name in ("a", "area"):
            candidates.append(el.get("href"))
        elif el.name in ("iframe", "embed", "source"):
            candidates.append(el.get("src"))
        elif el.name == "object":
            candidates.append(el.get("data"))

        for attr in ("data-href", "data-url", "data-link"):
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


def extract_resources(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    """Documents (PDF/Word/Excel links) and embedded content (iframes, embeds) with readable labels."""
    found: List[Dict[str, Any]] = []
    seen = set()
    for el in soup.find_all(["a", "iframe", "embed", "object"]):
        if el.find_parent(["nav", "footer", "aside"]):
            continue
        if el.name == "a":
            raw = el.get("href")
        elif el.name == "object":
            raw = el.get("data")
        else:
            raw = el.get("src")
        raw = (raw or "").strip()
        if not raw or raw.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = canonicalize_url(raw, base_url)
        if not url or url in seen:
            continue
        path = urlparse(url).path.lower()
        if el.name == "a":
            if not path.endswith(DOC_EXT):
                continue
            kind = path.rsplit(".", 1)[-1]
            text = clean_text(el.get_text(" ", strip=True)).replace("\n", " ")
        else:
            kind, text = "embed", ""
        text = text or el.get("title") or el.get("aria-label") or path.rsplit("/", 1)[-1] or url
        seen.add(url)
        found.append({"text": text[:200], "url": url, "type": kind})
    return found


def extract_alerts(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    """Turn alert/recall/notice cards (date + title + link) into structured records."""
    alerts: List[Dict[str, Any]] = []
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
        # Menu items and category index links are not alerts.
        if "/category/" in urlparse(url).path:
            continue
        if a.find_parent(["nav", "footer", "aside"]):
            continue
        if a.find_parent("header") and not a.find_parent("article"):
            continue  # site header; an <article>'s own <header> is a real card title

        date = None
        in_title = DATE_RE.search(title)
        if in_title:
            date = in_title.group(0)
            title = re.sub(r"\s+", " ", DATE_RE.sub("", title)).strip(" |-–:")
        else:
            node = a.parent
            for _ in range(5):  # climb to the card that holds exactly one date
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
        excerpt = clean_text(card.get_text(" ", strip=True)).replace("\n", " ")[:300] if card else ""
        alerts.append(
            {
                "date": date,
                "alert_no": re.sub(r"\s+", "", m.group(1)) if m else None,
                "title": title,
                "url": url,
                "excerpt": excerpt
            }
        )
    return alerts


def extract_page_document(url: str, html: str) -> PageDocument:
    soup = BeautifulSoup(html, "lxml")

    canonical = soup.find("link", rel=lambda x: x and "canonical" in " ".join(x).lower())
    if canonical and canonical.get("href"):
        url = canonicalize_url(canonical["href"], url) or url

    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""

    # Extract links / alerts / tables BEFORE removing tags from the tree.
    links = extract_links(soup, url)
    alerts = extract_alerts(soup, url)
    tables = extract_tables(soup, url)
    resources = extract_resources(soup, url)

    # Pick the main content region (tried in order, so a listing page isn't reduced to its first <article>).
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
        resources=resources
    )


def merge_documents(base: PageDocument, others: List[PageDocument]) -> PageDocument:
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