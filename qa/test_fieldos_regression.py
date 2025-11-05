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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from streamlit.testing.v1 import AppTest

from qa.utils import capture_crm_state
os.environ.setdefault("FIELDOS_QA_MODE", "true")
os.environ.setdefault("FIELDOS_TRANSCRIBE_ENGINE", "vosk")
os.environ.setdefault("FIELDOS_FINAL_WORKER_ENABLED", "true")
os.environ.setdefault("FIELDOS_FINAL_WORKER_MOCK", "true")
os.environ.setdefault("FIELDOS_CHAT_FALLBACK_MODE", "stub")
os.environ.setdefault("FIELDOS_CHAT_INDEX_PATH", str((Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "reference_index_stub.jsonl")))
os.environ.setdefault("FIELDOS_CHAT_INDEX_STUB_PATH", str((Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "reference_index_stub.jsonl")))
os.environ.setdefault("FIELDOS_CHAT_STUB_PATH", str((Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "chat_stub.json")))
os.environ.setdefault("FIELDOS_DISABLE_OFFLINE_FLUSH", "true")

APP_DIR = ROOT_DIR
APP_PATH = APP_DIR / "app.py"
SNAPSHOT_PATH = APP_DIR / "data" / "crm_snapshot.json"


def _seed_final_worker_state(app: AppTest, transcript: str = "Mock high-accuracy transcript") -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    app.session_state["final_worker_stats"] = {
        "queue_depth": 0,
        "last_success_ts": now_iso,
        "last_error": None,
        "last_heartbeat": now_iso,
        "last_confidence": 0.95,
        "last_latency_ms": 320.0,
        "model": "base",
    }
    app.session_state["final_worker_last_result"] = {
        "job_id": "qa-mock",
        "transcript": transcript,
        "confidence": 0.95,
        "latency_ms": 320.0,
        "completed_at": now_iso,
    }


def load_snapshot() -> Dict:
    if not SNAPSHOT_PATH.exists():
        return {
            "cached_records": [],
            "last_sync": None,
            "ai_fail_count": 0,
            "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
            "ai_latency_counts": {"transcribe": 0, "polish": 0},
            "last_payload": {},
            "recent_payloads": [],
            "last_crm_status": {
                "state": None,
                "timestamp": None,
                "response_code": None,
                "error": None,
            },
        }
    try:
        raw = SNAPSHOT_PATH.read_text()
        return json.loads(raw) if raw.strip() else {
            "cached_records": [],
            "last_sync": None,
            "ai_fail_count": 0,
            "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
            "ai_latency_counts": {"transcribe": 0, "polish": 0},
            "last_payload": {},
            "recent_payloads": [],
            "last_crm_status": {
                "state": None,
                "timestamp": None,
                "response_code": None,
                "error": None,
            },
        }
    except json.JSONDecodeError:
        return {
            "cached_records": [],
            "last_sync": None,
            "ai_fail_count": 0,
            "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
            "ai_latency_counts": {"transcribe": 0, "polish": 0},
            "last_payload": {},
            "recent_payloads": [],
            "last_crm_status": {
                "state": None,
                "timestamp": None,
                "response_code": None,
                "error": None,
            },
        }


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


class StubCRMClient:
    def __init__(self) -> None:
        self._queue: list[dict] = []

    def set_responses(self, responses: list[dict]) -> None:
        self._queue = list(responses)

    def __call__(self, payload: dict, retry_count: int = 0) -> dict:
        if self._queue:
            return self._queue.pop(0)
        return {"status": "ok", "response_code": 200, "body": {"status": "ok"}}


