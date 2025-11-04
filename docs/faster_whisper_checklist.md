# Faster-Whisper Deployment Checklist

FieldOS needs a reproducible way to install, cache, and optionally skip the faster-whisper dependency for environments that cannot spare the resources. Use this checklist before enabling the background transcription worker or switching the engine in production.

## 1. Environment & Dependencies

- [ ] Update `requirements.txt` to include the faster-whisper stack (`faster-whisper`, `ctranslate2`, `onnxruntime`, `tokenizers`, `huggingface-hub`, `av`) and commit the change.
- [ ] Confirm `requirements.txt` pins `numpy>=1.24,<2` to avoid incompatible C-extension builds.
- [ ] Verify all dependencies install cleanly on the target machine:
  ```bash
  source venv/bin/activate
  pip install -r requirements.txt
  ```
- [ ] For GPU setups, decide on the device:
  - Apple Silicon: export `FIELDOS_WHISPER_DEVICE=metal`.
  - NVIDIA CUDA: install the GPU wheel (`pip install faster-whisper[gpu-cuda]`) and set `FIELDOS_WHISPER_DEVICE=cuda`.
  - CPU fallback: `FIELDOS_WHISPER_DEVICE=cpu`, default compute type `int8_float32`.
- [ ] Ensure the bootstrap script (`scripts/setup_env.sh`) runs post-change so the venv picks up new wheels.

## 2. Model Download & Caching

- [ ] Choose a default model (e.g. `base`, `medium.en`, `large-v2`) and document expected download size.
- [ ] Add a helper script:
  ```bash
  scripts/download_faster_whisper.sh --model medium.en
  ```
  which preloads weights into `data/models/faster-whisper/<model-name>`.
- [ ] Call the download script from `scripts/setup_env.sh` (guarded by an env flag if necessary) or document that developers must run it manually after setup.
- [ ] Document the override flag (e.g., `FIELDOS_DOWNLOAD_FASTER_WHISPER=skip`) so CI environments can opt out of large downloads.
- [ ] Confirm the script respects `HUGGINGFACE_HUB_CACHE` if set, to avoid duplicating downloads across machines.
- [ ] Capture disk footprint in documentation (README or operations runbook) so ops can budget storage.
- [ ] Provide a standalone smoke harness (`scripts/start_final_worker.py`) so ops can validate model availability outside Streamlit.

## 3. Configuration Flags

- [ ] New `.env` keys:
  - `FIELDOS_FINAL_WORKER_ENABLED=true` to enable the background process.
  - `FIELDOS_WHISPER_MODEL=<variant>` for the worker (defaults to `base`).
  - `FIELDOS_WHISPER_DEVICE`, `FIELDOS_WHISPER_COMPUTE_TYPE`, `FIELDOS_WHISPER_BEAM_SIZE` to tune performance.
- [ ] Update `fieldos_config.py` (and associated tests) to read the new env keys with safe defaults so the app keeps working when they are absent.
- [ ] Document that the default Streamlit path still uses Vosk unless the worker is enabled.
- [ ] Update `README.md` or a dedicated doc to describe the flag interactions.
- [ ] Note that snapshots include `final_transcribe_stats` with queue depth, timestamps (ISO8601), confidence, latency, and model; keep these fields in sync when making schema changes.
- [ ] Document CRM payload fields (`transcription_stream_partial`, `transcription_final`, `transcription_final_confidence`, `transcription_final_latency_ms`, `transcription_final_completed_at`) and ensure downstream consumers handle the default (`""`/`null`) values.

## 4. QA / CI Guardrails

- [ ] Add `FIELDOS_FINAL_WORKER_ENABLED=false` in CI and QA environments by default.
- [ ] Provide a deterministic mock path controlled by `FIELDOS_FINAL_WORKER_MOCK=true` so tests do not spawn real faster-whisper processes. Example acceptance test:
  ```bash
  FIELDOS_FINAL_WORKER_MOCK=true ./venv/bin/python -m pytest tests/test_final_worker.py -q
  ```
