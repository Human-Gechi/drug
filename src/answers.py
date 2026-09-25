import asyncio
import os
import re
from typing import Any

from apify import Actor

from src.extractors import map_greenbook_fields
from src.greenbook import map_to_active
from src.retrieval import (
    DEFAULT_ALERT_RESULTS,
    DEFAULT_DOCUMENT_RESULTS,
    DEFAULT_REGISTRATION_RESULTS,
    DEFAULT_TABLE_RESULTS,
    GENERIC_TERMS,
    content_tokens,
    extract_count,
    is_alert_query,
    is_list_query,
    resolve_limit,
)

try:
    from groq import AsyncGroq
except ImportError:
    AsyncGroq = None  # type: ignore

FALLBACK_MODELS = ["openai/gpt-oss-20b", "llama-3.1-8b-instant"]
GROQ_TIMEOUT_S = 30.0

SYSTEM_PROMPT = (
    "You answer questions about NAFDAC (Nigeria's food and drug regulator) using ONLY the provided context. "
    "The context may contain dated Public Alerts / recalls / notices, Greenbook registration records, table rows and page excerpts. "
    "For alerts, state the date, alert number and title, and use the dates to decide what is 'latest'. "
    "If the answer is not in the context, say you cannot find it. "
    "Cite the source page URLs as plain text. Do not use special citation markers. Be concise."
)

SN_RE = re.compile(r"^\W*(s/?n|sn|no\.?|#|serial.*)\W*$", re.IGNORECASE)

_PLACEHOLDER_MARKERS = (
    "no matching records found",
    "no records found",
    "no data available in table",
    "no results found",
    "no matching records",
)

_SINGULAR_SUPERLATIVE_RE = re.compile(
    r"\b(?:the\s+)?(?:last|latest|newest|most\s+recent|first|only)\b"
    r"|\bwhich\s+(?:one|single)\b",
    re.IGNORECASE,
)

_DOCUMENT_QUERY_WORDS = {
    "document",
    "documents",
    "file",
    "files",
    "pdf",
    "pdfs",
    "download",
    "attachment",
    "attachments",
    "chemical",
    "chemicals",
    "pesticide",
    "pesticides",
    "disinfectant",
    "disinfectants",
    "narcotic",
    "narcotics",
}


def wants_single_answer(query: str) -> bool:
    return bool(_SINGULAR_SUPERLATIVE_RE.search(query))


def _wants_document(query: str) -> bool:
    return bool(set(content_tokens(query)) & _DOCUMENT_QUERY_WORDS)


# LLM context


