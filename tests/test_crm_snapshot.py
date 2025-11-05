import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import crm_sync


def _build_session() -> dict:
    session = {}
    crm_sync._ensure_session_lists(session)
    session["ai_fail_count"] = 0
    session["ai_latency_totals"] = {"transcribe": 0.0, "polish": 0.0}
    session["ai_latency_counts"] = {"transcribe": 0, "polish": 0}
    return session


class CRMSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.snapshot_path = Path(self.tmpdir.name) / "crm_snapshot.json"
        self.patcher_path = mock.patch.object(crm_sync, "SNAPSHOT_PATH", self.snapshot_path)
        self.patcher_path.start()
        self.addCleanup(self.patcher_path.stop)
        self.session = _build_session()
        self.original_st = crm_sync.st
        crm_sync.st = types.SimpleNamespace(session_state=self.session)
        self.addCleanup(self._restore_streamlit)
        self.crm_client_patcher = mock.patch.object(
            crm_sync,
            "CRM_DELIVERY_CLIENT",
            side_effect=lambda payload, retry_count=0: {"status": "ok", "response_code": 200, "body": {"status": "ok"}},
        )
        self.crm_client_patcher.start()
        self.addCleanup(self.crm_client_patcher.stop)

    def _restore_streamlit(self) -> None:
        crm_sync.st = self.original_st

    def test_snapshot_updates_on_synced_payload(self) -> None:
        payload = {
            "account": "QA Account",
            "note": "Polished note body",
            "ts": "2025-11-04T15:00:00",
            "quote_summary": {"service": "Mulch", "total": 2400},
        }
        crm_sync._process_payload(payload, offline=False)
        self.assertTrue(self.snapshot_path.exists())
        snapshot = json.loads(self.snapshot_path.read_text())
        self.assertEqual(snapshot["last_payload"]["note"], "Polished note body")
        self.assertEqual(snapshot["last_payload"]["quote_summary"]["total"], 2400)
        self.assertEqual(self.session["last_crm_payload"]["note"], "Polished note body")
        self.assertEqual(snapshot["last_crm_status"]["state"], "synced")
        self.assertEqual(self.session["last_crm_status"]["state"], "synced")
        self.assertLessEqual(len(snapshot.get("recent_payloads", [])), crm_sync.RECENT_PAYLOAD_LIMIT)

    def test_offline_cache_flush_updates_snapshot(self) -> None:
        payload = {
            "account": "Offline Account",
            "note": "Cached note",
            "ts": "2025-11-04T16:00:00",
        }
        self.session["offline"] = True
        crm_sync._process_payload(payload, offline=True)
        self.session["offline"] = False
        crm_sync.flush_offline_cache()
        snapshot = json.loads(self.snapshot_path.read_text())
        self.assertEqual(snapshot["last_payload"]["note"], "Cached note")
        history = snapshot.get("recent_payloads", [])
        self.assertTrue(history)
        self.assertEqual(history[0]["note"], "Cached note")
        self.assertEqual(snapshot["last_crm_status"]["state"], "synced")

    def test_snapshot_failure_sets_warning_flag(self) -> None:
        self.session["last_crm_payload"] = {"note": "existing"}

        with mock.patch.object(crm_sync, "save_snapshot", side_effect=IOError("disk full")):
            crm_sync._process_payload({"note": "new"}, offline=False)

        self.assertEqual(self.session["last_crm_payload"]["note"], "existing")
        self.assertTrue(self.session["crm_snapshot_warning_pending"])
        self.assertTrue(self.session["crm_snapshot_warning_logged"])

        # Successful retry clears warning flags
        crm_sync._process_payload({"note": "next"}, offline=False)
        self.assertFalse(self.session["crm_snapshot_warning_pending"])
        self.assertFalse(self.session["crm_snapshot_warning_logged"])

    def test_failure_moves_payload_to_offline(self) -> None:
        with mock.patch.object(
            crm_sync,
            "CRM_DELIVERY_CLIENT",
            side_effect=lambda payload, retry_count=0: {"status": "error", "response_code": 503, "error": "mock failure"},
        ), mock.patch.object(crm_sync, "_get_crm_config", return_value={
            "endpoint": "stub",
            "api_key": None,
            "timeout": 1,
            "max_retries": 1,
        }):
            crm_sync._process_payload({"note": "needs retry", "ts": "2025-11-05T10:00:00"}, offline=False)

        self.assertTrue(self.session["offline_cache"])
        self.assertEqual(self.session["last_crm_status"]["state"], "failed")
        self.assertTrue(self.session["crm_retry_available"])


if __name__ == "__main__":
    unittest.main()
