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

## QA & Local Validation

```bash
source venv/bin/activate
python3 -m compileall app.py crm_sync.py audio_cache.py
pytest tests/test_audio_cache.py tests/test_ops_log.py
FIELDOS_QA_MODE=true python qa/test_fieldos_streaming_deterministic.py
bash qa/qa_suite.sh
```

The QA suite now seeds `data/ops_log.jsonl` deterministically and fails fast if the log is missing. Whisper regressions are still best-effort (skipped when dependencies are unavailable).

## Audio Cache Hygiene

- Uploaded audio clips are stored under `data/audio_cache/clip_<ts>.wav`.
- `audio_cache.ensure_cache_dir` purges stale clips based on `AUDIO_TTL_HOURS`, throttled to once per minute to avoid churn.
- `audio_cache.calculate_audio_duration` powers the duration guard—clips longer than `AUDIO_MAX_SECONDS` are rejected before being written to disk.

## Ops Telemetry

- Each CRM transition (`cached`, `synced`, `flushed`) appends a JSON line to `data/ops_log.jsonl` containing queue size, AI failures, and streaming metrics (first partial latency, updates, dropouts).
- Summaries: `python scripts/report_ops_log.py` prints a Markdown table you can paste into weekly updates.
- Dashboard: `streamlit run ops_dashboard.py` renders a lightweight view of the same metrics for ops analysts.
- **Privacy note:** scrub or rotate `data/ops_log.jsonl` before sharing it outside the team—timestamps and status data may reveal customer interactions.

## CI Publishing

`.github/workflows/qa-suite.yml` runs the QA suite on every push/PR, uploads both `qa/last_whisper_accuracy.json` and `data/ops_log.jsonl` as artifacts, and surfaces failures when telemetry goes missing.

## Helpful Scripts

| Script | Purpose |
| --- | --- |
| `qa/qa_suite.sh` | Full regression sweep (baseline, AI, fallback, streaming) with ops-log verification |
| `scripts/run_streaming_session.sh` | Launch Streamlit headless, optionally tail logs, then run deterministic streaming QA |
| `scripts/report_ops_log.py` | Emit Markdown summary of ops metrics |
| `ops_dashboard.py` | Streamlit dashboard for visualizing ops log entries |

Keep non-ASCII characters out of source files, and avoid committing real ops logs or secrets. For any questions, reach out to the FieldOS platform team.
