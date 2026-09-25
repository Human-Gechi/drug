import math
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.extractors import PageDocument
from src.utils import tokenize

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "for", "and", "or", "in", "on",
    "at", "by", "with", "this", "that", "these", "those", "what", "which", "who", "me", "my",
    "about", "any", "there", "does", "do", "did", "it", "its", "be", "been", "can", "you",
    "tell", "show", "give", "please", "from", "has", "have", "how",
}

GENERIC_TERMS = {
    "list", "lists", "product", "products", "drug", "drugs", "nafdac", "latest", "recent",
    "new", "all", "every", "complete", "full", "many", "much", "find", "see", "get",
    "medicine", "medicines", "item", "items", "entries", "entry", "name", "names", "table",
}
RECENCY_WORDS = {"latest", "recent", "newest", "new", "today", "current", "last", "recently"}
ALERT_WORDS = {
    "alert", "alerts", "recall", "recalls", "notice", "notices", "warning", "warnings", "fsn", "fsns",
    "withdrawn", "withdrawal", "withdrawals", "recalled", "banned",
}
LIST_WORDS = {"list", "lists", "all", "every", "complete", "full"}
LIST_LIMIT = 300


class BM25:
    """Small BM25 (Lucene-style IDF, always positive, so tiny corpora still score sensibly)."""

    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(doc) for doc in corpus]
        self.lens = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.lens) / len(self.lens)) if corpus else 0.0
        df: Counter = Counter()
        for doc in corpus:
            df.update(set(doc))
        n = len(corpus)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def get_scores(self, query: List[str]) -> List[float]:
        scores = []
        for tf, dl in zip(self.tf, self.lens):
            s = 0.0
            for t in query:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
            scores.append(s)
        return scores


def is_registration_query(query: str) -> bool:
    q = query.lower()
    return any(
        phrase in q
        for phrase in [
            "registered", "registration", "greenbook", "green book", "reg no",
            "registration number", "approved", "licensed", "is this drug",
        ]
    )


def is_list_query(query: str) -> bool:
    return bool(set(tokenize(query)) & LIST_WORDS)


def content_tokens(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


def priority_terms(query: str) -> List[str]:
    """6-letter stems of the meaningful words in the query (e.g. 'withdrawals' -> 'withdr'),
    used by the crawler to visit matching URLs first."""
    return sorted({t[:6] for t in content_tokens(query) if len(t) >= 4 and t not in GENERIC_TERMS})


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%B %d, %Y")
    except ValueError:
        return None


def is_alert_recency_query(query: str) -> bool:
    toks = set(tokenize(query))
    return bool(toks & RECENCY_WORDS) and bool(toks & ALERT_WORDS)


def _alert_text(a: Dict[str, Any]) -> str:
    parts = [f"Alert dated {a.get('date') or 'unknown date'}"]
    if a.get("alert_no"):
        parts.append(f"No. {a['alert_no']}")
    parts.append(a.get("title") or "")
    parts.append(f"URL: {a.get('url')}")
    return " | ".join(parts)


def collect_alerts(documents: List[PageDocument]) -> List[Dict[str, Any]]:
    """All unique alerts across pages, newest first (undated last)."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for doc in documents:
        for a in doc.alerts:
            key = a.get("url") or a.get("title")
            if key in seen:
                continue
            seen.add(key)
            out.append({"alert": a, "document": doc, "dt": _parse_date(a.get("date"))})
    out.sort(key=lambda x: x["dt"] or datetime.min, reverse=True)
    return out


def build_chunks(documents: List[PageDocument], size: int = 900, overlap: int = 150) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    step = size - overlap

    for alert in collect_alerts(documents):
        chunks.append({"document": alert["document"], "chunk": _alert_text(alert["alert"]), "kind": "alert", "alert": alert["alert"]})

    for doc in documents:
        if doc.greenbook and "hint" not in doc.greenbook:
            gb = "; ".join(f"{k}: {v}" for k, v in doc.greenbook.items())
            chunks.append({"document": doc, "chunk": f"{doc.title}\nGreenbook record: {gb}", "kind": "greenbook"})

        text = doc.text or ""
        for i in range(0, max(len(text), 1), step):
            piece = text[i : i + size]
            if piece.strip():
                chunks.append({"document": doc, "chunk": f"{doc.title}\n{piece}", "kind": "page"})

        # One chunk per table row; the page title is included so "withdrawn products" matches every row.
        for t_i, t in enumerate(doc.tables):
            row_links = t.get("row_links") or []
            for r_i, row in enumerate(t.get("rows", [])):
                cells = " | ".join(
                    " ; ".join(v) if k == "cells" else f"{k}: {v}" for k, v in row.items()
                )
                if cells.strip():
                    chunks.append(
                        {
                            "document": doc,
                            "chunk": f"{doc.title}\nTable row: {cells}",
                            "kind": "table",
                            "row": row,
                            "links": row_links[r_i] if r_i < len(row_links) else [],
                            "order": (t_i, r_i),
                        }
                    )
    return chunks


def rank_documents(query: str, documents: List[PageDocument], top_k: int = 6) -> List[Dict[str, Any]]:
    limit = max(top_k, LIST_LIMIT) if is_list_query(query) else top_k
    chunks = build_chunks(documents)
    results: List[Dict[str, Any]] = []

    # "latest alerts" questions: sort by date ourselves, don't rely on text similarity.
    if set(tokenize(query)) & ALERT_WORDS:
        for rank, alert in enumerate(collect_alerts(documents)[:10]):
            results.append(
                {
                    "document": alert["document"],
                    "chunk": _alert_text(alert["alert"]),
                    "kind": "alert",
                    "alert": alert["alert"],
                    "score": 100.0 - rank,
                }
            )

    tokenized = [(c, content_tokens(c["chunk"])) for c in chunks]
    tokenized = [(c, t) for c, t in tokenized if t]
    q_tokens = content_tokens(query)

    if tokenized and q_tokens:
        bm25 = BM25([t for _, t in tokenized])
        scores = bm25.get_scores(q_tokens)
        ranked = sorted(zip(scores, [c for c, _ in tokenized]), key=lambda x: x[0], reverse=True)
        taken = {r["chunk"] for r in results}
        for score, chunk in ranked:
            if len(results) >= limit:
                break
            if score <= 0 and results:
                break
            if chunk["chunk"] in taken:
                continue
            results.append({**chunk, "score": float(score)})

    return results[:limit]