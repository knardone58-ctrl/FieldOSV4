# FieldOS V4.4 Pilot

Streaming cockpit for daily FieldOS operations with real-time transcription, CRM sync, and auditable telemetry.

## Setup

```bash
git clone <repo-url>
cd FieldOSV4
bash scripts/setup_env.sh
streamlit run app.py
```

Copy `.env.example` to `.env` and provide the required API keys/engine flags before launching the app.

### Transcription engines

- `FIELDOS_TRANSCRIBE_ENGINE=vosk` (default) keeps everything fully offline with low CPU usage.
- `FIELDOS_TRANSCRIBE_ENGINE=faster_whisper` loads the [faster-whisper](https://github.com/guillaumekln/faster-whisper) model for significantly better accuracy while remaining on-device. Tune with `FIELDOS_WHISPER_MODEL` (e.g. `medium.en`) and `FIELDOS_WHISPER_COMPUTE_TYPE` (e.g. `int8_float32`) if your Mac can handle the larger models.
- `FIELDOS_TRANSCRIBE_ENGINE=whisper_local` uses the reference PyTorch Whisper implementation; `whisper_api` sends audio to OpenAI’s hosted model.

QA harnesses force deterministic transcripts by exporting `FIELDOS_QA_MODE=true`; unset it (or use the defaults in `.env`) for real audio capture.

⚠️  If you change engines, re-run `scripts/setup_env.sh` (or `pip install -r requirements.txt`) so the correct dependencies and the NumPy < 2.x pin take effect.

## QA & Local Validation

```bash
source venv/bin/activate
python3 -m compileall app.py crm_sync.py audio_cache.py
pytest tests/test_audio_cache.py tests/test_ops_log.py
FIELDOS_QA_MODE=true python qa/test_fieldos_streaming_deterministic.py
bash qa/qa_suite.sh
```

The QA suite now seeds `data/ops_log.jsonl` deterministically and fails fast if the log is missing. Whisper regressions are still best-effort (skipped when dependencies are unavailable).
The harness exports `FIELDOS_FINAL_WORKER_ENABLED=false` and `FIELDOS_FINAL_WORKER_MOCK=true` so the faster-whisper worker is never invoked during automated runs.

## Audio Cache Hygiene

- Uploaded audio clips are stored under `data/audio_cache/clip_<ts>.wav`.
- `audio_cache.ensure_cache_dir` purges stale clips based on `AUDIO_TTL_HOURS`, throttled to once per minute to avoid churn.
- `audio_cache.calculate_audio_duration` powers the duration guard—clips longer than `AUDIO_MAX_SECONDS` are rejected before being written to disk.

## Ops Telemetry

- Each CRM transition (`cached`, `synced`, `flushed`) appends a JSON line to `data/ops_log.jsonl` containing queue size, AI failures, and streaming metrics (first partial latency, updates, dropouts).
- Summaries: `python scripts/report_ops_log.py` prints a Markdown table you can paste into weekly updates.
- Dashboard: `streamlit run ops_dashboard.py` renders a lightweight view of the same metrics for ops analysts.
- Final worker telemetry now rides alongside these metrics: queue depth, last success timestamp, and any surfaced error. The summary script and dashboard flag queue depths above 3 and highlight errors so ops crews can react quickly.
- **Privacy note:** scrub or rotate `data/ops_log.jsonl` before sharing it outside the team—timestamps and status data may reveal customer interactions.

## High-Accuracy Transcript Panel

- Enable the final transcription worker with `FIELDOS_FINAL_WORKER_ENABLED=true`. For development and QA runs, set `FIELDOS_FINAL_WORKER_MOCK=true` to return deterministic mock transcripts.
- When enabled, the main app renders a **High-Accuracy Transcript** panel beneath the draft note showing transcript text, confidence, latency, and completion timestamp. The sidebar metric mirrors worker telemetry (queue depth, heartbeat, last error) so operators know if processing is pending.
- New “Raw transcript” callout keeps the original audio text read-only while the draft note stays fully editable. After “Save & Queue CRM Push,” the latest CRM payload appears in an expander so demo viewers see both streaming and final transcript fields.

- Run `scripts/download_faster_whisper.sh ${FIELDOS_WHISPER_MODEL:-base}` to fetch model weights into `data/models/faster-whisper/`. Set `HUGGINGFACE_HUB_CACHE` to reuse downloads across machines.
- `scripts/setup_env.sh` calls the downloader automatically; export `FIELDOS_DOWNLOAD_FASTER_WHISPER=skip` to bypass the download (e.g., CI runners without audio workloads).
- Default flags: `FIELDOS_FINAL_WORKER_ENABLED=false` keeps the worker off in dev. Enable live mode with `FIELDOS_FINAL_WORKER_ENABLED=true` and (optionally) `FIELDOS_FINAL_WORKER_MOCK=false`. QA/CI harnesses continue to force mock mode.
- For standalone verification, run `scripts/start_final_worker.py [--clip path.wav]` to launch the worker outside Streamlit and confirm the model loads correctly.
- See the [Final Worker Runbook](docs/final_worker_runbook.md) for operational toggles, monitoring tips, and rollback instructions ahead of production rollout.
- Hardware note: the real faster-whisper path needs AVX2/F16C (most modern Intel/AMD CPUs) or Apple Silicon + Metal; on older hosts run the mock smoke (`FIELDOS_FINAL_WORKER_ENABLED=true FIELDOS_FINAL_WORKER_MOCK=true scripts/run_final_worker_smoke.sh`) to validate wiring without the heavy model.
- GPU tuning: set `FIELDOS_WHISPER_DEVICE=cuda` (or `metal` on Apple Silicon) and adjust `FIELDOS_WHISPER_COMPUTE_TYPE` as needed.
- See [`docs/final_worker_prototype.md`](docs/final_worker_prototype.md) for a walkthrough and mock validation steps.

### CRM Payload Fields

- Each CRM payload now includes both streaming and final transcript data:
  - `transcription_stream_partial`: latest streaming/Vosk text (empty string when unavailable).
  - `transcription_final`: final worker transcript (empty string when the worker has not produced a result).
  - `transcription_final_confidence`, `transcription_final_latency_ms`, `transcription_final_completed_at`: populated when a final transcript exists, otherwise `null`.
- `ai_model_version` appends `| final_worker=<model>` only when a final transcript has been generated.

## CI Publishing

`.github/workflows/qa-suite.yml` runs the QA suite on every push/PR, uploads both `qa/last_whisper_accuracy.json` and `data/ops_log.jsonl` as artifacts, and surfaces failures when telemetry goes missing.

## Helpful Scripts

| Script | Purpose |
| --- | --- |
| `qa/qa_suite.sh` | Full regression sweep (baseline, AI, fallback, streaming) with ops-log verification |
| `scripts/run_streaming_session.sh` | Launch Streamlit headless, optionally tail logs, then run deterministic streaming QA |
| `scripts/report_ops_log.py` | Emit Markdown summary of ops metrics |
| `scripts/start_final_worker.py` | Spin up the final worker outside Streamlit; optional one-shot transcription for local smoke tests |
| `ops_dashboard.py` | Streamlit dashboard for visualizing ops log entries |
| `scripts/post_ci_wrap.sh` | Package artifacts and tag releases (`--tag v4.4.0-beta` for the final worker rollout) |

Additional references:

- `docs/faster_whisper_checklist.md` — dependency & deployment checklist before enabling the high-accuracy transcription worker.

Keep non-ASCII characters out of source files, and avoid committing real ops logs or secrets. For any questions, reach out to the FieldOS platform team.
