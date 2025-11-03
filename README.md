<<<<<<< HEAD
# FieldOSV4

## Setup

```bash
git clone <repo>
cd FieldOSV4
bash scripts/setup_env.sh
streamlit run app.py
```

Environment variables live in `.env` (copy from `.env.example`).

## QA Suite

Run all automated regressions (baseline, AI, fallback, accuracy) from the repo root:

```bash
source FieldOSV4/venv/bin/activate
bash qa/qa_suite.sh
```

The whisper accuracy harness writes results to `qa/last_whisper_accuracy.json`; publish this file as a CI artifact to track confidence trends over time.
=======
# FieldOSV4
>>>>>>> 1d4cf3ba26f8da9909c4925bf16bb70864887da3
