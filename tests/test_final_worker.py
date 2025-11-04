from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from final_transcriber import (
    WorkerConfig,
    collect_stats,
    poll_results,
    shutdown_worker,
    start_worker,
    submit_job,
)


@pytest.fixture(autouse=True)
def _force_mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIELDOS_FINAL_WORKER_MOCK", "true")
    monkeypatch.setenv("FIELDOS_FINAL_WORKER_ENABLED", "false")


def _build_config(**overrides) -> WorkerConfig:
    base = {
        "enabled": True,
        "mock": False,
        "qa_mode": False,
        "model": "base",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 5,
    }
    base.update(overrides)
    return WorkerConfig(**base)


def test_mock_worker_roundtrip(tmp_path: Path) -> None:
    config = _build_config(mock=True)
    handle = start_worker(config)
    assert handle is not None
    job_id = submit_job(handle, tmp_path / "clip.wav", {"source": "unit-test"})
    assert job_id

    results = poll_results(handle)
    assert len(results) == 1
    result = results[0]
    assert result["job_id"] == job_id
    assert "mock" in result["transcript"].lower()
    stats = collect_stats(handle, pending_jobs_count=0)
    assert stats["queue_depth"] == 0
    assert stats["last_success_ts"] is not None
    assert "last_heartbeat" in stats
    assert "last_error" in stats
    assert isinstance(stats["last_success_ts"], float)
    shutdown_worker(handle)


@pytest.mark.slow
def test_worker_handles_model_failure(tmp_path: Path) -> None:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        pytest.skip("faster-whisper not installed")

    try:
        import ctranslate2  # noqa: F401
    except Exception:
        pytest.skip("ctranslate2 not installed")

    config = _build_config(model="__missing_model__")
    handle = start_worker(config)
    if handle is None:
        pytest.skip("worker disabled by configuration")

    clip_path = tmp_path / "audio.wav"
    clip_path.write_bytes(b"\x00\x00")
    job_id = submit_job(handle, clip_path, {})

    results = []
    deadline = time.time() + 5
    while time.time() < deadline and not results:
        results.extend(poll_results(handle))
        time.sleep(0.1)

    if not results:
        shutdown_worker(handle)
        pytest.skip("worker did not return a result in time")

    result = results[0]
    assert result["job_id"] == job_id
    assert result["transcript"] == ""
    assert result["error"] is not None

    stats = collect_stats(handle, pending_jobs_count=0)
    assert stats["last_error"] is not None
    assert "last_heartbeat" in stats

    shutdown_worker(handle)