def build_context_block(query: str, ranked_docs: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for idx, item in enumerate(ranked_docs[:20], start=1):
        doc = item["document"]
        lines = [f"[{idx}] Source page: {doc.url}", f"Page title: {doc.title}"]
        lines.append(f"Excerpt: {item.get('chunk') or doc.text[:2500]}")
        if doc.resources:
            lines.append(
                "Documents linked from this page: "
                + "; ".join(f"{r['text']} -> {r['url']}" for r in doc.resources[:5])
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# Deterministic answer branches (no LLM)


def structured_greenbook_answer(
    query: str,
    ranked_docs: list[dict[str, Any]],
    max_results: int | None = None,
    default_max_results: int | None = None,
) -> dict[str, Any] | None:
    q_tokens = set(content_tokens(query)) - {
        "registered",
        "registration",
        "greenbook",
        "green",
        "book",
        "nafdac",
        "approved",
        "licensed",
        "drug",
        "number",
        "reg",
    }
    if not q_tokens:
        return None

    notes: list[str] = []
    expanded = set()
    for t in q_tokens:
        expanded.add(t)
        mapped = map_to_active(t)
        if mapped and mapped.lower() != t.lower():
            expanded.update(content_tokens(mapped))
            notes.append(
                f"Searched for '{mapped}' because '{t}' is a brand name of {mapped}."
            )
    q_tokens = expanded

    records: list[dict[str, Any]] = []
    source_urls: list[str] = []
    seen = set()
    for item in ranked_docs:
        doc = item["document"]
        gb = doc.greenbook
        if not gb or "hint" in gb or not gb.get("product_name"):
            continue

        name_tokens = set(content_tokens(gb["product_name"]))
        if not name_tokens:
            continue
        if not (q_tokens & name_tokens):
            continue

        mapped = map_greenbook_fields(gb)
        key = (mapped.get("product_name"), mapped.get("nrn"), doc.url)
        if key in seen:
            continue
        seen.add(key)
        records.append(mapped)
        if doc.url not in source_urls:
            source_urls.append(doc.url)

    if not records:
        return None

    limit = resolve_limit(
        query,
        max_results,
        default_max_results or DEFAULT_REGISTRATION_RESULTS,
    )
    if len(records) > limit:
        records = records[:limit]

    first = records[0]
    parts: list[str] = []
    if (first.get("status") or "").lower() == "active":
        parts.append("Yes.")
    name = first.get("product_name") or "The product"
    nrn = first.get("nrn")
    applicant = first.get("applicant_name")
    if nrn:
        parts.append(f"{name} (NAFDAC Reg. No. {nrn})")
    else:
        parts.append(name)
    if applicant:
        parts.append(f"is registered to {applicant}.")
    else:
        parts.append("is registered.")
    if first.get("status"):
        parts.append(f"Status: {first['status']}.")
    if first.get("active_ingredients"):
        parts.append(f"Active ingredient: {first['active_ingredients']}.")
    if first.get("approval_date"):
        parts.append(f"Approved {first['approval_date']}.")
    if len(records) > 1:
        parts.append(f"({len(records) - 1} more matching record(s) in answerDetail.)")

    sources = [
        {"title": f"NAFDAC Greenbook – search: {first.get('product_name')}", "url": u}
        for u in source_urls[:3]
    ]
    return {
        "answer": " ".join(parts),
        "detail": {"records": records},
        "sources": sources,
        "notes": notes,
    }


def _format_row(row: dict[str, Any]) -> str:
    if "cells" in row:
        vals = row["cells"]
    else:
        vals = [v for k, v in row.items() if not SN_RE.match(str(k))]
    vals = [str(v).strip() for v in vals if str(v).strip() and len(str(v)) <= 100]
    return " | ".join(vals)


def _row_fields(row: dict[str, Any]) -> dict[str, Any]:
    if "cells" in row:
        return {f"col_{i + 1}": v for i, v in enumerate(row["cells"])}
    return {k: v for k, v in row.items() if not SN_RE.match(str(k))}


def _row_cell_count(row: dict[str, Any]) -> int:
    if "cells" in row:
        cells = row["cells"]
    else:
        cells = [v for k, v in row.items() if not SN_RE.match(str(k))]
    return sum(
        1
        for c in cells
        if isinstance(c, str)
        and len(c.strip()) >= 2
        and c.strip() not in {"×", "x", "-"}
    )


def _is_placeholder_row(row: dict[str, Any]) -> bool:
    text = _format_row(row).lower()
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)


def structured_table_answer(
    query: str,
    ranked_docs: list[dict[str, Any]],
    max_results: int | None = None,
    default_max_results: int | None = None,
) -> dict[str, Any] | None:
    rows = [i for i in ranked_docs if i.get("kind") == "table" and i.get("row")]
    if len(rows) < 3:
        return None

    rows = [r for r in rows if _row_cell_count(r["row"]) >= 2]
    if len(rows) < 3:
        return None

    # A table answer is only valid when the user asked for a list.
    if not is_list_query(query):
        return None

    top_url = rows[0]["document"].url
    top_doc = rows[0]["document"]

    page_words = set(content_tokens(top_doc.title))
    for t in top_doc.tables:
        for h in t.get("headers", []) or []:
            page_words.update(content_tokens(h))

    if not (set(content_tokens(query)) & page_words):
        return None

    rows = [r for r in rows if r["document"].url == top_url]
    rows.sort(key=lambda r: r.get("order", (0, 0)))
    doc = rows[0]["document"]
    total = sum(len(t.get("rows", [])) for t in doc.tables)

    q_specific = [t for t in content_tokens(query) if t not in GENERIC_TERMS]
    if q_specific:

        def row_matches(r: dict[str, Any]) -> bool:
            cell_words = set(
                content_tokens(
                    _format_row(r["row"]) + " " + " ".join(map(str, r["row"].values()))
                )
            )
            return any(w in cell_words for w in q_specific)

        rows = [r for r in rows if row_matches(r)]
        if not rows:
            return None

    records: list[dict[str, Any]] = []
    for r in rows:
        if _is_placeholder_row(r["row"]):
            continue
        if _row_cell_count(r["row"]) < 2:
            continue
        record = _row_fields(r["row"])
        links = r.get("links") or []
        if links:
            record["urls"] = [l["url"] for l in links]
            if len(links) == 1:
                record["url"] = links[0]["url"]
        records.append(record)

    if not records:
        return None

    limit = resolve_limit(
        query,
        max_results,
        default_max_results or DEFAULT_TABLE_RESULTS,
    )
    if len(records) > limit:
        records = records[:limit]

    n = len(records)
    lines = [
        f'{n} entr{"y" if n == 1 else "ies"} in "{doc.title}" ({total} rows total):',
        "",
    ]
    for i, r in enumerate(records, start=1):
        text_parts: list[str] = []
        urls: list[str] = []
        for k, v in r.items():
            if k in ("url", "urls"):
                if isinstance(v, str):
                    urls.append(v)
                elif isinstance(v, list):
                    urls.extend(str(x) for x in v)
                continue
            s = str(v).strip()
            if not s:
                continue
            if s.startswith("http"):
                urls.append(s)
            else:
                text_parts.append(s)
        lines.append(f"{i}. {' | '.join(text_parts)[:250]}")
        for u in urls:
            lines.append(f"   {u}")
        lines.append("")

    return {
        "answer": "\n".join(lines).rstrip(),
        "detail": {
            "page_title": doc.title,
            "page_url": doc.url,
            "total_rows": total,
            "records": records,
        },
        "sources": [{"title": doc.title or doc.url, "url": doc.url}],
        "notes": [],
    }


def structured_alerts_answer(
    query: str,
    ranked_docs: list[dict[str, Any]],
    max_results: int | None = None,
    default_max_results: int | None = None,
) -> dict[str, Any] | None:
    seen = set()
    records: list[dict[str, Any]] = []
    for item in ranked_docs:
        alert = item.get("alert")
        if item.get("kind") != "alert" or not alert:
            continue
        key = alert.get("url") or alert.get("title")
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "date": alert.get("date"),
                "alert_no": alert.get("alert_no"),
                "title": alert.get("title"),
                "url": alert.get("url"),
            }
        )
    if not records:
        return None

    def _sort_key(a: dict[str, Any]) -> tuple:
        no = a.get("alert_no") or ""
        try:
            parts = no.split("/")
            return (int(parts[1]), int(parts[0]))
        except (ValueError, IndexError):
            return (0, 0)

    records = sorted(records, key=_sort_key, reverse=True)

    if (
        max_results is None
        and wants_single_answer(query)
        and extract_count(query) is None
    ):
        limit = 1
    else:
        limit = resolve_limit(
            query,
            max_results,
            default_max_results or DEFAULT_ALERT_RESULTS,
        )

    if len(records) > limit:
        records = records[:limit]

    if len(records) == 1:
        first = records[0]
        text = first.get("title") or "Latest alert"
        if first.get("alert_no"):
            text = f"Public Alert No. {first['alert_no']} – {text}"
        if first.get("date"):
            text = f"{text} ({first['date']})"
        return {"answer": text, "detail": {"alert": first}, "sources": [], "notes": []}

    lines = [f"Found {len(records)} matching alert(s):", ""]
    for i, r in enumerate(records, start=1):
        header = f"{i}. "
        if r.get("alert_no"):
            header += f"{r['alert_no']} — "
        header += (r.get("title") or "(untitled)")[:200]
        lines.append(header)
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")

    return {
        "answer": "\n".join(lines).rstrip(),
        "detail": {"records": records},
        "sources": [],
        "notes": [],
    }


