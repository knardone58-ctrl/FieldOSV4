"""
FieldOS V4.2 — Audio + AI Parser
---------------------------------
- Supports transcription via Vosk or Whisper (local/API) with QA fallbacks.
- GPT-based note polish with built-in retries and QA-safe determinism.
"""

from __future__ import annotations

import json
import os
import time
from logging import getLogger
from pathlib import Path
from typing import Dict, Tuple

from fieldos_config import QA_MODE, TRANSCRIBE_ENGINE, VOSK_MODEL_PATH

try:
    import soundfile as sf
except ImportError:  # pragma: no cover
    sf = None  # type: ignore

_OPENAI_CLIENT = None
_VOSK_MODEL = None
_FASTER_WHISPER_MODEL = None
LOGGER = getLogger(__name__)


def _get_openai_client():
    """Lazy-load the OpenAI client, respecting QA mode."""
    global _OPENAI_CLIENT
    if QA_MODE:
        return None
    if _OPENAI_CLIENT is None:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing in environment.")
        _OPENAI_CLIENT = OpenAI()
    return _OPENAI_CLIENT


def _load_vosk_model():
    """Load Vosk model once (if selected)."""
    global _VOSK_MODEL
    if QA_MODE:
        return None
    if _VOSK_MODEL is None:
        try:
            from vosk import Model  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Vosk not installed. Set FIELDOS_TRANSCRIBE_ENGINE to whisper_* or install vosk.") from exc

        if not Path(VOSK_MODEL_PATH).exists():
            raise RuntimeError(f"Vosk model not found at {VOSK_MODEL_PATH}.")
        _VOSK_MODEL = Model(VOSK_MODEL_PATH)
    return _VOSK_MODEL


def _load_faster_whisper():
    """Lazy-load faster-whisper model for CPU/GPU execution."""
    global _FASTER_WHISPER_MODEL
    if QA_MODE:
        return None
    if _FASTER_WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "faster-whisper not installed. Run scripts/setup_env.sh or pip install faster-whisper."
            ) from exc

        model_name = os.getenv("FIELDOS_WHISPER_MODEL", "base")
        device = os.getenv("FIELDOS_WHISPER_DEVICE", "cpu")
        compute_type = os.getenv("FIELDOS_WHISPER_COMPUTE_TYPE", "int8")
        _FASTER_WHISPER_MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _FASTER_WHISPER_MODEL


def _transcribe_vosk(file_path: str) -> Tuple[str, float, float]:
    from vosk import KaldiRecognizer  # type: ignore
    import wave

    model = _load_vosk_model()
    wf = wave.open(file_path, "rb")
    rec = KaldiRecognizer(model, wf.getframerate())
    rec.SetWords(True)

    result = []
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            result.append(json.loads(rec.Result()))
    result.append(json.loads(rec.FinalResult()))

    transcript = " ".join(seg.get("text", "") for seg in result).strip()
    conf_scores = []
    for seg in result:
        if "result" in seg:
            conf_scores.extend(word.get("conf", 0.0) for word in seg["result"])
    confidence = float(sum(conf_scores) / len(conf_scores)) if conf_scores else 0.0
    duration = wf.getnframes() / float(wf.getframerate())
    wf.close()
    return transcript, confidence, duration


def _transcribe_whisper_local(file_path: str) -> Tuple[str, float, float]:
    try:
        import torch  # type: ignore
        import whisper  # type: ignore
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("Whisper import failed: %s", exc)
        return ("", 0.0, 0.0)

    model_name = os.getenv("FIELDOS_WHISPER_MODEL", "base")
    try:
        model = whisper.load_model(model_name)
        start = time.time()
        with torch.inference_mode(), torch.amp.autocast("cpu", enabled=False):
            result = model.transcribe(file_path, language="en")
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("Whisper transcription failed: %s", exc)
        return ("", 0.0, 0.0)

    duration = time.time() - start
    transcript = result.get("text", "").strip()
    segments = result.get("segments") or []
    avg_logprob = [seg.get("avg_logprob", 0.0) for seg in segments if isinstance(seg, dict)]
    confidence = float(sum(avg_logprob) / len(avg_logprob)) if avg_logprob else 0.0
    return transcript, confidence, duration


