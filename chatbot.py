from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from reference_search import DEFAULT_INDEX_PATH, DEFAULT_STUB_PATH, Snippet, load_index, search as search_index

LOGGER = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = os.getenv("FIELDOS_CHAT_COMPLETION_MODEL", "gpt-4o-mini")
DEFAULT_CHAT_STUB_PATH = Path(os.getenv("FIELDOS_CHAT_STUB_PATH", "tests/fixtures/chat_stub.json"))

MAX_TURNS = 6
POSITIONING_KEYWORDS = {"position", "pitch", "value", "selling", "sell", "upsell", "recommend"}


def _fallback_mode() -> str:
    return os.getenv("FIELDOS_CHAT_FALLBACK_MODE", "").lower()


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


def _trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(history) <= MAX_TURNS:
        return history[-MAX_TURNS:]
    return history[-MAX_TURNS:]


def _format_citation(snippet: Snippet) -> str:
    prefix = {"wiki": "Wiki", "crm": "CRM", "playbook": "Playbook"}.get(snippet.source, snippet.source.title())
    return f"{prefix}: {snippet.title}"


def _build_context_block(snippets: List[Snippet]) -> str:
    lines = []
    for idx, snippet in enumerate(snippets, start=1):
        label = _format_citation(snippet)
        lines.append(f"[{idx}] {label}\nSnippet: {snippet.content}")
    return "\n\n".join(lines)


def build_prompt(query: str, snippets: List[Snippet], history: List[Dict[str, str]]) -> List[Dict[str, object]]:
    trimmed_history = _trim_history(history)
    context = _build_context_block(snippets) if snippets else "No snippets available."
    system_text = (
        "You are the FieldOS Reference Copilot. "
        "Answer questions using only the provided snippets. "
        "Keep replies under 120 words, use natural language, and add inline citations "
        "like [Wiki: Mulch Promo Guidelines]. "
        "If you lack data, say you don't have that information."
    )
    messages: List[Dict[str, object]] = [
        {"role": "system", "content": [{"type": "text", "text": system_text}]},
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Reference snippets:\n" + context,
                }
            ],
        },
    ]
    for turn in trimmed_history:
        messages.append({"role": turn.get("role", "user"), "content": [{"type": "text", "text": turn.get("content", "")}]})
    user_text = f"Question: {query.strip()}"
    messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
    return messages


@dataclass
class ChatResult:
    answer: str
    citations: List[Snippet]
    used_fallback: bool
    is_positioning: bool = False
    summary: Optional[str] = None


