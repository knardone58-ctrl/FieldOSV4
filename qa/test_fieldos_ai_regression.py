"""
FieldOS V4.2 — AI/Audio Regression Harness
------------------------------------------
Automates the AI-enabled 60-second loop:
    1. Inserts suggestion → simulates transcription → GPT polish
    2. Queues CRM push, exercises offline cache, flushes back online
    3. Verifies telemetry drift (latency, failure count)
    4. Ensures CRM payload schema contains new AI fields
    5. Prints benchmark summary for approval
"""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np
from streamlit.testing.v1 import AppTest

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR
APP_PATH = APP_DIR / "app.py"
SNAPSHOT_PATH = APP_DIR / "data" / "crm_snapshot.json"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("FIELDOS_QA_MODE", os.getenv("FIELDOS_QA_MODE", "true"))
os.environ.setdefault("FIELDOS_TRANSCRIBE_ENGINE", os.getenv("FIELDOS_TRANSCRIBE_ENGINE", "whisper_local"))

from fieldos_config import POLISH_CTA, QA_MODE, TRANSCRIBE_ENGINE


@contextmanager
def change_dir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def load_snapshot() -> Dict:
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text())


def generate_dummy_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    """Create a short silent WAV clip for transcription engines to consume."""
    num_samples = int(seconds * sample_rate)
    data = np.zeros(num_samples, dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(data.tobytes())


def click_button(app: AppTest, label: str) -> None:
    for button in app.button:
        if button.label == label:
            button.click().run()
            return
    raise AssertionError(f"Button {label!r} not found. Buttons present: {[b.label for b in app.button]}")


def get_toggle(app: AppTest, label: str):
    for toggle in app.toggle:
        if toggle.label == label:
            return toggle
    raise AssertionError(f"Toggle {label!r} not found.")


def wait_for(condition, app: AppTest, timeout: float = 6.0, poll: float = 0.25) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(poll)
        app.run()
    raise AssertionError("Timed out waiting for condition.")


def verify_schema(payload: Dict) -> list[str]:
    required = [
        "transcription_raw",
        "note_polished",
        "transcription_confidence",
        "ai_model_version",
        "processing_time",
    ]
    return [key for key in required if key not in payload]


def run_test() -> Dict[str, object]:
    if not APP_PATH.exists():
        raise FileNotFoundError(f"Streamlit app missing at {APP_PATH}")

    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    from ai_parser import polish_note_with_gpt, transcribe_audio

    report: Dict[str, object] = {"start": datetime.now().isoformat(timespec="seconds")}

    with change_dir(APP_DIR):
        app = AppTest.from_file("app.py")
        app.run()

        hero_block = [md.value for md in app.markdown[:2]]
        assert any("Focus Lead" in block for block in hero_block), "Hero banner missing Focus Lead copy."
        report["startup"] = "ok"

        suggestion = app.session_state["suggestion"] if "suggestion" in app.session_state else ""
        click_button(app, "Insert into draft note (Sounds good?)")
        app.run()
        assert suggestion and suggestion in app.session_state["draft_note"], "Suggestion failed to insert."

        # Generate dummy audio clip and trigger transcription pipeline.
        audio_dir = Path("data/audio_cache")
        audio_dir.mkdir(parents=True, exist_ok=True)
        dummy_clip = audio_dir / "qa_dummy.wav"
        generate_dummy_wav(dummy_clip)

        t_start = time.perf_counter()
        transcript, confidence, duration = transcribe_audio(str(dummy_clip))
        transcribe_latency_ms = round((time.perf_counter() - t_start) * 1000, 1)

        app.session_state["raw_transcript"] = transcript
        app.session_state["draft_note"] = (app.session_state["draft_note"] + "\n" + transcript).strip()
        app.session_state["ai_latency_totals"]["transcribe"] += duration
        app.session_state["ai_latency_counts"]["transcribe"] += 1
        app.session_state["last_transcription_confidence"] = confidence
        app.session_state["last_transcription_duration"] = duration
        report["transcribe_latency_ms"] = transcribe_latency_ms
        report["voice_capture"] = "executed"
        app.run()

        focus_contact = app.session_state["focus_contact"] if "focus_contact" in app.session_state else {}
        metadata = {
            "account": focus_contact.get("name", "QA Account"),
            "service": focus_contact.get("service", "QA Service"),
            "contact": focus_contact.get("contact", "QA Contact"),
        }
        t_polish = time.perf_counter()
        style_guidelines = app.session_state["style_guidelines"] if "style_guidelines" in app.session_state else ""
        polished, polish_duration = polish_note_with_gpt(
            app.session_state["draft_note"],
            metadata,
            style_guidelines,
        )
        polish_latency_ms = round((time.perf_counter() - t_polish) * 1000, 1)
        if polished:
            app.session_state["draft_note"] = polished
            app.session_state["ai_latency_totals"]["polish"] += polish_duration
            app.session_state["ai_latency_counts"]["polish"] += 1
            app.session_state["last_polish_duration"] = polish_duration
        else:
            app.session_state["ai_fail_count"] += 1
        report["polish_latency_ms"] = polish_latency_ms
        app.run()

        click_button(app, "✅ Save & Queue CRM Push")
        queue_after_push = len(app.session_state["crm_queue"])
        report["queue_post_push"] = queue_after_push
        wait_for(lambda: not app.session_state["crm_queue"], app)

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(True).run()
        report["offline_toggle"] = True
        click_button(app, "✅ Save & Queue CRM Push")

        wait_for(lambda: len(app.session_state["offline_cache"]) > 0, app)
        offline_cache_count = len(app.session_state["offline_cache"])

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(False).run()

        click_button(app, "Flush Offline Cache")
        wait_for(lambda: not app.session_state["crm_queue"], app)

        queue_after_flush = len(app.session_state["crm_queue"])
        cache_after_flush = len(app.session_state["offline_cache"])

        statuses = {entry.get("status") for entry in app.session_state["crm_sync_log"]}
        assert {"synced", "cached"}.issubset(statuses), "CRM sync log missing expected lifecycle statuses."

        last_payload = next(
            (entry["payload"] for entry in reversed(app.session_state["crm_sync_log"]) if entry.get("payload")),
            None,
        )
        assert last_payload, "No CRM payload recorded."
        missing_keys = verify_schema(last_payload)

        click_button(app, "✅ Day Complete")
        assert app.session_state["progress_done"] == 3, "Day completion did not set progress to 3."

    snapshot = load_snapshot()
    totals = app.session_state["ai_latency_totals"]
    counts = app.session_state["ai_latency_counts"]
    avg_transcribe = totals["transcribe"] / max(1, counts["transcribe"])
    avg_polish = totals["polish"] / max(1, counts["polish"])
    success_count = sum(1 for entry in app.session_state["crm_sync_log"] if entry.get("status") == "synced")
    cached_count = sum(1 for entry in app.session_state["crm_sync_log"] if entry.get("status") == "cached")
    total_attempts = success_count + cached_count
    success_pct = round((success_count / total_attempts) * 100, 1) if total_attempts else 0.0

    report.update(
        {
            "offline_queue_len": offline_cache_count,
            "queue_after_flush": queue_after_flush,
            "cache_after_flush": cache_after_flush,
            "ai_avg_transcribe_s": round(avg_transcribe, 2),
            "ai_avg_polish_s": round(avg_polish, 2),
            "ai_failures": app.session_state["ai_fail_count"],
            "missing_schema_keys": missing_keys,
            "last_sync": snapshot.get("last_sync"),
            "queue_len": len(app.session_state["crm_queue"]),
            "success_count": success_count,
            "cached_count": cached_count,
            "success_pct": success_pct,
        }
    )

    report["status"] = (
        "PASS"
        if not missing_keys
        and app.session_state["ai_fail_count"] == 0
        and queue_after_flush == 0
        else "WARN"
    )
    report["end"] = datetime.now().isoformat(timespec="seconds")
    return report


if __name__ == "__main__":
    results = run_test()
    print("\n=== FieldOS AI Regression Report ===")
    for key, value in results.items():
        print(f"{key:25s}: {value}")
    print("====================================\n")
    if results["status"] == "PASS":
        print("✅  All major AI / audio checks passed.")
    else:
        print("⚠️  Review required before approval.")