def _transcribe_whisper_api(file_path: str) -> Tuple[str, float, float]:
    client = _get_openai_client()
    if client is None:
        return "QA transcript (API bypass)", 1.0, 0.5
    start = time.time()
    with open(file_path, "rb") as audio_file:
        res = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
    duration = time.time() - start
    transcript = res.text.strip()
    confidence = 0.0  # API does not expose confidence yet
    return transcript, confidence, duration


def _transcribe_faster_whisper(file_path: str) -> Tuple[str, float, float]:
    model = _load_faster_whisper()
    if model is None:
        return ("", 0.0, 0.0)
    beam_size = int(os.getenv("FIELDOS_WHISPER_BEAM_SIZE", "5"))
    start = time.time()
    segments, info = model.transcribe(file_path, language="en", beam_size=beam_size)
    segment_list = list(segments)
    duration = time.time() - start
    transcript = " ".join(seg.text.strip() for seg in segment_list if getattr(seg, "text", None)).strip()
    conf_scores = [seg.avg_logprob for seg in segment_list if getattr(seg, "avg_logprob", None) is not None]
    confidence = float(sum(conf_scores) / len(conf_scores)) if conf_scores else 0.0
    clip_duration = getattr(info, "duration", 0.0) if info is not None else 0.0
    if not clip_duration and segment_list:
        clip_duration = max((seg.end or 0.0) for seg in segment_list)
    return transcript, confidence, clip_duration


def transcribe_audio(file_path: str) -> Tuple[str, float, float]:
    """Transcribe audio file and return (text, confidence, duration_seconds)."""
    if QA_MODE:
        return ("QA transcript: seasonal cleanup already delivered. Client wants mulch promo.", 0.99, 1.2)

    engine = TRANSCRIBE_ENGINE
    try:
        if engine == "vosk":
            return _transcribe_vosk(file_path)
        if engine == "whisper_local":
            return _transcribe_whisper_local(file_path)
        if engine == "whisper_api":
            return _transcribe_whisper_api(file_path)
        if engine == "faster_whisper":
            return _transcribe_faster_whisper(file_path)
        raise ValueError(f"Unknown FIELDOS_TRANSCRIBE_ENGINE: {engine}")
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("Transcription engine failed (%s): %s", engine, exc)
        fallback_text = "Transcription unavailable — using note field only."
        return (fallback_text, 0.0, 0.0)


def polish_note_with_gpt(text: str, metadata: Dict[str, str], style_guidelines: str = "") -> Tuple[str, float]:
    """Return (polished_note, duration_seconds)."""
    if QA_MODE:
        return (
            "- Completed seasonal cleanup.\n- Client asked for mulch promo pricing.\n- Follow up Tuesday with package options.",
            0.8,
        )

    client = _get_openai_client()
    prompt = (
        "You are a landscaping field-sales assistant. "
        "Convert the following note into 3-5 concise bullets covering outcomes, decisions, and next steps.\n\n"
        f"Account: {metadata.get('account')}\n"
        f"Service: {metadata.get('service')}\n"
        f"Contact: {metadata.get('contact')}\n"
    )
    if style_guidelines:
        prompt += f"Style guidelines: {style_guidelines}\n"
    prompt += f"\nRaw Note:\n{text.strip()}\n"

    start = time.time()
    try:
        response = client.responses.create(
            model="gpt-5-turbo",
            input=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
        )
        content = response.output_text.strip()
    except Exception:  # pragma: no cover - network path
        return "", time.time() - start
    duration = time.time() - start
    return content, duration
