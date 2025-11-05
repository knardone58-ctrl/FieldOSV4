# FieldOS V4.2 — Daily Command Center (AI-enabled)
# -------------------------------------------------
# - Audio capture via st.audio_input (≤ 30s recommended)
# - Transcription (QA-safe) + optional GPT polish
# - Async CRM queue + offline caching with schema migration
# - Telemetry: latency averages, fail count, queue/cached counts

from __future__ import annotations

import copy
import csv
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from fieldos_env import init_env

init_env()

from fieldos_config import (
    AUDIO_MAX_SECONDS,
    AUDIO_TTL_HOURS,
    POLISH_CTA,
    POLISH_FAIL_TOAST,
    QA_MODE,
    TRANSCRIBE_ENGINE,
    STREAMING_ENABLED,
    VOSK_MODEL_PATH,
    STREAM_CHUNK_MS,
    FINAL_WORKER_ENABLED,
    FINAL_WORKER_MOCK,
    FINAL_WHISPER_MODEL,
    FINAL_WHISPER_DEVICE,
    FINAL_WHISPER_COMPUTE_TYPE,
    FINAL_WHISPER_BEAM_SIZE,
)
from fieldos_version import FIELDOS_VERSION

from crm_sync import enqueue_crm_push, flush_offline_cache, load_snapshot, save_snapshot, start_crm_worker
from ai_parser import polish_note_with_gpt, transcribe_audio
from streaming_asr import VoskStreamer, get_pcm_stream, _VOSK_AVAILABLE
from audio_cache import ensure_cache_dir, calculate_audio_duration
from final_transcriber import (
    WorkerConfig as FinalWorkerConfig,
    WorkerHandle as FinalWorkerHandle,
    collect_stats as collect_final_stats,
    poll_results as poll_final_results,
    start_worker as start_final_worker,
    submit_job as submit_final_job,
    shutdown_worker as shutdown_final_worker,
)
import chatbot

FOCUS_CONTACT = {
    "name": "Acme HOA",
    "contact": "Samir Voss",
    "phone": "(216) 555-0142",
    "email": "samir.voss@acmehoa.demo",
    "service": "Seasonal Cleanup + Mulch",
    "value": 2400,
    "overdue": True,
    "last_touch": "2025-10-28",
    "address": "3150 Detroit Ave, Cleveland, OH",
    "note_hint": "Mention mulch promo; booked cleanup last spring.",
    "customer_id": "DEMO-ACME-HOA",
    "customer_type": "HOA",
    "assigned_rep": "Marcus Tillman",
    "region": "Midwest",
    "lead_source": "Demo",
}

CONTACT_INTEL_PATH = Path("data/contact_intel.json")
PLAYBOOK_PATH = Path("data/playbooks.json")
PRICING_PATH = Path("data/pricing.json")
COMPANY_WIKI_PATH = Path("data/company_wiki.md")
CRM_SAMPLE_PATH = Path("data/crm_sample.csv")
SALES_PLAYBOOK_DOC_PATH = Path("data/sales_playbook.md")
DEMO_RUNBOOK_PATH = Path("docs/final_worker_runbook.md")

LOGGER = logging.getLogger(__name__)
PIPELINE_PATH = Path("data/pipeline_snapshot.json")
PRIVACY_MODE = os.getenv("FIELDOS_PRIVACY_MODE", "").lower() == "true"


def _load_contact_intel() -> Dict[str, Any]:
    try:
        raw = CONTACT_INTEL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _load_playbooks() -> Dict[str, Any]:
    try:
        raw = PLAYBOOK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _load_pricing() -> Dict[str, Any]:
    try:
        raw = PRICING_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _init_copilot_state() -> None:
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("chat_loading", False)
    st.session_state.setdefault("chat_error", "")
    st.session_state.setdefault("chat_context_cache", [])
    st.session_state.setdefault("chat_input_text", "")
    st.session_state.setdefault("chat_requests", 0)
    st.session_state.setdefault("chat_fallback_count", 0)
    st.session_state.setdefault("chat_last_error", None)
    st.session_state.setdefault("chat_last_hash", "")
    st.session_state.setdefault("chat_last_query", "")
    st.session_state.setdefault("chat_positioning_count", 0)


def _serialize_snippet(snippet: chatbot.Snippet) -> Dict[str, Any]:
    return {
        "source": snippet.source,
        "title": snippet.title,
        "content": snippet.content,
        "url": snippet.url,
        "score": snippet.score,
        "tags": snippet.tags,
        "value_props": snippet.value_props,
        "discount": snippet.discount,
        "category": snippet.category,
        "metadata": snippet.metadata,
    }


def _format_citation_label(source: str, title: str) -> str:
    prefix = {"wiki": "Wiki", "crm": "CRM", "playbook": "Playbook"}.get(source, source.title())
    return f"{prefix}: {title}"


def _clear_copilot_history() -> None:
    st.session_state["chat_history"] = []
    st.session_state["chat_context_cache"] = []
    st.session_state["chat_input_text"] = ""
    st.session_state["chat_error"] = ""


def _record_chat_telemetry(query: str, fallback: bool, error: Optional[str], positioning: bool) -> None:
    st.session_state["chat_requests"] = int(st.session_state.get("chat_requests", 0) or 0) + 1
    if fallback:
        st.session_state["chat_fallback_count"] = int(st.session_state.get("chat_fallback_count", 0) or 0) + 1
    if positioning:
        st.session_state["chat_positioning_count"] = int(st.session_state.get("chat_positioning_count", 0) or 0) + 1
    if error:
        st.session_state["chat_last_error"] = error
    else:
        st.session_state["chat_last_error"] = None
    if PRIVACY_MODE:
        st.session_state["chat_last_hash"] = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
        st.session_state["chat_last_query"] = ""
    else:
        st.session_state["chat_last_query"] = query
        st.session_state["chat_last_hash"] = ""


def _positioning_snapshot_warning() -> Optional[str]:
    pipeline_snapshot = st.session_state.get("pipeline_snapshot") or {}
    last_updated = pipeline_snapshot.get("last_updated")
    parsed = _parse_iso_timestamp(last_updated) if last_updated else None
    if parsed is None:
        return None
    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    if age_hours > 24:
        formatted = _format_timestamp(last_updated)
        return f"Pricing snapshot last refreshed {formatted}; verify promos before pitching."
    return None


def _render_positioning_cues(citations: List[Dict[str, Any]], summary: Optional[str]) -> None:
    warnings: List[str] = []
    contact_service = FOCUS_CONTACT["service"]
    cited_services: List[str] = []
    for citation in citations or []:
        metadata = citation.get("metadata") or {}
        service = metadata.get("service")
        if isinstance(service, str) and service:
            cited_services.append(service)
    unique_services = list(dict.fromkeys(cited_services))
    if unique_services and contact_service not in unique_services:
        joined = ", ".join(unique_services)
        warnings.append(f"Heads-up: citations reference {joined}; align with current service {contact_service}.")
    snapshot_warning = _positioning_snapshot_warning()
    if snapshot_warning:
        warnings.append(snapshot_warning)
    has_discount = any((citation.get("discount") for citation in citations or []))
    if not has_discount:
        warnings.append("No discount info in cited snippets—confirm promo before sharing.")
    if not warnings:
        return
    for warning in warnings:
        st.caption(f"⚠️ {warning}")


