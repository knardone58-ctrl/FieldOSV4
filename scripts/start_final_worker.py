#!/usr/bin/env python3
"""
Run the high-accuracy transcription worker outside of Streamlit.

Examples:
    # Dry-run to ensure dependencies + model load
    scripts/start_final_worker.py

    # Transcribe one or more audio clips using the worker
    scripts/start_final_worker.py --clip data/audio_cache/sample.wav

    # Stay alive after processing clips (Ctrl+C to exit)
    scripts/start_final_worker.py --stay-alive --clip my.wav
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from final_transcriber import (  # noqa: E402
    WorkerConfig,
    collect_stats,
    poll_results,
    shutdown_worker,
    start_worker,
    submit_job,
)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _build_config(args: argparse.Namespace) -> WorkerConfig:
    return WorkerConfig(
        enabled=True,
        mock=args.mock,
        qa_mode=args.qa_mode,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the final transcription worker.")
    parser.add_argument("--model", default=os.getenv("FIELDOS_WHISPER_MODEL", "base"))
    parser.add_argument("--device", default=os.getenv("FIELDOS_WHISPER_DEVICE", "cpu"))
    parser.add_argument(
        "--compute-type",
        default=os.getenv("FIELDOS_WHISPER_COMPUTE_TYPE", "int8"),
        help="faster-whisper compute type (e.g., int8, int8_float32, float16).",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=int(os.getenv("FIELDOS_WHISPER_BEAM_SIZE", "5")),
        help="Beam size for decoding.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=_env_flag("FIELDOS_FINAL_WORKER_MOCK"),
        help="Run the deterministic mock worker instead of the real model.",
    )
    parser.add_argument(
        "--qa-mode",
        action="store_true",
        default=_env_flag("FIELDOS_QA_MODE"),
        help="Force QA mode (disables the worker).",
    )
    parser.add_argument(
        "--clip",
        dest="clips",
        action="append",
        default=[],
        metavar="PATH",
        help="Optional audio clip(s) to transcribe before exiting.",
    )
    parser.add_argument(
        "--stay-alive",
        action="store_true",
        help="Keep the worker running until interrupted (Ctrl+C).",
    )
    return parser.parse_args(argv)


def _wait_for_heartbeat(handle, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        poll_results(handle)
        if handle.last_heartbeat:
            return True
        time.sleep(0.1)
    return False


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    config = _build_config(args)

    if config.qa_mode:
        print("QA mode forces the worker off. Unset FIELDOS_QA_MODE or --qa-mode.", file=sys.stderr)
        return 1

    try:
        handle = start_worker(config)
    except Exception as exc:
        print(f"Failed to start worker: {exc}", file=sys.stderr)
        return 1

    if handle is None:
        print("Worker disabled by configuration.", file=sys.stderr)
        return 1

    stop_requested = False

    def _shutdown(signum, _frame):  # noqa: ANN001
        nonlocal stop_requested
        if not stop_requested:
            print(f"\nReceived signal {signum}. Shutting down worker...", file=sys.stderr)
        stop_requested = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(
        f"Worker started (mock={config.mock}) "
        f"model={config.model} device={config.device} compute_type={config.compute_type}"
    )

    if not config.mock and not _wait_for_heartbeat(handle):
        print("⚠️  Did not receive heartbeat from worker within timeout.", file=sys.stderr)

    pending: Dict[str, Path] = {}
    for clip in args.clips:
        clip_path = Path(clip).expanduser()
        if not clip_path.exists():
            print(f"Skipping missing clip: {clip_path}", file=sys.stderr)
            continue
        job_id = submit_job(handle, clip_path, {"invoked_by": "start_final_worker"})
        pending[job_id] = clip_path
        print(f"Queued {clip_path} (job_id={job_id})")

    try:
        while not stop_requested:
            results = poll_results(handle)
            for result in results:
                job_id = result.get("job_id")
                clip_path = pending.pop(job_id, None)
                error_text = result.get("error")
                if error_text:
                    print(f"[ERROR] job={job_id} clip={clip_path} reason={error_text}")
                else:
                    print(
                        f"[OK] job={job_id} clip={clip_path} "
                        f"transcript={result.get('transcript','').strip()} "
                        f"confidence={result.get('confidence')} latency_ms={result.get('latency_ms')}"
                    )
            if not pending and not args.stay_alive and not args.clips:
                break
            if not pending and not args.stay_alive and args.clips:
                # All requested clips processed; exit after final stats dump.
                break
            if not pending and args.stay_alive:
                stats = collect_stats(handle, pending_jobs_count=0)
                heartbeat = stats.get("last_heartbeat")
                if heartbeat:
                    age = time.time() - heartbeat
                    print(f"Heartbeat OK (age={age:.1f}s)", end="\r")
            time.sleep(0.25)
    finally:
        shutdown_worker(handle)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
