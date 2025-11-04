import io
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audio_cache import calculate_audio_duration, ensure_cache_dir


def _write_clip(path: Path, age_hours: float) -> None:
    path.write_bytes(b"audio")
    ts = time.time() - (age_hours * 3600)
    os.utime(path, (ts, ts))


def _make_wav_bytes(seconds: int, samplerate: int = 16000) -> bytes:
    """Create silent mono wav bytes of the requested duration."""
    buf = io.BytesIO()
    frames = b"\x00\x00" * (samplerate * seconds)
    import wave

    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(samplerate)
        wav.writeframes(frames)
    return buf.getvalue()


class AudioCacheTests(unittest.TestCase):
    def test_purge_removes_stale_and_skips_recent(self) -> None:
        base_time = 1_000.0
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            stale = cache_dir / "clip_111.wav"
            _write_clip(stale, age_hours=5)

            state: dict = {}
            ensure_cache_dir(cache_dir, ttl_hours=2, state=state, now=lambda: base_time)
            self.assertFalse(stale.exists())

            # Recreate a stale clip but call within 60 seconds → no purge
            second = cache_dir / "clip_222.wav"
            _write_clip(second, age_hours=5)
            ensure_cache_dir(cache_dir, ttl_hours=2, state=state, now=lambda: base_time + 30)
            self.assertTrue(second.exists())

    def test_purge_short_circuits_when_ttl_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            clip = cache_dir / "clip_333.wav"
            _write_clip(clip, age_hours=10)

            state: dict = {}
            ensure_cache_dir(cache_dir, ttl_hours=0, state=state, now=lambda: 2_000.0)

            self.assertTrue(clip.exists())

    def test_calculate_audio_duration_for_long_clip(self) -> None:
        wav_bytes = _make_wav_bytes(seconds=4)
        duration = calculate_audio_duration(wav_bytes, "clip.wav")
        self.assertIsNotNone(duration)
        self.assertGreater(duration, 3.5)


if __name__ == "__main__":
    unittest.main()
