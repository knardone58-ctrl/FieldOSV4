from __future__ import annotations

import csv
import json
import tempfile
import types
from pathlib import Path
from unittest import mock

import crm_sync


def _make_session_state() -> dict:
    session = {}
    crm_sync._ensure_session_lists(session)
    session["ai_latency_totals"] = {"transcribe": 0.0, "polish": 0.0}
    session["ai_latency_counts"] = {"transcribe": 0, "polish": 0}
    return session


def test_save_and_queue_updates_crm_snapshot_and_csv(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_snapshot = Path(tmpdir) / "crm_snapshot.json"
        tmp_ops = Path(tmpdir) / "ops_log.jsonl"
        tmp_csv = Path(tmpdir) / "crm_sample.csv"
        tmp_csv.write_text((Path("data") / "crm_sample.csv").read_text(encoding="utf-8"), encoding="utf-8")

        monkeypatch.setattr(crm_sync, "SNAPSHOT_PATH", tmp_snapshot)
        monkeypatch.setattr(crm_sync, "OPS_LOG_PATH", tmp_ops)
        monkeypatch.setattr(crm_sync, "CRM_SAMPLE_PATH", tmp_csv)

        session = _make_session_state()
        original_streamlit = crm_sync.st
        crm_sync.st = types.SimpleNamespace(session_state=session)

        monkeypatch.setattr(
            crm_sync,
            "CRM_DELIVERY_CLIENT",
            lambda payload, retry_count=0: {"status": "ok", "response_code": 200, "body": {"status": "ok"}},
        )

        payload = {
            "contact_name": "Samir Voss",
            "contact_phone": "(216) 555-0142",
            "contact_email": "samir.voss@acmehoa.demo",
            "account": "Acme HOA",
            "service": "Seasonal Cleanup + Mulch",
            "customer_id": "DEMO-ACME-HOA",
            "customer_type": "HOA",
            "account_address": "3150 Detroit Ave, Cleveland, OH",
            "assigned_rep": "Marcus Tillman",
            "region": "Midwest",
            "lead_source": "Demo",
            "note": "QA note: customer confirmed mulch promo.",
            "transcription_raw": "mulch promo transcript",
            "transcription_stream_partial": "mulch promo...",
            "transcription_final": "",
            "transcription_confidence": 0.9,
            "ai_model_version": "vosk + gpt",
            "processing_time": 1.2,
            "ts": "2025-11-04T12:00:00",
        }
        try:
            crm_sync._process_payload(payload, offline=False)

            assert session["last_crm_status"]["state"] == "synced"
            assert not session["crm_queue"]
            assert not session["offline_cache"]

            snapshot = json.loads(tmp_snapshot.read_text(encoding="utf-8"))
            assert snapshot["last_payload"]["note"] == payload["note"]
            assert snapshot["last_crm_status"]["state"] == "synced"

            with tmp_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            assert any(row.get("Customer_Name") == payload["account"] for row in rows)
        finally:
            crm_sync.st = original_streamlit
