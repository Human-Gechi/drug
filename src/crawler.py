import io
import re
import subprocess
import sys
from collections import deque
from typing import List, Optional, Sequence, Set
from urllib.parse import urlparse

from apify import Actor
from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.extractors import PageDocument, extract_greenbook, extract_page_document, merge_documents
from src.utils import canonicalize_url, clean_text, same_domain

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

# URLs containing these are crawled first so alerts/recalls/greenbook aren't starved by max_pages.
PRIORITY_KEYWORDS = (
    "alert", "recall", "notice", "greenbook", "green-book", "fsn", "warning",
    "withdraw", "suspend", "cancel", "blacklist", "watchlist", "list-of"
)

CLICK_SELECTORS = [
    '[role="tab"]',
    '[data-toggle="tab"]',
    '[data-bs-toggle="tab"]',
    ".nav-tabs a",
    ".nav-tabs button",
    ".tab-link",
    'button:has-text("Load more")',
    'button:has-text("View more")',
    'button:has-text("Show more")'
]
MAX_CLICKS_PER_PAGE = 12

# greenbook.nafdac.gov.ng renders its product table client-side via AJAX after a search
# is submitted; a plain goto() + networkidle wait (what snapshot_page does) sees an empty
# table. search_greenbook() fills the "Product Name" box and waits for that AJAX result.
GREENBOOK_HOST = "greenbook.nafdac.gov.ng"


async def search_greenbook(page: Page, term: str, timeout_ms: int) -> bool:
    try:
        inputs = await page.query_selector_all('input[type="text"], input:not([type])')
        target = None
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                meta = " ".join(
                    (await inp.get_attribute(attr)) or ""
                    for attr in ("placeholder", "name", "id", "aria-label")
                ).lower()
            except Exception:
                meta = ""
            if "product" in meta:
                target = inp
                break
        if target is None:
            for inp in inputs:
                try:
                    if await inp.is_visible():
                        target = inp
                        break
                except Exception:
                    continue
        if not target:
            return False

        await target.click(timeout=1500)
        await target.fill(term, timeout=1500)
        await page.keyboard.press("Enter")
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(1500)  # let the AJAX-rendered table settle
        return True
    except Exception as exc:
        Actor.log.warning("Greenbook search failed for %r: %r", term, exc)
        return False


def is_priority(url: str, query_stems: Sequence[str] = ()) -> bool:
    """Crawl first: URLs with alert/list keywords, or URL words that match the user's question."""
    path = urlparse(url).path.lower()
    if any(k in path for k in PRIORITY_KEYWORDS):
        return True
    if query_stems:
        words = re.findall(r"[a-z0-9]+", path)
        return any(w.startswith(s) for s in query_stems for w in words)
    return False


def is_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


async def launch_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=True)
    except PlaywrightError:
        try:
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        except Exception:
            try:
                subprocess.check_call([sys.executable, "-m", "playwright", "install"])
            except Exception:
                raise RuntimeError(
                    "Playwright browsers are missing and automatic installation failed. "
                    "Please run: python -m playwright install"
                )
        try:
            return await playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise RuntimeError(
                "Playwright failed to launch after 'python -m playwright install'."
            ) from exc


async def fetch_pdf_document(context: BrowserContext, url: str) -> Optional[PageDocument]:
    if PdfReader is None:
        Actor.log.warning("pypdf not installed; skipping PDF %s", url)
        return None
    resp = await context.request.get(url, timeout=60_000)
    if not resp.ok:
        return None
    reader = PdfReader(io.BytesIO(await resp.body()))
    text = clean_text("\n".join((p.extract_text() or "") for p in reader.pages[:30]))
    if not text:
        return None
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    title = first_line[:150] or url.rsplit("/", 1)[-1]
    return PageDocument(
        url=url,
        title=title,
        text=text,
        links=[],
        tables=[],
        greenbook=extract_greenbook(text, []),
        alerts=[]
    )


