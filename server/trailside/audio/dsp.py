"""DSP building blocks for procedural sound design.

Loops are made *circular* by construction: noise is shaped in the frequency domain,
modulators are band-limited periodic noise, events wrap around the buffer end and reverb
is a circular convolution. The result loops seamlessly with no crossfade.
"""
from __future__ import annotations

import zlib

import numpy as np
from scipy import signal

SR = 44_100


def rng(name: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(name.encode()))


def seconds(n: float) -> int:
    return int(round(n * SR))


def _freqs(n: int) -> np.ndarray:
    return np.fft.rfftfreq(n, 1 / SR)


def shaped_noise(r: np.random.Generator, n: int, response) -> np.ndarray:
    """White noise filtered by an arbitrary magnitude response (periodic in n)."""
    spec = np.fft.rfft(r.standard_normal(n))
    f = _freqs(n)
    f[0] = 1e-3
    out = np.fft.irfft(spec * response(f), n)
    return out / (np.std(out) + 1e-12)


def band(lo: float, hi: float, order: int = 2):
    def h(f):
        low = 1 / np.sqrt(1 + (lo / f) ** (2 * order)) if lo > 0 else 1
        high = 1 / np.sqrt(1 + (f / hi) ** (2 * order)) if hi else 1
        return low * high
    return h


def tilt(slope_db_per_oct: float, lo: float = 20, hi: float | None = None, order: int = 2):
    """1/f^a noise colouring (pink ≈ -3 dB/oct, brown ≈ -6 dB/oct) with optional band limits."""
    a = -slope_db_per_oct / 6.02
    b = band(lo, hi, order)
    return lambda f: np.power(np.maximum(f, lo), -a) * b(f)


def smooth(r: np.random.Generator, n: int, cutoff_hz: float) -> np.ndarray:
    """Periodic, band-limited random control signal in 0..1."""
    spec = np.fft.rfft(r.standard_normal(n))
    f = _freqs(n)
    spec[f > cutoff_hz] = 0
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    x -= x.min()
    return x / (x.max() + 1e-12)


def place(buf: np.ndarray, event: np.ndarray, at: int, gain: float = 1.0, wrap: bool = True) -> None:
    """Add `event` into `buf` at sample `at`; wraps around the end for seamless loops."""
    n = len(buf)
    m = len(event)
    if wrap:
        idx = (np.arange(m) + at) % n
        np.add.at(buf, idx, event * gain)
    else:
        end = min(n, at + m)
        if at < n:
            buf[at:end] += event[: end - at] * gain


def poisson_times(r: np.random.Generator, n: int, rate_hz: float, jitter: float = 1.0) -> np.ndarray:
    count = max(1, r.poisson(rate_hz * n / SR))
    return np.sort(r.integers(0, n, count))


def env_exp(n: int, attack_s: float, decay_s: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack_s, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(0, t - attack_s) / max(decay_s, 1e-4))


def env_hann(n: int) -> np.ndarray:
    return np.hanning(n) if n > 1 else np.ones(n)


def tone(freq, dur_s: float, partials=((1, 1.0),), phase0: float = 0.0) -> np.ndarray:
    """Additive tone. `freq` may be a scalar or a per-sample array (glides/vibrato)."""
    n = seconds(dur_s)
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,)) if np.ndim(freq) == 0 else np.asarray(freq, dtype=float)[:n]
    phase = 2 * np.pi * np.cumsum(f) / SR + phase0
    out = np.zeros(n)
    for mult, amp in partials:
        out += amp * np.sin(phase * mult)
    return out


def glide(f0: float, f1: float, dur_s: float, curve: str = "exp") -> np.ndarray:
    t = np.linspace(0, 1, seconds(dur_s), endpoint=False)
    if curve == "exp":
        return f0 * (f1 / f0) ** t
    return f0 + (f1 - f0) * t


def filt(x: np.ndarray, kind: str, freq, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, freq, btype=kind, fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def noise_burst(r: np.random.Generator, dur_s: float, lo: float, hi: float, attack_s: float = 0.002, decay_s: float = 0.05) -> np.ndarray:
    n = seconds(dur_s)
    x = filt(r.standard_normal(n), "bandpass", [lo, min(hi, SR / 2 - 100)])
    return x * env_exp(n, attack_s, decay_s)


def reverb_ir(r: np.random.Generator, decay_s: float, damp_hz: float = 5000, predelay_s: float = 0.01) -> np.ndarray:
    n = seconds(decay_s * 1.5)
    t = np.arange(n) / SR
    ir = r.standard_normal(n) * np.exp(-t * 6.9 / decay_s)
    ir = filt(ir, "lowpass", damp_hz)
    ir[: seconds(predelay_s)] = 0
    ir[0] = 0
    return ir / np.sqrt(np.sum(ir**2) + 1e-12)


def reverb(x: np.ndarray, ir: np.ndarray, wet: float, circular: bool = True) -> np.ndarray:
    if circular:
        n = len(x)
        h = np.zeros(n)
        h[: min(n, len(ir))] = ir[:n]
        y = np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(h), n)
    else:
        y = signal.fftconvolve(x, ir)[: len(x)]
    return x * (1 - wet) + y * wet


def pan(mono: np.ndarray, position: float) -> np.ndarray:
    """Equal-power pan, position -1 (left) .. 1 (right). Returns (n, 2)."""
    a = (position + 1) * np.pi / 4
    return np.stack([mono * np.cos(a), mono * np.sin(a)], axis=1)


def stereo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.stack([left, right], axis=1)


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


def normalize_rms(x: np.ndarray, target_db: float, peak_limit_db: float = -1.0) -> np.ndarray:
    y = x * 10 ** ((target_db - rms_db(x)) / 20)
    peak = np.max(np.abs(y)) + 1e-12
    limit = 10 ** (peak_limit_db / 20)
    if peak > limit:  # soft-knee limiter rather than hard gain drop
        y = np.tanh(y / limit) * limit
    return y


def normalize_peak(x: np.ndarray, target_db: float = -2.0) -> np.ndarray:
    return x * (10 ** (target_db / 20) / (np.max(np.abs(x)) + 1e-12))


def fade(x: np.ndarray, in_s: float = 0.0, out_s: float = 0.0) -> np.ndarray:
    y = x.copy()
    if in_s:
        k = seconds(in_s)
        ramp = np.linspace(0, 1, k) ** 2
        y[:k] *= ramp[:, None] if y.ndim == 2 else ramp
    if out_s:
        k = seconds(out_s)
        ramp = np.linspace(1, 0, k) ** 2
        y[-k:] *= ramp[:, None] if y.ndim == 2 else ramp
    return y