def document_answer(
    query: str,
    ranked_docs: list[dict[str, Any]],
    max_results: int | None = None,
    default_max_results: int | None = None,
) -> dict[str, Any] | None:
    if not _wants_document(query):
        return None

    docs: list[dict[str, Any]] = []
    seen = set()
    for item in ranked_docs:
        for r in item["document"].resources:
            t = r.get("type")
            if t not in ("pdf", "doc", "docx", "xls", "xlsx", "csv"):
                continue
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            docs.append(
                {
                    "title": r.get("text") or r["url"].rsplit("/", 1)[-1],
                    "url": r["url"],
                    "type": t,
                }
            )

    if not docs:
        return None

    q_words = [w for w in content_tokens(query) if w not in GENERIC_TERMS]
    if q_words:

        def score(d: dict[str, Any]) -> int:
            hay = (d["title"] + " " + d["url"]).lower()
            return sum(1 for w in q_words if w in hay)

        docs.sort(key=score, reverse=True)

    limit = resolve_limit(
        query,
        max_results,
        default_max_results or DEFAULT_DOCUMENT_RESULTS,
    )
    if len(docs) > limit:
        docs = docs[:limit]

    lines = [f"Found {len(docs)} document(s):", ""]
    for i, d in enumerate(docs, start=1):
        lines.append(f"{i}. {d['title']}")
        lines.append(f"   {d['url']}")
        lines.append("")

    return {
        "answer": "\n".join(lines).rstrip(),
        "detail": {"documents": docs},
        "sources": [{"title": d["title"], "url": d["url"]} for d in docs],
        "notes": [],
    }