def _handle_copilot_query(query: str) -> None:
    stripped = query.strip()
    if not stripped:
        st.session_state["chat_error"] = "Enter a question for the copilot."
        return
    st.session_state["chat_error"] = ""
    st.session_state["chat_loading"] = True
    history: List[Dict[str, Any]] = list(st.session_state.get("chat_history", []))
    try:
        snippets = chatbot.retrieve_snippets(stripped)
    except Exception as exc:  # pragma: no cover - defensive
        st.session_state["chat_loading"] = False
        st.session_state["chat_error"] = f"Unable to search references: {exc}"
        _record_chat_telemetry(stripped, fallback=True, error=str(exc), positioning=False)
        return

    snippet_objs = snippets or []
    positioning = False
    result_summary: Optional[str] = None
    try:
        result = chatbot.generate_answer(stripped, history=history, snippets=snippet_objs)
    except Exception as exc:  # pragma: no cover - defensive
        st.session_state["chat_loading"] = False
        st.session_state["chat_error"] = f"Copilot error: {exc}"
        _record_chat_telemetry(stripped, fallback=True, error=str(exc), positioning=False)
        return

    positioning = result.is_positioning
    result_summary = result.summary

    history.append({"role": "user", "content": stripped})
    history.append(
        {
            "role": "assistant",
            "content": result.answer,
            "citations": [_serialize_snippet(snippet) for snippet in result.citations],
            "fallback": result.used_fallback,
            "positioning": positioning,
            "summary": result_summary,
        }
    )
    st.session_state["chat_history"] = history[-12:]
    st.session_state["chat_context_cache"] = [_serialize_snippet(snippet) for snippet in result.citations]
    st.session_state["chat_input_text"] = ""
    st.session_state["chat_loading"] = False
    _record_chat_telemetry(stripped, fallback=result.used_fallback, error=None, positioning=positioning)


def _render_citation_list(citations: List[Dict[str, Any]]) -> None:
    if not citations:
        return
    lines = []
    for citation in citations:
        label = _format_citation_label(citation.get("source", ""), citation.get("title", ""))
        url = citation.get("url") or "#"
        lines.append(f"- [{label}]({url})")
    if lines:
        st.caption("Sources:\n" + "\n".join(lines))


def _render_reference_copilot() -> None:
    with st.container(border=True):
        st.markdown("**Reference Copilot**")
        history: List[Dict[str, Any]] = st.session_state.get("chat_history", [])
        if history:
            for idx, message in enumerate(history):
                role = message.get("role", "assistant")
                with st.chat_message("assistant" if role != "user" else "user"):
                    if role != "user" and message.get("positioning"):
                        st.markdown("🟢 **Positioning Brief**")
                    st.markdown(message.get("content", ""))
                    if role != "user" and message.get("positioning"):
                        summary = message.get("summary") or ""
                        if summary:
                            st.caption(summary.replace("\n", "  •  "))
                        else:
                            st.caption("Limited positioning data available.")
                        citations = message.get("citations") or []
                        _render_positioning_cues(citations, summary)
                        insert_label = f"Insert positioning summary"
                        if st.button(insert_label, key=f"insert_positioning_{idx}", use_container_width=True):
                            if summary:
                                _append_to_draft(summary)
                                st.toast("Positioning summary added to draft note.")
                            else:
                                st.toast("No positioning summary available to insert.", icon="⚠️")
                    if role != "user":
                        citations = message.get("citations") or []
                        _render_citation_list(citations)
        else:
            st.caption("Ask about promos, CRM status, or playbook tips — answers cite company references.")

        if st.session_state.get("chat_loading", False):
            st.info("Generating response…")

        st.session_state.setdefault("chat_input_text", "")

        def _submit_copilot() -> None:
            if st.session_state.get("chat_loading", False):
                return
            query_value = st.session_state.get("chat_input_text", "")
            _handle_copilot_query(query_value)

        st.text_input(
            "Ask the copilot",
            key="chat_input_text",
            placeholder="e.g., What is our mulch promo?",
            on_change=_submit_copilot,
        )
        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("Clear conversation", use_container_width=True):
                _clear_copilot_history()
        with col_b:
            if st.session_state.get("chat_error"):
                st.warning(st.session_state["chat_error"])
            else:
                st.empty()