def _load_chat_stub(path: Path = DEFAULT_CHAT_STUB_PATH) -> List[Dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _stub_answer(query: str, snippets: List[Snippet]) -> ChatResult:
    stub_path = Path(os.getenv("FIELDOS_CHAT_STUB_PATH", DEFAULT_CHAT_STUB_PATH))
    entries = _load_chat_stub(stub_path)
    query_lower = query.strip().lower()
    for entry in entries:
        if entry.get("query", "").lower() == query_lower:
            citations = [
                Snippet(
                    source=item.get("source", ""),
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    url=item.get("url", "#"),
                    score=1.0,
                    tags=item.get("tags", []),
                    value_props=item.get("value_props", []),
                    discount=item.get("discount"),
                    category=item.get("category", "general"),
                    metadata=item.get("metadata", {}),
                )
                for item in entry.get("citations", [])
            ]
            citations = _hydrate_stub_citations(citations, snippets)
            return ChatResult(
                answer=entry.get("answer", ""),
                citations=citations,
                used_fallback=True,
                is_positioning=bool(entry.get("mode") == "positioning"),
                summary=entry.get("summary"),
            )
    # Deterministic generic fallback using snippets
    if snippets:
        lines = []
        citations = []
        for snippet in snippets[:3]:
            label = _format_citation(snippet)
            lines.append(f"- {snippet.content} [{label}]")
            citations.append(snippet)
        answer = "Here is what I found:\n" + "\n".join(lines)
        return ChatResult(answer=answer, citations=citations, used_fallback=True, summary=answer)
    return ChatResult(
        answer="I don't have any reference snippets for that yet.",
        citations=[],
        used_fallback=True,
        summary=None,
    )


def _hydrate_stub_citations(citations: List[Snippet], snippets: List[Snippet]) -> List[Snippet]:
    if not snippets:
        return citations
    mapped = []
    for citation in citations:
        match = next((s for s in snippets if s.title == citation.title or s.url == citation.url), None)
        mapped.append(match or citation)
    return mapped


def _is_positioning_query(query: str, snippets: List[Snippet]) -> bool:
    lowered = query.lower()
    if any(keyword in lowered for keyword in POSITIONING_KEYWORDS):
        return True
    return any(
        snippet.category in {"value_prop", "pricing", "promo"} or any(tag in ("promo", "upsell", "pricing") for tag in snippet.tags)
        for snippet in snippets
    )


def _collect_positioning_snippets(snippets: List[Snippet]) -> List[Snippet]:
    positioning = [s for s in snippets if s.category in {"value_prop", "pricing", "promo"}]
    if not positioning:
        positioning = [s for s in snippets if any(tag in ("promo", "upsell") for tag in s.tags)]
    return positioning or snippets


def _compose_positioning_answer(query: str, snippets: List[Snippet]) -> ChatResult:
    relevant = _collect_positioning_snippets(snippets)
    services = [
        str(s.metadata.get("service"))
        for s in relevant
        if isinstance(s.metadata, dict) and s.metadata.get("service")
    ]
    service_label = ", ".join(dict.fromkeys([srv for srv in services if srv]))
    if not service_label:
        service_label = "this service"

    value_props: List[str] = []
    discounts: List[str] = []
    pricing_notes: List[str] = []

    for snippet in relevant:
        for prop in snippet.value_props:
            if prop not in value_props:
                value_props.append(prop)
        if snippet.discount and snippet.discount not in discounts:
            discounts.append(snippet.discount)
        upsells = []
        metadata = snippet.metadata or {}
        if isinstance(metadata, dict):
            upsells = metadata.get("upsells") or []
            base_price = metadata.get("base_price")
            unit = metadata.get("pricing_unit", "USD")
            if base_price:
                pricing_notes.append(f"Base price {base_price} {unit}")
        if upsells:
            upsell_descriptions = []
            for item in upsells:
                if isinstance(item, dict):
                    upsell_descriptions.append(f"{item.get('name')} (${item.get('price')})")
                elif isinstance(item, str):
                    upsell_descriptions.append(item)
            if upsell_descriptions:
                pricing_notes.append("Upsells: " + "; ".join(upsell_descriptions))

    if not value_props:
        value_props.append("Highlight ROI and maintenance savings.")

    summary_lines = []
    summary_lines.append("Value props: " + "; ".join(value_props[:3]))
    if discounts:
        summary_lines.append("Promo: " + "; ".join(discounts))
    else:
        summary_lines.append("Promo: No discount info captured yet.")
    if pricing_notes:
        summary_lines.append("Pricing: " + "; ".join(pricing_notes[:2]))

    summary = "\n".join(summary_lines)
    answer_lines = [
        f"Here’s how to position {service_label}:",
        "",
        f"• Value props: {', '.join(value_props[:3])}",
        f"• Pricing & promo: {', '.join(discounts) if discounts else 'No discount info captured yet.'}",
    ]
    if pricing_notes:
        answer_lines.append(f"• Upsell angle: {pricing_notes[0]}")
    answer_lines.append(
        "• Suggested angle: Lead with the promo window, reinforce the value props, and recommend the most relevant upsell."
    )
    return ChatResult(
        answer="\n".join(answer_lines),
        citations=relevant[:3],
        used_fallback=False,
        is_positioning=True,
        summary=summary,
    )


def _call_llm(query: str, snippets: List[Snippet], history: List[Dict[str, str]]) -> Optional[str]:
    client = _load_openai_client()
    if client is None:
        return None
    messages = build_prompt(query, snippets, history)
    try:
        response = client.responses.create(model=DEFAULT_CHAT_MODEL, input=messages)
        return response.output_text.strip()
    except Exception as exc:  # pragma: no cover - network guard
        LOGGER.info("Chat completion failed; switching to fallback: %s", exc)
        return None


def generate_answer(
    query: str,
    history: Optional[List[Dict[str, str]]] = None,
    snippets: Optional[List[Snippet]] = None,
) -> ChatResult:
    history = history or []
    snippets = snippets or []
    positioning_mode = _is_positioning_query(query, snippets)
    if _fallback_mode() in {"stub", "keyword"}:
        result = _stub_answer(query, snippets)
        if positioning_mode:
            result.is_positioning = True
        return result

    if positioning_mode:
        return _compose_positioning_answer(query, snippets)

    response_text = _call_llm(query, snippets, history)
    if response_text is None:
        fallback = _stub_answer(query, snippets)
        if positioning_mode:
            fallback.is_positioning = True
        return fallback

    citation_text = " ".join(f"[{_format_citation(snippet)}]" for snippet in snippets[:3])
    answer = response_text
    if citation_text and citation_text not in response_text:
        answer = f"{response_text}\n\nSources: {citation_text}"
    return ChatResult(answer=answer, citations=snippets[:3], used_fallback=False)


def retrieve_snippets(query: str, top_k: int = 4) -> List[Snippet]:
    # Ensure index loaded; returning stub results if necessary
    try:
        load_index(DEFAULT_INDEX_PATH, DEFAULT_STUB_PATH)
    except FileNotFoundError:
        load_index(DEFAULT_STUB_PATH, DEFAULT_STUB_PATH)
    return search_index(query, top_k=top_k)