# Fallback used only when the LLM is unavailable


def _fallback_text(query: str, ranked_docs: list[dict[str, Any]]) -> dict[str, Any]:
    if not ranked_docs:
        return {
            "answer": "No relevant NAFDAC pages were found.",
            "detail": None,
            "sources": [],
            "notes": [],
        }
    best = ranked_docs[0]
    doc = best["document"]
    snippet = (best.get("chunk") or doc.text)[:500]
    return {
        "answer": f"{doc.title or doc.url}: {snippet}",
        "detail": None,
        "sources": useful_sources(ranked_docs),
        "notes": ["Groq was unavailable; showing top retrieved excerpt."],
    }


# LLM path (this is where Groq is called)


def _clean_llm_text(text: str) -> str:
    return re.sub(r"【[^】]*】", "", text).strip()


async def _llm_text_answer(
    query: str, ranked_docs: list[dict[str, Any]], model: str
) -> str | None:
    """Call Groq with the retrieved context. Returns None on any failure so
    the caller can fall back to a plain excerpt."""
    api_key = (
        os.getenv("GROQ_API_KEY")
        or ""
    ).strip()
    if not api_key:
        Actor.log.warning("GROQ_API_KEY is not set; using fallback answer.")
        return None
    if AsyncGroq is None:
        Actor.log.warning("The 'groq' package is not installed; using fallback answer.")
        return None
    if not ranked_docs:
        return None

    client = AsyncGroq(api_key=api_key)
    context = build_context_block(query, ranked_docs)

    for candidate in dict.fromkeys([model] + FALLBACK_MODELS):
        try:
            kwargs: dict[str, Any] = {}
            if "gpt-oss" in candidate:
                kwargs["extra_body"] = {"reasoning_effort": "low"}

            Actor.log.info("Calling Groq with model %s", candidate)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=candidate,
                    temperature=0,
                    max_tokens=2000,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"Question: {query}\n\nContext:\n{context}",
                        },
                    ],
                    **kwargs,
                ),
                timeout=GROQ_TIMEOUT_S,
            )
            text = _clean_llm_text(response.choices[0].message.content or "")
            if text:
                Actor.log.info(
                    "Groq returned %d characters using %s", len(text), candidate
                )
                return text
            Actor.log.warning("Model %s returned empty content.", candidate)
        except asyncio.TimeoutError:
            Actor.log.error(
                "Groq call timed out after %.0fs with model %s",
                GROQ_TIMEOUT_S,
                candidate,
            )
        except (RuntimeError, KeyError, ValueError) as exc:
            Actor.log.error("Groq call failed with model %s: %r", candidate, exc)

    return None


# Source filtering

_EMPTY_MARKERS = (
    "no matching records found",
    "no records found",
    "no results",
    "0 results",
    "0 records",
)