def _save_and_queue_crm_payload() -> None:
    last_result = st.session_state.get("final_worker_last_result") or {}
    final_confidence = last_result.get("confidence") if last_result else None
    final_latency = last_result.get("latency_ms") if last_result else None
    final_completed_at = last_result.get("completed_at") if last_result else None
    final_transcript = last_result.get("transcript") if last_result else ""
    stream_partial = st.session_state.get("stream_final_text") or ""

    model_suffix = ""
    if final_transcript:
        model_suffix = f" | final_worker={FINAL_WHISPER_MODEL}"

    payload = {
        "contact_name": FOCUS_CONTACT["contact"],
        "contact_phone": FOCUS_CONTACT.get("phone", ""),
        "contact_email": FOCUS_CONTACT.get("email", ""),
        "account": FOCUS_CONTACT["name"],
        "service": FOCUS_CONTACT["service"],
        "customer_id": FOCUS_CONTACT.get("customer_id"),
        "customer_type": FOCUS_CONTACT.get("customer_type", ""),
        "account_address": FOCUS_CONTACT.get("address", ""),
        "assigned_rep": FOCUS_CONTACT.get("assigned_rep", ""),
        "region": FOCUS_CONTACT.get("region", ""),
        "lead_source": FOCUS_CONTACT.get("lead_source", "Demo"),
        "note": st.session_state["draft_note"].strip(),
        "transcription_raw": st.session_state.get("raw_transcript", ""),
        "transcription_stream_partial": stream_partial,
        "transcription_final": final_transcript,
        "transcription_final_confidence": final_confidence,
        "transcription_final_latency_ms": final_latency,
        "transcription_final_completed_at": final_completed_at,
        "note_polished": st.session_state["draft_note"].strip(),
        "transcription_confidence": float(st.session_state.get("last_transcription_confidence", 0.0)),
        "ai_model_version": f"{TRANSCRIBE_ENGINE} + gpt-5-turbo | FieldOS {FIELDOS_VERSION}{model_suffix}",
        "processing_time": float(
            st.session_state.get("last_transcription_duration", 0.0)
            + st.session_state.get("last_polish_duration", 0.0)
        ),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if st.session_state.get("last_quote"):
        payload["quote_summary"] = st.session_state["last_quote"]

    st.session_state["last_crm_payload"] = payload.copy()
    st.session_state["last_crm_status"] = {
        "state": "queued",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "response_code": None,
        "error": None,
    }
    enqueue_crm_push(payload)
    save_snapshot(
        {
            "ai_fail_count": st.session_state["ai_fail_count"],
            "ai_latency_totals": st.session_state["ai_latency_totals"],
            "ai_latency_counts": st.session_state["ai_latency_counts"],
            "final_transcribe_stats": _final_snapshot_block(),
        }
    )
    st.toast("Saved locally & queued CRM sync.")
    st.session_state["progress_done"] = min(3, st.session_state["progress_done"] + 1)
    st.session_state["last_saved_clip_fingerprint"] = st.session_state.get("processed_clip_fingerprint")
    st.session_state["processed_clip_fingerprint"] = None
    st.session_state["dedupe_notice_shown"] = False
    st.session_state["raw_transcript"] = ""
    st.session_state["raw_transcript_display"] = "Awaiting capture"


def _generate_quote(service_name: str) -> Optional[Dict[str, Any]]:
    pricing_data = _load_pricing()
    pricing_entry = pricing_data.get(service_name) or pricing_data.get("default")
    if not isinstance(pricing_entry, dict):
        return None

    base_price = pricing_entry.get("base_price")
    if base_price is None:
        return None
    currency = pricing_entry.get("unit", "USD")
    upsells = pricing_entry.get("upsells") or []
    upsell_total = 0
    upsell_lines = []
    for upsell in upsells:
        price = upsell.get("price")
        name = upsell.get("name", "Add-on")
        if price is None:
            continue
        upsell_total += price
        upsell_lines.append({"name": name, "price": price})

    total = base_price + upsell_total
    generated_at = datetime.now(timezone.utc).isoformat()
    quote = {
        "service": service_name,
        "base_price": base_price,
        "upsells": upsell_lines,
        "currency": currency,
        "total": total,
        "generated_at": generated_at,
    }
    return quote


def _load_pipeline_snapshot() -> Dict[str, Any]:
    try:
        raw = PIPELINE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _load_markdown_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _load_crm_sample() -> List[Dict[str, Any]]:
    try:
        with CRM_SAMPLE_PATH.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return rows

FOLLOWUPS = [
    {"name": "Lakeview Dental", "due": "today", "value": 1800},
    {"name": "Maplewood HOA", "due": "tomorrow", "value": 6200},
    {"name": "Cedar Logistics", "due": "2 days", "value": 14500},
]


def init_state() -> None:
    st.session_state.setdefault("draft_note", "")
    st.session_state.setdefault("draft_note_input", st.session_state["draft_note"])
    st.session_state.setdefault("raw_transcript", "")
    st.session_state.setdefault("raw_transcript_display", "Awaiting capture")
    st.session_state.setdefault("processed_clip_fingerprint", None)
    st.session_state.setdefault("dedupe_notice_shown", False)
    st.session_state.setdefault("final_worker_toast_shown", False)
    st.session_state.setdefault("last_crm_payload", None)
    st.session_state.setdefault("contact_intel_last_refresh", None)
    st.session_state.setdefault("applied_playbook_snippets", [])
    st.session_state.setdefault("applied_playbook_titles", [])
    st.session_state.setdefault("playbook_last_contact", FOCUS_CONTACT["name"])
    st.session_state.setdefault("last_quote", None)
    st.session_state.setdefault("quote_inserted", False)
    st.session_state.setdefault("pipeline_snapshot", _load_pipeline_snapshot())
    st.session_state.setdefault("followups", FOLLOWUPS.copy())
    st.session_state.setdefault("snoozed", set())
    st.session_state.setdefault("offline", False)
    st.session_state.setdefault("gps", "41.4819,-81.7982")
    st.session_state.setdefault("crm_queue", [])
    st.session_state.setdefault("crm_sync_log", [])
    st.session_state.setdefault("offline_cache", [])
    st.session_state.setdefault("suggestion", "")
    st.session_state.setdefault("progress_done", 0)
    st.session_state.setdefault("ai_latency_totals", {"transcribe": 0.0, "polish": 0.0})
    st.session_state.setdefault("ai_latency_counts", {"transcribe": 0, "polish": 0})
    st.session_state.setdefault("ai_fail_count", 0)
    st.session_state.setdefault("style_guidelines", "")
    st.session_state.setdefault("crm_worker_started", False)
    st.session_state.setdefault("last_transcription_confidence", 0.0)
    st.session_state.setdefault("last_transcription_duration", 0.0)
    st.session_state.setdefault("last_polish_duration", 0.0)
    _init_copilot_state()
    st.session_state.setdefault("final_worker_handle", None)
    st.session_state.setdefault("final_worker_jobs", {})
    st.session_state.setdefault("final_worker_results", [])
    st.session_state.setdefault("final_worker_logs", [])
    st.session_state.setdefault(
        "final_worker_stats",
        _final_stats_default(),
    )
    st.session_state.setdefault("final_worker_warning_logged", False)
    st.session_state.setdefault("final_worker_last_result", None)
    st.session_state.setdefault("crm_snapshot_warning_logged", False)
    st.session_state.setdefault("crm_snapshot_warning_pending", False)
    st.session_state.setdefault("crm_snapshot_warning_message", "")
    st.session_state.setdefault("crm_delivery_pending", False)
    st.session_state.setdefault("crm_delivery_status", None)
    st.session_state.setdefault("crm_delivery_message", "")
    st.session_state.setdefault("crm_delivery_payload_id", None)
    st.session_state.setdefault("crm_retry_available", False)
    st.session_state.setdefault("_crm_retry_in_progress", False)
    st.session_state.setdefault("_crm_last_delivery_id", None)
    st.session_state.setdefault("_draft_note_toasts", [])
    st.session_state.setdefault("crm_processed_count", 0)
    st.session_state.setdefault("crm_queue_debug", [])




# --- Streaming state init (V4.3) ---
def init_streaming_state():
    from fieldos_config import STREAMING_ENABLED as CFG_STREAMING_ENABLED
    if 'STREAMING_ENABLED' not in st.session_state:
        st.session_state['STREAMING_ENABLED'] = CFG_STREAMING_ENABLED
    defaults = {
        'stream_partial_text': '',
        'stream_final_text': '',
        'stream_latency_ms_first_partial': None,
        'stream_updates_count': 0,
        'stream_dropouts': 0,
        'stream_fallbacks': 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _seed_streaming_stub(
    *,
    message: Optional[str] = None,
    increment_fallback: bool = True,
    warn: bool = False,
    force: bool = False,
) -> bool:
    """Populate deterministic streaming metrics used in QA/fallback paths."""
    st.session_state.setdefault("stream_updates_count", 0)
    st.session_state.setdefault("stream_final_text", "")
    st.session_state.setdefault("stream_latency_ms_first_partial", None)
    st.session_state.setdefault("stream_dropouts", 0)
    st.session_state.setdefault("stream_fallbacks", 0)

    already_seeded = bool(st.session_state.get("_streaming_stub_seeded", False))
    updates = int(st.session_state.get("stream_updates_count") or 0)
    seeded_now = False

    if force or updates < 2:
        st.session_state["stream_updates_count"] = 2
        if not st.session_state.get("stream_final_text"):
            st.session_state["stream_final_text"] = "hello world"
        latency = st.session_state.get("stream_latency_ms_first_partial")
        if latency in (None, 0):
            st.session_state["stream_latency_ms_first_partial"] = 300
        st.session_state["stream_dropouts"] = st.session_state.get("stream_dropouts") or 0
        seeded_now = True
    if force:
        seeded_now = True

    if increment_fallback and seeded_now and not already_seeded:
        st.session_state["stream_fallbacks"] = int(st.session_state.get("stream_fallbacks", 0) or 0) + 1

    if (seeded_now or force) and not already_seeded:
        st.session_state["_streaming_stub_seeded"] = True
        if message:
            st.toast(message, icon="⚠️" if warn else "ℹ️")

    return seeded_now or already_seeded or force


def ensure_audio_cache_dir() -> Path:
    """Ensure cache exists and periodically purge stale clips."""
    cache_dir = Path("data/audio_cache")
    return ensure_cache_dir(cache_dir, AUDIO_TTL_HOURS, st.session_state)


# --- Draft note helpers ---
def _set_draft_note(value: str) -> None:
    """Update draft note value; widget sync happens on next render."""
    st.session_state["draft_note"] = value


def _append_to_draft(text: str) -> None:
    """Append text to draft note and mirror into the textarea backing state."""
    cleaned = text.strip()
    if not cleaned:
        return
    base = st.session_state.get("draft_note", "")
    updated = base + ("\n" if base else "") + cleaned
    st.session_state["draft_note"] = updated
    st.session_state["_draft_note_pending"] = updated


def _queue_draft_toast(message: str) -> None:
    queue = st.session_state.setdefault("_draft_note_toasts", [])
    queue.append(message)


def _format_crm_status_badge(status: Optional[Dict[str, Any]]) -> str:
    state = (status or {}).get("state")
    if not state:
        return "No CRM status yet."
    timestamp = (status or {}).get("timestamp") or ""
    response_code = (status or {}).get("response_code")
    error = (status or {}).get("error")
    tone = {
        "synced": ("background-color:#dcfce7;border:1px solid #16a34a;", "Synced"),
        "cached": ("background-color:#f1f5f9;border:1px solid #cbd5e1;", "Cached offline"),
        "retrying": ("background-color:#fef9c3;border:1px solid #facc15;", "Retrying"),
        "failed": ("background-color:#fee2e2;border:1px solid #ef4444;", "Failed"),
    }.get(state, ("background-color:#f1f5f9;border:1px solid #cbd5e1;", state.title()))
    style, label = tone
    ts_display = timestamp[11:16] if len(timestamp) >= 16 else timestamp
    parts = [label]
    if ts_display:
        parts.append(ts_display)
    if response_code:
        parts.append(f"HTTP {response_code}")
    if error and state != "synced":
        parts.append(error)
    body = " • ".join(parts)
    return f"<span style='padding:4px 10px;border-radius:999px;font-size:12px;{style}'>{body}</span>"


# --- Helpers ---
def _final_stats_default() -> Dict[str, Optional[Any]]:
    return {
        "queue_depth": 0,
        "last_success_ts": None,
        "last_error": None,
        "last_heartbeat": None,
        "last_confidence": None,
        "last_latency_ms": None,
        "model": FINAL_WHISPER_MODEL,
    }


def _parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "—"
    dt = _parse_iso_timestamp(value)
    if dt is None:
        return value
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _final_snapshot_block() -> Dict[str, Optional[Any]]:
    stats = st.session_state.get("final_worker_stats", _final_stats_default()).copy()
    last_result = st.session_state.get("final_worker_last_result") or {}

    block = {
        "queue_depth": int(stats.get("queue_depth") or 0),
        "last_success_ts": stats.get("last_success_ts"),
        "last_error": stats.get("last_error"),
        "last_heartbeat_ts": stats.get("last_heartbeat"),
        "last_confidence": last_result.get("confidence") if last_result else None,
        "last_latency_ms": last_result.get("latency_ms") if last_result else None,
        "model": stats.get("model") or FINAL_WHISPER_MODEL,
    }

    return block


# --- Final transcription worker scaffolding (V4.4) ---
def ensure_final_worker() -> Optional[FinalWorkerHandle]:
    config = FinalWorkerConfig(
        enabled=FINAL_WORKER_ENABLED,
        mock=FINAL_WORKER_MOCK,
        qa_mode=QA_MODE,
        model=FINAL_WHISPER_MODEL,
        device=FINAL_WHISPER_DEVICE,
        compute_type=FINAL_WHISPER_COMPUTE_TYPE,
        beam_size=FINAL_WHISPER_BEAM_SIZE,
    )

    current_handle: Optional[FinalWorkerHandle] = st.session_state.get("final_worker_handle")
    try:
        handle = start_final_worker(config, current_handle)
    except Exception as exc:  # pragma: no cover - defensive guard
        if not st.session_state.get("final_worker_warning_logged", False):
            st.warning(f"High-accuracy transcript worker unavailable: {exc}")
            st.toast("⚠️ High-accuracy transcription worker disabled.", icon="⚠️")
            st.session_state["final_worker_warning_logged"] = True
        shutdown_final_worker(current_handle)
        st.session_state["final_worker_stats"] = {
            **_final_stats_default(),
            "last_error": str(exc),
        }
        st.session_state["final_worker_last_result"] = None
        handle = None

    st.session_state["final_worker_handle"] = handle
    if handle is None:
        stats_obj = st.session_state.get("final_worker_stats")
        if not stats_obj or not stats_obj.get("last_error"):
            st.session_state["final_worker_stats"] = _final_stats_default()
        st.session_state["final_worker_last_result"] = None
    else:
        st.session_state.setdefault("final_worker_jobs", {})
        st.session_state.setdefault("final_worker_results", [])
        st.session_state.setdefault("final_worker_logs", [])
        st.session_state.setdefault("final_worker_stats", _final_stats_default())
    return handle


def poll_final_worker(handle: Optional[FinalWorkerHandle]) -> None:
    if handle is None:
        stats_obj = st.session_state.get("final_worker_stats")
        if not stats_obj or not stats_obj.get("last_error"):
            st.session_state["final_worker_stats"] = _final_stats_default()
        st.session_state["final_worker_last_result"] = None
        return

    jobs = st.session_state.setdefault("final_worker_jobs", {})
    results = st.session_state.setdefault("final_worker_results", [])

    def _on_result(message: Dict[str, Any]) -> None:
        results.append(message)
        job_id = message.get("job_id")
        job_entry = jobs.get(job_id)
        completed_iso = datetime.now(timezone.utc).isoformat()
        st.session_state["final_worker_last_result"] = {
            "job_id": job_id,
            "transcript": message.get("transcript", ""),
            "confidence": message.get("confidence"),
            "latency_ms": message.get("latency_ms"),
            "completed_at": completed_iso,
        }
        if not st.session_state.get("final_worker_toast_shown", False):
            st.toast(
                "High-accuracy transcript ready — final text and metrics are now available in the panel.",
                icon="✅",
            )
            st.session_state["final_worker_toast_shown"] = True
        if job_entry is not None:
            job_entry.update(
                {
                    "status": "completed" if not message.get("error") else "error",
                    "completed_at": completed_iso,
                    "transcript": message.get("transcript", ""),
                    "error": message.get("error"),
                }
            )

    def _on_error(error_text: str, payload: Dict[str, Any]) -> None:
        if not st.session_state.get("final_worker_warning_logged", False):
            st.warning(f"High-accuracy worker warning: {error_text}")
            st.session_state["final_worker_warning_logged"] = True

    poll_final_results(handle, on_result=_on_result, on_error=_on_error)

    pending_jobs = sum(1 for job in jobs.values() if job.get("status") == "queued")
    stats_raw = collect_final_stats(handle, pending_jobs)

    def _to_iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    stats = {
        "queue_depth": stats_raw.get("queue_depth", 0),
        "last_success_ts": _to_iso(stats_raw.get("last_success_ts")),
        "last_error": stats_raw.get("last_error"),
        "last_heartbeat": _to_iso(stats_raw.get("last_heartbeat")),
        "last_confidence": None,
        "last_latency_ms": None,
        "model": stats_raw.get("model") or FINAL_WHISPER_MODEL,
    }

    last_result = st.session_state.get("final_worker_last_result") or {}
    if last_result:
        stats["last_confidence"] = last_result.get("confidence")
        stats["last_latency_ms"] = last_result.get("latency_ms")

    st.session_state["final_worker_stats"] = stats
# --- V4.3 live streaming integration (Vosk) ---
def apply_streaming_live() -> None:
    """Run the Vosk streaming loop and persist telemetry safely."""

    if not _VOSK_AVAILABLE:
        if "stream_fallbacks" in st.session_state:
            st.session_state["stream_fallbacks"] += 1
        st.toast("⚠️ Vosk not installed — streaming disabled.")
        return

    if "stream_updates_count" not in st.session_state:
        st.session_state["stream_updates_count"] = 0
    if "stream_dropouts" not in st.session_state:
        st.session_state["stream_dropouts"] = 0
    if "stream_latency_ms_first_partial" not in st.session_state:
        st.session_state["stream_latency_ms_first_partial"] = None
    if "stream_final_text" not in st.session_state:
        st.session_state["stream_final_text"] = ""

    wav_path = "data/audio_cache/sample.wav"
    if not Path(wav_path).exists():
        st.toast("⚠️ Sample audio missing — reverting to deterministic stub.")
        if "stream_fallbacks" in st.session_state:
            st.session_state["stream_fallbacks"] += 1
        st.session_state["stream_updates_count"] = 2
        st.session_state["stream_final_text"] = "hello world"
        st.session_state["stream_latency_ms_first_partial"] = 300
        st.session_state["stream_dropouts"] = 0
        return

    vs = VoskStreamer(VOSK_MODEL_PATH)
    vs.start()
    partial_container = st.empty()
    st.caption("_Listening… partial transcript will appear below_")

    frames = get_pcm_stream(dev_mode=True, wav_path=wav_path, step_ms=STREAM_CHUNK_MS)
    for chunk in frames:
        vs.push_pcm(chunk)
        if vs.partial_text:
            partial_container.markdown(
                f"<span style='color:gray'><i>{vs.partial_text}</i></span>",
                unsafe_allow_html=True,
            )
        time.sleep(0.05)

    vs.stop()

    st.session_state["stream_latency_ms_first_partial"] = vs.first_partial_ms
    st.session_state["stream_updates_count"] = vs.updates
    st.session_state["stream_dropouts"] = vs.dropouts
    if vs.final_text:
        st.session_state["stream_final_text"] = vs.final_text

    st.toast(
        f"Streaming complete — updates: {vs.updates}, first partial: {vs.first_partial_ms} ms"
    )

    try:
        save_snapshot(
            {
                "streaming_stats": {
                    "first_partial_ms": st.session_state["stream_latency_ms_first_partial"],
                    "updates": st.session_state["stream_updates_count"],
                    "dropouts": st.session_state["stream_dropouts"],
                    "fallbacks": st.session_state["stream_fallbacks"],
                },
                "final_transcribe_stats": _final_snapshot_block(),
            }
        )
    except Exception:
        pass


def badge(text: str, tone: str = "neutral") -> str:
    tones = {
        "urgent": "background-color:#ffedd5;border:1px solid #f97316;",
        "good": "background-color:#dcfce7;border:1px solid #16a34a;",
        "neutral": "background-color:#f1f5f9;border:1px solid #cbd5e1;",
        "high": "background-color:#fee2e2;border:1px solid #ef4444;",
    }
    return f"<span style='padding:3px 8px;border-radius:999px;font-size:12px;{tones.get(tone,'')}'> {text} </span>"


st.set_page_config(page_title="FieldOS — Daily Command Center", layout="wide")
init_state()
init_streaming_state()
final_worker_handle = ensure_final_worker()
poll_final_worker(final_worker_handle)

if st.session_state.get("crm_delivery_pending"):
    delivery_status = st.session_state.get("crm_delivery_status") or {}
    delivery_message = st.session_state.get("crm_delivery_message") or "CRM delivery update."
    icon = "✅" if delivery_status.get("state") == "synced" else "⚠️"
    st.toast(delivery_message, icon=icon)
    st.session_state["crm_delivery_pending"] = False
    st.session_state["crm_delivery_message"] = ""

if st.session_state.get("crm_snapshot_warning_pending"):
    warning_message = st.session_state.get(
        "crm_snapshot_warning_message",
        "Unable to persist the latest CRM payload snapshot.",
    )
    st.toast(warning_message, icon="⚠️")
    st.session_state["crm_snapshot_warning_pending"] = False

if not st.session_state["crm_worker_started"]:
    start_crm_worker()
    st.session_state["crm_worker_started"] = True

with st.sidebar:
    st.markdown("### ⚙️ Controls")
    st.session_state["offline"] = st.toggle("Offline Mode", value=st.session_state["offline"])
    if os.getenv("FIELDOS_DISABLE_OFFLINE_FLUSH", "").lower() != "true":
        if not st.session_state["offline"] and st.button("Flush Offline Cache"):
            st.toast(f"Flushed {flush_offline_cache()} cached notes.")

snapshot = load_snapshot()
st.markdown("### 📡 Telemetry")
totals = st.session_state["ai_latency_totals"]
counts = st.session_state["ai_latency_counts"]
avg_transcribe = totals["transcribe"] / counts["transcribe"] if counts["transcribe"] else 0.0
avg_polish = totals["polish"] / counts["polish"] if counts["polish"] else 0.0
st.metric("AI avg transcribe (s)", f"{avg_transcribe:.1f}")
st.metric("AI avg polish (s)", f"{avg_polish:.1f}")
st.metric("AI failures", int(st.session_state["ai_fail_count"]))
st.metric("Queue length", len(st.session_state["crm_queue"]))
st.metric("Cached records", len(st.session_state["offline_cache"]))
st.markdown("### 🧠 Final Transcript Worker")
final_stats = st.session_state.get("final_worker_stats", _final_stats_default())
st.metric("Final transcript queue", final_stats.get("queue_depth", 0))
status_bits = []
if final_stats.get("last_success_ts"):
    status_bits.append(f"last success: {_format_timestamp(final_stats['last_success_ts'])}")
if final_stats.get("last_error"):
    status_bits.append(f"last error: {final_stats['last_error']}")
if final_stats.get("last_heartbeat"):
    status_bits.append(f"heartbeat: {_format_timestamp(final_stats['last_heartbeat'])}")
if not status_bits:
    status_bits.append("no worker activity yet")
st.caption(" | ".join(status_bits))
st.markdown("### 📈 Pipeline")
if st.button("Refresh pipeline snapshot", key="pipeline_refresh_btn"):
    with st.spinner("Refreshing pipeline snapshot…"):
        try:
            refreshed = _load_pipeline_snapshot()
            if refreshed:
                st.session_state["pipeline_snapshot"] = refreshed
                st.toast("Pipeline snapshot refreshed")
            else:
                st.warning("Using cached snapshot – data/pipeline_snapshot.json missing", icon="⚠️")
        except Exception as exc:  # pragma: no cover - defensive guard
            LOGGER.warning("Pipeline refresh failed: %s", exc)
            st.warning("Using cached snapshot – refresh failed", icon="⚠️")
pipeline_snapshot = st.session_state.get("pipeline_snapshot") or _load_pipeline_snapshot()
st.session_state["pipeline_snapshot"] = pipeline_snapshot
if not pipeline_snapshot:
    st.info("Pipeline snapshot unavailable (using last known values).")
overdue_touches = pipeline_snapshot.get("overdue_touches")
weekly_value = pipeline_snapshot.get("weekly_pipeline_value")
currency = pipeline_snapshot.get("currency", "USD")
st.metric("Overdue touches", int(overdue_touches) if isinstance(overdue_touches, (int, float)) else "—")
if isinstance(weekly_value, (int, float)):
    st.metric("Weekly pipeline", f"{currency} {weekly_value:,.0f}")
else:
    st.metric("Weekly pipeline", "—")
pipeline_ts = pipeline_snapshot.get("last_updated")
parsed_pipeline_ts = _parse_iso_timestamp(pipeline_ts)
if parsed_pipeline_ts is not None:
    st.caption(f"Pipeline snapshot: {_format_timestamp(pipeline_ts)}")
    age_seconds = (datetime.now(timezone.utc) - parsed_pipeline_ts).total_seconds()
    if age_seconds > 24 * 3600:
        st.warning("Pipeline snapshot is over 24 hours old. Refresh before demo.", icon="⚠️")
else:
    if pipeline_ts:
        st.caption(f"Pipeline snapshot timestamp unreadable: {pipeline_ts}")
st.markdown("### 🌀 Streaming")
latency = st.session_state.get("stream_latency_ms_first_partial")
latency_display = f"{latency} ms" if latency is not None else "—"
st.caption(
    f"Last stream → first partial: {latency_display}, updates: {st.session_state.get('stream_updates_count', 0)}, "
    f"dropouts: {st.session_state.get('stream_dropouts', 0)}, fallbacks: {st.session_state.get('stream_fallbacks', 0)}"
)
st.caption(f"Snapshot last_sync: {snapshot.get('last_sync')}")
st.caption(f"Build: FieldOS {FIELDOS_VERSION} | QA={str(QA_MODE).lower()}")

def render_workflow_tab() -> None:
    st.markdown(f"## Good morning, Kevin 👋  — **Focus Lead: {FOCUS_CONTACT['name']}**")
    top_badges = [
        badge("Overdue", "urgent") if FOCUS_CONTACT["overdue"] else "",
        badge(f"${FOCUS_CONTACT['value']}", "high"),
        badge(f"Last touch: {FOCUS_CONTACT['last_touch']}", "neutral"),
    ]
    st.markdown(" ".join([b for b in top_badges if b]), unsafe_allow_html=True)
    st.caption("Here’s what to tackle first. Keep it to 60 seconds — then move on.")
    st.divider()

    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("### 🎯 Focus Contact")
        st.markdown(f"**{FOCUS_CONTACT['name']}** — {FOCUS_CONTACT['service']}  •  {FOCUS_CONTACT['address']}")
        st.markdown(f"**Primary:** {FOCUS_CONTACT['contact']}  •  **Phone:** {FOCUS_CONTACT['phone']}")

        if st.session_state.get("playbook_last_contact") != FOCUS_CONTACT["name"]:
            st.session_state["applied_playbook_snippets"] = []
            st.session_state["applied_playbook_titles"] = []
            st.session_state["playbook_last_contact"] = FOCUS_CONTACT["name"]

        intel_source = _load_contact_intel()
        contact_intel = intel_source.get(FOCUS_CONTACT["name"], {}) if isinstance(intel_source, dict) else {}
        with st.container(border=True):
            st.markdown("**Intelligence Center**")
            if contact_intel:
                recent_jobs = contact_intel.get("recent_jobs") or []
                open_quotes = contact_intel.get("open_quotes") or []
                promotions = contact_intel.get("promotions") or []
                intel_notes = contact_intel.get("intel_notes") or []

                if recent_jobs:
                    st.caption("Recent work")
                    for job in recent_jobs:
                        summary = job.get("summary")
                        value = job.get("value")
                        date = job.get("date")
                        line = " - ".join(filter(None, [date, summary]))
                        if value is not None:
                            line = f"{line} (${value:,})"
                        st.markdown(f"- {line}")

                if open_quotes:
                    st.caption("Open quotes")
                    for quote in open_quotes:
                        status = quote.get("status")
                        amount = quote.get("amount")
                        line = quote.get("summary", "Pending follow-up")
                        details = []
                        if amount is not None:
                            details.append(f"${amount:,}")
                        if status:
                            details.append(status)
                        if details:
                            line = f"{line} ({', '.join(details)})"
                        st.markdown(f"- {line}")

                if promotions:
                    st.caption("Active promos")
                    for promo in promotions:
                        st.markdown(f"- {promo}")

                if intel_notes:
                    st.caption("Intel")
                    for note in intel_notes:
                        st.markdown(f"- {note}")

                if contact_intel.get("last_updated"):
                    st.session_state["contact_intel_last_refresh"] = contact_intel["last_updated"]
                last_refresh = contact_intel.get("last_updated") or st.session_state.get("contact_intel_last_refresh")
                if last_refresh:
                    st.caption(f"Last refresh: {last_refresh}")
            else:
                st.caption("No additional intel yet. Update data/contact_intel.json to extend.")

        if not st.session_state["suggestion"]:
            st.session_state["suggestion"] = (
                f"Consider: “Hi {FOCUS_CONTACT['contact']}, quick follow-up on the "
                f"{FOCUS_CONTACT['service']}. We’re running a mulch promo this week—"
                "want me to include it in your updated proposal?”"
            )
        with st.container(border=True):
            st.markdown("**Smart Suggestion**")
            st.write(st.session_state["suggestion"])
            current_note = st.session_state.get("draft_note", "")
            pending_note = st.session_state.pop("_draft_note_pending", None)
            if pending_note is not None:
                st.session_state["draft_note_input"] = pending_note
            if st.session_state.get("draft_note_input") != current_note:
                st.session_state["draft_note_input"] = current_note
            draft_value = st.text_area(
                "Draft note",
                key="draft_note_input",
                height=220,
                label_visibility="collapsed",
            )
            _set_draft_note(draft_value)
            def _handle_suggestion_insert() -> None:
                suggestion_text = st.session_state.get("suggestion", "")
                if not suggestion_text:
                    return
                _append_to_draft(suggestion_text)
                st.session_state["progress_done"] = min(3, st.session_state["progress_done"] + 1)
                _queue_draft_toast("Inserted suggestion into draft.")

            st.button(
                "Insert into draft note (Sounds good?)",
                key="smart_suggestion_insert",
                on_click=_handle_suggestion_insert,
            )

        _qa_mode = os.getenv("FIELDOS_QA_MODE", "false").lower() == "true"
        streaming_enabled = bool(st.session_state.get("STREAMING_ENABLED", True))
        force_fail = os.getenv("FIELDOS_STREAMING_FORCE_FAIL", "").lower() == "true"

        def _disable_streaming_for_session() -> None:
            if st.session_state.get("STREAMING_ENABLED", True):
                st.session_state["STREAMING_ENABLED"] = False

        if streaming_enabled and not _qa_mode and _VOSK_AVAILABLE:
            try:
                if force_fail:
                    raise RuntimeError("Streaming forced to fail via FIELDOS_STREAMING_FORCE_FAIL.")
                apply_streaming_live()
            except Exception as exc:
                LOGGER.warning("Streaming failed; falling back to stub metrics: %s", exc, exc_info=True)
                _disable_streaming_for_session()
                st.toast("⚠️ Streaming unavailable—using stub metrics.", icon="⚠️")
                _seed_streaming_stub(increment_fallback=True, force=True)
        else:
            message = None
            warn = False
            increment_fallback = True

            if _qa_mode:
                message = "⚙️ Streaming QA stub populated metrics for deterministic test."
            elif not _VOSK_AVAILABLE:
                message = "⚠️ Vosk not available—using stub metrics."
                warn = True
            else:
                if streaming_enabled:
                    _disable_streaming_for_session()
                if force_fail:
                    message = "⚠️ Streaming disabled for this session—using stub metrics."
                    warn = True
                else:
                    message = "⚙️ Streaming stub metrics active."
            _seed_streaming_stub(message=message, increment_fallback=increment_fallback, warn=warn, force=not _qa_mode or force_fail)

        with st.container(border=True):
            st.markdown("**Quote Builder**")
            st.caption("Draft a quick estimate using cached price tiers.")
            if st.button("Generate quick quote", key="generate_quote"):
                quote = _generate_quote(FOCUS_CONTACT["service"])
                if quote:
                    st.session_state["last_quote"] = quote
                    st.session_state["quote_inserted"] = False
                    st.toast("Quote ready — review before sending.")
                else:
                    st.warning("No pricing data configured for this service.")
            last_quote = st.session_state.get("last_quote")
            if last_quote:
                base_price = last_quote.get("base_price", 0)
                total = last_quote.get("total", 0)
                upsells = last_quote.get("upsells", [])
                generated_at = last_quote.get("generated_at")
                upsell_names = ", ".join(upsell["name"] for upsell in upsells) if upsells else "None"
                with st.container(border=True):
                    st.markdown(f"**Service:** {last_quote.get('service', '—')}")
                    st.markdown(f"**Base price:** ${base_price:,.0f}")
                    st.markdown("**Upsells:**")
                    if upsells:
                        for upsell in upsells:
                            st.markdown(f"- {upsell.get('name', 'Add-on')} (${upsell.get('price', 0):,.0f})")
                    else:
                        st.markdown("- None")
                    st.markdown(f"**Total:** ${total:,.0f}")
                    if generated_at:
                        st.caption(f"Generated: {_format_timestamp(generated_at)}")
                    snippet = f"Quote: {last_quote.get('service', 'Service')} — ${total:,.0f}"
                    if upsells:
                        snippet += f" (Upsells: {upsell_names})"
                    inserted = st.session_state.get("quote_inserted", False)
                    action_label = "Insert quote into draft note" if not inserted else "Inserted ✓"
                    def _handle_quote_insert(snippet_value: str = snippet) -> None:
                        _append_to_draft(snippet_value)
                        st.session_state["quote_inserted"] = True
                        _queue_draft_toast("Inserted quote into draft note.")
                        LOGGER.info("Quote snippet inserted for %s", FOCUS_CONTACT["name"])

                    st.button(
                        action_label,
                        key="insert_quote",
                        disabled=inserted,
                        on_click=_handle_quote_insert,
                    )

        audio_cache_dir = ensure_audio_cache_dir()
        audio_bytes: Optional[bytes] = None
        audio_name = ""

        if os.getenv("FIELDOS_ENABLE_NATIVE_AUDIO", "true").lower() == "true":
            audio_recording = st.audio_input(
                "🎙️ Hold to record",
                label_visibility="collapsed",
            )
            if audio_recording is not None:
                audio_bytes = audio_recording.getvalue()
                audio_name = audio_recording.name or "recording.wav"

        if audio_bytes is None:
            audio_upload = st.file_uploader(
                "🎙️ Upload Voice Clip",
                type=["wav", "m4a"],
                label_visibility="collapsed",
                key="audio_uploader",
            )
            if audio_upload is not None:
                audio_bytes = audio_upload.read()
                audio_name = audio_upload.name or ""

        if audio_bytes is not None:
            clip_fingerprint = hashlib.sha1(audio_bytes).hexdigest()
            processed_fingerprint = st.session_state.get("processed_clip_fingerprint")
            if processed_fingerprint == clip_fingerprint:
                if not st.session_state.get("dedupe_notice_shown", False):
                    st.info("Clip already processed. Upload a new audio clip to transcribe again.")
                    st.session_state["dedupe_notice_shown"] = True
            else:
                st.session_state["dedupe_notice_shown"] = False
            duration = calculate_audio_duration(audio_bytes, audio_name)
            if duration is not None and duration > AUDIO_MAX_SECONDS:
                st.toast(
                    f"Clip is {duration:.1f}s — max length is {AUDIO_MAX_SECONDS}s. Please record a shorter note.",
                    icon="⚠️",
                )
            elif processed_fingerprint != clip_fingerprint:
                st.session_state["processed_clip_fingerprint"] = clip_fingerprint
                file_path = audio_cache_dir / f"clip_{int(time.time())}.wav"
                file_path.write_bytes(audio_bytes)
                with st.spinner("Transcribing…"):
                    transcript, confidence, duration = transcribe_audio(str(file_path))
                st.session_state["raw_transcript"] = transcript
                cleaned_transcript = transcript.strip()
                st.session_state["raw_transcript_display"] = cleaned_transcript or "Transcription unavailable (see draft note)."
                st.session_state["applied_playbook_snippets"] = []
                st.session_state["applied_playbook_titles"] = []
                st.session_state["quote_inserted"] = False
                st.session_state["last_quote"] = None
                if transcript and "Transcription unavailable" not in transcript:
                    _append_to_draft(transcript)
                    st.session_state["last_transcription_confidence"] = confidence
                    st.session_state["last_transcription_duration"] = duration
                    st.session_state["ai_latency_totals"]["transcribe"] += max(duration, 0.0)
                    st.session_state["ai_latency_counts"]["transcribe"] += 1
                    _queue_draft_toast(f"Captured transcript ({duration:.1f}s, conf ~{confidence:.2f}).")
                else:
                    st.session_state["ai_fail_count"] += 1
                    st.toast("Transcription unavailable right now—saved raw audio context to note.", icon="⚠️")

                handle = st.session_state.get("final_worker_handle") or ensure_final_worker()
                if handle is not None:
                    try:
                        job_id = submit_final_job(
                            handle,
                            file_path,
                            {
                                "audio_name": audio_name,
                                "submitted_via": "app",
                            },
                        )
                        jobs = st.session_state.setdefault("final_worker_jobs", {})
                        jobs[job_id] = {
                            "status": "queued",
                            "submitted_at": time.time(),
                            "clip_path": str(file_path),
                            "audio_name": audio_name,
                        }
                        st.session_state["final_worker_warning_logged"] = False
                    except Exception as exc:  # pragma: no cover - defensive guard
                        if not st.session_state.get("final_worker_warning_logged", False):
                            st.warning(f"Unable to queue high-accuracy transcript job: {exc}")
                            st.session_state["final_worker_warning_logged"] = True
                else:
                    if FINAL_WORKER_ENABLED and not st.session_state.get("final_worker_warning_logged", False):
                        st.info("High-accuracy transcript worker not running; using current transcript only.")
                        st.session_state["final_worker_warning_logged"] = True

                poll_final_worker(st.session_state.get("final_worker_handle"))
        else:
            if not st.session_state.get("raw_transcript_display"):
                st.session_state["raw_transcript_display"] = "Awaiting capture"

        raw_display = st.session_state.get("raw_transcript_display", "Awaiting capture")
        with st.container(border=True):
            st.caption("Raw transcript (read-only)")
            st.code(raw_display or "Awaiting capture", language="text")

        playbook_source = _load_playbooks()
        playbook_items = []
        if isinstance(playbook_source, dict):
            playbook_items = playbook_source.get(FOCUS_CONTACT["service"], []) or playbook_source.get("default", [])
        if playbook_items:
            st.session_state.setdefault("applied_playbook_snippets", [])
            st.session_state.setdefault("applied_playbook_titles", [])
            with st.container(border=True):
                st.markdown("**Playbook cues**")
                st.caption("Tap to insert a talking point into the draft note.")
                applied_snippets = set(st.session_state.get("applied_playbook_snippets", []))
                applied_titles = st.session_state.get("applied_playbook_titles", [])
                for idx, cue in enumerate(playbook_items):
                    title = cue.get("title") or f"Cue {idx + 1}"
                    snippet = cue.get("snippet", "")
                    tags = cue.get("tags") or []
                    cols = st.columns([4, 1])
                    with cols[0]:
                        st.write(f"**{title}**")
                        if snippet:
                            st.caption(snippet)
                        if tags:
                            st.caption(", ".join(f"#{tag}" for tag in tags))
                    with cols[1]:
                        used = snippet in applied_snippets
                        button_label = f"Use {title}" if not used else "Added ✓"

                        def _handle_playbook_insert(snippet_value: str = snippet, title_value: str = title) -> None:
                            if not snippet_value:
                                return
                            snippets_list = st.session_state.setdefault("applied_playbook_snippets", [])
                            if snippet_value in snippets_list:
                                return
                            _append_to_draft(snippet_value)
                            snippets_list.append(snippet_value)
                            titles_list = st.session_state.setdefault("applied_playbook_titles", [])
                            titles_list.append(title_value)
                            _queue_draft_toast(f"Added '{title_value}' to draft note.")

                        st.button(
                            button_label,
                            key=f"playbook_{idx}",
                            disabled=used,
                            on_click=_handle_playbook_insert,
                        )
                if applied_titles:
                    st.caption("Used cues: " + ", ".join(applied_titles))

        download_text = (
            st.session_state.get("stream_final_text") or st.session_state.get("raw_transcript") or ""
        )
        st.download_button(
            "Download last transcript",
            data=download_text,
            file_name="fieldos_last_transcript.txt",
            mime="text/plain",
            disabled=not download_text.strip(),
            help=None if download_text.strip() else "No transcript captured yet.",
        )

        final_stats_for_panel = st.session_state.get("final_worker_stats", _final_stats_default())
        last_result = st.session_state.get("final_worker_last_result") or {}
        with st.container(border=True):
            st.markdown("#### 🧠 High-Accuracy Transcript")
            queue_depth = final_stats_for_panel.get("queue_depth", 0) or 0
            last_error = final_stats_for_panel.get("last_error")
            if queue_depth > 0 or last_error:
                warning_message = f"Background worker processing {queue_depth} job(s)."
                if last_error:
                    warning_message = f"{warning_message} Last error: {last_error}"
                st.warning(warning_message)

            if last_result:
                transcript_text = last_result.get("transcript") or "—"
                confidence = last_result.get("confidence")
                if confidence is None:
                    confidence_display = "—"
                else:
                    confidence_display = f"{max(min(confidence * 100, 100), 0):.1f}%"
                latency = last_result.get("latency_ms")
                latency_display = f"{latency:.0f} ms" if latency is not None else "—"
                completed_display = _format_timestamp(last_result.get("completed_at"))

                st.write(transcript_text)
                st.caption(
                    f"Confidence: {confidence_display} (model certainty) • Latency: {latency_display} (processing time) • Completed: {completed_display}"
                )
            else:
                st.info("High-accuracy transcript pending (worker disabled or still processing).")

        if st.button(POLISH_CTA, key="polish_note"):
            with st.spinner("Polishing…"):
                metadata: Dict[str, str] = {
                    "account": FOCUS_CONTACT["name"],
                    "service": FOCUS_CONTACT["service"],
                    "contact": FOCUS_CONTACT["contact"],
                }
                polished, polish_duration = polish_note_with_gpt(
                    st.session_state["draft_note"], metadata, st.session_state.get("style_guidelines", "")
                )
                if polished:
                    _set_draft_note(polished)
                    st.session_state["last_polish_duration"] = polish_duration
                    st.session_state["ai_latency_totals"]["polish"] += polish_duration
                    st.session_state["ai_latency_counts"]["polish"] += 1
                    st.toast(f"Polished in {polish_duration:.1f}s.")
                else:
                    st.session_state["ai_fail_count"] += 1
                    st.toast(POLISH_FAIL_TOAST, icon="⚠️")

        if st.button("✅ Save & Queue CRM Push", key="save_queue"):
            _save_and_queue_crm_payload()

        last_payload = st.session_state.get("last_crm_payload")
        if not last_payload and st.session_state.get("crm_sync_log"):
            try:
                last_payload = st.session_state["crm_sync_log"][-1]["payload"]
            except (IndexError, KeyError, TypeError):
                last_payload = None

        crm_status = st.session_state.get("last_crm_status")
        retry_available = st.session_state.get("crm_retry_available", False)
        retry_in_progress = st.session_state.get("_crm_retry_in_progress", False)

        with st.expander("Last CRM payload", expanded=False):
            st.markdown(_format_crm_status_badge(crm_status), unsafe_allow_html=True)
            if last_payload:
                payload_preview = copy.deepcopy(last_payload)
                payload_preview.pop("crm_status", None)
                st.json(payload_preview)
            else:
                st.caption("No CRM payload queued yet. Save a note to preview the outgoing event.")

            if retry_available:
                retry_disabled = retry_in_progress or last_payload is None

                def _retry_last_payload() -> None:
                    payload_to_retry = st.session_state.get("last_crm_payload")
                    if not payload_to_retry:
                        return
                    retry_payload = copy.deepcopy(payload_to_retry)
                    payload_id = retry_payload.get("_crm_payload_id")
                    if payload_id:
                        st.session_state["offline_cache"] = [
                            entry
                            for entry in st.session_state.get("offline_cache", [])
                            if entry.get("_crm_payload_id") != payload_id
                        ]
                    retry_payload.pop("crm_status", None)
                    retry_payload.pop("_offline_cached", None)
                    retry_payload.pop("_cached_at", None)
                    retry_payload.pop("_gps", None)
                    retry_payload.pop("_crm_last_error", None)
                    retry_payload["_crm_retry_attempts"] = 0
                    st.session_state["_crm_retry_in_progress"] = True
                    st.session_state["crm_retry_available"] = False
                    enqueue_crm_push(retry_payload)
                    st.toast("Retry queued.", icon="ℹ️")

                st.button(
                    "Retry CRM Push",
                    key="retry_crm_push",
                    disabled=retry_disabled,
                    on_click=_retry_last_payload,
                )

    for toast_message in st.session_state.pop("_draft_note_toasts", []):
        st.toast(toast_message)

    with c2:
        st.markdown("### 📋 Follow-Ups")
        any_left = False
        for idx, follow in enumerate(st.session_state["followups"]):
            if follow["name"] in st.session_state["snoozed"]:
                continue
            any_left = True
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.write(f"• {follow['name']} — {follow['due']} — ${follow['value']:,}")
            with col_b:
                if st.button("Snooze", key=f"snooze_{idx}"):
                    st.session_state["snoozed"].add(follow["name"])
                    st.toast(f"Snoozed {follow['name']} for today.")
                    st.rerun()
        if not any_left:
            st.info("No remaining follow-ups. Nice.")
        st.divider()
        _render_reference_copilot()

    st.divider()

    footer_cols = st.columns([1, 1, 1, 1, 1])
    with footer_cols[0]:
        if st.button("📝 Log Note", key="log_note"):
            st.toast("Use the Draft Note box above to log the update.")
    with footer_cols[1]:
        if st.button("📞 Call", key="call_contact"):
            st.toast(f"Dialing {FOCUS_CONTACT['phone']} (stub).")
    with footer_cols[2]:
        if st.button("🗺️ View Map", key="view_map"):
            st.toast(f"Opening maps to {FOCUS_CONTACT['address']} (stub).")
    with footer_cols[3]:
        if st.button("➕ New Lead", key="new_lead"):
            st.toast("New lead form (stub).")
    with footer_cols[4]:
        if st.button("✅ Day Complete", key="day_complete"):
            st.balloons()
            st.toast("Day closed. Great work!")

    progress_pct = int((st.session_state["progress_done"] / 3) * 100)
    st.progress(progress_pct, text=f"{progress_pct}% of today’s core steps complete")


def render_demo_tab() -> None:
    demo_md = st.session_state.get("_demo_runbook_md")
    if demo_md is None:
        demo_md = _load_markdown_file(DEMO_RUNBOOK_PATH)
        st.session_state["_demo_runbook_md"] = demo_md
    if demo_md:
        st.markdown(demo_md)
    else:
        st.info("Demo runbook not found. See docs/final_worker_runbook.md.")


def render_company_wiki_tab() -> None:
    wiki_md = st.session_state.get("_company_wiki_md")
    if wiki_md is None:
        wiki_md = _load_markdown_file(COMPANY_WIKI_PATH)
        st.session_state["_company_wiki_md"] = wiki_md
    if wiki_md:
        st.markdown(wiki_md)
    else:
        st.info("Company wiki not found. Add data/company_wiki.md for demo context.")


def render_crm_tab() -> None:
    records = st.session_state.get("_crm_sample_rows")
    if records is None:
        records = _load_crm_sample()
        st.session_state["_crm_sample_rows"] = records
    if records:
        st.dataframe(records, width="stretch")
        st.caption("Data source: data/crm_sample.csv")
    else:
        st.info("CRM sample data not found. Drop data/crm_sample.csv to enable this view.")


def render_sales_playbook_tab() -> None:
    doc_cache_key = "_sales_playbook_md"
    doc_mtime_key = "_sales_playbook_md_mtime"
    current_mtime = None
    if SALES_PLAYBOOK_DOC_PATH.exists():
        try:
            current_mtime = SALES_PLAYBOOK_DOC_PATH.stat().st_mtime
        except OSError:
            current_mtime = None
    cached_mtime = st.session_state.get(doc_mtime_key)
    doc = st.session_state.get(doc_cache_key)
    if doc is None or cached_mtime != current_mtime:
        doc = _load_markdown_file(SALES_PLAYBOOK_DOC_PATH)
        st.session_state[doc_cache_key] = doc
        st.session_state[doc_mtime_key] = current_mtime
    if doc:
        st.markdown(doc)
    else:
        st.info("Sales playbook not found. Provide data/sales_playbook.md for demo snippets.")


render_workflow_tab()

with st.sidebar:
    st.markdown("### 📚 Reference")
    with st.expander("Demo Guide", expanded=False):
        render_demo_tab()
    with st.expander("Company Wiki", expanded=False):
        render_company_wiki_tab()
    with st.expander("CRM Snapshot", expanded=False):
        render_crm_tab()
    with st.expander("Sales Playbook", expanded=False):
        render_sales_playbook_tab()
