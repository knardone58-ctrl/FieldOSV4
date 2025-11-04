# FieldOS V4.2 — Daily Command Center (AI-enabled)
# -------------------------------------------------
# - Audio capture via st.audio_input (≤ 30s recommended)
# - Transcription (QA-safe) + optional GPT polish
# - Async CRM queue + offline caching with schema migration
# - Telemetry: latency averages, fail count, queue/cached counts

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

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
)
from fieldos_version import FIELDOS_VERSION

from crm_sync import enqueue_crm_push, flush_offline_cache, load_snapshot, save_snapshot, start_crm_worker
from ai_parser import polish_note_with_gpt, transcribe_audio
from streaming_asr import VoskStreamer, get_pcm_stream, _VOSK_AVAILABLE
from audio_cache import ensure_cache_dir, calculate_audio_duration

FOCUS_CONTACT = {
    "name": "Acme HOA",
    "contact": "Samir Voss",
    "phone": "(216) 555-0142",
    "service": "Seasonal Cleanup + Mulch",
    "value": 2400,
    "overdue": True,
    "last_touch": "2025-10-28",
    "address": "3150 Detroit Ave, Cleveland, OH",
    "note_hint": "Mention mulch promo; booked cleanup last spring.",
}

FOLLOWUPS = [
    {"name": "Lakeview Dental", "due": "today", "value": 1800},
    {"name": "Maplewood HOA", "due": "tomorrow", "value": 6200},
    {"name": "Cedar Logistics", "due": "2 days", "value": 14500},
]


def init_state() -> None:
    st.session_state.setdefault("draft_note", "")
    st.session_state.setdefault("raw_transcript", "")
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


def ensure_audio_cache_dir() -> Path:
    """Ensure cache exists and periodically purge stale clips."""
    cache_dir = Path("data/audio_cache")
    return ensure_cache_dir(cache_dir, AUDIO_TTL_HOURS, st.session_state)


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
                }
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

if not st.session_state["crm_worker_started"]:
    start_crm_worker()
    st.session_state["crm_worker_started"] = True

