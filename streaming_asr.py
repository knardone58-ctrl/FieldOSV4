
"""FieldOS V4.3 — Vosk streaming helpers."""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterable, Optional

# Optional import: allow QA/CI to run without Vosk installed
try:
    from vosk import Model, KaldiRecognizer  # type: ignore
    _VOSK_AVAILABLE = True
except Exception:  # pragma: no cover
    Model = KaldiRecognizer = None  # type: ignore
    _VOSK_AVAILABLE = False


class VoskStreamer:
    """Consumes PCM chunks and exposes partial/final text plus telemetry."""

    def __init__(self, model_path: str, sample_rate: int = 16000):
        if not _VOSK_AVAILABLE:
            raise RuntimeError("Vosk not available")
        self.model = Model(model_path)
        self.rec = KaldiRecognizer(self.model, sample_rate)
        self.q: "queue.Queue[bytes]" = queue.Queue(maxsize=128)
        self.partial_text = ""
        self.final_text = ""
        self.running = False
        self.updates = 0
        self.dropouts = 0
        self.first_partial_ms: Optional[int] = None
        self._t0: Optional[float] = None

    def start(self) -> None:
        self.running = True
        self._t0 = time.perf_counter()
        threading.Thread(target=self._consume, name="vosk-stream-consumer", daemon=True).start()

    def push_pcm(self, chunk: bytes) -> None:
        if not self.running:
            return
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            self.dropouts += 1

    def stop(self) -> None:
        self.running = False
        time.sleep(0.1)
        try:
            result = json.loads(self.rec.FinalResult()).get("text", "").strip()
            if result:
                self.final_text = result
        except Exception:
            pass

    def _consume(self) -> None:
        while self.running or not self.q.empty():
            try:
                chunk = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self.rec.AcceptWaveform(chunk):
                    result = json.loads(self.rec.Result()).get("text", "").strip()
                    if result:
                        self.final_text = result
                else:
                    partial = json.loads(self.rec.PartialResult()).get("partial", "").strip()
                    if partial:
                        self.partial_text = partial
                        self.updates += 1
                        if self.first_partial_ms is None and self._t0 is not None:
                            self.first_partial_ms = int((time.perf_counter() - self._t0) * 1000)
            except Exception:
                self.dropouts += 1


def simulate_pcm_frames_wav(path: str, step_ms: int = 300) -> Iterable[bytes]:
    """Iterate deterministic PCM chunks from a WAV file."""
    import soundfile as sf

    audio, sr = sf.read(path, dtype="int16", always_2d=False)
    if audio.ndim == 2:
        import numpy as np

        audio = (0.5 * (audio[:, 0].astype("int32") + audio[:, 1].astype("int32"))).astype("int16")
    frame = int(sr * (step_ms / 1000))
    for i in range(0, len(audio), frame):
        yield audio[i : i + frame].tobytes()


def get_pcm_stream(dev_mode: bool, *, wav_path: str, step_ms: int) -> Iterable[bytes]:
    """Return PCM generator. Dev mode uses deterministic WAV playback."""
    if dev_mode:
        return simulate_pcm_frames_wav(wav_path, step_ms=step_ms)
    return simulate_pcm_frames_wav(wav_path, step_ms=step_ms)
