# FieldOS V4.3 – Deterministic streaming QA (SafeSessionState-safe)
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import os, sys, time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

@patch("streaming_asr.VoskStreamer._consume")
def test_streaming_minimal(mock_consume):
    def fake_consume(self):
        self.partial_text = "hello"
        self.updates = 1
        self.first_partial_ms = 300
        time.sleep(0.05)
        self.partial_text = "hello world"
        self.updates = 2
        time.sleep(0.05)
        self.final_text = "hello world"
    mock_consume.side_effect = fake_consume

    cwd = os.getcwd()
    try:
        os.chdir(APP_DIR)
        Path("qa/tmp").mkdir(parents=True, exist_ok=True)
        Path("qa/tmp/stub.wav").write_bytes(b"\x00\x00")

        app = AppTest.from_file("app.py")
        app.run(timeout=5)

        assert "stream_updates_count" in app.session_state
        assert "stream_final_text" in app.session_state
        assert "stream_latency_ms_first_partial" in app.session_state

        assert app.session_state["stream_updates_count"] >= 2
        assert app.session_state["stream_final_text"] == "hello world"

        latency = app.session_state["stream_latency_ms_first_partial"]
        assert latency is None or latency <= 1000
    finally:
        os.chdir(cwd)

    print("✅ Deterministic streaming test PASS")

def test_streaming_fallback_stub():
    cwd = os.getcwd()
    prior_env = {
        "FIELDOS_STREAMING_FORCE_FAIL": os.environ.get("FIELDOS_STREAMING_FORCE_FAIL"),
        "FIELDOS_QA_MODE": os.environ.get("FIELDOS_QA_MODE"),
        "STREAMING_ENABLED": os.environ.get("STREAMING_ENABLED"),
    }
    os.environ["FIELDOS_STREAMING_FORCE_FAIL"] = "true"
    os.environ["FIELDOS_QA_MODE"] = "false"
    os.environ["STREAMING_ENABLED"] = "true"
    try:
        os.chdir(APP_DIR)
        app = AppTest.from_file("app.py")
        app.run(timeout=5)

        assert app.session_state["STREAMING_ENABLED"] is False
        assert app.session_state["stream_updates_count"] >= 2
        assert app.session_state["stream_final_text"] == "hello world"
        latency = app.session_state["stream_latency_ms_first_partial"]
        assert latency in (300, None)
        assert app.session_state["stream_fallbacks"] >= 1
    finally:
        os.chdir(cwd)
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("FIELDOS_STREAMING_FORCE_FAIL", None)

    print("✅ Streaming fallback stub test PASS")

if __name__ == "__main__":
    test_streaming_minimal()
    test_streaming_fallback_stub()
