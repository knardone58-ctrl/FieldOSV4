# FieldOS Demo Guide

This playbook captures everything you need to launch the latest FieldOS demo, tune the experience with environment flags, and keep QA runs deterministic when needed.

---

## 1. Bootstrap & Preflight

| Task | Command / Notes |
| --- | --- |
| Clone/refresh repo | `git pull origin main` |
| Dev venv | `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt` |
| QA venv (source-built NumPy) | `./scripts/setup_qa_env.sh` <br> Add `--run-qa` to provision and execute the QA suite in one shot. |
| Seed narrative/timeline | `python3 scripts/update_product_timeline.py` |
| Reset CRM sample (optional) | `python3 scripts/reset_crm_sample.py` |
| Quiet Streamlit warnings (optional) | `export STREAMLIT_SUPPRESS_RUN_CONTEXT_WARNINGS=1` before QA runs |

---

## 2. Launching the Demo

1. Activate the desired environment:
   - Dev: `source venv/bin/activate`
   - QA-safe: `source venv-qa/bin/activate`
2. Export the flags you want (see §3).
3. Start Streamlit: `streamlit run app.py`

Stop with `Ctrl+C`; `deactivate` exits the virtualenv.

---

## 3. Flag Reference

| Variable | Values | Impact |
| --- | --- | --- |
| `FIELDOS_QA_MODE` | `true` / `false` | `true` enables deterministic stubs (copilot answers, CRM queue, streaming); `false` runs full live experience. |
| `STREAMING_ENABLED` | `true` / `false` | `true` invokes the Vosk streaming loop; `false` seeds deterministic streaming metrics (safe on macOS/CI). |
| `FIELDOS_CHAT_FALLBACK_MODE` | `stub` / `keyword` / `live` | Controls reference-copilot sources; default `live` uses embeddings + OpenAI, `stub` uses fixtures, `keyword` is deterministic keyword ranking. |
| `FIELDOS_CHAT_USE_STUB` | `true` / `false` | Force stubbed index/responses even when `*_FALLBACK_MODE=live`. |
| `FIELDOS_FINAL_WORKER_ENABLED` | `true` / `false` | Toggle faster-whisper final worker. Combine with `_MOCK=true` for deterministic stats. |
| `FIELDOS_FINAL_WORKER_MOCK` | `true` / `false` | When `true`, bypass the worker but keep stats visible. |
| `FIELDOS_DISABLE_OFFLINE_FLUSH` | `true` / `false` | `true` hides the sidebar “Flush Offline Cache” button (used in QA). |
| `STREAMLIT_SUPPRESS_RUN_CONTEXT_WARNINGS` | `1` | Optional: silences “missing ScriptRunContext” spam when running in bare/AppTest mode. |
| `PYTHONWARNINGS` | `ignore:NotOpenSSLWarning` | Optional: suppress macOS LibreSSL warning from urllib3. |

### Recommended Profiles

**Full Live Demo**
```bash
source venv/bin/activate
export FIELDOS_QA_MODE=false
export STREAMING_ENABLED=true
export FIELDOS_CHAT_FALLBACK_MODE=live
export FIELDOS_FINAL_WORKER_ENABLED=true
export FIELDOS_FINAL_WORKER_MOCK=false
streamlit run app.py
```

**Safe / Deterministic Demo**
```bash
source venv-qa/bin/activate   # or venv
export FIELDOS_QA_MODE=true
export STREAMING_ENABLED=false
export FIELDOS_CHAT_FALLBACK_MODE=stub
export FIELDOS_FINAL_WORKER_ENABLED=false
streamlit run app.py
```

**QA Regression (source NumPy env)**
```bash
./scripts/setup_qa_env.sh --run-qa
# internally runs:
# FIELDOS_QA_MODE=true STREAMING_ENABLED=false bash qa/qa_suite.sh
```

---

## 4. Pre-Demo Checklist

- ✅ QA suite green (`bash qa/qa_suite.sh` or `setup_qa_env.sh --run-qa`).
- ✅ `data/ops_log.jsonl` and `data/crm_snapshot.json` reflect current state (use `scripts/cleanup_snapshot.py` if you need a blank slate).
- ✅ `.env` populated with any required secrets (OpenAI key for live copilot, etc.).
- ✅ If streaming is enabled, confirm Vosk model files exist under `data/models/` and macOS mic permissions are set.

---

## 5. Troubleshooting

| Symptom | Quick Fix |
| --- | --- |
| Vosk/Kaldi crashes on macOS | `export STREAMING_ENABLED=false` or wrap `apply_streaming_live()` in a try/except and fall back to the stub. |
| NumPy SIGFPE during QA | Always run QA in `venv-qa` (source-built NumPy). |
| `ModuleNotFoundError: streamlit` in QA env | Ensure `scripts/setup_qa_env.sh` ran or install manually: `pip install streamlit`. |
| Snapshot assert in QA (`Snapshot missing last_payload`) | Latest tests fall back to session payloads; rerun `qa/test_fieldos_regression.py` to verify. |
| Excess “missing ScriptRunContext” noise | `export STREAMLIT_SUPPRESS_RUN_CONTEXT_WARNINGS=1`. |

---

## 6. Handy Commands

```bash
# Refresh narrative timeline
python3 scripts/update_product_timeline.py

# Reset CRM sample deck
python3 scripts/reset_crm_sample.py

# Run QA suite (existing env)
FIELDOS_QA_MODE=true STREAMING_ENABLED=false bash qa/qa_suite.sh

# Manual deterministic streaming test
FIELDOS_QA_MODE=true STREAMING_ENABLED=false ./venv/bin/python qa/test_fieldos_streaming_deterministic.py

# Clean ops log / snapshot before a demo
python3 scripts/cleanup_snapshot.py
```

---

_With these toggles and scripts captured in one place, you can flip between hardened QA runs and the full-field demo without surprises._
