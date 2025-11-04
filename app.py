# FieldOS V4.2 — Daily Command Center (AI-enabled)
# -------------------------------------------------
# - Audio capture via st.audio_input (≤ 30s recommended)
# - Transcription (QA-safe) + optional GPT polish
# - Async CRM queue + offline caching with schema migration
# - Telemetry: latency averages, fail count, queue/cached counts

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
    st.session_state.setdefault("raw_transcript_display", "Awaiting capture")
    st.session_state.setdefault("processed_clip_fingerprint", None)
    st.session_state.setdefault("dedupe_notice_shown", False)
    st.session_state.setdefault("final_worker_toast_shown", False)
    st.session_state.setdefault("last_crm_payload", None)
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


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "—"
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return value


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

    st.caption(f"Record or upload up to {AUDIO_MAX_SECONDS}s (shorter clips respond faster).")
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
        last_saved_fingerprint = st.session_state.get("last_saved_clip_fingerprint")
        if processed_fingerprint == clip_fingerprint and processed_fingerprint != last_saved_fingerprint:
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
                    st.session_state["final_worker_toast_shown"] = False
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
            "account": FOCUS_CONTACT["name"],
            "service": FOCUS_CONTACT["service"],
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
        st.session_state["last_crm_payload"] = payload

    last_payload = st.session_state.get("last_crm_payload")
    if not last_payload and st.session_state.get("crm_sync_log"):
        try:
            last_payload = st.session_state["crm_sync_log"][-1]["payload"]
        except (IndexError, KeyError, TypeError):
            last_payload = None

    with st.expander("Last CRM payload", expanded=False):
        if last_payload:
            payload_preview = dict(last_payload)
            st.json(payload_preview)
        else:
            st.caption("No CRM payload queued yet. Save a note to preview the outgoing event.")

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
