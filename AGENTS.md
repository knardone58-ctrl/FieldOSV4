# FieldOS Intelligence & Workflow Expansion

## Mission & Guardrails
Deliver demo-ready intelligence that enriches the High-Accuracy Transcript flow, reinforces CRM trust, and respects the Streamlit two-column layout. Every change must run with `FIELDOS_FINAL_WORKER_MOCK=true`, maintain telemetry visibility, and stay mindful of offline constraints.

## Guiding Principles
- **High Leverage First**: Ship inline intelligence panels, quote helpers, and CRM trust cues before longer-term experiments.
- **Respect the Layout**: Extend the hero card and draft note with expanders, captions, or chips—avoid new routes.
- **Offline Mindset**: Cache the minimum viable datasets and badge stale data.
- **Telemetry Safe**: Preserve queue/error metrics; propose schema diffs before merge.
- **Demo Ready**: Upload → save → insight in a single smooth pass.
- **Reference Light**: Keep runbooks/wiki/CRM samples in sidebar expanders so the workflow remains the hero.

## Priority Backlog & Acceptance Targets
1. **Focus Contact Intel (Quick Win)**: Collapsible snapshot of recent jobs, quotes, promotions. *Loads <250 ms from cache; timestamp badge; no live calls in mock mode.*
2. **Inline Playbook Chips (Quick Win)**: Guidance under raw transcript driven by contact/service JSON. *≥90% correct chip matches; graceful empty state.*
3. **Rapid Quote Cards (Medium)**: `Generate Quote` pre-fills tiers/upsells from cached pricing tables. *Offline response <500 ms; sync stub enqueues update for reconnect.*
4. **Next-Step Nudges (Medium)**: Heuristic/ML actions on final transcript. *Precision ≥80% on mock transcripts; dismissible chips only.*
5. **Ops Intelligence Command Center (Med/High)**: Expand `ops_dashboard.py` with worker success trends and throughput bands. *Warnings mirror `scripts/report_ops_log.py`; renders mock data <2 s.*
6. **Offline Pricing Helper (Med/High)**: Cached catalog + margin calculator. *Refresh script <60 s; last-sync banner visible to operators.*
7. **Reference Copilot Streaming (Medium)**: Upgrade the new copilot to stream responses and surface typing indicators. *Latency <3 s over stub data; graceful fallback when streaming unsupported.*
8. **Copilot Team Filters (Medium)**: Add region/rep-aware filtering for search results. *Respect user metadata, return only approved snippets, document schema contract.*
9. **Copilot Analytics (Medium)**: Aggregate chat usage in ops dashboard (top queries, fallback %, last error timeline). *No raw text stored when `FIELDOS_PRIVACY_MODE=true`; charts render mock data under 2 s.*
10. **Positioning Data Parity (Medium)**: Pull value props/discounts straight from `playbooks.json` / `pricing.json`, flag stale or missing service intel in the UI, and exercise degradation paths in stubs/tests. *Brief shows freshness/banner cues when metadata is missing or outdated; QA harness covers fallback copy.*

## Data & Privacy Ownership
- CRM snapshot: RevOps export in `data/crm_snapshot.json`; worker now persists `last_payload`, `last_crm_status`, and a five-entry `recent_payloads` history. Keep sample data sanitized and run `python3 scripts/cleanup_snapshot.py` before sharing artifacts (clears payloads + status metadata).
- Reference copilot: hashed queries/logs ride in `data/ops_log.jsonl` when `FIELDOS_PRIVACY_MODE=true`; never persist raw chat transcripts outside session state.
- Pricing tables: Finance CSV under `data/pricing/`; log checksum/version on ingest.
- Telemetry: append-only `data/ops_log.jsonl`; gate new fields or backfill.
- Team intel stub: `data/team_intel_stub.json`; scrub/label PII before storage.

## Telemetry & Testing Expectations
- Update `scripts/report_ops_log.py` and `ops_dashboard.py` together; add fixture-based tests for new fields.
- Cover helpers with `pytest` and extend deterministic QA flows for heuristics or caches.
- Run `scripts/run_final_worker_smoke.sh --clip data/audio_cache/demo_sample.wav` before demo sign-off; capture screenshots or logs for UX/telemetry PRs.

## Collaboration & Demo Checklist
- Secure Design approval for chips/cards; sync with RevOps/Finance on data contracts and Ops/Platform on telemetry changes.
- Document trade-offs, blockers, and TODOs in PRs.
- Refresh README, runbook highlights, and product timeline (`python3 scripts/update_product_timeline.py`); rehearse Streamlit on port 8766, simulate offline mode, and demo the sidebar expanders plus pipeline refresh control.