- [ ] Skip or stub weight downloads in automated pipelines (e.g., guard `scripts/download_faster_whisper.sh` behind an env flag).
- [ ] Add a smoke test that validates importability without running the worker (e.g., `python -c "from faster_whisper import WhisperModel"`).
- [ ] Update `qa/qa_suite.sh` (and related QA scripts) to export `FIELDOS_FINAL_WORKER_ENABLED=false` or `FIELDOS_FINAL_WORKER_MOCK=true`, documenting the flags inline for future maintainers.

## 5. Failure Handling & Fallbacks

- [ ] Implement runtime detection: if faster-whisper import fails, log a warning and mark worker as disabled (fall back to Vosk transcripts).
- [ ] Track worker health metrics (queue depth, last success timestamp, error flag) so ops can spot resource exhaustion.
- [ ] Update `append_ops_log_event` to record the new fields (`final_worker_queue_depth`, `final_worker_last_success`, `final_worker_error`) when they are available.
- [ ] Surface the new metrics in `scripts/report_ops_log.py` / `ops_dashboard.py`, flagging queue depth > 3 and recent errors so ops teams get actionable warnings.

## 6. Documentation & Support

- [ ] Update the operations runbook with troubleshooting steps (common errors: missing model weights, `OMP` conflicts, NumPy version mismatches).
- [ ] Provide a rollback guide: how to disable the worker quickly (`FIELDOS_FINAL_WORKER_ENABLED=false` + restart).
- [ ] Note any platform-specific caveats (macOS LibreSSL warning, multiple OpenMP runtimes, etc.).
- [ ] Publish `docs/final_worker_runbook.md` covering enablement, monitoring, CLI smoke tests, and rollback guidance.
- [ ] Document release tagging flow (e.g., `scripts/post_ci_wrap.sh --tag v4.4.0-beta`) once live smoke completes.
- [ ] Call out hardware requirements (AVX2/F16C or Apple Silicon + Metal) and provide the mock smoke fallback command (`FIELDOS_FINAL_WORKER_ENABLED=true FIELDOS_FINAL_WORKER_MOCK=true scripts/run_final_worker_smoke.sh`) for unsupported hosts.

## Prototype & Task Success Criteria

- [ ] Prototype Streamlit page survives reruns without spawning duplicate worker processes (PID remains constant; no additional process after rerun).
- [ ] Queue state persists across reruns; submitted job count matches completed job count before exit.
- [ ] Clean shutdown verified by logging the worker PID at startup and confirming it no longer exists once Streamlit exits (see `docs/final_worker_prototype.md` for the exact verification steps).
- [ ] Task A acceptance: worker module unit tests (`pytest tests/test_final_worker.py -q`) cover job submission, mocked transcripts, and failure fallbacks; real-worker behavior is exercised in a `@pytest.mark.slow` test while mocks run by default.
- [ ] Task B acceptance: main app displays the **High-Accuracy Transcript** panel (transcript/confidence/latency/timestamp), surfaces queue/error warnings, and persists `final_transcribe_stats` (including confidence/latency) in the snapshot; regression tests assert the new state.
- [ ] Task C acceptance: CRM payload includes both streaming partials and final transcript fields; ops log records `final_worker_queue_depth`, `final_worker_last_success`, and `final_worker_error`; documentation reflects schema updates.
- [ ] Task D acceptance: QA harness/export scripts set `FIELDOS_FINAL_WORKER_ENABLED=false` and `FIELDOS_FINAL_WORKER_MOCK=true`; regression helpers seed deterministic results so suites never spawn the real worker.
- [ ] Task E acceptance: model download helper available, live worker toggle documented, dashboard/report thresholds configured, runbook + CLI smoke test instructions published (including demo flow highlights), and release tagging guidance captured.

Complete this checklist before wiring the worker into `app.py` so development and deployment flows remain unblocked.
