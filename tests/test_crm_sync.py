import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import crm_sync


def _make_session() -> dict:
    session = {}
    crm_sync._ensure_session_lists(session)
    session["ai_latency_totals"] = {"transcribe": 0.0, "polish": 0.0}
    session["ai_latency_counts"] = {"transcribe": 0, "polish": 0}
    return session


class CRMSyncDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.snapshot_path = Path(self.tmpdir.name) / "crm_snapshot.json"
        self.ops_path = Path(self.tmpdir.name) / "ops_log.jsonl"
        self.patchers = [
            mock.patch.object(crm_sync, "SNAPSHOT_PATH", self.snapshot_path),
            mock.patch.object(crm_sync, "OPS_LOG_PATH", self.ops_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

        self.session = _make_session()
        self.original_st = crm_sync.st
        crm_sync.st = types.SimpleNamespace(session_state=self.session)
        self.addCleanup(self._restore_streamlit)

    def _restore_streamlit(self) -> None:
        crm_sync.st = self.original_st

    def _set_client(self, responses) -> None:
        iterator = iter(responses)

        def _client(payload, retry_count=0):
            try:
                return next(iterator)
            except StopIteration:
                return {"status": "ok", "response_code": 200, "body": {}}

        patcher = mock.patch.object(crm_sync, "CRM_DELIVERY_CLIENT", side_effect=_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_updates_status_and_snapshot(self) -> None:
        self._set_client([{ "status": "ok", "response_code": 200, "body": {"status": "ok"}}])

        payload = {"account": "Acme", "note": "test", "ts": "2025-11-04T10:00:00"}
        crm_sync._process_payload(payload, offline=False)

        snapshot = json.loads(self.snapshot_path.read_text())
        self.assertEqual(snapshot["last_crm_status"]["state"], "synced")
        self.assertEqual(self.session["last_crm_status"]["state"], "synced")
        self.assertFalse(self.session["crm_retry_available"])

    def test_failure_moves_to_offline_and_enables_retry(self) -> None:
        self._set_client([{ "status": "error", "response_code": 503, "error": "mock"}])
        with mock.patch.object(crm_sync, "_get_crm_config", return_value={
            "endpoint": "stub",
            "api_key": None,
            "timeout": 1,
            "max_retries": 1,
        }):
            crm_sync._process_payload({"note": "needs retry", "ts": "2025-11-04T11:00:00"}, offline=False)

        self.assertTrue(self.session["offline_cache"])
        self.assertEqual(self.session["last_crm_status"]["state"], "failed")
        self.assertTrue(self.session["crm_retry_available"])

    def test_retry_flag_cleared_after_success(self) -> None:
        self.session["_crm_retry_in_progress"] = True
        self._set_client([{ "status": "ok", "response_code": 200, "body": {"status": "ok"}}])
        crm_sync._process_payload({"note": "retry", "ts": "2025-11-04T12:00:00"}, offline=False)
        self.assertFalse(self.session["_crm_retry_in_progress"])

    def test_retry_removes_offline_cache_entry(self) -> None:
        self._set_client([
            {"status": "error", "response_code": 503, "error": "mock"},
            {"status": "ok", "response_code": 200, "body": {"status": "ok"}},
        ])
        with mock.patch.object(crm_sync, "_get_crm_config", return_value={
            "endpoint": "stub",
            "api_key": None,
            "timeout": 1,
            "max_retries": 1,
        }):
            crm_sync._process_payload({"note": "needs retry", "ts": "2025-11-04T13:00:00"}, offline=False)

        self.assertTrue(self.session["offline_cache"])

        retry_payload = copy.deepcopy(self.session["last_crm_payload"])
        retry_payload.pop("crm_status", None)
        retry_payload.pop("_offline_cached", None)
        retry_payload.pop("_cached_at", None)
        retry_payload.pop("_gps", None)
        retry_payload["_crm_retry_attempts"] = 0

        crm_sync.enqueue_crm_push(retry_payload)
        payload = self.session["crm_queue"].pop(0)

        crm_sync._process_payload(payload, offline=False)

        self.assertFalse(self.session["offline_cache"])


if __name__ == "__main__":
    unittest.main()
