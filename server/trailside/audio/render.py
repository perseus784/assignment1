"""Render sounds and voices to compressed audio files."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Protocol

import lameenc
import numpy as np
from scipy import signal
from scipy.io import wavfile

from ..config import ROOT, settings
from . import dsp
from .library import LIBRARY

log = logging.getLogger(__name__)

OVERRIDES_DIR = ROOT / "assets" / "sounds"  # drop real recordings here as <name>.wav
LIBRARY_VERSION = "2"


def encode_mp3(samples: np.ndarray, sr: int, kbps: int = 96) -> bytes:
    """Float samples in -1..1, shape (n,) or (n, channels)."""
    x = samples if samples.ndim == 2 else samples[:, None]
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(sr)
    enc.set_channels(pcm.shape[1])
    enc.set_quality(2)
    return bytes(enc.encode(pcm.tobytes()) + enc.flush())


def _load_override(name: str) -> Optional[np.ndarray]:
    path = OVERRIDES_DIR / f"{name}.wav"
    if not path.exists():
        return None
    sr, data = wavfile.read(path)
    x = data.astype(np.float32) / (np.iinfo(data.dtype).max if data.dtype.kind == "i" else 1.0)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    if sr != dsp.SR:
        x = signal.resample_poly(x, dsp.SR, sr, axis=0)
    return x


def render_sound(name: str, cache_dir: Optional[Path] = None) -> tuple[bytes, float]:
    """MP3 bytes + duration for a library sound. Cached across tours."""
    sound = LIBRARY[name]
    cache_dir = cache_dir or settings.data_dir / "sound-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    override = OVERRIDES_DIR / f"{name}.wav"
    tag = hashlib.sha1(f"{LIBRARY_VERSION}:{name}:{override.stat().st_mtime if override.exists() else ''}".encode()).hexdigest()[:10]
    path = cache_dir / f"{name}-{tag}.mp3"
    meta = cache_dir / f"{name}-{tag}.dur"
    if path.exists() and meta.exists():
        return path.read_bytes(), float(meta.read_text())
    x = _load_override(name)
    if x is None:
        x = sound.make()
    if sound.kind != "bed":  # one-shots must end in silence, whatever the generator did
        x = dsp.fade(x, out_s=0.25)
    data = encode_mp3(x.astype(np.float32), dsp.SR, 96 if sound.kind != "bed" else 80)
    path.write_bytes(data)
    duration = len(x) / dsp.SR
    meta.write_text(f"{duration:.3f}")
    return data, duration


# ---------------------------------------------------------------------------------------
# Text to speech
# ---------------------------------------------------------------------------------------


class TTS(Protocol):
    name: str

    def synth(self, text: str, role: str) -> tuple[np.ndarray, int]: ...


class KokoroTTS:
    """Kokoro-82M (Apache-2.0) via the `kokoro-onnx` package.

    Setup: `pip install kokoro-onnx`, download kokoro-v1.0.onnx and voices-v1.0.bin from the
    official kokoro-onnx release, then set TRAILSIDE_KOKORO_MODEL / TRAILSIDE_KOKORO_VOICES.
    """

    name = "kokoro"

    def __init__(self, model_path: str, voices_path: str):
        from kokoro_onnx import Kokoro  # optional dependency

        self.engine = Kokoro(model_path, voices_path)
        self.voices = {"narrator": settings.narrator_voice, "storyteller": settings.storyteller_voice}
        # Narrator a touch slower and more measured; storyteller a touch warmer and freer.
        self.speed = {"narrator": 0.96, "storyteller": 1.0}

    def synth(self, text: str, role: str) -> tuple[np.ndarray, int]:
        samples, sr = self.engine.create(text, voice=self.voices.get(role, self.voices["narrator"]), speed=self.speed.get(role, 1.0), lang="en-us")
        return np.asarray(samples, dtype=np.float32), sr


def get_tts() -> Optional[TTS]:
    if settings.kokoro_model and settings.kokoro_voices:
        try:
            return KokoroTTS(settings.kokoro_model, settings.kokoro_voices)
        except Exception as exc:  # missing package or model: fall back to device voices
            log.warning("Kokoro TTS unavailable (%s); the app will use on-device voices", exc)
    return None


def render_voice(tts: TTS, text: str, role: str) -> tuple[bytes, float]:
    samples, sr = tts.synth(text, role)
    # Gentle clean-up: remove DC, trim silence, level to a podcast-like loudness.
    samples = samples - np.mean(samples)
    loud = np.where(np.abs(samples) > 0.01)[0]
    if len(loud):
        samples = samples[max(0, loud[0] - int(0.05 * sr)) : loud[-1] + int(0.15 * sr)]
    samples = dsp.normalize_rms(samples, -18, peak_limit_db=-1.5)
    return encode_mp3(samples, sr, 64), len(samples) / sr