def useful_sources(
    ranked_docs: list[dict[str, Any]],
    max_sources: int = 5,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()

    for item in ranked_docs:
        doc = item["document"]
        if doc.url in seen:
            continue

        chunk = (item.get("chunk") or "").lower()
        if any(marker in chunk for marker in _EMPTY_MARKERS) and len(chunk) < 120:
            continue

        if (
            not (doc.text or "").strip()
            and not doc.tables
            and not doc.alerts
            and not (doc.greenbook and "hint" not in doc.greenbook)
            and not doc.resources
        ):
            continue

        seen.add(doc.url)
        out.append({"title": doc.title or doc.url, "url": doc.url})
        if len(out) >= max_sources:
            return out

    for item in ranked_docs:
        for r in item["document"].resources:
            if r["url"] in seen:
                continue
            if r.get("type") not in ("pdf", "doc", "docx", "xls", "xlsx", "csv"):
                continue
            seen.add(r["url"])
            out.append({"title": r.get("text") or r["url"], "url": r["url"]})
            if len(out) >= max_sources:
                return out

    return out


# Entry point


async def answer_question(
    query: str,
    ranked_docs: list[dict[str, Any]],
    model: str,
    max_results: int | None = None,
    default_max_results: int | None = None,
) -> dict[str, Any]:
    """Returns:
    {
        "answerType": "registration" | "table" | "alerts" | "document" | "text",
        "answer": str,
        "answerDetail": dict | None,
        "recordCount": int,
        "sources": [ {title, url} ... ],
        "notes": [str],
    }
    """
    # 1. Registration
    reg = structured_greenbook_answer(
        query,
        ranked_docs,
        max_results=max_results,
        default_max_results=default_max_results,
    )
    if reg:
        count = len(reg["detail"]["records"])
        Actor.log.info("answer branch: registration (%d record(s))", count)
        return {
            "answerType": "registration",
            "answer": reg["answer"],
            "answerDetail": reg["detail"],
            "recordCount": count,
            "sources": reg["sources"],
            "notes": reg["notes"],
        }

    # 2. Table
    tbl = structured_table_answer(
        query,
        ranked_docs,
        max_results=max_results,
        default_max_results=default_max_results,
    )
    if tbl:
        count = len(tbl["detail"]["records"])
        Actor.log.info("answer branch: table (%d row(s))", count)
        return {
            "answerType": "table",
            "answer": tbl["answer"],
            "answerDetail": tbl["detail"],
            "recordCount": count,
            "sources": tbl["sources"],
            "notes": tbl["notes"],
        }

    # 3. Alerts
    if is_alert_query(query):
        al = structured_alerts_answer(
            query,
            ranked_docs,
            max_results=max_results,
            default_max_results=default_max_results,
        )
        if al:
            count = 1 if "alert" in al["detail"] else len(al["detail"]["records"])
            Actor.log.info("answer branch: alerts (%d record(s))", count)
            return {
                "answerType": "alerts",
                "answer": al["answer"],
                "answerDetail": al["detail"],
                "recordCount": count,
                "sources": al["sources"],
                "notes": al["notes"],
            }

    # 4. Document
    doc = document_answer(
        query,
        ranked_docs,
        max_results=max_results,
        default_max_results=default_max_results,
    )
    if doc:
        count = len(doc["detail"]["documents"])
        Actor.log.info("answer branch: document (%d link(s))", count)
        return {
            "answerType": "document",
            "answer": doc["answer"],
            "answerDetail": doc["detail"],
            "recordCount": count,
            "sources": doc["sources"],
            "notes": doc["notes"],
        }

    # 5. Free-form text via LLM (fallback if no key)
    Actor.log.info("answer branch: LLM text")
    text = await _llm_text_answer(query, ranked_docs, model)
    if text:
        return {
            "answerType": "text",
            "answer": text,
            "answerDetail": None,
            "recordCount": 0,
            "sources": useful_sources(ranked_docs),
            "notes": [],
        }

    fb = _fallback_text(query, ranked_docs)
    return {
        "answerType": "text",
        "answer": fb["answer"],
        "answerDetail": fb["detail"],
        "recordCount": 0,
        "sources": fb["sources"],
        "notes": fb["notes"],
    }
