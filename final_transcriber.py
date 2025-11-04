"""
Final transcription worker harness using faster-whisper.

The worker module is Streamlit-free so that it can be reused from scripts,
unit tests, or a future external service. Callers must guard worker startup
inside ``if __name__ == "__main__"`` blocks when launching from scripts to
avoid multiprocessing spawn loops on macOS/Windows.

Queues are created via ``multiprocessing.Manager`` to simplify sharing between
the app process and the worker; they are explicitly closed during shutdown to
avoid leaked semaphores. Consumers should treat ``queue_depth`` values as
app-provided (e.g. from ``st.session_state``) because ``Queue.qsize()`` is not
reliable on macOS.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

STOP_SENTINEL: Dict[str, str] = {"type": "stop"}


@dataclass
class WorkerConfig:
    """Runtime configuration for the transcription worker."""

    enabled: bool
    mock: bool
    qa_mode: bool
    model: str
    device: str
    compute_type: str
    beam_size: int


@dataclass
class WorkerHandle:
    """Handle pointing at the running worker process (or mock)."""

    config: WorkerConfig
    process: Optional[mp.Process]
    job_queue: Optional[Any]
    result_queue: Optional[Any]
    manager: Optional[Any]
    last_heartbeat: Optional[float] = None
    last_success_ts: Optional[float] = None
    last_error: Optional[str] = None
    _mock_outbox: List[Dict[str, Any]] = field(default_factory=list)


WorkerResult = Dict[str, Any]
ResultCallback = Optional[Callable[[WorkerResult], None]]
ErrorCallback = Optional[Callable[[str, Dict[str, Any]], None]]


def start_worker(config: WorkerConfig, handle: Optional[WorkerHandle] = None) -> Optional[WorkerHandle]:
    """
    Start (or reuse) a transcription worker.

    Returns ``None`` when the worker should be disabled (e.g. QA mode or flag off).
    """
    if not config.enabled or config.qa_mode:
        shutdown_worker(handle)
        return None

    if config.mock:
        if handle and handle.config.mock:
            handle.config = config
            return handle
        shutdown_worker(handle)
        return WorkerHandle(
            config=config,
            process=None,
            job_queue=None,
            result_queue=None,
            manager=None,
        )

    ctx = mp.get_context("spawn")
    if handle and handle.process is not None:
        if handle.process.is_alive():
            handle.config = config
            return handle
        shutdown_worker(handle)

    manager = ctx.Manager()
    job_queue = manager.Queue()
    result_queue = manager.Queue()
    proc = ctx.Process(
        target=_worker_loop,
        args=(
            {
                "model": config.model,
                "device": config.device,
                "compute_type": config.compute_type,
                "beam_size": config.beam_size,
            },
            job_queue,
            result_queue,
        ),
        daemon=True,
    )
    proc.start()
    new_handle = WorkerHandle(
        config=config,
        process=proc,
        job_queue=job_queue,
        result_queue=result_queue,
        manager=manager,
    )
    return new_handle


def submit_job(handle: WorkerHandle, clip_path: Path, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Queue an audio clip for transcription."""
    if handle is None:
        raise RuntimeError("Transcription worker is not running.")
    job_id = uuid.uuid4().hex[:8]
    payload = {
        "job_id": job_id,
        "clip_path": str(Path(clip_path)),
        "metadata": metadata or {},
        "submitted_at": time.time(),
    }
    if handle.config.mock:
        result = {
            "type": "result",
            "job_id": job_id,
            "clip_path": payload["clip_path"],
            "metadata": payload["metadata"],
            "transcript": "QA transcript: mock worker output.",
            "confidence": 0.99,
            "duration_s": 1.2,
            "latency_ms": 0.0,
            "error": None,
            "pid": None,
        }
        handle._mock_outbox.append(result)
        handle.last_success_ts = time.time()
        handle.last_error = None
    else:
        if handle.job_queue is None:
            raise RuntimeError("Worker job queue is unavailable.")
        handle.job_queue.put(payload)
    return job_id


def poll_results(
    handle: Optional[WorkerHandle],
    *,
    on_result: ResultCallback = None,
    on_error: ErrorCallback = None,
) -> List[WorkerResult]:
    """Drain any available worker results and update handle metadata."""
    if handle is None:
        return []

    results: List[WorkerResult] = []
    if handle.config.mock:
        results.extend(handle._mock_outbox)
        handle._mock_outbox.clear()
        if results:
            handle.last_success_ts = time.time()
        return results

    if handle.result_queue is None:
        return results

    while True:
        try:
            message = handle.result_queue.get_nowait()
        except queue.Empty:
            break

        mtype = message.get("type")
        if mtype == "heartbeat":
            handle.last_heartbeat = message.get("ts")
            continue

        if mtype == "result":
            results.append(message)
            error_text = message.get("error")
            if error_text:
                handle.last_error = error_text
                if on_error:
                    on_error(error_text, message)
            else:
                handle.last_error = None
                handle.last_success_ts = time.time()
                if on_result:
                    on_result(message)
            continue

        if mtype == "worker_error":
            handle.last_error = message.get("error")
            if on_error:
                on_error(handle.last_error, message)
            continue

        if mtype == "log":
            # Logs are informational; ignore for now.
            continue

    return results


