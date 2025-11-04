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
