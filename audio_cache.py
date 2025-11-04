"""Utilities for managing FieldOS audio cache lifecycle."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Callable, MutableMapping, Optional

try:
    import soundfile as sf  # type: ignore
except Exception:  # pragma: no cover - optional dependency not installed
    sf = None  # type: ignore


def purge_old_audio(cache_dir: Path, ttl_hours: int) -> int:
    """Delete cached clips older than the configured TTL."""
    if ttl_hours <= 0:
        return 0
    cutoff = time.time() - (ttl_hours * 3600)
    removed = 0
    for clip in cache_dir.glob("clip_*.wav"):
        try:
            if clip.stat().st_mtime < cutoff:
                clip.unlink()
                removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return removed


def ensure_cache_dir(
    cache_dir: Path,
    ttl_hours: int,
    state: MutableMapping[str, float],
    *,
    now: Callable[[], float] = time.time,
) -> Path:
    """
    Ensure cache directory exists and purge stale clips at most once per minute.

    Returns the cache directory path to mirror os.makedirs semantics.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    last_purge_ts = float(state.get("_audio_cache_last_purge_ts", 0.0) or 0.0)
    current = float(now())
    if current - last_purge_ts >= 60:
        purge_old_audio(cache_dir, ttl_hours)
        state["_audio_cache_last_purge_ts"] = current
    return cache_dir


def calculate_audio_duration(file_bytes: bytes, filename: str) -> Optional[float]:
    """Return clip duration in seconds if it can be derived."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".wav":
        import wave

        try:
            with wave.open(io.BytesIO(file_bytes), "rb") as wav_file:
                frames = wav_file.getnframes()
                samplerate = wav_file.getframerate()
                return frames / float(samplerate) if samplerate else None
        except Exception:
            return None
    if sf is not None:
        try:
            with sf.SoundFile(io.BytesIO(file_bytes)) as snd:
                frames = len(snd)
                samplerate = snd.samplerate
                return frames / float(samplerate) if samplerate else None
        except Exception:
            return None
    return None