async def snapshot_page(page: Page, timeout_ms: int) -> List[str]:
    """HTML snapshots: after lazy-load scroll, after each tab/'load more' click, plus iframes."""
    htmls: List[str] = []

    for _ in range(3):  # trigger lazy loading
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)
        except Exception:
            break
    htmls.append(await page.content())

    origin = page.url
    clicks = 0
    for sel in CLICK_SELECTORS:
        try:
            handles = await page.query_selector_all(sel)
        except Exception:
            continue
        for h in handles:
            if clicks >= MAX_CLICKS_PER_PAGE:
                break
            try:
                if not await h.is_visible():
                    continue
                await h.click(timeout=1500)
                clicks += 1
                await page.wait_for_timeout(700)
                if page.url != origin:  # the click navigated away; go back, links are captured anyway
                    await page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
                    continue
                htmls.append(await page.content())
            except Exception:
                continue

    for frame in page.frames[1:]:
        try:
            htmls.append(await frame.content())
        except Exception:
            pass

    return htmls


async def crawl_site(
    start_urls: Sequence[str],
    allowed_domains: Sequence[str],
    max_pages: int,
    crawl_depth: int,
    page_timeout_ms: int,
    debug_html: bool = False,
    priority_terms: Sequence[str] = (),
    greenbook_terms: Sequence[str] = ()
) -> List[PageDocument]:
    visited: Set[str] = set()
    documents: List[PageDocument] = []
    # Three tiers: routed seed URLs first, then priority links, then everything else.
    seeds: deque = deque((url, 0) for url in start_urls)
    priority: deque = deque()
    queue: deque = deque()

    def next_item():
        for q in (seeds, priority, queue):
            if q:
                return q.popleft()
        return None

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright)
        context = await browser.new_context()

        try:
            while len(visited) < max_pages:
                item = next_item()
                if item is None:
                    break
                current_url, depth = item
                current_url = canonicalize_url(current_url)
                if not current_url or current_url in visited:
                    continue
                if depth > crawl_depth or not same_domain(current_url, allowed_domains):
                    continue

                visited.add(current_url)

                # ---- PDFs: download and extract text instead of rendering ----
                if is_pdf_url(current_url):
                    try:
                        pdf_doc = await fetch_pdf_document(context, current_url)
                        if pdf_doc:
                            documents.append(pdf_doc)
                    except Exception as exc:
                        Actor.log.warning("PDF failed %s: %r", current_url, exc)
                    continue

                page = await context.new_page()
                try:
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=page_timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeoutError:
                        pass

                    htmls = await snapshot_page(page, page_timeout_ms)

                    if debug_html:
                        slug = re.sub(r"[^a-zA-Z0-9]+", "_", current_url)[:100]
                        await Actor.set_value(f"debug_{slug}", htmls[0], content_type="text/html")

                    doc = extract_page_document(current_url, htmls[0])
                    if len(htmls) > 1:
                        doc = merge_documents(doc, [extract_page_document(current_url, h) for h in htmls[1:]])
                    documents.append(doc)

                    if greenbook_terms and urlparse(current_url).netloc.lower() == GREENBOOK_HOST:
                        for term in list(greenbook_terms)[:3]:
                            try:
                                if await search_greenbook(page, term, page_timeout_ms):
                                    result_html = await page.content()
                                    result_doc = extract_page_document(current_url, result_html)
                                    result_doc.url = f"{current_url}?product_name={term}"
                                    result_doc.title = f"Greenbook search: {term}"
                                    documents.append(result_doc)
                            except Exception as exc:
                                Actor.log.warning("Greenbook search step failed for %r: %r", term, exc)

                    if depth < crawl_depth:
                        for link in doc.links:
                            link = canonicalize_url(link, current_url)
                            if link and link not in visited and same_domain(link, allowed_domains):
                                if is_priority(link, priority_terms):
                                    priority.append((link, depth + 1))
                                else:
                                    queue.append((link, depth + 1))
                except Exception as exc:
                    # Download-triggering URLs (PDF served without .pdf) land here.
                    if "Download is starting" in str(exc):
                        try:
                            pdf_doc = await fetch_pdf_document(context, current_url)
                            if pdf_doc:
                                documents.append(pdf_doc)
                        except Exception:
                            pass
                    else:
                        Actor.log.warning("Failed %s: %r", current_url, exc)
                finally:
                    await page.close()
        finally:
            await context.close()
            await browser.close()

    return documents