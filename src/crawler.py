import io
import re
from typing import List, Optional, Sequence
from urllib.parse import urlparse

from apify import Actor
from crawlee import Request
from crawlee.crawlers import (
    AdaptivePlaywrightCrawler,
    AdaptivePlaywrightCrawlingContext,
    RenderingType,
    RenderingTypePrediction,
    RenderingTypePredictor,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.extractors import PageDocument, extract_greenbook, extract_page_document
from src.greenbook import GREENBOOK_HOST, has_rows, map_to_active, search_greenbook
from src.utils import clean_text, same_domain

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None  # type: ignore

PRIORITY_KEYWORDS = (
    "alert", "recall", "notice", "greenbook", "green-book", "fsn", "warning",
    "withdraw", "suspend", "cancel", "blacklist", "watchlist", "list-of"
)

CRAWL_CONCURRENCY = 4


def is_priority(url: str, query_stems: Sequence[str] = ()) -> bool:
    path = urlparse(url).path.lower()
    if any(k in path for k in PRIORITY_KEYWORDS):
        return True
    if query_stems:
        words = re.findall(r"[a-z0-9]+", path)
        return any(w.startswith(s) for s in query_stems for w in words)
    return False


def is_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


from crawlee.crawlers import RenderingType, RenderingTypePrediction, RenderingTypePredictor
from crawlee import Request

class NafdacRenderingTypePredictor(RenderingTypePredictor):
    """Force Playwright for the Greenbook domain, static for everything else."""

    def predict(self, request: Request) -> RenderingTypePrediction:
        if GREENBOOK_HOST in request.url:
            return RenderingTypePrediction(
                rendering_type='client only',  # lowercase, with a space
                detection_probability_recommendation=0.0,  # always trust this
            )
        # Return a static prediction for everything else, instead of None.
        return RenderingTypePrediction(
            rendering_type='static',
            detection_probability_recommendation=0.0
        )

    def store_result(self, request: Request, rendering_type: RenderingType) -> None:
        """No-op: the policy is fixed, so there is nothing to learn."""
        pass

async def _fetch_pdf(context, url: str) -> Optional[PageDocument]:
    """Download and extract text from a PDF using the crawler's HTTP client."""
    if PdfReader is None:
        Actor.log.warning("pypdf not installed; skipping PDF %s", url)
        return None
    try:
        resp = await context.http_client.get(url, timeout=60_000)
        if not resp.ok:
            return None
        reader = PdfReader(io.BytesIO(resp.content))
        text = clean_text("\n".join((p.extract_text() or "") for p in reader.pages[:60]))
        if not text:
            return None
        first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
        title = first_line[:150] or url.rsplit("/", 1)[-1]
        return PageDocument(
            url=url, title=title, text=text, links=[], tables=[],
            greenbook=extract_greenbook(text, []), alerts=[],
        )
    except Exception as exc:
        Actor.log.warning("PDF failed %s: %r", url, exc)
        return None


async def crawl_site(
    start_urls: Sequence[str],
    allowed_domains: Sequence[str],
    max_pages: int,
    crawl_depth: int,
    page_timeout_ms: int,
    debug_html: bool = False,
    priority_terms: Sequence[str] = (),
    greenbook_terms: Sequence[str] = (),
) -> List[PageDocument]:
    """Crawl NAFDAC using Crawlee's AdaptivePlaywrightCrawler.

    Static pages (alerts, press releases, chemicals) go through HTTP for speed.
    The Greenbook, which renders its table via JavaScript, is forced through
    Playwright by the custom rendering type predictor.
    """
    documents: List[PageDocument] = []

    crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
        max_requests_per_crawl=max_pages,
        max_crawl_depth=crawl_depth,
        playwright_crawler_specific_kwargs={"headless": True},
        configure_logging=True,
        rendering_type_predictor=NafdacRenderingTypePredictor()
    )

    @crawler.router.default_handler
    async def request_handler(context: AdaptivePlaywrightCrawlingContext) -> None:
        url = context.request.url

        # --- PDF handling ---
        if is_pdf_url(url):
            pdf_doc = await _fetch_pdf(context, url)
            if pdf_doc:
                documents.append(pdf_doc)
            return

        # --- Extract HTML content ---
        # For static pages, parsed_content is the BeautifulSoup object.
        # For Playwright pages, we need the raw HTML from the page.
        try:
            html = str(context.parsed_content) if hasattr(context, "parsed_content") else ""
        except Exception:
            html = ""

        if not html:
            try:
                html = await context.page.content()
            except Exception:
                html = ""

        if not html:
            Actor.log.warning("No HTML content extracted for %s", url)
            return

        if debug_html:
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:100]
            await Actor.set_value(f"debug_{slug}", html, content_type="text/html")

        doc = extract_page_document(url, html)
        documents.append(doc)

        # --- Greenbook search (Playwright is guaranteed by the predictor) ---
        if greenbook_terms and urlparse(url).netloc.lower() == GREENBOOK_HOST:
            for original in list(greenbook_terms)[:3]:
                candidates = [original]
                mapped = map_to_active(original)
                if mapped and mapped.lower() != original.lower():
                    candidates.append(mapped)

                for term in candidates:
                    try:
                        if not await search_greenbook(context.page, term, page_timeout_ms):
                            continue
                        result_html = await context.page.content()
                        if not has_rows(result_html):
                            continue
                        result_doc = extract_page_document(url, result_html)
                        result_doc.url = f"{url}?product_name={term}"
                        result_doc.title = f"Greenbook search: {term}"
                        documents.append(result_doc)
                        break
                    except Exception as exc:
                        Actor.log.warning("Greenbook search failed for %r: %r", term, exc)

        # --- Enqueue discovered links ---
        await context.enqueue_links(strategy="same-domain")

    Actor.log.info(
        "Crawl starting: %d URL(s), max %d pages, depth %d",
        len(start_urls), max_pages, crawl_depth
    )

    await crawler.run(start_urls)

    Actor.log.info("Crawl finished: %d page(s)", len(documents))
    return documents