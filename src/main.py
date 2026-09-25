import asyncio
import json
import sys

from apify import Actor

from src.answers import answer_question, collect_links, format_links
from src.config import AppConfig
from src.crawler import crawl_site
from src.retrieval import priority_terms, rank_documents


async def main() -> None:
    # Windows consoles for the apfiy platform default to cp1252 and crash on characters like \u202f.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    async with Actor:
        raw_input = await Actor.get_input() or {}
        config = AppConfig.from_input(raw_input)

        documents = await crawl_site(
            start_urls=config.start_urls,
            allowed_domains=config.allowed_domains,
            max_pages=config.max_pages,
            crawl_depth=config.crawl_depth,
            page_timeout_ms=config.page_timeout_ms,
            debug_html=config.debug_html,
            priority_terms=priority_terms(config.query),
            greenbook_terms=config.greenbook_terms,
        )

        for doc in documents:
            await Actor.push_data({"type": "page", **doc.to_item()})

        summary = {
            "type": "summary",
            "crawledPages": len(documents),
            "alertsFound": sum(len(d.alerts) for d in documents),
            "tableRows": sum(len(t.get("rows", [])) for d in documents for t in d.tables),
            "crawledUrls": [d.url for d in documents],
            "startUrls": config.start_urls,
            "allowedDomains": config.allowed_domains,
        }

        if config.query:
            ranked = rank_documents(config.query, documents, top_k=6)
            answer = await answer_question(config.query, ranked, config.ai_model)
            links = collect_links(config.query, ranked)
            links_text = format_links(links)
            if links_text:
                answer = f"{answer}\n\n{links_text}"

            sources, seen = [], set()
            for item in ranked:
                url = item["document"].url
                if url in seen:
                    continue
                seen.add(url)
                sources.append(
                    {
                        "url": url,
                        "title": item["document"].title,
                        "kind": item.get("kind"),
                        "score": item["score"],
                        "excerpt": (item.get("chunk") or "")[:300],
                    }
                )
                if len(sources) >= 10:
                    break

            result = {"type": "answer", "query": config.query, "answer": answer, "links": links, "sources": sources}
            await Actor.push_data(result)
            summary = {**summary, **result}

        await Actor.push_data(summary)
        try:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())