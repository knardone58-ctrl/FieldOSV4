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
- **Snapshot**: `data/crm_snapshot.json` now captures the latest CRM payload (`last_payload`) plus a bounded history (`recent_payloads`, max five entries) alongside `final_transcribe_stats`. Scrub payloads with `python3 scripts/cleanup_snapshot.py` before sharing artifacts. Clear transient CRM sample rows with `python3 scripts/reset_crm_sample.py [--keep-demo]` when needed.
- **CRM delivery**: ops log entries now include `crm_response_code`, `crm_error`, and `crm_attempts`. Watch the Streamlit cockpit for synced/cached/failed badges and retry prompts; the mock server lives at `scripts/mock_crm_server.py`.

## 4.1 Reference Copilot Operations
- **Index build**: run `python3 scripts/build_reference_index.py`. With `OPENAI_API_KEY` unset (or `FIELDOS_CHAT_USE_STUB=true`), the script copies the deterministic stub index instead of making API calls. Artifacts land at `data/reference_index.jsonl` + `.meta.json`.
- **Environment toggles**:
  - `FIELDOS_CHAT_EMBED_MODEL`, `FIELDOS_CHAT_COMPLETION_MODEL`
  - `FIELDOS_CHAT_INDEX_PATH` / `_STUB_PATH`
  - `FIELDOS_CHAT_STUB_PATH`
  - `FIELDOS_CHAT_FALLBACK_MODE` (`stub` for offline demos, `keyword` for embeddings-off keyword search, blank for live API usage)
  - `FIELDOS_PRIVACY_MODE=true` hashes the last query in ops telemetry.
- **Fallback behaviour**: In QA/CI exports, set `FIELDOS_CHAT_FALLBACK_MODE=stub` to rely on deterministic answers (no network). When both index and stub are missing, the UI surfaces a banner prompting you to run the build script or set the stub env vars.
- **Telemetry hooks**: Ops logs now include `chat_requests`, `chat_fallback_count`, `chat_last_error`, plus either `chat_last_hash` (privacy mode) or `chat_last_query`. The dashboard/report summarize these values; investigate repeated fallbacks or errors before demos.
- **Positioning briefs**: When a query includes pitch/positioning cues, the copilot surfaces a 🟢 Positioning Brief with value props, promo/pricing highlights, and an “Insert positioning summary” button that appends the brief to the draft note.
- **Rollback**: remove the copilot container from `app.py`, delete `chatbot.py` / `reference_search.py`, and reset the env vars. Scrub the index artifacts (`data/reference_index*`) if they should not persist on the host.

## 5. Demo Walkthrough Highlights
- Record or upload audio; the **Intelligence Center** surfaces recent jobs/open quotes/promos pulled from `data/contact_intel.json`, while the **Raw transcript** panel keeps the unedited text visible next to the editable draft.
- Tap a **Playbook cue** to drop a scripted talking point into the note—the cue grays out, shows up under “Used cues,” and resets when a new transcript arrives.
- Generate a quote with the **Quote Builder** card (`data/pricing.json`); use the one-time “Insert quote into draft note” CTA to append the templated summary (capture a screenshot for `docs/screenshots/quote-card.png`).
- Watch the **High-Accuracy Transcript** panel update when the worker finishes—confidence, latency, and completion timestamps are called out with plain-language captions.
- After pressing **Save & Queue CRM Push**, show the “Last CRM payload” expander (streaming partial + final transcript + quote summary) and highlight the pipeline sidebar metrics. Call out the CRM status badge (Synced/Cached/Retrying/Failed) and, if failure occurs, demonstrate the one-click **Retry CRM Push** button. Use the “Refresh pipeline snapshot” button (toast: “Pipeline snapshot refreshed”) if you tweak `data/pipeline_snapshot.json`.
- Reference material (runbook, wiki, CRM sample, sales playbook) now lives in sidebar expanders so you can surface context without leaving the workflow.
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
- [ ] Scrub `last_payload`/`recent_payloads` before committing or distributing demo data (`python3 scripts/cleanup_snapshot.py`).
- [ ] Reset `data/crm_sample.csv` with `python3 scripts/reset_crm_sample.py [--keep-demo]` if demo pushes added extra rows.
- [ ] Reset `last_crm_status` / `crm_status` entries with the same script to keep shared artifacts clean.
- [ ] Tag release via `scripts/post_ci_wrap.sh --tag v4.4.0-beta` (or appropriate semantic version).
- [ ] Update `docs/fieldos_narrative/timeline.json` + regenerate timeline (already automated via `scripts/update_product_timeline.py`).
- [ ] Follow-up action: run the live smoke on an AVX2/Apple-Silicon machine and record results for ops sign-off (mock mode validated locally).

### Known limitation: macOS streaming asserts
Streamlit now survives Vosk/Kaldi crashes by disabling streaming and seeding stub metrics, but the upstream macOS wheel can still abort the process before that guard runs. Workaround: run demos with `STREAMING_ENABLED=false` on macOS unless you can ship a custom Vosk build.

Potential long-term improvement (heavy lift):

- Build Kaldi and Vosk from source on macOS, bundle into a custom wheel, and update the demo instructions.
- Requires Homebrew toolchain, multi-hour compile, Rosetta/x86 shell on Apple Silicon, scripting the env setup, and ongoing maintenance of the wheel.
- Trade-off: real live streaming on macOS vs. added complexity; keep the stub fallback for machines without the custom build.
