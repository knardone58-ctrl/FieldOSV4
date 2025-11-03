"""Configuration helpers for FieldOS."""

import os

QA_MODE = os.getenv("FIELDOS_QA_MODE", "false").lower() == "true"
AUDIO_MAX_SECONDS = int(os.getenv("FIELDOS_AUDIO_MAX_SECONDS", "30"))
TRANSCRIBE_ENGINE = os.getenv("FIELDOS_TRANSCRIBE_ENGINE", "vosk")
POLISH_CTA = "✨ Polish with AI (≈ 3 s)"
POLISH_FAIL_TOAST = "AI polish unavailable right now. Draft saved as-is."

# TTL (hours) for cached audio clips before cleanup (not yet used in this drop)
AUDIO_TTL_HOURS = int(os.getenv("FIELDOS_AUDIO_TTL_HOURS", "24"))


# Streaming configuration (V4.3)
STREAMING_ENABLED = os.getenv("STREAMING_ENABLED", "true").lower() == "true"
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "data/models/vosk-model-small-en-us-0.15")
STREAM_CHUNK_MS = int(os.getenv("STREAM_CHUNK_MS", "300"))
