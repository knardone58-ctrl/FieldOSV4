from __future__ import annotations

import os
from pathlib import Path

import reference_search

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures"


def setup_module(module):
    os.environ["FIELDOS_CHAT_INDEX_PATH"] = str(FIXTURES / "reference_index_stub.jsonl")
    os.environ["FIELDOS_CHAT_INDEX_STUB_PATH"] = str(FIXTURES / "reference_index_stub.jsonl")
    # ensure fallback mode reset
    if "FIELDOS_CHAT_FALLBACK_MODE" in os.environ:
        del os.environ["FIELDOS_CHAT_FALLBACK_MODE"]


def test_load_index_from_stub():
    index = reference_search.load_index(FIXTURES / "reference_index_stub.jsonl")
    assert index.records, "Index should load stub entries"
    titles = {record.snippet.title for record in index.records}
    assert "Mulch Promo Guidelines" in titles
    mulch_record = next(record for record in index.records if record.snippet.title == "Mulch Upsell Script")
    assert "promo" in mulch_record.snippet.tags
    assert mulch_record.snippet.discount == "15% promo"
    assert mulch_record.snippet.value_props, "Expected value props populated"
    crm_record = next(record for record in index.records if record.snippet.source == "crm")
    assert crm_record.snippet.category == "general"
    assert crm_record.snippet.metadata.get("record_type") == "account"


def test_search_ranks_mulch_snippet_first():
    reference_search.load_index(FIXTURES / "reference_index_stub.jsonl")
    results = reference_search.search("mulch promo discount", top_k=3)
    assert results, "Expected snippets for mulch query"
    top_titles = {snippet.title for snippet in results}
    assert "Mulch Promo Guidelines" in top_titles
    assert results[0].title in {"Mulch Promo Guidelines", "Mulch Upsell Script", "Seasonal Cleanup + Mulch Pricing"}
    assert any("pricing" in snippet.tags for snippet in reference_search.search("pricing mulch", top_k=3))


def test_keyword_fallback_mode_stub():
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = "stub"
    reference_search.load_index(FIXTURES / "reference_index_stub.jsonl")
    results = reference_search.search("Acme HOA status", top_k=2)
    assert results, "Expected results in stub fallback"
    assert any(r.title == "Acme HOA Summary" for r in results)
    os.environ["FIELDOS_CHAT_FALLBACK_MODE"] = ""