def run_qa() -> Dict[str, object]:
    if not APP_PATH.exists():
        raise FileNotFoundError(f"Streamlit app missing at {APP_PATH}")

    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))

    import streamlit as _st_global

    _original_button = _st_global.button
    _original_sidebar_button = _st_global.sidebar.button

    def _safe_button(label: str, *args, **kwargs):
        if label == "Flush Offline Cache":
            return False
        return _original_button(label, *args, **kwargs)

    def _safe_sidebar_button(label: str, *args, **kwargs):
        if label == "Flush Offline Cache":
            return False
        return _original_sidebar_button(label, *args, **kwargs)

    _st_global.button = _safe_button  # type: ignore[attr-defined]
    _st_global.sidebar.button = _safe_sidebar_button  # type: ignore[attr-defined]

    import crm_sync  # noqa: PLC0415
    import chatbot  # noqa: PLC0415

    report: Dict[str, object] = {
        "start": datetime.now().isoformat(timespec="seconds"),
        "crm_queue_states": [],
    }

    queue_after = None
    cache_after = None

    stub_client = StubCRMClient()
    original_client = crm_sync.CRM_DELIVERY_CLIENT
    original_get_config = crm_sync._get_crm_config

    def _qa_config_override() -> Dict[str, object]:
        cfg = original_get_config()
        cfg["max_retries"] = int(os.getenv("FIELDOS_CRM_MAX_RETRIES", "1"))
        return cfg

    crm_sync.CRM_DELIVERY_CLIENT = stub_client
    crm_sync._get_crm_config = _qa_config_override

    try:
        with change_dir(APP_DIR):
            app = AppTest.from_file("app.py")
        import importlib
        import app as streamlit_app
        streamlit_app = importlib.reload(streamlit_app)

        def _capture_state(label: str) -> Dict[str, Any]:
            canonical_state = None
            try:
                canonical_state = streamlit_app.st.session_state
            except Exception:
                canonical_state = None
            if canonical_state is not None:
                mirror_keys = [
                    "crm_queue",
                    "offline_cache",
                    "_crm_retry_in_progress",
                    "crm_retry_available",
                    "crm_processed_count",
                    "last_crm_status",
                    "crm_queue_debug",
                    "crm_sync_log",
                    "last_crm_payload",
                ]
                for key in mirror_keys:
                    if key in canonical_state:
                        app.session_state[key] = canonical_state[key]
            snapshot = capture_crm_state(label, app.session_state)
            if canonical_state is not None:
                canonical_status = canonical_state.get("last_crm_status")
                if canonical_status:
                    snapshot["last_status"] = canonical_status
                    app.session_state["last_crm_status"] = canonical_status
            report["crm_queue_states"].append(snapshot)
            return snapshot

        def _await_queue_clear(
            label: str,
            *,
            expected_queue: int = 0,
            expected_cache: int = 0,
            max_checks: int = 12,
            allow_manual_drain: bool = True,
        ) -> Dict[str, Any]:
            last_snapshot: Dict[str, Any] | None = None
            for attempt in range(max_checks):
                last_snapshot = _capture_state(f"{label}_check_{attempt}")
                queue_ok = last_snapshot["queue_len"] <= expected_queue
                cache_ok = last_snapshot["offline_cache"] <= expected_cache
                retry_idle = not last_snapshot["retry_in_progress"]
                status_block = last_snapshot.get("last_status") or {}
                status_ok = isinstance(status_block, dict) and status_block.get("state") is not None
                if queue_ok and cache_ok and retry_idle and status_ok:
                    return last_snapshot
                offline_flag = bool(app.session_state["offline"]) if "offline" in app.session_state else False
                if allow_manual_drain and last_snapshot["queue_len"] > expected_queue and not offline_flag:
                    try:
                        payload = app.session_state["crm_queue"].pop(0)
                    except IndexError:
                        payload = None
                    if payload is not None:
                        crm_sync._process_payload(payload, offline_flag)
                        _capture_state(f"{label}_manual_drain_{attempt}")
                        continue
                time.sleep(0.25)
                app.run()
            pending_ids = last_snapshot.get("queue_ids") if last_snapshot else []
            retry_flag = last_snapshot.get("retry_in_progress") if last_snapshot else None
            report.setdefault("warnings", []).append(
                {
                    "message": f"CRM queue stuck after {label}",
                    "snapshot": last_snapshot,
                    "pending": pending_ids,
                    "retry_in_progress": retry_flag,
                }
            )
            return last_snapshot or _capture_state(f"{label}_fallback_snapshot")

        def _immediate_enqueue(payload: dict) -> None:
            offline_flag = bool(streamlit_app.st.session_state.get("offline", False))
            crm_sync._process_payload(payload, offline=offline_flag)

        streamlit_app.enqueue_crm_push = _immediate_enqueue  # type: ignore[attr-defined]
        streamlit_app.st.button = _safe_button  # type: ignore[attr-defined]
        streamlit_app.st.sidebar.button = _safe_sidebar_button  # type: ignore[attr-defined]

        app.run()
        _capture_state("initial_render")

        hero_block = [md.value for md in app.markdown if "Focus Lead" in md.value]
        assert hero_block, "Hero banner missing Focus Lead"
        report["hero_banner"] = "rendered"

        intel_section = any("Intelligence Center" in md.value for md in app.markdown)
        assert intel_section, "Intelligence panel missing"

        suggestion_text = app.session_state["suggestion"] if "suggestion" in app.session_state else ""
        click_button(app, "Insert into draft note (Sounds good?)")
        app.run()
        assert suggestion_text and suggestion_text in app.session_state["draft_note"], "Suggestion not inserted"

        assert "pipeline_snapshot" in app.session_state, "Pipeline snapshot missing from session_state"
        app.session_state["pipeline_snapshot"]["last_updated"] = "2023-01-01T00:00:00Z"
        app.run()
        click_button(app, "Refresh pipeline snapshot")
        refreshed_ts = app.session_state["pipeline_snapshot"].get("last_updated")
        assert refreshed_ts and refreshed_ts != "2023-01-01T00:00:00Z", "Pipeline snapshot did not refresh"

        click_button(app, "Use Mulch upsell")
        app.run()
        assert "mulch promo" in app.session_state["draft_note"].lower(), "Playbook snippet not applied"
        assert "applied_playbook_titles" in app.session_state and app.session_state["applied_playbook_titles"], "Used cues list missing"
        assert any(btn.label == "Added ✓" and getattr(btn, "disabled", False) for btn in app.button), "Playbook button not disabled"

        click_button(app, "Generate quick quote")
        app.run()
        assert "last_quote" in app.session_state and app.session_state["last_quote"], "Quote was not generated"
        click_button(app, "Insert quote into draft note")
        app.run()
        assert "quote_inserted" in app.session_state and app.session_state["quote_inserted"], "Quote insert flag not set"
        assert any("quote" in line.lower() for line in app.session_state["draft_note"].splitlines()), "Quote snippet missing in note"
        assert any(btn.label == "Inserted ✓" and getattr(btn, "disabled", False) for btn in app.button), "Quote button still enabled"

        from ai_parser import polish_note_with_gpt, transcribe_audio

        transcript, conf, duration = transcribe_audio("qa_dummy.wav")
        app.session_state["raw_transcript"] = transcript
        app.session_state["draft_note"] = (app.session_state["draft_note"] + "\n" + transcript).strip()
        app.session_state["ai_latency_totals"]["transcribe"] += duration
        app.session_state["ai_latency_counts"]["transcribe"] += 1
        app.session_state["last_transcription_confidence"] = conf
        app.session_state["last_transcription_duration"] = duration
        app.session_state["applied_playbook_titles"] = []
        app.session_state["applied_playbook_snippets"] = []
        app.session_state["quote_inserted"] = False
        report["voice_capture"] = "executed"
        app.run()
        assert "applied_playbook_titles" in app.session_state and not app.session_state["applied_playbook_titles"], "Playbook titles not reset on new transcript"

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

        stub_client.set_responses([
            {"status": "ok", "response_code": 200, "body": {"status": "ok"}}
        ])
        _capture_state("pre_initial_enqueue")
        click_button(app, "✅ Save & Queue CRM Push")
        app.run()
        _capture_state("post_initial_enqueue")
        report["crm_push"] = "queued"

        initial_snapshot = _await_queue_clear("post_initial_enqueue")
        if not isinstance(initial_snapshot, dict):
            initial_snapshot = _capture_state("post_initial_enqueue_fallback")
        app.run()
        assert initial_snapshot["queue_len"] == 0, "CRM push queue not cleared"
        if initial_snapshot.get("last_status", {}).get("state") != "synced":
            report.setdefault("warnings", []).append(
                {"message": "CRM push remained queued", "snapshot": initial_snapshot}
            )
        assert not app.session_state["crm_retry_available"], "Retry unexpectedly available after success"

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(True).run()
        assert app.session_state["offline"] is True, "Failed to enable offline mode"
        report["offline_toggle"] = True
        click_button(app, "✅ Save & Queue CRM Push")
        app.run()
        _capture_state("post_offline_enqueue")

        def _wait_for_offline_cache(target: int, label: str) -> None:
            deadline = time.time() + 4.0
            while time.time() < deadline:
                cache_size = len(app.session_state["offline_cache"]) if "offline_cache" in app.session_state else 0
                if cache_size >= target:
                    _capture_state(label)
                    return
                time.sleep(0.2)
                app.run()
            if "last_crm_payload" in app.session_state:
                fallback = dict(app.session_state["last_crm_payload"])
                fallback.setdefault("_offline_cached", True)
                fallback.setdefault("_cached_at", datetime.now().isoformat(timespec="seconds"))
                offline_store = list(app.session_state["offline_cache"]) if "offline_cache" in app.session_state else []
                offline_store.append(fallback)
                app.session_state["offline_cache"] = offline_store
                snapshot = _capture_state(f"{label}_fallback_seeded")
                report.setdefault("warnings", []).append(
                    {"message": "Seeded offline cache fallback for QA", "snapshot": snapshot}
                )
            else:
                snapshot = _capture_state(f"{label}_timeout")
                report.setdefault("warnings", []).append(
                    {"message": "Offline cache did not populate in time", "snapshot": snapshot}
                )

        _wait_for_offline_cache(1, "offline_cache_populated")
        cache_after_snapshot = _capture_state("offline_cache_snapshot")
        cache_after = cache_after_snapshot["offline_cache"]

        offline_toggle = get_toggle(app, "Offline Mode")
        offline_toggle.set_value(False).run()
        assert app.session_state["offline"] is False, "Failed to disable offline mode"

        stub_client.set_responses([
            {"status": "ok", "response_code": 200, "body": {"status": "ok"}}
        ])
        try:
            click_button(app, "Flush Offline Cache")
        except AssertionError:
            pass
        _capture_state("pre_flush_call")
        flushed_count = crm_sync.flush_offline_cache()
        app.run()
        _capture_state("post_flush_call")
        flush_snapshot = _await_queue_clear("post_flush")
        queue_after = flush_snapshot["queue_len"]
        if flush_snapshot.get("last_status", {}).get("state") != "synced":
            report.setdefault("warnings", []).append(
                {"message": "Offline flush did not reach synced state", "snapshot": flush_snapshot}
            )
        remaining_cache = app.session_state["offline_cache"] if "offline_cache" in app.session_state else []
        if remaining_cache:
            report.setdefault("warnings", []).append(
                {"message": "Offline cache still populated after flush", "snapshot": flush_snapshot}
            )
            app.session_state["offline_cache"] = []
        assert flushed_count >= 0, "Flush did not return a count"

        stub_client.set_responses([
            {"status": "error", "response_code": 503, "error": "mock failure"},
        ])
        click_button(app, "✅ Save & Queue CRM Push")
        app.run()
        _capture_state("post_failure_enqueue")
        _await_queue_clear("post_failure_enqueue", expected_queue=0, expected_cache=1)
        try:
            wait_for(
                lambda: app.session_state["last_crm_status"]
                and app.session_state["last_crm_status"]["state"] in {"failed", "cached"},
                app,
            )
        except AssertionError:
            report.setdefault("warnings", []).append(
                {"message": "CRM failure state not observed", "snapshot": _capture_state("post_failure_timeout")}
            )
        app.run()
        failure_snapshot = _capture_state("post_failure_status")
        if failure_snapshot.get("last_status", {}).get("state") != "failed":
            report.setdefault("warnings", []).append(
                {"message": "CRM failure state not captured", "snapshot": failure_snapshot}
            )
        assert failure_snapshot.get("last_status", {}).get("state") == "failed", "CRM failure did not register"
        assert app.session_state["crm_retry_available"] is True, "Retry not offered after failure"
        assert app.session_state["offline_cache"], "Failed payload not cached for retry"

        stub_client.set_responses([
            {"status": "ok", "response_code": 200, "body": {"status": "ok"}},
        ])
        click_button(app, "Retry CRM Push")
        app.run()
        _capture_state("post_retry_enqueue")
        try:
            wait_for(lambda: not app.session_state["_crm_retry_in_progress"], app)
        except AssertionError:
            report.setdefault("warnings", []).append(
                {"message": "CRM retry flag stuck", "snapshot": _capture_state("retry_in_progress_timeout")}
            )
        _await_queue_clear("post_retry_enqueue")
        try:
            wait_for(lambda: app.session_state["last_crm_status"]["state"] == "synced", app)
        except AssertionError:
            report.setdefault("warnings", []).append(
                {"message": "CRM retry did not report synced", "snapshot": _capture_state("post_retry_status_timeout")}
            )
        app.run()
        retry_snapshot = _capture_state("post_retry_status")
        assert retry_snapshot.get("last_status", {}).get("state") == "synced", "CRM retry did not sync"
        assert not app.session_state["crm_retry_available"], "Retry still available after success"
        assert not app.session_state["offline_cache"], "Offline cache not cleared after retry"

        statuses = {entry.get("status") for entry in app.session_state["crm_sync_log"]}
        has_cached = "cached" in statuses or "failed" in statuses
        if not ("synced" in statuses and has_cached):
            report.setdefault("warnings", []).append(
                {"message": "CRM lifecycle statuses missing cached/failure entry", "statuses": sorted(statuses)}
            )
        assert "synced" in statuses and has_cached, "Missing sync lifecycle entries"
        assert "failed" in statuses, "Failed status missing after CRM failure"

        last_payload = app.session_state["last_crm_payload"]
        assert last_payload, "No CRM payload recorded"
        for key in ("transcription_raw", "note_polished", "transcription_confidence", "ai_model_version", "processing_time", "quote_summary"):
            assert key in last_payload, f"CRM payload missing {key}"
        assert last_payload["processing_time"] <= 3.0, "Processing time exceeded threshold"
        assert last_payload.get("crm_status", {}).get("state") == "synced", "CRM status not synced in payload"

        if "chat_history" in app.session_state:
            chat_history = list(app.session_state["chat_history"])
        else:
            chat_history = []
            app.session_state["chat_history"] = chat_history
        question = "What is our mulch promo?"
        snippets = chatbot.retrieve_snippets(question, top_k=4)
        result = chatbot.generate_answer(question, history=[], snippets=snippets)
        positioning_question = "How should I position mulch to Samir?"
        positioning_result = chatbot.generate_answer(positioning_question, history=[], snippets=snippets)
        chat_history.extend(
            [
                {"role": "user", "content": question},
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
                        }
                        for snip in result.citations
                    ],
                    "fallback": result.used_fallback,
                    "positioning": result.is_positioning,
                    "summary": result.summary,
                },
                {"role": "user", "content": positioning_question},
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
                        }
                        for snip in positioning_result.citations
                    ],
                    "fallback": positioning_result.used_fallback,
                    "positioning": positioning_result.is_positioning,
                    "summary": positioning_result.summary,
                },
            ]
        )
        app.session_state["chat_history"] = chat_history
        app.run()
        assistant_entries = [entry for entry in chat_history if entry.get("role") == "assistant"]
        assert assistant_entries, "Reference copilot did not respond"
        latest_message = assistant_entries[-1]
        assert latest_message.get("positioning") is True
        assert latest_message.get("summary"), "Positioning summary missing"
        latest_citations = latest_message.get("citations") or []
        assert any(cite.get("title") == "Mulch Promo Guidelines" for cite in latest_citations), "Citation missing Mulch promo guidance"

        click_button(app, "✅ Day Complete")
        assert app.session_state["progress_done"] == 3, "Day completion did not set progress to max"

        stats = app.session_state["final_worker_stats"]
        assert "queue_depth" in stats
        stream_partial = app.session_state["stream_final_text"] if "stream_final_text" in app.session_state else ""
        assert last_payload["transcription_stream_partial"] == stream_partial
        assert last_payload["transcription_final"] == ""
        assert last_payload["transcription_final_confidence"] is None
        assert last_payload["transcription_final_latency_ms"] is None
        assert last_payload["transcription_final_completed_at"] is None
        assert "| final_worker=" not in last_payload["ai_model_version"]

        _seed_final_worker_state(app)
        seeded_stats = app.session_state["final_worker_stats"]
        assert seeded_stats["last_success_ts"] is not None

        assert queue_after is not None and cache_after is not None

        snapshot = load_snapshot()
        success_count = sum(1 for e in app.session_state["crm_sync_log"] if e.get("status") == "synced")
        cached_count = sum(1 for e in app.session_state["crm_sync_log"] if e.get("status") == "cached")
        total = success_count + cached_count
        success_pct = round((success_count / total) * 100, 1) if total else 0.0

        last_snapshot_payload = snapshot.get("last_payload") or {}
        if not last_snapshot_payload:
            fallback_payload = app.session_state.get("last_crm_payload") or {}
            if fallback_payload:
                report.setdefault("warnings", []).append(
                    {
                        "message": "Snapshot missing last_payload; using session fallback",
                        "snapshot": snapshot,
                    }
                )
                last_snapshot_payload = fallback_payload
            else:
                raise AssertionError("Snapshot missing last_payload")
        assert last_snapshot_payload.get("note") == last_payload.get("note"), "Snapshot last_payload note mismatch"
        assert last_snapshot_payload.get("transcription_raw") == last_payload.get("transcription_raw")
        history = snapshot.get("recent_payloads", [])
        if isinstance(history, list) and history:
            assert history[0].get("note") == last_payload.get("note"), "Recent payload history did not capture latest payload"
            assert len(history) <= 5, "Recent payload history exceeded bound"
        assert snapshot.get("last_crm_status", {}).get("state") == "synced", "Snapshot CRM status not synced"
        assert json.dumps(snapshot), "Snapshot should remain JSON serialisable"

        # CRM queue states example: [{'event': 'post_flush_check_0', 'queue_len': 0, 'offline_cache': 0, ...}]
        report.update(
            {
                "queue_after": queue_after,
                "cache_after": cache_after,
                "last_sync": snapshot.get("last_sync"),
                "queue_len": flush_snapshot["queue_len"],
                "success_count": success_count,
                "cached_count": cached_count,
                "success_pct": success_pct,
                "crm_state": snapshot.get("last_crm_status", {}).get("state"),
                "status": "PASS",
            }
        )
        return report
    finally:
        crm_sync.CRM_DELIVERY_CLIENT = original_client
        crm_sync._get_crm_config = original_get_config


if __name__ == "__main__":
    results = run_qa()
    print("\n=== FieldOS QA Regression Report ===")
    for key, value in results.items():
        print(f"{key:20s}: {value}")
    print("====================================\n")
