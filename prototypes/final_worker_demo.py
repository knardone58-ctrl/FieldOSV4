"""
Prototype: validate faster-whisper worker lifecycle within Streamlit.

Key goals (see docs/faster_whisper_checklist.md):
 - Spawn exactly one worker process across reruns.
 - Preserve queue state while Streamlit reruns (no lost jobs).
 - Allow manual shutdown and confirm the PID is cleaned up.
"""

from __future__ import annotations

import os
import queue
import time
import uuid
import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import streamlit as st

@dataclass
class WorkerState:
    process: mp.Process
    pid: int
    job_queue: mp.Queue
    result_queue: mp.Queue
    started_at: float = field(default_factory=time.time)
    last_heartbeat: Optional[float] = None


def _worker_main(job_q: mp.Queue, result_q: mp.Queue) -> None:
    """Background worker loop; simulates transcription latency."""
    pid = os.getpid()
    result_q.put({"type": "log", "msg": f"Worker started (PID={pid})"})
    running = True
    while running:
        try:
            job = job_q.get(timeout=0.5)
        except queue.Empty:
            result_q.put({"type": "heartbeat", "pid": pid, "ts": time.time()})
            continue

        if job == "STOP":
            running = False
            result_q.put({"type": "log", "msg": f"Worker stopping (PID={pid})"})
            break

        job_id, payload = job
        start = time.perf_counter()
        # Simulate transcription latency
        time.sleep(0.35)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        result_q.put(
            {
                "type": "result",
                "job_id": job_id,
                "payload": payload,
                "transcript": payload.upper(),
                "confidence": 0.87,
                "duration_ms": duration_ms,
                "pid": pid,
            }
        )
    result_q.put({"type": "shutdown", "pid": pid, "ts": time.time()})


def _cleanup_worker_resources(worker: Optional["WorkerState"]) -> None:
    """Best-effort cleanup for queues/process artifacts."""
    if worker is None:
        return
    for q in (worker.job_queue, worker.result_queue):
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


def _get_or_start_worker() -> WorkerState:
    ctx = mp.get_context("spawn")
    state: Optional[WorkerState] = st.session_state.get("_final_worker_state")
    if state:
        if state.process.is_alive():
            return state
        st.session_state.setdefault("final_worker_logs", []).append(
            f"Detected dead worker (PID={state.pid}); restarting."
        )
        _cleanup_worker_resources(state)
        st.session_state.pop("_final_worker_state", None)
        st.session_state.pop("final_worker_jobs", None)
        st.session_state.pop("final_worker_results", None)
        st.session_state.pop("final_worker_logs", None)
        manager = st.session_state.pop("_final_worker_manager", None)
        if manager is not None:
            try:
                manager.shutdown()
            except Exception:
                pass

    manager = st.session_state.get("_final_worker_manager")
    if manager is None:
        manager = ctx.Manager()
        st.session_state["_final_worker_manager"] = manager

    job_q = manager.Queue()
    result_q = manager.Queue()
    proc = ctx.Process(target=_worker_main, args=(job_q, result_q), daemon=True)
    proc.start()
    worker_state = WorkerState(process=proc, pid=proc.pid, job_queue=job_q, result_queue=result_q)
    st.session_state["_final_worker_state"] = worker_state
    st.session_state.setdefault("final_worker_jobs", {})
    st.session_state.setdefault("final_worker_results", [])
    st.session_state.setdefault("final_worker_logs", [])
    return worker_state


def _enqueue_job(worker: WorkerState, text: str) -> str:
    job_id = uuid.uuid4().hex[:8]
    worker.job_queue.put((job_id, text))
    st.session_state["final_worker_jobs"][job_id] = {
        "text": text,
        "submitted_at": time.time(),
        "status": "queued",
    }
    return job_id


