import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import crm_sync
import scripts.report_ops_log as report_ops


class OpsLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.ops_path = Path(self.tmpdir.name) / "ops_log.jsonl"
        self.patchers = [
            mock.patch.object(crm_sync, "OPS_LOG_PATH", self.ops_path),
            mock.patch.object(report_ops, "OPS_LOG_PATH", self.ops_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])

    def test_append_ops_log_writes_expected_fields(self) -> None:
        state: Dict[str, Any] = {
            "crm_queue": [1, 2],
            "ai_fail_count": 3,
            "stream_updates_count": 7,
            "stream_latency_ms_first_partial": 512,
            "stream_dropouts": 2,
            "final_worker_stats": {
                "queue_depth": 4,
                "last_success_ts": "2025-01-01T11:59:00+00:00",
                "last_error": "timeout",
            },
            "chat_requests": 5,
            "chat_fallback_count": 2,
            "chat_last_error": "stub fallback",
            "chat_last_hash": "abcd1234ef56",
            "chat_last_query": "",
            "chat_positioning_count": 1,
        }
        fixed_ts = datetime(2025, 1, 1, 12, 0, 0)

        crm_meta = {
            "crm_response_code": 200,
            "crm_error": None,
            "crm_attempts": 1,
        }
        record = crm_sync.append_ops_log_event("synced", state=state, timestamp=fixed_ts, crm_meta=crm_meta)

        self.assertEqual(record["status"], "synced")
        self.assertEqual(record["queue_len"], 2)
        self.assertEqual(record["ai_failures"], 3)
        self.assertEqual(record["stream_updates"], 7)
        self.assertEqual(record["stream_latency_ms_first_partial"], 512)
        self.assertEqual(record["stream_dropouts"], 2)
        self.assertEqual(record["ts"], fixed_ts.isoformat())
        self.assertEqual(record["final_worker_queue_depth"], 4)
        self.assertEqual(record["final_worker_last_success"], "2025-01-01T11:59:00+00:00")
        self.assertEqual(record["final_worker_error"], "timeout")
        self.assertEqual(record["crm_response_code"], 200)
        self.assertIsNone(record["crm_error"])
        self.assertEqual(record["crm_attempts"], 1)
        self.assertEqual(record["chat_requests"], 5)
        self.assertEqual(record["chat_fallback_count"], 2)
        self.assertEqual(record["chat_last_error"], "stub fallback")
        self.assertEqual(record["chat_last_hash"], "abcd1234ef56")
        self.assertEqual(record["chat_last_query"], "")
        self.assertEqual(record["chat_positioning_count"], 1)

        contents = self.ops_path.read_text().strip().splitlines()
        self.assertEqual(len(contents), 1)
        parsed = json.loads(contents[0])
        self.assertEqual(parsed, record)

    def test_report_ops_log_summarises_success_rate(self) -> None:
        sample_entries = [
            {
                "ts": "2025-11-04T12:00:00Z",
                "status": "synced",
                "ai_failures": 0,
                "stream_latency_ms_first_partial": 300,
                "stream_updates": 2,
                "stream_dropouts": 0,
                "final_worker_queue_depth": 1,
                "final_worker_last_success": "2025-11-04T11:59:00Z",
                "final_worker_error": None,
                "crm_response_code": 200,
                "crm_error": None,
                "crm_attempts": 1,
                "chat_requests": 1,
                "chat_fallback_count": 0,
                "chat_last_error": None,
                "chat_last_hash": "abcd1234ef56",
                "chat_last_query": "",
                "chat_positioning_count": 0,
            },
            {
                "ts": "2025-11-04T12:05:00Z",
                "status": "synced",
                "ai_failures": 0,
                "stream_latency_ms_first_partial": 320,
                "stream_updates": 3,
                "stream_dropouts": 0,
                "final_worker_queue_depth": 4,
                "final_worker_last_success": "2025-11-04T12:04:30Z",
                "final_worker_error": "timeout",
                "crm_response_code": 503,
                "crm_error": "mock failure",
                "crm_attempts": 3,
                "chat_requests": 3,
                "chat_fallback_count": 1,
                "chat_last_error": "timeout",
                "chat_last_hash": "",
                "chat_last_query": "Mulch promo?",
                "chat_positioning_count": 2,
            },
        ]
        with self.ops_path.open("w", encoding="utf-8") as handle:
            for entry in sample_entries:
                handle.write(json.dumps(entry) + "\n")

        summary = report_ops._format_markdown(sample_entries)
        self.assertIn("Final worker success rate", summary)
        self.assertIn("Final worker queue trend", summary)
        self.assertIn("CRM success rate", summary)
        self.assertIn("Latest CRM error", summary)
        self.assertIn("Copilot positioning briefs", summary)


if __name__ == "__main__":
    unittest.main()
