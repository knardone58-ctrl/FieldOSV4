from __future__ import annotations

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

import chatbot
import reference_search

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures"


def _click_button(app: AppTest, label: str) -> AppTest:
    for button in app.button:
        if button.label == label:
            return button.click().run()
    raise AssertionError(f"Button {label!r} not found. Available: {[b.label for b in app.button]}")


def test_copilot_stub_response(monkeypatch):
    monkeypatch.setenv("FIELDOS_QA_MODE", "true")
    monkeypatch.setenv("FIELDOS_CHAT_FALLBACK_MODE", "stub")
    monkeypatch.setenv("FIELDOS_CHAT_INDEX_PATH", str(FIXTURES / "reference_index_stub.jsonl"))
    monkeypatch.setenv("FIELDOS_CHAT_INDEX_STUB_PATH", str(FIXTURES / "reference_index_stub.jsonl"))
    monkeypatch.setenv("FIELDOS_CHAT_STUB_PATH", str(FIXTURES / "chat_stub.json"))

    app = AppTest.from_file("app.py")
    app.run()

    snippets = reference_search.search("mulch promo", top_k=4)
    result = chatbot.generate_answer("What is our mulch promo?", history=[], snippets=snippets)
    positioning_result = chatbot.generate_answer("How should I position mulch to Samir?", history=[], snippets=snippets)
    if positioning_result.citations:
        first_citation = positioning_result.citations[0]
        metadata = dict(first_citation.metadata)
        metadata["service"] = "Holiday Lighting"
        positioning_result.citations[0] = chatbot.Snippet(
            source=first_citation.source,
            title=first_citation.title,
            content=first_citation.content,
            url=first_citation.url,
            score=first_citation.score,
            tags=first_citation.tags,
            value_props=first_citation.value_props,
            discount=first_citation.discount,
            category=first_citation.category,
            metadata=metadata,
        )
    wiki_snippet = next((snip for snip in snippets if snip.title == "Mulch Promo Guidelines"), None)
    if wiki_snippet and wiki_snippet not in positioning_result.citations:
        mutated_metadata = dict(wiki_snippet.metadata)
        mutated_metadata["service"] = "Holiday Lighting"
        positioning_result.citations.append(
            chatbot.Snippet(
                source=wiki_snippet.source,
                title=wiki_snippet.title,
                content=wiki_snippet.content,
                url=wiki_snippet.url,
                score=wiki_snippet.score,
                tags=wiki_snippet.tags,
                value_props=wiki_snippet.value_props,
                discount=wiki_snippet.discount,
                category=wiki_snippet.category,
                metadata=mutated_metadata,
            )
        )
    mutated_citations = []
    for citation in positioning_result.citations:
        meta = dict(citation.metadata)
        meta["service"] = "Holiday Lighting"
        mutated_citations.append(
            chatbot.Snippet(
                source=citation.source,
                title=citation.title,
                content=citation.content,
                url=citation.url,
                score=citation.score,
                tags=citation.tags,
                value_props=citation.value_props,
                discount=citation.discount,
                category=citation.category,
                metadata=meta,
            )
        )
    positioning_result = chatbot.ChatResult(
        answer=positioning_result.answer,
        citations=mutated_citations,
        used_fallback=positioning_result.used_fallback,
        is_positioning=positioning_result.is_positioning,
        summary=positioning_result.summary,
    )
    app.session_state["chat_history"] = [
        {"role": "user", "content": "What is our mulch promo?"},
        {
            "role": "assistant",
            "content": result.answer,
            "citations": [
                {
                    "source": snip.source,
                    "title": snip.title,
                    "content": snip.content,
                    "url": snip.url,
                    "score": snip.score,
                    "tags": snip.tags,
                    "value_props": snip.value_props,
                    "discount": None,
                    "metadata": snip.metadata,
                }
                for snip in result.citations
            ],
            "fallback": result.used_fallback,
            "positioning": result.is_positioning,
            "summary": result.summary,
        },
        {"role": "user", "content": "How should I position mulch to Samir?"},
        {
            "role": "assistant",
            "content": positioning_result.answer,
            "citations": [
                {
                    "source": snip.source,
                    "title": snip.title,
                    "content": snip.content,
                    "url": snip.url,
                    "score": snip.score,
                    "tags": snip.tags,
                    "value_props": snip.value_props,
                    "discount": None,
                    "metadata": snip.metadata,
                }
                for snip in positioning_result.citations
            ],
            "fallback": positioning_result.used_fallback,
            "positioning": positioning_result.is_positioning,
            "summary": positioning_result.summary,
        },
    ]
    app.session_state["pipeline_snapshot"] = {"last_updated": "2023-01-01T00:00:00Z"}
    app.run()

    rendered = [md.value for md in app.markdown if "Mulch promo" in md.value or "position" in md.value.lower()]
    assert rendered, "Expected copilot response to render in markdown"
    assert any("Positioning Brief" in md.value for md in app.markdown), "Positioning badge missing"
    assert any(button.label == "Insert positioning summary" for button in app.button), "Insert positioning button missing"

    app = _click_button(app, "Insert positioning summary")
    assert app.session_state["draft_note"], "Draft note did not update after inserting positioning summary"
    assert "Value props" in app.session_state["draft_note"]

    assistant_entries = [entry for entry in app.session_state["chat_history"] if entry.get("role") == "assistant"]
    latest = assistant_entries[-1]
    citations = latest.get("citations") or []
    assert any(cite.get("title") == "Mulch Promo Guidelines" for cite in citations)
    assert latest.get("positioning") is True
    assert positioning_result.summary and "Value props" in positioning_result.summary
    positioning_warnings = [cap.value for cap in app.caption if "⚠️" in cap.value]
    assert positioning_warnings, "Expected stale or mismatch warning in positioning brief"
    assert any("Pricing snapshot last refreshed" in warning for warning in positioning_warnings), "Expected pricing freshness warning"
    assert any("Heads-up" in warning for warning in positioning_warnings), "Expected service mismatch warning"
