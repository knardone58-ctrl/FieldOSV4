"""
FieldOS V4.2.1 — Whisper Accuracy & Fallback Validation
-------------------------------------------------------
Runs four deterministic AI-audio scenarios (quiet, yard, chatter, offline)
by stubbing the transcription engine. Captures latency, confidence, and
fallback behaviour for telemetry review.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "FieldOSV4"
APP_PATH = APP_DIR / "app.py"
DUMMY_CLIP = ROOT_DIR / "qa" / "tmp" / "stubbed_clip.wav"

os.environ.setdefault("FIELDOS_QA_MODE", "false")
os.environ.setdefault("FIELDOS_TRANSCRIBE_ENGINE", "whisper_local")
os.environ.setdefault("FIELDOS_AUDIO_MAX_SECONDS", "30")

if str(APP_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(APP_DIR))

import ai_parser  # noqa: E402


def ensure_session_defaults(app: AppTest) -> None:
    if "draft_note" not in app.session_state:
        app.session_state["draft_note"] = ""
    if "ai_fail_count" not in app.session_state:
        app.session_state["ai_fail_count"] = 0
    if "ai_latency_totals" not in app.session_state:
        app.session_state["ai_latency_totals"] = {"transcribe": 0.0, "polish": 0.0}
    if "ai_latency_counts" not in app.session_state:
        app.session_state["ai_latency_counts"] = {"transcribe": 0, "polish": 0}
    if "last_transcription_confidence" not in app.session_state:
        app.session_state["last_transcription_confidence"] = 0.0
    if "last_transcription_duration" not in app.session_state:
        app.session_state["last_transcription_duration"] = 0.0


def set_offline_mode(app: AppTest, value: bool) -> None:
    for toggle in app.toggle:
        if toggle.label == "Offline Mode":
            toggle.set_value(value).run()
            return


def apply_transcription(
    app: AppTest, transcript: str, confidence: float, duration: float, base_fail: int
) -> Dict[str, float]:
    if transcript and "Transcription unavailable" not in transcript:
        note = app.session_state["draft_note"]
        app.session_state["draft_note"] = f"{note}\n{transcript}".strip() if note else transcript
        totals = app.session_state["ai_latency_totals"]
        counts = app.session_state["ai_latency_counts"]
        totals["transcribe"] += duration
        counts["transcribe"] += 1
        app.session_state["last_transcription_confidence"] = confidence
        app.session_state["last_transcription_duration"] = duration
        app.session_state["raw_transcript"] = transcript
    else:
        app.session_state["ai_fail_count"] = base_fail + 1
        app.session_state["last_transcription_confidence"] = 0.0
        app.session_state["last_transcription_duration"] = duration
    return {
        "latency_s": round(duration, 2),
        "confidence": round(app.session_state["last_transcription_confidence"], 2),
        "ai_fail": "Yes" if app.session_state["ai_fail_count"] > base_fail else "No",
    }


def run_clip(app: AppTest, label: str, offline: bool = False) -> Dict[str, float]:
    ensure_session_defaults(app)
    base_fail = app.session_state["ai_fail_count"]

    if offline:
        set_offline_mode(app, True)

    transcript, confidence, duration = ai_parser.transcribe_audio(str(DUMMY_CLIP))
    result = apply_transcription(app, transcript, confidence, duration, base_fail)

    if offline:
        set_offline_mode(app, False)
        for button in app.button:
            if button.label == "Flush Offline Cache":
                button.click().run()
                break
        time.sleep(0.5)

    result["label"] = label
    return result


STUB_TRANSCRIPTS = [
    ("Transcript: quiet office success", 0.94, 14.2),
    ("Transcript: typical yard noise", 0.88, 22.1),
    ("", 0.00, 18.3),
    ("", 0.00, 10.5),
]


@patch("ai_parser.transcribe_audio", side_effect=STUB_TRANSCRIPTS)
def run_suite(mock_transcribe) -> List[Dict[str, float]]:
    previous_cwd = os.getcwd()
    os.chdir(APP_DIR)
    try:
        DUMMY_CLIP.parent.mkdir(parents=True, exist_ok=True)
        DUMMY_CLIP.write_bytes(b"\x00\x00")

        app = AppTest.from_file("app.py")
        app.run(timeout=5)

        results = [
            run_clip(app, "Quiet office"),
            run_clip(app, "Typical yard noise"),
            run_clip(app, "Background chatter"),
            run_clip(app, "Offline mode", offline=True),
        ]
        return results
    finally:
        os.chdir(previous_cwd)


if __name__ == "__main__":
    rows = run_suite()
    (ROOT_DIR / "qa" / "last_whisper_accuracy.json").write_text(json.dumps(rows, indent=2))
    print("\n=== FieldOS Whisper Accuracy & Fallback Report ===")
    print(f"{'Clip #':<6} {'Scenario':<22} {'Latency(s)':<12} {'Conf':<6} {'AI Fail?':<8}")
    print("-" * 60)
    for idx, row in enumerate(rows, start=1):
        print(f"{idx:<6} {row['label']:<22} {row['latency_s']:<12} {row['confidence']:<6} {row['ai_fail']:<8}")
    print("-" * 60)
    confidences = [row["confidence"] for row in rows if row["ai_fail"] == "No"]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    print(f"Average confidence: {avg_conf:.2f}")
    print(f"Completed at: {datetime.now().isoformat(timespec='seconds')}")
    print("==================================================\n")
