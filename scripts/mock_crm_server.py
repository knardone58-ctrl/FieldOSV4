#!/usr/bin/env python3
"""
Minimal mock CRM server for local testing.

Usage:
    python3 scripts/mock_crm_server.py --host 127.0.0.1 --port 8787

Endpoints:
    POST /crm/push  → returns {"status": "ok"} by default.
                       Use --failures N to simulate the first N requests failing.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar


class _MockCRMHandler(BaseHTTPRequestHandler):
    failures_remaining: ClassVar[int] = 0

    def do_POST(self) -> None:  # noqa: N802 (handler API)
        if self.path != "/crm/push":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(content_length)
        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except json.JSONDecodeError:
            body = None

        if _MockCRMHandler.failures_remaining > 0:
            _MockCRMHandler.failures_remaining -= 1
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"status": "error", "error": "mock failure"}
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = {"status": "ok", "received": body}
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # noqa: A003 (handler API)
        return  # Silence default logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock CRM server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--failures", type=int, default=0, help="Number of initial failures to simulate")
    args = parser.parse_args()

    _MockCRMHandler.failures_remaining = max(0, args.failures)
    server = HTTPServer((args.host, args.port), _MockCRMHandler)
    print(f"Mock CRM server listening on http://{args.host}:{args.port} (failures remaining: {args.failures})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down mock CRM server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
