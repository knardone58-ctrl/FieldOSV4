from __future__ import annotations

import os
from pathlib import Path

import chatbot
import reference_search

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures"


def setup_module(module):
    os.environ["FIELDOS_CHAT_INDEX_PATH"] = str(FIXTURES / "reference_index_stub.jsonl")
    os.environ["FIELDOS_CHAT_INDEX_STUB_PATH"] = str(FIXTURES / "reference_index_stub.jsonl")
    os.environ["FIELDOS_CHAT_STUB_PATH"] = str(FIXTURES / "chat_stub.json")


def test_build_prompt_contains_snippet_context():
    snippets = reference_search.load_index(FIXTURES / "reference_index_stub.jsonl").records[0:1]
    prompt = chatbot.build_prompt("What is our mulch promo?", [rec.snippet for rec in snippets], [])
    content_blocks = [entry["content"][0]["text"] for entry in prompt if entry["role"] == "system"]
    assert any("Reference snippets" in block for block in content_blocks)


def test_generate_answer_stub_mode_returns_stub_response():
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = "stub"
    snippets = reference_search.load_index(FIXTURES / "reference_index_stub.jsonl").records
    snippet_objs = [rec.snippet for rec in snippets]
    result = chatbot.generate_answer("What is our mulch promo?", history=[], snippets=snippet_objs)
    assert result.used_fallback is True
    assert "15% off" in result.answer
    assert result.citations, "Expected citations from stub"
    assert result.summary
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = ""


def test_generate_answer_generic_fallback_when_no_match():
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = "stub"
    snippets = reference_search.load_index(FIXTURES / "reference_index_stub.jsonl").records
    snippet_objs = [rec.snippet for rec in snippets]
    result = chatbot.generate_answer("Unknown query", history=[{"role": "user", "content": "First"}], snippets=snippet_objs)
    assert result.used_fallback is True
    assert "Here is what I found" in result.answer
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = ""


def test_positioning_stub_sets_flag():
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = "stub"
    snippets = reference_search.load_index(FIXTURES / "reference_index_stub.jsonl").records
    snippet_objs = [rec.snippet for rec in snippets]
    result = chatbot.generate_answer("How should I position mulch to Samir?", history=[], snippets=snippet_objs)
    assert result.is_positioning is True
    assert result.summary and "Value props" in result.summary
    assert "Promo: 15% promo" in result.summary
    assert any(c.source == "playbook" for c in result.citations)
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = ""
