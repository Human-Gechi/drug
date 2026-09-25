import asyncio
import json
import sys
from typing import Any

from apify import Actor

from src.answers import answer_question
from src.config import AppConfig
from src.crawler import crawl_site
from src.retrieval import is_list_query, priority_terms, rank_documents

PUSH_BATCH_SIZE = 50


async def _push_batched(records: list[dict[str, Any]]) -> None:
    if not records:
        return
    for i in range(0, len(records), PUSH_BATCH_SIZE):
        await Actor.push_data(records[i : i + PUSH_BATCH_SIZE])


async def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, RuntimeError):
            Actor.log.debug("Could not reconfigure stream encoding")

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

        Actor.log.info("crawled %d page(s)", len(documents))

        if config.query:
            top_k = 40 if is_list_query(config.query) else 12
            ranked = rank_documents(config.query, documents, top_k=top_k)
            result = await answer_question(
                config.query,
                ranked,
                config.ai_model,
                max_results=config.max_results,
                default_max_results=config.default_max_results,
            )
            output: dict[str, Any] = {
                "type": "answer",
                "query": config.query,
                "answerType": result["answerType"],
                "answer": result["answer"],
                "recordCount": result.get("recordCount", 0),
                "answerDetail": result["answerDetail"],
                "sources": result["sources"],
                "notes": result["notes"],
            }
        else:
            output = {
                "type": "answer",
                "query": "",
                "answerType": "none",
                "answer": f"No question was asked. {len(documents)} NAFDAC page(s) were crawled.",
                "recordCount": 0,
                "answerDetail": None,
                "sources": [],
                "notes": [],
            }

        # Only one record per run goes into the default dataset.
        await Actor.push_data(output)

        await Actor.set_value("OUTPUT", output)
        try:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        except UnicodeEncodeError:
            print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
