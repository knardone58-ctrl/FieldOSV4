# FieldOS Product Journey Timeline

> Generated via `python scripts/update_product_timeline.py`. Edit `docs/fieldos_narrative/timeline.json` and rerun to update.

| Date | Release | Headline |
| --- | --- | --- |
| 2025-10-15 | V4.1 | Daily cockpit narrative anchored the workflow |
| 2025-10-24 | V4.2 | Prototype Streamlit app shipped with AI + CRM scaffolding |
| 2025-11-03 | V4.3 | Streaming telemetry scaffold and deterministic QA landed |
| 2025-11-03 | V4.3 | Remote repo + QA workflow activated |
| 2025-11-03 | V4.4 | Vosk streaming stood up with headless runner |
| 2025-11-03 | V4.4 | Audio hygiene + ops telemetry made observable |
| 2025-11-04 | V4.4 | Streaming pilot preflight + QA hardening |
| 2025-11-04 | V4.4 | Faster-whisper background worker scaffolding |
| 2025-11-04 | V4.4 | Final worker production rollout readiness |
| 2025-11-04 | V4.4 | Intelligence center, playbooks, and quote builder |
| 2025-11-04 | V4.4 | Reference Copilot and knowledge retrieval |

## 2025-10-15 · FieldOS V4.1 — Daily cockpit narrative anchored the workflow

**Summary:** Product story locked the FieldOS day-in-the-life: focus lead card, single-tap voice notes, GPT polish, CRM queue, and a celebratory close.

**Highlights**
- Framed the hero banner with focus contact and urgency badges.
- Kept voice capture + GPT polish at the center of the workflow.
- Outlined follow-up chips, offline queue, and end-of-day celebration.

**Key Artifacts**
- `docs/fieldos_narrative/product_story.md:1`

## 2025-10-24 · FieldOS V4.2 — Prototype Streamlit app shipped with AI + CRM scaffolding

**Summary:** Delivered a working cockpit with stubbed AI parser, CRM worker, offline cache, and QA harness so reps could rehearse the daily command center.

**Highlights**
- Streamlit app drove focus lead, follow-ups, and action footer.
- Whisper/Vosk stubs plus GPT polish kept the workflow usable offline.
- CRM queue, snapshot persistence, and QA scripts established regression coverage.

**Key Artifacts**
- `app.py`
- `crm_sync.py`
- `ai_parser.py`
- `qa/qa_suite.sh`

## 2025-11-03 · FieldOS V4.3 — Streaming telemetry scaffold and deterministic QA landed

**Summary:** Seeded stream_* session keys, archived metrics, and extended QA so CI tracked whisper accuracy and streaming health.

**Highlights**
- init_streaming_state() and apply_streaming_live() established telemetry plumbing.
- Deterministic streaming QA covered fallback behaviour until live PCM shipped.
- Metadata, scaffold, and technical notes documented the streaming spine.

**Key Artifacts**
- `streaming_asr.py`
- `qa/test_fieldos_streaming_deterministic.py`
- `docs/fieldos_narrative/scaffold.md:1`
- `docs/fieldos_narrative/technical_notes.md:1`
- `docs/fieldos_narrative/metadata.yaml:1`

## 2025-11-03 · FieldOS V4.3 — Remote repo + QA workflow activated

**Summary:** Rebased into main, resolved README conflicts, and pushed FieldOSV4 to origin so CI and collaboration could begin.

**Highlights**
- Resolved README merge, rebased cleanly, and established main on GitHub.
- QA suite wired into remote repo; README clarified setup and QA instructions.

**Key Artifacts**
- `README.md`
- `scripts/setup_env.sh`
- `qa/qa_suite.sh`
- `commit f341f7a`

## 2025-11-03 · FieldOS V4.4 — Vosk streaming stood up with headless runner

**Summary:** Downloaded model assets, converted PCM ingestion to stdlib, and verified streaming telemetry with a helper script.

**Highlights**
- Vosk model bundle unpacked under data/models/.
- Refined streaming_asr simulate_pcm_frames_wav() to avoid soundfile dependency.
- scripts/run_streaming_session.sh automated headless tests + QA fallback.

**Key Artifacts**
- `streaming_asr.py`
- `scripts/run_streaming_session.sh`
- `data/models/vosk-model-small-en-us-0.15/`

## 2025-11-03 · FieldOS V4.4 — Audio hygiene + ops telemetry made observable

**Summary:** TTL cleanup guardrails landed alongside ops_log.jsonl, streaming summary UI, ops dashboard, and CI artifacts.

**Highlights**
- audio_cache helpers purge stale clips and block over-length uploads before hitting disk.
- Streaming metrics now surface in-app with download CTA, plus ops_log.jsonl captures CRM + streaming stats.
- Ops tests, dashboard, and GitHub Actions artifact publishing keep telemetry visible.

**Key Artifacts**
- `audio_cache.py`
- `app.py`
- `crm_sync.py`
- `tests/test_audio_cache.py`
- `tests/test_ops_log.py`
- `scripts/report_ops_log.py`
- `ops_dashboard.py`
- `.github/workflows/qa-suite.yml`

## 2025-11-04 · FieldOS V4.4 — Streaming pilot preflight + QA hardening

**Summary:** Confirmed local environment readiness, tightened automated coverage, and documented the path to live streaming telemetry ahead of the field pilot.

