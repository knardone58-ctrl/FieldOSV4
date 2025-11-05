from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_INDEX_PATH = Path(os.getenv("FIELDOS_CHAT_INDEX_PATH", "data/reference_index.jsonl"))
DEFAULT_STUB_PATH = Path(os.getenv("FIELDOS_CHAT_INDEX_STUB_PATH", "tests/fixtures/reference_index_stub.jsonl"))
DEFAULT_EMBED_MODEL = os.getenv("FIELDOS_CHAT_EMBED_MODEL", "text-embedding-3-small")

_keyword_warning_logged = False
_DEFAULT_INDEX: Optional["ReferenceIndex"] = None


def _load_openai_client():
    try:
        from ai_parser import _get_openai_client  # type: ignore
    except ImportError:  # pragma: no cover - defensive
        _get_openai_client = None  # type: ignore

    if _get_openai_client is None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return OpenAI()
    try:
        return _get_openai_client()
    except RuntimeError:
        return None


def _fallback_mode() -> str:
    return os.getenv("FIELDOS_CHAT_FALLBACK_MODE", "").lower()


@dataclass
class Snippet:
    source: str
    title: str
    content: str
    url: str
    score: float
    tags: List[str]
    value_props: List[str]
    discount: Optional[str]
    category: str
    metadata: Dict[str, Any]


@dataclass
class _Record:
    id: str
    vector: Optional[List[float]]
    snippet: Snippet
    tokens: List[str]
    bigrams: List[str]
    norm: float


class ReferenceIndex:
    """In-memory index of reference snippets with embedding vectors."""

    def __init__(self, records: Sequence[_Record], embed_model: str) -> None:
        self.records = list(records)
        self.embed_model = embed_model
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = _load_openai_client()
        return self._client

    def _embed_query(self, query: str) -> Optional[List[float]]:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            response = client.embeddings.create(model=self.embed_model, input=[query])
        except Exception as exc:  # pragma: no cover - network guard
            LOGGER.info("Reference index falling back to keyword search (embedding error: %s)", exc)
            return None
        data = response.data[0]
        return list(data.embedding)  # type: ignore[attr-defined]

    def search(self, query: str, top_k: int = 4) -> List[Snippet]:
        query = query.strip()
        if not query:
            return []

        if _fallback_mode() == "stub":
            return _keyword_rank(self.records, query)[:top_k]

        query_vector = self._embed_query(query)
        if query_vector is None:
            return _keyword_rank(self.records, query)[:top_k]
        scores = []
        norm_q = math.sqrt(sum(v * v for v in query_vector))
        if norm_q == 0:
            return _keyword_rank(self.records, query)[:top_k]
        for record in self.records:
            if record.vector is None or record.norm == 0:
                continue
            dot = sum(qv * rv for qv, rv in zip(query_vector, record.vector))
            cosine = dot / (norm_q * record.norm)
            scores.append((cosine, record.snippet))

        if not scores:
            return _keyword_rank(self.records, query)[:top_k]
        scores.sort(key=lambda item: item[0], reverse=True)
        top = []
        for score, snippet in scores[:top_k]:
            top.append(
                Snippet(
                    source=snippet.source,
                    title=snippet.title,
                    content=snippet.content,
                    url=snippet.url,
                    score=score,
                    tags=snippet.tags,
                    value_props=snippet.value_props,
                    discount=snippet.discount,
                    category=snippet.category,
                    metadata=snippet.metadata,
                )
            )
        return top


def _tokenise(text: str) -> List[str]:
    text = text.lower()
    return re.findall(r"[a-z0-9]+", text)


def _bigrams(tokens: Sequence[str]) -> List[str]:
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]


def _keyword_rank(records: Sequence[_Record], query: str) -> List[Snippet]:
    global _keyword_warning_logged
    if not _keyword_warning_logged:
        LOGGER.info("Reference index falling back to keyword search")
        _keyword_warning_logged = True
    query_tokens = _tokenise(query)
    query_bigrams = set(_bigrams(query_tokens))
    scores = []
    for record in records:
        common = sum(record.tokens.count(tok) for tok in query_tokens)
        common += sum(1 for bigram in record.bigrams if bigram in query_bigrams)
        snippet = record.snippet
        tag_hits = sum(
            1
            for tag in snippet.tags
            for token in query_tokens
            if token in tag.lower()
        )
        value_hits = sum(
            1
            for prop in snippet.value_props
            for token in query_tokens
            if token in prop.lower()
        )
        common += tag_hits * 2 + value_hits
        if common == 0:
            continue
        scores.append((float(common), snippet))
    scores.sort(key=lambda item: item[0], reverse=True)
    return [
        Snippet(
            source=snippet.source,
            title=snippet.title,
            content=snippet.content,
            url=snippet.url,
            score=score,
            tags=snippet.tags,
            value_props=snippet.value_props,
            discount=snippet.discount,
            category=snippet.category,
            metadata=snippet.metadata,
        )
        for score, snippet in scores
    ]


def _load_records(path: Path) -> List[_Record]:
    records: List[_Record] = []
    if not path.exists():
        raise FileNotFoundError(f"Reference index missing at {path}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            tags = list(payload.get("tags") or [])
            value_props = list(payload.get("value_props") or [])
            discount = payload.get("discount")
            metadata = payload.get("metadata") or {}
            category = metadata.get("category") if isinstance(metadata, dict) else None
            if not category and payload.get("source") == "pricing":
                category = "pricing"
            snippet = Snippet(
                source=payload["source"],
                title=payload["title"],
                content=payload["content"],
                url=payload.get("url") or "#",
                score=0.0,
                tags=tags,
                value_props=value_props,
                discount=discount,
                category=category or "general",
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            vector = payload.get("vector")
            vector_list = list(vector) if isinstance(vector, list) else None
            combined_text = " ".join([snippet.content] + tags + value_props)
            tokens = _tokenise(combined_text)
            records.append(
                _Record(
                    id=payload["id"],
                    vector=vector_list,
                    snippet=snippet,
                    tokens=tokens,
                    bigrams=_bigrams(tokens),
                    norm=math.sqrt(sum(v * v for v in vector_list)) if vector_list else 0.0,
                )
            )
    return records


def load_index(path: Path = DEFAULT_INDEX_PATH, fallback_path: Optional[Path] = DEFAULT_STUB_PATH) -> ReferenceIndex:
    resolved_path = path if path.is_absolute() else Path(path)
    if not resolved_path.exists() and fallback_path:
        resolved_fallback = fallback_path if fallback_path.is_absolute() else Path(fallback_path)
        if resolved_fallback.exists():
            resolved_path = resolved_fallback
    records = _load_records(resolved_path)
    index = ReferenceIndex(records, DEFAULT_EMBED_MODEL)
    global _DEFAULT_INDEX
    _DEFAULT_INDEX = index
    return index


def _ensure_default_index() -> ReferenceIndex:
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = load_index(DEFAULT_INDEX_PATH, DEFAULT_STUB_PATH)
    return _DEFAULT_INDEX


def search(query: str, top_k: int = 4) -> List[Snippet]:
    index = _ensure_default_index()
    return index.search(query, top_k=top_k)
