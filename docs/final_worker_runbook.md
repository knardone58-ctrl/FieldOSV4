# Final Transcription Worker Runbook

Operational guide for running the faster-whisper “final transcript” worker in production and rolling back quickly if needed.

## 1. Prerequisites
- Install dependencies via `bash scripts/setup_env.sh` (ensures faster-whisper stack + NumPy pin).
- Download model weights (default `base`): `scripts/download_faster_whisper.sh` or set `FIELDOS_DOWNLOAD_FASTER_WHISPER=skip` to opt out on constrained hosts.
- Copy `.env.example` to `.env` and review final worker toggles:
  - `FIELDOS_FINAL_WORKER_ENABLED=false` (default off)
  - `FIELDOS_FINAL_WORKER_MOCK=true` (keep true in QA/CI)
  - `FIELDOS_WHISPER_MODEL`, `FIELDOS_WHISPER_DEVICE`, `FIELDOS_WHISPER_COMPUTE_TYPE`, `FIELDOS_WHISPER_BEAM_SIZE`

## 2. Enabling the Worker
1. Set environment for live mode (mock off):
   ```bash
   export FIELDOS_FINAL_WORKER_ENABLED=true
   export FIELDOS_FINAL_WORKER_MOCK=false
   export FIELDOS_WHISPER_MODEL=base          # adjust as needed
   export FIELDOS_WHISPER_DEVICE=cpu          # or metal / cuda
   export FIELDOS_WHISPER_COMPUTE_TYPE=int8   # or int8_float32 / float16
   ```
2. (Optional) Override model location with `HUGGINGFACE_HUB_CACHE` for shared caches.
3. Launch Streamlit (`streamlit run app.py`) or the standalone CLI (below). The preflight check will display a warning toast if dependencies are missing and keep the app running on Vosk-only mode.

## 3. Standalone CLI Smoke Test
- Run outside Streamlit to confirm the model loads and returns transcripts:
  ```bash
  FIELDOS_FINAL_WORKER_MOCK=false ./venv/bin/python scripts/start_final_worker.py \
    --clip data/audio_cache/sample.wav
  ```
- Expected output: worker startup line, optional heartbeat, and transcript/confidence/latency per clip. Non-zero queue or errors are printed to stderr for quick diagnosis.
- Use `--stay-alive` to keep the worker running and monitor heartbeats (Ctrl+C to exit).

### 3.1 Mock Smoke (CPU-limited hosts)
- On hardware without AVX2/F16C (e.g., this Intel Mac), validate the full pipeline in mock mode:
  ```bash
  FIELDOS_FINAL_WORKER_ENABLED=true FIELDOS_FINAL_WORKER_MOCK=true scripts/run_final_worker_smoke.sh
  ```
- Expected output mirrors the real flow: worker start notice, clip queued, transcript logged as `QA transcript: mock worker output.` This confirms queue wiring, telemetry updates, CRM fields, and CLI helper behavior without invoking the heavyweight model.

## 4. Monitoring & Alerting
- **Dashboards**: `streamlit run ops_dashboard.py` highlights final worker queue depth (warning > 3), last success time, and any errors.
- **Reports**: `python3 scripts/report_ops_log.py` prints a Markdown summary with warnings when queue depth exceeds the threshold or recent errors exist.
- **Ops log**: `data/ops_log.jsonl` records every CRM event with `final_worker_queue_depth`, `final_worker_last_success`, and `final_worker_error`. Use `tail -f` for real-time monitoring.
- **Snapshot**: `data/crm_snapshot.json` captures `final_transcribe_stats` for postmortems.

## 5. Demo Walkthrough Highlights
- Record or upload audio; the **Raw transcript** panel renders the unedited text while the Draft Note remains fully editable.
- Watch the **High-Accuracy Transcript** panel update when the worker finishes—confidence, latency, and completion timestamps are called out with plain-language captions.
- After pressing **Save & Queue CRM Push**, scroll to the “Last CRM payload” expander to display the exact payload (streaming partial + final transcript) for the audience.
- Re-running the demo won’t duplicate clips; the uploader clears after save and the SHA1 guard warns if you try to reuse the same audio.

## 6. Troubleshooting
- **Missing weights**: run `scripts/download_faster_whisper.sh <model>`; confirm the directory `data/models/faster-whisper/<model>` exists.
- **Import errors**: verify the virtualenv is activated (`source venv/bin/activate`) and rerun `pip install -r requirements.txt`.
- **OpenMP duplicate runtime**: set `KMP_DUPLICATE_LIB_OK=TRUE` as a temporary workaround, but prefer to isolate conflicting libraries.
- **NumPy ABI mismatch**: ensure NumPy `< 2` is installed (handled by requirements). Recreate the venv if system Python has incompatible wheels.
- **GPU driver mismatch**: for CUDA use the `faster-whisper[gpu-cuda]` wheel and verify drivers/toolkit; for Metal, ensure macOS 12+ with Apple Silicon.
- **Large queue backlog (>3)**: check worker process health, CPU/GPU utilization, and consider temporarily setting `FIELDOS_FINAL_WORKER_ENABLED=false` to drain the queue with streaming transcripts only.
- **Missing CPU instructions (e.g., AVX2)**: older Intel Macs may fail to load onnxruntime/ctranslate2. In that case, stick with mock mode locally and schedule a live smoke test on a newer host (Apple Silicon or AVX2-capable Linux box).

## 7. Rollback Procedure
1. Disable the worker:
   ```bash
   export FIELDOS_FINAL_WORKER_ENABLED=false
   export FIELDOS_FINAL_WORKER_MOCK=true   # keep deterministic mode for QA
   ```
2. Restart Streamlit or the CLI worker; the app will fall back to streaming/Vosk transcripts immediately.
3. Communicate downtime to ops if final transcripts were expected; reference `data/ops_log.jsonl` for impacted events.

## 8. Release Checklist
- [ ] Run the CLI smoke test (Section 3) on the target host with real audio.
- [ ] Confirm dashboard/report show healthy queue depth and last success timestamps.
- [ ] Archive a CRM snapshot (`data/crm_snapshot.json`) post-smoke for validation.
- [ ] Tag release via `scripts/post_ci_wrap.sh --tag v4.4.0-beta` (or appropriate semantic version).
- [ ] Update `docs/fieldos_narrative/timeline.json` + regenerate timeline (already automated via `scripts/update_product_timeline.py`).
- [ ] Follow-up action: run the live smoke on an AVX2/Apple-Silicon machine and record results for ops sign-off (mock mode validated locally).