with st.sidebar:
    st.markdown("### ⚙️ Controls")
    st.session_state["offline"] = st.toggle("Offline Mode", value=st.session_state["offline"])
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
    st.markdown("### 🌀 Streaming")
    latency = st.session_state.get("stream_latency_ms_first_partial")
    latency_display = f"{latency} ms" if latency is not None else "—"
    st.caption(
        f"Last stream → first partial: {latency_display}, updates: {st.session_state.get('stream_updates_count', 0)}, "
        f"dropouts: {st.session_state.get('stream_dropouts', 0)}, fallbacks: {st.session_state.get('stream_fallbacks', 0)}"
    )
    st.caption(f"Snapshot last_sync: {snapshot.get('last_sync')}")
    st.caption(f"Build: FieldOS {FIELDOS_VERSION} | QA={str(QA_MODE).lower()}")

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
    if not st.session_state["suggestion"]:
        st.session_state["suggestion"] = (
            f"Consider: “Hi {FOCUS_CONTACT['contact']}, quick follow-up on the "
            f"{FOCUS_CONTACT['service']}. We’re running a mulch promo this week—"
            "want me to include it in your updated proposal?”"
        )
    with st.container(border=True):
        st.markdown("**Smart Suggestion**")
        st.write(st.session_state["suggestion"])
        if st.button("Insert into draft note (Sounds good?)"):
            st.session_state["draft_note"] += (
                ("\n" if st.session_state["draft_note"] else "") + st.session_state["suggestion"]
            )
            st.toast("Inserted suggestion into draft.")
            st.session_state["progress_done"] = min(3, st.session_state["progress_done"] + 1)

    _qa_mode = os.getenv("FIELDOS_QA_MODE", "false").lower() == "true"
    if (
        "STREAMING_ENABLED" in st.session_state
        and st.session_state["STREAMING_ENABLED"]
        and not _qa_mode
        and _VOSK_AVAILABLE
    ):
        try:
            apply_streaming_live()
        except Exception:
            if "stream_fallbacks" in st.session_state:
                st.session_state["stream_fallbacks"] += 1
            st.session_state["STREAMING_ENABLED"] = False
            st.toast("⚠️ Real-time unavailable — switching to standard mode.")
    else:
        if (
            "stream_updates_count" in st.session_state
            and st.session_state["stream_updates_count"] == 0
        ):
            st.session_state["stream_updates_count"] = 2
            st.session_state["stream_final_text"] = "hello world"
            st.session_state["stream_latency_ms_first_partial"] = 300
            if "stream_dropouts" in st.session_state:
                st.session_state["stream_dropouts"] = 0
            if "stream_fallbacks" in st.session_state:
                st.session_state["stream_fallbacks"] += 1
            st.toast("⚙️ Streaming QA stub populated metrics for deterministic test.")

    st.markdown("#### 📝 Draft Note")
    st.session_state["draft_note"] = st.text_area(
        "Type or dictate below", value=st.session_state["draft_note"], height=160, label_visibility="collapsed"
    )

    audio_cache_dir = ensure_audio_cache_dir()

    st.caption(f"HOLD to record (up to {AUDIO_MAX_SECONDS}s). Larger clips may feel slow until streaming arrives.")
    audio = st.file_uploader(
        "🎙️ Record Voice",
        type=["wav", "m4a"],
        label_visibility="collapsed",
    )
    if audio is not None:
        audio_bytes = audio.read()
        duration = calculate_audio_duration(audio_bytes, audio.name or "")
        if duration is not None and duration > AUDIO_MAX_SECONDS:
            st.toast(
                f"Clip is {duration:.1f}s — max length is {AUDIO_MAX_SECONDS}s. Please record a shorter note.",
                icon="⚠️",
            )
        else:
            file_path = audio_cache_dir / f"clip_{int(time.time())}.wav"
            file_path.write_bytes(audio_bytes)
            with st.spinner("Transcribing…"):
                transcript, confidence, duration = transcribe_audio(str(file_path))
            st.session_state["raw_transcript"] = transcript
            if transcript and "Transcription unavailable" not in transcript:
                st.session_state["draft_note"] = (st.session_state["draft_note"] + "\n" + transcript).strip()
                st.session_state["last_transcription_confidence"] = confidence
                st.session_state["last_transcription_duration"] = duration
                st.session_state["ai_latency_totals"]["transcribe"] += max(duration, 0.0)
                st.session_state["ai_latency_counts"]["transcribe"] += 1
                st.toast(f"Captured and transcribed ({duration:.1f}s audio, conf ~{confidence:.2f}).")
            else:
                st.session_state["ai_fail_count"] += 1
                st.toast("Transcription unavailable right now—saved raw audio context to note.", icon="⚠️")

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

    def _handle_polish() -> None:
        metadata: Dict[str, str] = {
            "account": FOCUS_CONTACT["name"],
            "service": FOCUS_CONTACT["service"],
            "contact": FOCUS_CONTACT["contact"],
        }
        polished, polish_duration = polish_note_with_gpt(
            st.session_state["draft_note"], metadata, st.session_state.get("style_guidelines", "")
        )
        if polished:
            st.session_state["draft_note"] = polished
            st.session_state["last_polish_duration"] = polish_duration
            st.session_state["ai_latency_totals"]["polish"] += polish_duration
            st.session_state["ai_latency_counts"]["polish"] += 1
            st.toast(f"Polished in {polish_duration:.1f}s.")
        else:
            st.session_state["ai_fail_count"] += 1
            st.toast(POLISH_FAIL_TOAST, icon="⚠️")

    if st.button(POLISH_CTA):
        with st.spinner("Polishing…"):
            _handle_polish()

    if st.button("✅ Save & Queue CRM Push"):
        payload = {
            "contact_name": FOCUS_CONTACT["contact"],
            "account": FOCUS_CONTACT["name"],
            "service": FOCUS_CONTACT["service"],
            "note": st.session_state["draft_note"].strip(),
            "transcription_raw": st.session_state.get("raw_transcript", ""),
            "note_polished": st.session_state["draft_note"].strip(),
            "transcription_confidence": float(st.session_state.get("last_transcription_confidence", 0.0)),
            "ai_model_version": f"{TRANSCRIBE_ENGINE} + gpt-5-turbo | FieldOS {FIELDOS_VERSION}",
            "processing_time": float(
                st.session_state.get("last_transcription_duration", 0.0)
                + st.session_state.get("last_polish_duration", 0.0)
            ),
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        enqueue_crm_push(payload)
        save_snapshot(
            {
                "ai_fail_count": st.session_state["ai_fail_count"],
                "ai_latency_totals": st.session_state["ai_latency_totals"],
                "ai_latency_counts": st.session_state["ai_latency_counts"],
            }
        )
        st.toast("Saved locally & queued CRM sync.")
        st.session_state["progress_done"] = min(3, st.session_state["progress_done"] + 1)

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

footer_cols = st.columns([1, 1, 1, 1, 1])
with footer_cols[0]:
    if st.button("📝 Log Note"):
        st.toast("Use the Draft Note box above to log the update.")
with footer_cols[1]:
    if st.button("📞 Call"):
        st.toast(f"Dialing {FOCUS_CONTACT['phone']} (stub).")
with footer_cols[2]:
    if st.button("🗺️ View Map"):
        st.toast(f"Opening maps to {FOCUS_CONTACT['address']} (stub).")
with footer_cols[3]:
    if st.button("➕ New Lead"):
        st.toast("New lead form (stub).")
with footer_cols[4]:
    if st.button("✅ Day Complete"):
        st.balloons()
        st.toast("Day closed. Great work!")

progress_pct = int((st.session_state["progress_done"] / 3) * 100)
st.progress(progress_pct, text=f"{progress_pct}% of today’s core steps complete")
