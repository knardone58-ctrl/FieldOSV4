"""
FieldOS V4.2 – Automated QA Regression
--------------------------------------
Runs the AI-enabled 60-second workflow, validates CRM payload schema,
and checks telemetry counters. Designed for deterministic runs when
FIELDOS_QA_MODE=true.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict

from streamlit.testing.v1 import AppTest

os.environ.setdefault("FIELDOS_QA_MODE", "true")
os.environ.setdefault("FIELDOS_TRANSCRIBE_ENGINE", "vosk")

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR
APP_PATH = APP_DIR / "app.py"
SNAPSHOT_PATH = APP_DIR / "data" / "crm_snapshot.json"


def load_snapshot() -> Dict:
    if not SNAPSHOT_PATH.exists():
        return {
            "cached_records": [],
            "last_sync": None,
            "ai_fail_count": 0,
            "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
            "ai_latency_counts": {"transcribe": 0, "polish": 0},
        }
    return json.loads(SNAPSHOT_PATH.read_text())


@contextmanager
def change_dir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def click_button(app: AppTest, label: str) -> None:
    for button in app.button:
        if button.label == label:
            button.click().run()
            return
    raise AssertionError(f"Button {label!r} not found. Available: {[b.label for b in app.button]}")


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


def run_qa() -> Dict[str, object]:
    if not APP_PATH.exists():
        raise FileNotFoundError(f"Streamlit app missing at {APP_PATH}")

    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    report: Dict[str, object] = {"start": datetime.now().isoformat(timespec="seconds")}

    queue_after = None
    cache_after = None

    with change_dir(APP_DIR):
        app = AppTest.from_file("app.py")
        app.run()

        hero_block = [md.value for md in app.markdown[:2]]
        assert any("Focus Lead" in block for block in hero_block), "Hero banner missing Focus Lead"
        report["hero_banner"] = "rendered"

        suggestion_text = app.session_state["suggestion"] if "suggestion" in app.session_state else ""
        click_button(app, "Insert into draft note (Sounds good?)")
        app.run()
        assert suggestion_text and suggestion_text in app.session_state["draft_note"], "Suggestion not inserted"

        from ai_parser import polish_note_with_gpt, transcribe_audio

        transcript, conf, duration = transcribe_audio("qa_dummy.wav")
        app.session_state["raw_transcript"] = transcript
        app.session_state["draft_note"] = (app.session_state["draft_note"] + "\n" + transcript).strip()
        app.session_state["ai_latency_totals"]["transcribe"] += duration
        app.session_state["ai_latency_counts"]["transcribe"] += 1
        app.session_state["last_transcription_confidence"] = conf
        app.session_state["last_transcription_duration"] = duration
        report["voice_capture"] = "executed"
        app.run()

        polished, polish_duration = polish_note_with_gpt(
            app.session_state["draft_note"],
            {
                "account": "QA Account",
                "service": "QA Service",
                "contact": "QA Contact",
            },
        )
        assert polished, "Polish returned empty result in QA mode"
        app.session_state["draft_note"] = polished
        app.session_state["last_polish_duration"] = polish_duration
        app.session_state["ai_latency_totals"]["polish"] += polish_duration
        app.session_state["ai_latency_counts"]["polish"] += 1
        app.run()

        click_button(app, "✅ Save & Queue CRM Push")
        queue_len = len(app.session_state["crm_queue"])
        assert queue_len >= 1, f"Unexpected queue length after queue action: {queue_len}"
        report["crm_push"] = "queued"

        wait_for(lambda: not app.session_state["crm_queue"], app)

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(True).run()
        assert app.session_state["offline"] is True, "Failed to enable offline mode"
        report["offline_toggle"] = True
        click_button(app, "✅ Save & Queue CRM Push")

        wait_for(lambda: len(app.session_state["offline_cache"]) > 0, app)
        cache_after = len(app.session_state["offline_cache"])

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(False).run()
        assert app.session_state["offline"] is False, "Failed to disable offline mode"

        click_button(app, "Flush Offline Cache")

        wait_for(lambda: not app.session_state["crm_queue"], app)
        queue_after = len(app.session_state["crm_queue"])

        statuses = {entry.get("status") for entry in app.session_state["crm_sync_log"]}
        assert {"synced", "cached"}.issubset(statuses), "Missing sync lifecycle entries"

        last_payload = next((entry["payload"] for entry in reversed(app.session_state["crm_sync_log"]) if entry.get("payload")), None)
        assert last_payload, "No CRM payload recorded"
        for key in ("transcription_raw", "note_polished", "transcription_confidence", "ai_model_version", "processing_time"):
            assert key in last_payload, f"CRM payload missing {key}"
        assert last_payload["processing_time"] <= 3.0, "Processing time exceeded threshold"

        click_button(app, "✅ Day Complete")
        assert app.session_state["progress_done"] == 3, "Day completion did not set progress to max"

    assert queue_after is not None and cache_after is not None

    snapshot = load_snapshot()
    success_count = sum(1 for e in app.session_state["crm_sync_log"] if e.get("status") == "synced")
    cached_count = sum(1 for e in app.session_state["crm_sync_log"] if e.get("status") == "cached")
    total = success_count + cached_count
    success_pct = round((success_count / total) * 100, 1) if total else 0.0

    report.update(
        {
            "queue_after": queue_after,
            "cache_after": cache_after,
            "last_sync": snapshot.get("last_sync"),
            "queue_len": len(app.session_state["crm_queue"]),
            "success_count": success_count,
            "cached_count": cached_count,
            "success_pct": success_pct,
            "status": "PASS",
        }
    )
    return report


if __name__ == "__main__":
    results = run_qa()
    print("\n=== FieldOS QA Regression Report ===")
    for key, value in results.items():
        print(f"{key:20s}: {value}")
    print("====================================\n")