def collect_stats(handle: Optional[WorkerHandle], pending_jobs_count: int = 0) -> Dict[str, Optional[Any]]:
    """Return lightweight telemetry for UI/ops logging."""
    if handle is None:
        return {
            "queue_depth": 0,
            "last_success_ts": None,
            "last_error": None,
            "last_heartbeat": None,
        }
    return {
        "queue_depth": pending_jobs_count,
        "last_success_ts": handle.last_success_ts,
        "last_error": handle.last_error,
        "last_heartbeat": handle.last_heartbeat,
    }


def shutdown_worker(handle: Optional[WorkerHandle]) -> None:
    """Stop the worker process and release queue resources."""
    if handle is None:
        return

    if handle.config.mock:
        handle._mock_outbox.clear()
        return

    if handle.job_queue is not None:
        try:
            handle.job_queue.put_nowait(STOP_SENTINEL)
        except Exception:
            pass
    if handle.process is not None:
        handle.process.join(timeout=2.0)
        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=1.0)

    _cleanup_handle_resources(handle)


def _cleanup_handle_resources(handle: WorkerHandle) -> None:
    """Close queues and shut down the manager to prevent leaked semaphores."""
    for q in (handle.job_queue, handle.result_queue):
        if q is None:
            continue
        close = getattr(q, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        join_thread = getattr(q, "join_thread", None)
        if callable(join_thread):
            try:
                join_thread()
            except Exception:
                pass
    if handle.manager is not None:
        try:
            handle.manager.shutdown()
        except Exception:
            pass
    handle.job_queue = None
    handle.result_queue = None
    handle.manager = None
    handle.process = None


def _worker_loop(config: Dict[str, Any], job_queue: Any, result_queue: Any) -> None:
    """Run inside the worker process."""
    pid = mp.current_process().pid
    result_queue.put({"type": "log", "msg": f"Worker started (PID={pid})"})

    model, model_error = _load_model(config, result_queue)

    while True:
        try:
            job = job_queue.get(timeout=0.5)
        except queue.Empty:
            result_queue.put({"type": "heartbeat", "ts": time.time(), "pid": pid})
            continue

        if job == STOP_SENTINEL:
            break

        job_id = job["job_id"]
        clip_path = job["clip_path"]
        metadata = job.get("metadata", {})

        start = time.perf_counter()
        transcript = ""
        confidence = 0.0
        duration_s = 0.0
        error_text: Optional[str] = None

        if model is None:
            error_text = model_error or "faster-whisper model unavailable."
        else:
            try:
                segments, info = model.transcribe(
                    clip_path,
                    beam_size=config["beam_size"],
                    language="en",
                )
                collected_segments = list(segments)
                transcript = " ".join(seg.text.strip() for seg in collected_segments if getattr(seg, "text", None))
                scores = [
                    seg.avg_logprob
                    for seg in collected_segments
                    if getattr(seg, "avg_logprob", None) is not None
                ]
                if scores:
                    confidence = float(sum(scores) / len(scores))
                duration_s = float(getattr(info, "duration", 0.0) or 0.0)
            except Exception as exc:  # pragma: no cover - unexpected runtime failure
                error_text = f"Transcription failed: {exc}"

        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        result_queue.put(
            {
                "type": "result",
                "job_id": job_id,
                "clip_path": clip_path,
                "metadata": metadata,
                "transcript": transcript,
                "confidence": confidence,
                "duration_s": duration_s,
                "latency_ms": latency_ms,
                "error": error_text,
                "pid": pid,
            }
        )

        if error_text is not None and model is not None:
            result_queue.put({"type": "worker_error", "error": error_text, "pid": pid})

    result_queue.put({"type": "log", "msg": f"Worker stopping (PID={pid})"})


def _load_model(config: Dict[str, Any], result_queue: Any):
    """Load faster-whisper model; returns (model, error_message)."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency missing
        error_text = f"faster-whisper import failed: {exc}"
        result_queue.put({"type": "worker_error", "error": error_text})
        return None, error_text

    try:
        model = WhisperModel(
            config["model"],
            device=config["device"],
            compute_type=config["compute_type"],
        )
        return model, None
    except Exception as exc:  # pragma: no cover - model download/init failure
        error_text = f"Failed to load model '{config['model']}': {exc}"
        result_queue.put({"type": "worker_error", "error": error_text})
        return None, error_text


__all__ = [
    "WorkerConfig",
    "WorkerHandle",
    "WorkerResult",
    "start_worker",
    "submit_job",
    "poll_results",
    "collect_stats",
    "shutdown_worker",
]