**Highlights**
- Verified clean repo state and virtualenv activation; documented that live Streamlit checks must run on-device due to audio dependencies.
- Reaffirmed audio hygiene + ops logging via targeted pytest runs and deterministic QA seeding of ops_log.jsonl.
- Validated ops reporting scripts on empty logs; flagged known NumPy/SIGFPE in AI regression as informational warning.
- Outlined push prerequisites (GitHub PAT/SSH) once network constraints lift for publishing V4.4 artifacts.

**Key Artifacts**
- `audio_cache.py`
- `app.py`
- `crm_sync.py`
- `tests/test_audio_cache.py`
- `tests/test_ops_log.py`
- `qa/test_fieldos_streaming_deterministic.py`
- `scripts/report_ops_log.py`
- `scripts/post_ci_wrap.sh`
- `docs/fieldos_narrative/timeline.json`
- `docs/fieldos_narrative/product_timeline.md`

## 2025-11-04 · FieldOS V4.4 — Faster-whisper background worker scaffolding

**Summary:** Introduced the final transcription worker harness with mockable fast path, app integration, and QA guardrails ahead of exposing high-accuracy transcripts in the UI.

**Highlights**
- Landed Streamlit-free final_transcriber module with faster-whisper support, mock mode, heartbeat/error tracking, and clean shutdown semantics.
- App now queues captured clips into the worker, records stats in session state, and surfaces warnings when dependencies are missing.
- Expanded docs, checklists, and scripts to cover new dependencies, mock-mode unit tests, and worker setup workflows.

**Key Artifacts**
- `final_transcriber.py`
- `app.py`
- `tests/test_final_worker.py`
- `docs/faster_whisper_checklist.md`
- `docs/final_worker_prototype.md`
- `scripts/prepare_final_worker.sh`

## 2025-11-04 · FieldOS V4.4 — Final worker production rollout readiness

**Summary:** Promoted the faster-whisper worker from mock scaffold to a production-ready path with operational runbooks, monitoring, and smoke-test tooling.

**Highlights**
- Added a standalone CLI to launch the worker outside Streamlit for live smoke tests and heartbeats.
- Extended ops dashboards and reports with final worker queue depth, last success timestamps, and warning thresholds.
- Published a runbook covering enablement, troubleshooting, rollback, and release tagging guidance.

**Key Artifacts**
- `scripts/start_final_worker.py`
- `ops_dashboard.py`
- `scripts/report_ops_log.py`
- `docs/final_worker_runbook.md`
- `docs/faster_whisper_checklist.md`
- `docs/fieldos_narrative/product_timeline.md`

## 2025-11-04 · FieldOS V4.4 — Intelligence center, playbooks, and quote builder

**Summary:** Elevated the focus-contact workflow with cached account intel, contextual playbook cues, rapid quotes, and pipeline visibility supported by telemetry upgrades.

**Highlights**
- Intelligence Center panel now surfaces demo-ready history, quotes, and promotions per account using JSON snapshots.
- Playbook cues and quote builder enrich the draft note while persisting quote summaries directly into CRM payloads.
- Pipeline sidebar refresh control, pricing cache, and ops dashboard success-rate metrics keep reps and managers aligned in real time.
- Reference material moved into sidebar expanders so demos stay focused on the workflow tab.
- CRM snapshot persists the latest payload (plus bounded history) for offline dashboards while remaining scrub-friendly.
- Smart Suggestion pane now surfaces CRM delivery status badges with one-click retries, and ops telemetry captures CRM response codes/errors.

**Key Artifacts**
- `app.py`
- `crm_sync.py`
- `data/contact_intel.json`
- `data/playbooks.json`
- `data/pricing.json`
- `data/pipeline_snapshot.json`
- `scripts/report_ops_log.py`
- `ops_dashboard.py`
- `docs/final_worker_runbook.md`
- `README.md`
- `scripts/cleanup_snapshot.py`
- `scripts/mock_crm_server.py`
- `tests/test_crm_snapshot.py`
- `tests/test_crm_sync.py`

## 2025-11-04 · FieldOS V4.4 — Reference Copilot and knowledge retrieval

**Summary:** Introduced an in-app chat copilot backed by indexed wiki/CRM/playbook sources, complete with offline stubs, telemetry, and documentation updates.

**Highlights**
- Reference Copilot panel now answers natural-language questions with cited snippets from the company wiki, CRM sample, and sales playbook.
- scripts/build_reference_index.py chunks reference docs, builds embeddings, and falls back to deterministic stubs when API keys are absent.
- Ops telemetry and dashboards capture copilot usage, fallback ratios, and hashed queries for privacy-compliant monitoring.
- QA regression and unit suites stub embeddings/LLM calls to keep offline runs deterministic.

**Key Artifacts**
- `chatbot.py`
- `reference_search.py`
- `scripts/build_reference_index.py`
- `app.py`
- `crm_sync.py`
- `ops_dashboard.py`
- `scripts/report_ops_log.py`
- `tests/test_reference_search.py`
- `tests/test_chatbot.py`
- `tests/test_chat_copilot.py`
- `qa/test_fieldos_regression.py`
- `README.md`
- `docs/final_worker_runbook.md`
- `AGENTS.md`
- `.env.example`
- `tests/fixtures/reference_index_stub.jsonl`
- `tests/fixtures/chat_stub.json`
