import os
import re
from typing import Any, Dict, List, Optional

from apify import Actor

from src.retrieval import GENERIC_TERMS, content_tokens, is_list_query, is_registration_query

try:
    from groq import AsyncGroq
except Exception: 
    AsyncGroq = None  # type: ignore

# Tried in order after the requested model;
FALLBACK_MODELS = ["openai/gpt-oss-20b", "llama-3.1-8b-instant"]

SYSTEM_PROMPT = (
    "You answer questions about NAFDAC (Nigeria's food and drug regulator) using ONLY the provided context. "
    "The context may contain dated Public Alerts / recalls / notices, Greenbook registration records, table rows and page excerpts. "
    "For alerts, state the date, alert number and title, and use the dates to decide what is 'latest'. "
    "If the answer is not in the context, say you cannot find it. "
    "Cite the source page URLs as plain text. Do not use special citation markers. Be concise."
)

SN_RE = re.compile(r"^\W*(s/?n|sn|no\.?|#|serial.*)\W*$", re.I)


def build_context_block(query: str, ranked_docs: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for idx, item in enumerate(ranked_docs[:8], start=1):
        doc = item["document"]
        lines = [f"[{idx}] Source page: {doc.url}", f"Page title: {doc.title}"]
        lines.append(f"Excerpt: {item.get('chunk') or doc.text[:1200]}")
        if doc.resources:
            lines.append("Documents/embeds on this page: " + "; ".join(f"{r['text']} -> {r['url']}" for r in doc.resources[:5]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def structured_greenbook_answer(query: str, ranked_docs: List[Dict[str, Any]]) -> Optional[str]:
    """Answer directly only when a real greenbook record clearly matches the query."""
    if not is_registration_query(query):
        return None

    q_tokens = set(content_tokens(query)) - {
        "registered", "registration", "greenbook", "green", "book", "nafdac",
        "approved", "licensed", "drug", "number", "reg",
    }
    if not q_tokens:
        return None

    for item in ranked_docs:
        doc = item["document"]
        gb = doc.greenbook
        if not gb or "hint" in gb or not gb.get("product_name"):
            continue

        name_tokens = set(content_tokens(gb["product_name"]))
        if not name_tokens:
            continue
        if len(q_tokens & name_tokens) < min(2, len(name_tokens)):
            continue

        lines = ["Registration record found:"]
        for key, value in gb.items():
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
        lines.append(f"- Source: {doc.url}")
        return "\n".join(lines)

    return None


def _format_row(row: Dict[str, Any]) -> str:
    if "cells" in row:
        vals = row["cells"]
    else:
        vals = [v for k, v in row.items() if not SN_RE.match(str(k))]
    vals = [str(v).strip() for v in vals if str(v).strip() and len(str(v)) <= 100]
    return " | ".join(vals)


def structured_table_answer(query: str, ranked_docs: List[Dict[str, Any]]) -> Optional[str]:
    """'List of ...' questions: print the matching table rows directly (no LLM, nothing truncated)."""
    if not is_list_query(query):
        return None
    rows = [i for i in ranked_docs if i.get("kind") == "table" and i.get("row")]
    if len(rows) < 3:
        return None

    top_url = rows[0]["document"].url
    rows = [r for r in rows if r["document"].url == top_url]
    rows.sort(key=lambda r: r.get("order", (0, 0)))
    doc = rows[0]["document"]
    total = sum(len(t.get("rows", [])) for t in doc.tables)

    # Words in the question that aren't about the page's topic as headers
    title_stems = {t[:6] for t in content_tokens(doc.title)}
    specific = [t[:6] for t in content_tokens(query) if t not in GENERIC_TERMS and t[:6] not in title_stems]
    if specific:
        def row_matches(r: Dict[str, Any]) -> bool:
            stems = {w[:6] for w in content_tokens(_format_row(r["row"]) + " " + " ".join(map(str, r["row"].values())))}
            return all(s in stems for s in specific)

        rows = [r for r in rows if row_matches(r)]
        if not rows:
            return f"No entries in \"{doc.title}\" match your question ({total} rows checked)."

    lines = [f"{doc.title}: {len(rows)} matching entries (table has {total} rows)", ""]
    for r in rows:
        line = f"- {_format_row(r['row'])}"
        if r.get("links"):
            line += "  -> " + ", ".join(l["url"] for l in r["links"])
        lines.append(line)
    if len(rows) < total:
        lines.append("\nTip: add a manufacturer, drug name or year to the question to narrow it down.")
    return "\n".join(lines)


def fallback_answer(query: str, ranked_docs: List[Dict[str, Any]]) -> str:
    if not ranked_docs:
        return "No relevant NAFDAC pages were found."

    alerts = [i for i in ranked_docs if i.get("kind") == "alert"]
    if alerts:
        return "\n".join(["Matching alerts:"] + [f"- {i['chunk']}" for i in alerts])

    best = ranked_docs[0]
    doc = best["document"]
    snippet = (best.get("chunk") or doc.text)[:700]
    return "\n".join(["Top match:", f"- Title: {doc.title}", f"- URL: {doc.url}", f"- Snippet: {snippet}"])


def _clean_llm_text(text: str) -> str:
    return re.sub(r"【[^】]*】", "", text).strip()  # strip gpt-oss citation markers


async def answer_question(query: str, ranked_docs: List[Dict[str, Any]], model: str) -> str:
    direct = structured_greenbook_answer(query, ranked_docs) or structured_table_answer(query, ranked_docs)
    if direct:
        return direct

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        Actor.log.warning("GROQ_API_KEY is not set; using fallback answer.")
    elif AsyncGroq is None:
        Actor.log.warning("The 'groq' package is not installed; using fallback answer.")
    elif ranked_docs:
        client = AsyncGroq(api_key=api_key)
        context = build_context_block(query, ranked_docs)
        for candidate in dict.fromkeys([model] + FALLBACK_MODELS):
            try:
                kwargs: Dict[str, Any] = {}
                if "gpt-oss" in candidate:
                    kwargs["extra_body"] = {"reasoning_effort": "low"}  # keeps reasoning from eating the token budget
                response = await client.chat.completions.create(
                    model=candidate,
                    temperature=0,
                    max_tokens=2000,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Question: {query}\n\nContext:\n{context}"},
                    ],
                    **kwargs,
                )
                text = _clean_llm_text(response.choices[0].message.content or "")
                if text:
                    return text
                Actor.log.warning("Model %s returned empty content.", candidate)
            except Exception as exc:
                Actor.log.error("Groq call failed with model %s: %r", candidate, exc)

    return fallback_answer(query, ranked_docs)


def collect_links(query: str, ranked_docs: List[Dict[str, Any]], max_pages: int = 5, max_resources: int = 8) -> Dict[str, Any]:
    """Clickable places behind an answer: source pages, alert pages, and PDFs/embeds found on those pages."""
    pages: List[Any] = []
    seen = set()
    for item in ranked_docs:
        doc = item["document"]
        if doc.url not in seen:
            seen.add(doc.url)
            pages.append(doc)
        if len(pages) >= max_pages:
            break

    alerts: List[Dict[str, Any]] = []
    alert_seen = set()
    for item in ranked_docs[:10]:
        al = item.get("alert")
        if al and al.get("url") not in alert_seen:
            alert_seen.add(al["url"])
            alerts.append({"title": al.get("title"), "date": al.get("date"), "url": al["url"]})

    stems = [t[:6] for t in content_tokens(query) if t not in GENERIC_TERMS]
    scored = []
    for doc in pages:
        for r in doc.resources:
            words = {w[:6] for w in content_tokens(f"{r['text']} {r['url']}")}
            scored.append((sum(1 for s in stems if s in words), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    resources = [r for s, r in scored if s > 0][:max_resources]
    # Embedded content (iframes/embeds) is easy to miss on the page, so always surface it.
    for _, r in scored:
        if r["type"] == "embed" and r not in resources and sum(1 for x in resources if x["type"] == "embed") < 3:
            resources.append(r)

    return {
        "pages": [{"title": d.title or d.url, "url": d.url} for d in pages],
        "alerts": alerts,
        "resources": resources,
    }


def format_links(links: Dict[str, Any]) -> str:
    out: List[str] = []
    if links.get("pages"):
        out.append("Pages:")
        out += [f"- {p['title']} - {p['url']}" for p in links["pages"]]
    if links.get("alerts"):
        out.append("Alerts:")
        out += [f"- {(a['date'] + ': ') if a.get('date') else ''}{a['title']} - {a['url']}" for a in links["alerts"]]
    if links.get("resources"):
        out.append("Documents and embedded content:")
        out += [f"- {r['text']} [{r['type']}] - {r['url']}" for r in links["resources"]]
    return ("Links:\n" + "\n".join(out)) if out else ""