def _drain_results(worker: WorkerState) -> None:
    job_states: Dict[str, Dict] = st.session_state["final_worker_jobs"]
    results: List[Dict] = st.session_state["final_worker_results"]
    logs: List[str] = st.session_state["final_worker_logs"]

    while True:
        try:
            message = worker.result_queue.get_nowait()
        except queue.Empty:
            break

        mtype = message.get("type")
        if mtype == "result":
            job_id = message["job_id"]
            results.append(message)
            if job_id in job_states:
                job_states[job_id]["status"] = "completed"
                job_states[job_id]["completed_at"] = time.time()
                job_states[job_id]["duration_ms"] = message["duration_ms"]
        elif mtype == "log":
            logs.append(message["msg"])
        elif mtype == "heartbeat":
            worker.last_heartbeat = message["ts"]
        elif mtype == "shutdown":
            logs.append(f"Worker shutdown (PID={message['pid']}) at {message['ts']:.2f}")


def _shutdown_worker(worker: WorkerState) -> None:
    try:
        worker.job_queue.put_nowait("STOP")
    except Exception:
        pass
    worker.process.join(timeout=2.0)
    if worker.process.is_alive():
        worker.process.terminate()
        worker.process.join(timeout=1.0)
    st.session_state.pop("_final_worker_state", None)
    manager = st.session_state.pop("_final_worker_manager", None)
    if manager is not None:
        try:
            manager.shutdown()
        except Exception:
            pass


def main() -> None:
    st.set_page_config(page_title="Final transcript worker prototype", layout="wide")
    st.title("Final Transcription Worker Prototype")
    st.caption("Validate worker lifecycle before full integration.")

    worker = _get_or_start_worker()
    _drain_results(worker)

    st.session_state.setdefault("final_worker_jobs", {})
    st.session_state.setdefault("final_worker_results", [])
    st.session_state.setdefault("final_worker_logs", [])

    col1, col2, col3 = st.columns(3)
    col1.metric("Worker PID", worker.pid or "—")
    col2.metric("Queued jobs", sum(1 for j in st.session_state["final_worker_jobs"].values() if j["status"] == "queued"))
    col3.metric(
        "Completed jobs",
        sum(1 for j in st.session_state["final_worker_jobs"].values() if j["status"] == "completed"),
    )

    if worker.last_heartbeat:
        heartbeat_age = time.time() - worker.last_heartbeat
        if heartbeat_age > 5:
            st.warning(
                f"Worker heartbeat stale ({heartbeat_age:.1f}s ago).",
                icon="⚠️",
            )
        else:
            st.success(f"Last heartbeat: {time.strftime('%H:%M:%S', time.localtime(worker.last_heartbeat))}")
    elif not worker.process.is_alive():
        st.error("Worker process is not alive.")

    with st.form("job_form", clear_on_submit=True):
        text = st.text_input("Dummy payload", "simulate note text")
        submitted = st.form_submit_button("Submit job")
        if submitted:
            job_id = _enqueue_job(worker, text)
            st.toast(f"Queued job {job_id}", icon="📡")

    if st.button("Poll results"):
        _drain_results(worker)

    queued_jobs = [
        job for job in st.session_state["final_worker_jobs"].values() if job["status"] == "queued"
    ]
    now = time.time()
    if queued_jobs:
        oldest = min(job["submitted_at"] for job in queued_jobs)
        if now - oldest > 2.0:
            st.warning(
                f"{len(queued_jobs)} job(s) pending for {now - oldest:.1f}s. "
                "Use Poll results to sync or inspect the worker.",
                icon="⏳",
            )

    if st.button("Shutdown worker"):
        _shutdown_worker(worker)
        st.session_state.pop("final_worker_jobs", None)
        st.session_state.pop("final_worker_results", None)
        st.session_state.pop("final_worker_logs", None)

    st.subheader("Job State")
    st.json(st.session_state.get("final_worker_jobs", {}))

    st.subheader("Completed Results")
    for item in st.session_state.get("final_worker_results", []):
        st.write(item)

    st.subheader("Worker Logs")
    for log_line in st.session_state.get("final_worker_logs", []):
        st.code(log_line)


if __name__ == "__main__":
    main()
