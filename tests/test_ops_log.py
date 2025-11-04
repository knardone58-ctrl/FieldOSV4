import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import crm_sync


class OpsLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.ops_path = Path(self.tmpdir.name) / "ops_log.jsonl"
        self.patchers = [
            mock.patch.object(crm_sync, "OPS_LOG_PATH", self.ops_path),
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
        }
        fixed_ts = datetime(2025, 1, 1, 12, 0, 0)

        record = crm_sync.append_ops_log_event("synced", state=state, timestamp=fixed_ts)

        self.assertEqual(record["status"], "synced")
        self.assertEqual(record["queue_len"], 2)
        self.assertEqual(record["ai_failures"], 3)
        self.assertEqual(record["stream_updates"], 7)
        self.assertEqual(record["stream_latency_ms_first_partial"], 512)
        self.assertEqual(record["stream_dropouts"], 2)
        self.assertEqual(record["ts"], fixed_ts.isoformat())

        contents = self.ops_path.read_text().strip().splitlines()
        self.assertEqual(len(contents), 1)
        parsed = json.loads(contents[0])
        self.assertEqual(parsed, record)


if __name__ == "__main__":
    unittest.main()
