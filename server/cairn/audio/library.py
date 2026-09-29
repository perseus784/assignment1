"""The procedural sound library: ambience beds, one-shot effects and music stings.

Every sound is synthesized from scratch (no samples, no licensing), deterministic per
name, and rendered once then cached. Real field recordings can override any entry by
dropping `<name>.wav` into server/assets/sounds/ (see render.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .dsp import (
    SR, band, env_exp, env_hann, fade, filt, glide, noise_burst, normalize_peak, normalize_rms, pan, place,
    poisson_times, reverb, reverb_ir, rng, seconds, shaped_noise, smooth, stereo, tilt, tone,
)

BED_SECONDS = 24.0


@dataclass(frozen=True)
class Sound:
    name: str
    kind: str  # "bed" (seamless loop), "sfx" (one-shot) or "music" (sting)
    description: str
    make: Callable[[], np.ndarray]


# ---------------------------------------------------------------------------------------
# Reusable voices of nature and industry
# ---------------------------------------------------------------------------------------


def mix(*parts: np.ndarray) -> np.ndarray:
    """Sum 1-D signals of different lengths (shorter ones are zero-padded)."""
    out = np.zeros(max(len(p) for p in parts))
    for p in parts:
        out[: len(p)] += p
    return out


def _layer(n: int) -> np.ndarray:
    return np.zeros((n, 2))


def _texture(r, n, response, mod=None, width=0.35) -> np.ndarray:
    """Stereo noise texture: mostly shared, partly independent per channel for width."""
    mid = shaped_noise(r, n, response)
    left = mid * (1 - width) + shaped_noise(r, n, response) * width
    right = mid * (1 - width) + shaped_noise(r, n, response) * width
    if mod is not None:
        left, right = left * mod, right * mod
    return stereo(left, right)


def _events(r, buf, times, make_event, spread=0.8, gain=1.0):
    for t in times:
        ev = make_event()
        p = pan(ev, float(r.uniform(-spread, spread)))
        place(buf[:, 0], p[:, 0], int(t), gain)
        place(buf[:, 1], p[:, 1], int(t), gain)


def _bubble(r, f_lo, f_hi, tau_lo=0.01, tau_hi=0.04):
    f0 = float(np.exp(r.uniform(np.log(f_lo), np.log(f_hi))))
    tau = float(r.uniform(tau_lo, tau_hi))
    dur = tau * 5
    # Minnaert bubble: a decaying sine whose pitch rises as the bubble shrinks.
    f = f0 * (1 + 0.6 * np.linspace(0, 1, seconds(dur)))
    return tone(f, dur) * env_exp(seconds(dur), 0.001, tau)


def _bird_phrase(r) -> np.ndarray:
    kind = r.integers(0, 4)
    parts = []
    if kind == 0:  # melodic warbler: a few gliding notes
        for _ in range(int(r.integers(3, 7))):
            f0 = r.uniform(2200, 3600)
            d = r.uniform(0.07, 0.16)
            note = tone(glide(f0, f0 * r.uniform(0.8, 1.3), d), d, ((1, 1), (2, 0.12))) * env_hann(seconds(d))
            parts += [note, np.zeros(seconds(r.uniform(0.02, 0.07)))]
    elif kind == 1:  # two-note whistle ("fee-bee")
        f = r.uniform(3300, 4100)
        for f0, d in ((f, 0.32), (f * 0.84, 0.36)):
            vib = f0 + 25 * np.sin(2 * np.pi * 30 * np.arange(seconds(d)) / SR)
            parts += [tone(vib, d) * env_hann(seconds(d)), np.zeros(seconds(0.06))]
    elif kind == 2:  # trill
        f0 = r.uniform(4200, 5600)
        for _ in range(int(r.integers(10, 20))):
            d = 0.028
            parts += [tone(glide(f0 * 1.1, f0 * 0.9, d), d) * env_hann(seconds(d)), np.zeros(seconds(0.018))]
    else:  # short chip calls
        for _ in range(int(r.integers(2, 5))):
            d = 0.05
            f0 = r.uniform(3000, 5000)
            parts += [tone(glide(f0, f0 * 1.5, d), d) * env_hann(seconds(d)), np.zeros(seconds(r.uniform(0.1, 0.3)))]
    return np.concatenate(parts)


def _birds(r, n, rate=0.5, gain=0.25) -> np.ndarray:
    buf = _layer(n)
    _events(r, buf, poisson_times(r, n, rate), lambda: _bird_phrase(r), spread=0.9, gain=gain)
    ir = reverb_ir(r, 0.6, 6000)
    return stereo(reverb(buf[:, 0], ir, 0.25), reverb(buf[:, 1], ir, 0.25))


def _wind(r, n, level=1.0, gust_rate=0.08, howl=True) -> np.ndarray:
    base = _texture(r, n, tilt(-3, 60, 1200), mod=0.35 + 0.65 * smooth(r, n, gust_rate))
    out = base * 0.6
    if howl:
        for centre in (260, 420, 690):
            g = smooth(r, n, gust_rate * 1.5) ** 2
            out += _texture(r, n, band(centre * 0.85, centre * 1.18, 4), mod=g, width=0.6) * 0.45
    return out * level


def _hoof(r, surface="dirt") -> np.ndarray:
    lo, hi = (300, 1600) if surface == "dirt" else (700, 3500)
    knock = noise_burst(r, 0.08, lo, hi, 0.001, 0.018)
    thump = tone(90, 0.08) * env_exp(seconds(0.08), 0.001, 0.02)
    return knock + 0.6 * thump


def _metal_hit(r, f0, decay=0.35) -> np.ndarray:
    d = decay * 3
    partials = ((1, 1.0), (2.76, 0.6), (5.4, 0.35), (8.93, 0.2))
    x = sum(a * tone(f0 * m * r.uniform(0.99, 1.01), d) * env_exp(seconds(d), 0.0005, decay / (1 + m * 0.3)) for m, a in partials)
    return mix(x, noise_burst(r, 0.03, 1500, 9000, 0.0005, 0.006) * 0.6)


def _murmur(r, n, voices=14) -> np.ndarray:
    """Crowd babble: formant-filtered noise with syllable-rate envelopes."""
    buf = _layer(n)
    for _ in range(voices):
        centre = r.uniform(400, 1800)
        v = shaped_noise(r, n, band(centre * 0.6, centre * 1.6, 2))
        syll = smooth(r, n, r.uniform(3.5, 6.0)) ** 3  # syllables
        phrase = (smooth(r, n, 0.25) > 0.35).astype(float)  # people pause
        phrase = filt(phrase, "lowpass", 8)
        p = pan(v * syll * phrase, float(r.uniform(-0.8, 0.8)))
        buf += p
    return buf


# ---------------------------------------------------------------------------------------
# Beds (seamless loops)
# ---------------------------------------------------------------------------------------


def bed_river():
    r, n = rng("river"), seconds(BED_SECONDS)
    flow = _texture(r, n, tilt(-3, 150, 5000), mod=0.7 + 0.3 * smooth(r, n, 0.3))
    buf = flow * 0.8
    _events(r, buf, poisson_times(r, n, 45), lambda: _bubble(r, 350, 1600), gain=0.10)
    return normalize_rms(buf, -24)


def bed_waterfall():
    r, n = rng("waterfall"), seconds(BED_SECONDS)
    roar = _texture(r, n, tilt(-2, 80, 7000), mod=0.85 + 0.15 * smooth(r, n, 1.5), width=0.5)
    rumble = _texture(r, n, tilt(-6, 30, 250))
    buf = roar + rumble * 0.8
    _events(r, buf, poisson_times(r, n, 30), lambda: _bubble(r, 500, 2500, 0.005, 0.02), gain=0.05)
    return normalize_rms(buf, -22)


def bed_lake():
    r, n = rng("lake"), seconds(BED_SECONDS)
    buf = _wind(r, n, 0.25, howl=False)
    def lap():
        d = r.uniform(0.35, 0.7)
        x = filt(r.standard_normal(seconds(d)), "bandpass", [180, 1400]) * env_exp(seconds(d), 0.06, d / 3)
        return x
    _events(r, buf, poisson_times(r, n, 1.3), lap, spread=0.6, gain=0.5)
    buf += _birds(r, n, 0.12, 0.12)
    return normalize_rms(buf, -28)


def _wave_layer(r, n):
    buf = _layer(n)
    t = 0
    while t < n:
        period = seconds(r.uniform(7, 11))
        d = period / SR
        env = env_exp(period, d * 0.35, d * 0.25)
        body = filt(r.standard_normal(period), "lowpass", 900) * env
        crash = filt(r.standard_normal(period), "highpass", 1500) * env ** 3 * 0.6
        p = pan(body + crash, float(r.uniform(-0.3, 0.3)))
        place(buf[:, 0], p[:, 0], t)
        place(buf[:, 1], p[:, 1], t)
        t += period
    return buf


def bed_ocean():
    r, n = rng("ocean"), seconds(BED_SECONDS)
    buf = _wave_layer(r, n) + _texture(r, n, tilt(-6, 30, 400)) * 0.5
    return normalize_rms(buf, -24)


def bed_harbor():
    r, n = rng("harbor"), seconds(BED_SECONDS)
    buf = bed_lake() * 1.0
    _events(r, buf, poisson_times(r, n, 0.12), lambda: _gull_call(r), gain=0.25)
    rope = lambda: tone(glide(180, 140, 0.4), 0.4, ((1, 1), (3, 0.3))) * env_hann(seconds(0.4))  # noqa: E731
    _events(r, buf, poisson_times(r, n, 0.2), rope, gain=0.05)
    return normalize_rms(buf, -26)


def bed_forest():
    r, n = rng("forest"), seconds(BED_SECONDS)
    leaves = _texture(r, n, band(500, 6000, 2), mod=0.3 + 0.7 * smooth(r, n, 0.15) ** 2, width=0.7)
    buf = leaves * 0.5 + _wind(r, n, 0.3, howl=False) + _birds(r, n, 0.35, 0.2)
    return normalize_rms(buf, -28)


def bed_birds():
    r, n = rng("birds"), seconds(BED_SECONDS)
    buf = _birds(r, n, 1.1, 0.25) + _texture(r, n, band(300, 4000), mod=0.4 + 0.3 * smooth(r, n, 0.1)) * 0.08
    return normalize_rms(buf, -28)


def bed_meadow():
    r, n = rng("meadow"), seconds(BED_SECONDS)
    buf = _wind(r, n, 0.3, howl=False) + _birds(r, n, 0.25, 0.15)
    for i in range(4):  # crickets
        f = 4400 + i * 170
        chirp = np.concatenate([np.concatenate([tone(f, 0.018) * env_hann(seconds(0.018)), np.zeros(seconds(0.012))]) for _ in range(3)])
        times = np.arange(int(r.integers(0, seconds(0.5))), n, seconds(r.uniform(0.45, 0.65)))
        layer = _layer(n)
        _events(r, layer, times, lambda: chirp, spread=0.9)
        buf += layer * 0.03
    return normalize_rms(buf, -30)


def bed_wind():
    r, n = rng("wind"), seconds(BED_SECONDS)
    return normalize_rms(_wind(r, n, 1.0), -26)


def _drip(r):
    d = 0.25
    f = r.uniform(900, 1700)
    return tone(glide(f, f * 1.9, 0.02).tolist() + [f * 1.9] * (seconds(d) - seconds(0.02)), d) * env_exp(seconds(d), 0.0005, 0.03)


def bed_cave():
    r, n = rng("cave"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 25, 200)) * 0.8
    _events(r, buf, poisson_times(r, n, 0.9), lambda: _drip(r), gain=0.25)
    ir = reverb_ir(r, 2.2, 3500, 0.03)
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.55), reverb(buf[:, 1], ir, 0.55)), -30)


def bed_hot_spring():
    r, n = rng("hot_spring"), seconds(BED_SECONDS)
    hiss = _texture(r, n, band(2500, 12000, 2), mod=0.6 + 0.4 * smooth(r, n, 0.2)) * 0.25
    boil = _texture(r, n, tilt(-6, 40, 300), mod=0.5 + 0.5 * smooth(r, n, 2)) * 0.6
    buf = hiss + boil
    _events(r, buf, poisson_times(r, n, 35), lambda: _bubble(r, 90, 420, 0.02, 0.06), spread=0.5, gain=0.25)
    return normalize_rms(buf, -27)


def bed_mudpot():
    r, n = rng("mudpot"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 30, 200)) * 0.5

    def blorp():
        d = r.uniform(0.12, 0.25)
        body = tone(glide(r.uniform(140, 200), r.uniform(55, 80), d), d, ((1, 1), (2, 0.3))) * env_exp(seconds(d), 0.01, d / 2.5)
        pop = np.zeros_like(body)
        pop[-seconds(0.02):] = noise_burst(r, 0.02, 800, 4000, 0.0005, 0.004)
        return body + pop * 0.5

    _events(r, buf, poisson_times(r, n, 1.6), blorp, spread=0.5, gain=0.8)
    return normalize_rms(buf, -27)


def bed_train():
    r, n = rng("train"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 25, 180)) * 0.9  # rumble
    beat = SR / 3.2  # chuffs per second (a steam engine at speed)
    accents = [1.0, 0.55, 0.8, 0.55]
    count = int(n / beat)
    beat = n / count  # make the rhythm divide the loop exactly
    for i in range(count):
        ch = noise_burst(r, 0.2, 250, 3200, 0.004, 0.07) * accents[i % 4]
        p = pan(ch, 0.0)
        place(buf[:, 0], p[:, 0], int(i * beat), 0.9)
        place(buf[:, 1], p[:, 1], int(i * beat), 0.9)
    joints = int(n / (SR * 1.2))
    for i in range(joints):  # rail joints: a pair of clicks per car
        t0 = int(i * n / joints)
        for off in (0, seconds(0.14)):
            click = noise_burst(r, 0.05, 600, 5000, 0.0005, 0.01) + tone(110, 0.05) * env_exp(seconds(0.05), 0.0005, 0.015)
            place(buf[:, 0], click, t0 + off, 0.5)
            place(buf[:, 1], click, t0 + off + 40, 0.5)
    ir = reverb_ir(r, 0.8, 4000)
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.2), reverb(buf[:, 1], ir, 0.2)), -25)


def bed_mine():
    r, n = rng("mine"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 25, 180)) * 0.6
    _events(r, buf, poisson_times(r, n, 0.5), lambda: _drip(r), gain=0.15)
    t = 0
    while t < n:  # pickaxe / hand-steel strikes in the distance
        f0 = r.uniform(700, 950)
        hit = mix(_metal_hit(r, f0, 0.12), noise_burst(r, 0.06, 150, 900, 0.001, 0.02) * 0.8)
        p = pan(hit, float(r.uniform(-0.6, 0.6)))
        place(buf[:, 0], p[:, 0], t, 0.35)
        place(buf[:, 1], p[:, 1], t, 0.35)
        t += seconds(r.uniform(0.9, 1.8))
    ir = reverb_ir(r, 1.9, 3000, 0.02)
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.5), reverb(buf[:, 1], ir, 0.5)), -27)


def bed_construction():
    r, n = rng("construction"), seconds(BED_SECONDS)
    buf = _wind(r, n, 0.25, howl=False)
    for worker, (period, pos, f0) in enumerate(((0.95, -0.4, 1900), (1.25, 0.5, 2300))):
        t = int(r.integers(0, seconds(period)))
        while t < n:  # hammer on steel (spike driving / hand drilling)
            p = pan(_metal_hit(r, f0 * r.uniform(0.97, 1.03), 0.2), pos)
            place(buf[:, 0], p[:, 0], t, 0.3)
            place(buf[:, 1], p[:, 1], t, 0.3)
            t += seconds(period * r.uniform(0.9, 1.1))

    def shovel():
        d = r.uniform(0.25, 0.45)
        return filt(r.standard_normal(seconds(d)), "bandpass", [900, 5000]) * env_hann(seconds(d))

    _events(r, buf, poisson_times(r, n, 0.4), shovel, gain=0.15)
    buf += _murmur(r, n, 5) * 0.02
    ir = reverb_ir(r, 1.4, 5000, 0.04)  # canyon walls
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.3), reverb(buf[:, 1], ir, 0.3)), -26)


def _hoof_pattern(r, n, gait=(0.0, 0.2, 0.5, 0.7), step_s=1.1, surface="dirt", level=0.3):
    buf = _layer(n)
    cycles = max(1, int(n / seconds(step_s)))
    step = n / cycles
    for c in range(cycles):
        for g in gait:
            p = pan(_hoof(r, surface), 0.2 * np.sin(2 * np.pi * c / cycles))
            place(buf[:, 0], p[:, 0], int(c * step + g * step), level)
            place(buf[:, 1], p[:, 1], int(c * step + g * step), level)
    return buf


def bed_town():
    r, n = rng("town"), seconds(BED_SECONDS)
    buf = _murmur(r, n, 12) * 0.05 + _hoof_pattern(r, n, step_s=1.2, level=0.15)
    t = int(r.integers(0, seconds(2)))
    while t < n:  # blacksmith's anvil down the street
        p = pan(_metal_hit(r, 1250, 0.4), 0.6)
        place(buf[:, 0], p[:, 0], t, 0.08)
        place(buf[:, 1], p[:, 1], t, 0.08)
        t += seconds(r.uniform(1.5, 3.5))
    buf += _wind(r, n, 0.15, howl=False)
    return normalize_rms(buf, -28)


def bed_horses():
    r, n = rng("horses"), seconds(BED_SECONDS)
    buf = _hoof_pattern(r, n, gait=(0.0, 0.5), step_s=0.62, level=0.35) + _wind(r, n, 0.35, howl=False)

    def snort():
        return noise_burst(r, 0.3, 250, 1600, 0.02, 0.1)

    _events(r, buf, poisson_times(r, n, 0.15), snort, gain=0.2)
    return normalize_rms(buf, -27)


def bed_crowd():
    r, n = rng("crowd"), seconds(BED_SECONDS)
    ir = reverb_ir(r, 0.9, 5000)
    buf = _murmur(r, n, 22)
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.3), reverb(buf[:, 1], ir, 0.3)), -28)


def bed_campfire():
    r, n = rng("campfire"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 30, 350), mod=0.6 + 0.4 * smooth(r, n, 1.0)) * 0.6

    def crackle():
        return noise_burst(r, 0.02, 1500, 12000, 0.0002, r.uniform(0.001, 0.005)) * r.exponential(0.6)

    _events(r, buf, poisson_times(r, n, 14), crackle, spread=0.3, gain=0.6)
    _events(r, buf, poisson_times(r, n, 0.8), lambda: noise_burst(r, 0.05, 300, 3000, 0.0005, 0.015), spread=0.3, gain=0.8)
    return normalize_rms(buf, -28)


def bed_city():
    r, n = rng("city"), seconds(BED_SECONDS)
    buf = _texture(r, n, tilt(-6, 30, 400), mod=0.8 + 0.2 * smooth(r, n, 0.2))

    def car():
        d = r.uniform(2.5, 4.5)
        return filt(r.standard_normal(seconds(d)), "bandpass", [80, 1500]) * env_hann(seconds(d)) ** 2

    _events(r, buf, poisson_times(r, n, 0.4), car, spread=1.0, gain=0.6)
    buf += _murmur(r, n, 8) * 0.03
    return normalize_rms(buf, -28)


def bed_solemn():
    r, n = rng("solemn"), seconds(BED_SECONDS)
    swell = 0.5 + 0.5 * smooth(r, n, 0.06)
    drone = np.zeros(n)
    for f, a in ((110.0, 1.0), (164.81, 0.6), (220.0, 0.45), (329.63, 0.18)):
        drone += a * tone(f + 0.3 * np.sin(2 * np.pi * np.arange(n) / n), n / SR, ((1, 1), (2, 0.2)))
    buf = stereo(drone, np.roll(drone, 300)) * swell[:, None] * 0.25 + _wind(r, n, 0.3, howl=False)
    return normalize_rms(buf, -30)


def _piano_note(freq, dur, r=None, vel=1.0):
    n = seconds(dur)
    x = np.zeros(n)
    for k in range(1, 7):
        fk = freq * k * (1 + 0.0004 * k * k)  # slight stiffness inharmonicity
        x += (vel / k**1.4) * tone(fk, dur) * env_exp(n, 0.006, 1.6 / (1 + 0.5 * k))
    return x


def bed_pad():
    r, n = rng("pad"), seconds(BED_SECONDS)
    chords = [(146.83, 185.0, 220.0, 277.18), (123.47, 146.83, 185.0, 220.0)]  # Dmaj7 → Bm7
    half = n // 2
    buf = np.zeros((n, 2))
    for i, chord in enumerate(chords):
        seg = np.zeros(half)
        for f in chord:
            for det in (-0.004, 0.004):
                seg += tone(f * (1 + det), half / SR, ((1, 1), (2, 0.35), (3, 0.12)))
        seg = filt(seg, "lowpass", 1800) * np.hanning(half) ** 0.5
        buf[i * half : (i + 1) * half, 0] += seg
        buf[i * half : (i + 1) * half, 1] += np.roll(seg, 220)
    ir = reverb_ir(r, 3.0, 4000)
    return normalize_rms(stereo(reverb(buf[:, 0], ir, 0.5), reverb(buf[:, 1], ir, 0.5)), -30)


# ---------------------------------------------------------------------------------------
# One-shot effects
# ---------------------------------------------------------------------------------------


def _tail(x_stereo: np.ndarray, decay: float, wet: float, name: str) -> np.ndarray:
    r = rng(name + "-rev")
    ir = reverb_ir(r, decay, 5000, 0.02)
    pad_ = np.zeros((len(ir), 2))
    x = np.concatenate([x_stereo, pad_])
    return stereo(reverb(x[:, 0], ir, wet, circular=False), reverb(x[:, 1], ir * 0.98, wet, circular=False))


def sfx_geyser_eruption():
    r = rng("geyser_eruption")
    d = 11.0
    n = seconds(d)
    t = np.arange(n) / SR
    env = np.clip(t / 1.8, 0, 1) ** 2 * np.where(t < 6.5, 1, np.exp(-(t - 6.5) / 1.4))
    roar = shaped_noise(r, n, tilt(-3, 40, 2500)) * env
    hiss = shaped_noise(r, n, band(2500, 14000)) * env ** 1.5 * 0.4
    whump = tone(glide(70, 40, 0.6), 0.6) * env_exp(seconds(0.6), 0.02, 0.2)
    x = roar + hiss
    x[: len(whump)] += whump * 1.5
    buf = stereo(x, np.roll(x, 400))
    splash = _layer(n)
    times = (r.beta(2, 2, 90) * 8 * SR).astype(int) + seconds(1.5)
    _events(r, splash, times, lambda: noise_burst(r, 0.08, 1500, 9000, 0.001, 0.02), gain=0.25)
    return normalize_peak(buf + splash, -2)


def _gull_call(r) -> np.ndarray:
    parts = []
    base = r.uniform(1400, 1900)
    for i in range(int(r.integers(2, 5))):
        d = r.uniform(0.18, 0.3)
        f = glide(base * 1.25, base * 0.85, d)
        rough = 1 + 0.4 * np.sin(2 * np.pi * 45 * np.arange(len(f)) / SR)
        parts += [tone(f, d, ((1, 1), (2, 0.5), (3, 0.3))) * rough * env_hann(seconds(d)), np.zeros(seconds(0.08))]
        base *= 0.96
    return np.concatenate(parts)


def sfx_gulls():
    r = rng("gulls")
    x = np.concatenate([_gull_call(r), np.zeros(seconds(0.4)), _gull_call(r) * 0.6])
    return normalize_peak(_tail(pan(x, 0.4), 1.0, 0.25, "gulls"), -4)


def sfx_raptor():
    r = rng("raptor")
    d = 1.6
    f = glide(3200, 1900, d)
    x = tone(f, d, ((1, 1), (2, 0.25), (3, 0.1))) * env_exp(seconds(d), 0.08, 0.6)
    x += filt(r.standard_normal(seconds(d)), "bandpass", [1800, 4000]) * env_exp(seconds(d), 0.08, 0.5) * 0.25
    return normalize_peak(_tail(pan(x, -0.3), 2.5, 0.45, "raptor"), -5)


def sfx_wolf_howl():
    r = rng("wolf_howl")
    d = 4.0
    n = seconds(d)
    t = np.arange(n) / SR
    contour = np.interp(t, [0, 0.8, 2.6, 4.0], [360, 610, 590, 430]) + 7 * np.sin(2 * np.pi * 5 * t)
    x = tone(contour, d, ((1, 1), (2, 0.18), (3, 0.06))) * np.interp(t, [0, 0.4, 3.2, 4.0], [0, 1, 0.8, 0])
    x += filt(r.standard_normal(n), "bandpass", [300, 1400]) * 0.04 * np.interp(t, [0, 0.4, 3.2, 4.0], [0, 1, 0.8, 0])
    return normalize_peak(_tail(pan(x, 0.3), 3.5, 0.5, "wolf_howl"), -5)


def sfx_train_whistle():
    d = 3.2
    n = seconds(d)
    t = np.arange(n) / SR
    env = np.interp(t, [0, 0.12, 1.4, 1.55, 1.75, 1.85, 2.9, 3.2], [0, 1, 0.95, 0, 0, 1, 0.9, 0])
    x = np.zeros(n)
    for f in (349.23, 440.0, 523.25):  # steam chime chord
        pitch = f * (1 - 0.03 * np.exp(-t / 0.08))
        x += tone(pitch, d, ((1, 1), (2, 0.3), (3, 0.12)))
    r = rng("train_whistle")
    x += filt(r.standard_normal(n), "bandpass", [300, 1800]) * 0.25
    x *= env
    return normalize_peak(_tail(pan(x, -0.2), 3.0, 0.45, "train_whistle"), -4)


def sfx_blast():
    r = rng("blast")
    d = 5.5
    n = seconds(d)
    crack = noise_burst(r, 0.05, 800, 8000, 0.0005, 0.01)
    boom = filt(shaped_noise(r, n, tilt(-6, 20, 300)), "lowpass", 180) * env_exp(n, 0.01, 1.2)
    x = boom * 1.0
    x[: len(crack)] += crack * 0.4
    for delay, g in ((0.45, 0.35), (1.1, 0.18)):  # echoes off the valley walls
        k = seconds(delay)
        x[k:] += boom[: n - k] * g
    debris = np.zeros(n)
    times = seconds(0.8) + (r.exponential(1.0, 60) * SR).astype(int)
    for tt in times[times < n - SR // 10]:
        place(debris, noise_burst(r, 0.03, 1000, 6000, 0.0005, 0.006), int(tt), 0.15, wrap=False)
    return normalize_peak(stereo(x + debris, np.roll(x, 200) + debris), -3)


def sfx_bell():
    r = rng("bell")
    d = 10.0
    n = seconds(d)
    base = 330.0
    strike = np.zeros(n)
    for m, a, dec in ((0.5, 0.6, 4.0), (1.0, 1.0, 3.0), (1.2, 0.5, 2.2), (1.5, 0.4, 2.0), (2.0, 0.45, 1.6), (2.5, 0.2, 1.0), (3.0, 0.15, 0.8)):
        strike += a * tone(base * m, d) * env_exp(n, 0.002, dec)
    x = strike.copy()
    k = seconds(2.4)
    x[k:] += strike[: n - k] * 0.85
    x = fade(x, out_s=1.5)
    return normalize_peak(_tail(pan(x, 0.25), 3.0, 0.4, "bell"), -4)


def sfx_splash():
    r = rng("splash")
    x = noise_burst(r, 1.2, 400, 9000, 0.005, 0.25)
    buf = pan(x, 0.2)
    _events(r, buf, (r.random(40) * SR * 0.9).astype(int), lambda: _bubble(r, 400, 2000, 0.005, 0.02), gain=0.2)
    return normalize_peak(buf, -4)


def sfx_thunder():
    r = rng("thunder")
    d = 7.0
    n = seconds(d)
    x = shaped_noise(r, n, tilt(-6, 20, 600)) * env_exp(n, 0.3, 2.0) * (0.6 + 0.4 * smooth(r, n, 3))
    return normalize_peak(stereo(x, np.roll(x, 900)), -3)


def sfx_hoofbeats():
    r = rng("hoofbeats")
    n = seconds(5.0)
    buf = _hoof_pattern(r, n, gait=(0.0, 0.25, 0.5, 0.75), step_s=0.45, level=1.0)
    ramp = np.interp(np.arange(n), [0, n / 2, n], [0.1, 1, 0.1])
    buf[:, 0] *= ramp * np.linspace(1.2, 0.4, n)  # passes left to right
    buf[:, 1] *= ramp * np.linspace(0.4, 1.2, n)
    return normalize_peak(buf, -5)


# ---------------------------------------------------------------------------------------
# Music stings
# ---------------------------------------------------------------------------------------


def _music(notes, total_s, pad_chord=None, name="music"):
    r = rng(name)
    total_s = max(total_s, max(at + dur for at, _, dur, _ in notes))
    n = seconds(total_s)
    x = np.zeros(n)
    for at, freq, dur, vel in notes:
        note = _piano_note(freq, dur, r, vel)
        place(x, note, seconds(at), 1.0, wrap=False)
    if pad_chord:
        pad_ = np.zeros(n)
        for f in pad_chord:
            pad_ += tone(f, total_s, ((1, 1), (2, 0.3)))
        env = np.interp(np.arange(n), [0, n * 0.3, n * 0.75, n], [0, 0.12, 0.1, 0])
        x += filt(pad_, "lowpass", 1500) * env
    x = fade(x, out_s=min(1.0, total_s / 3))
    return normalize_peak(_tail(stereo(x, np.roll(x, 150)), 2.5, 0.35, name), -4)


D4, Fs4, A4, B4, Cs5, D5, E5, Fs5, A3, D3 = 293.66, 369.99, 440.0, 493.88, 554.37, 587.33, 659.25, 739.99, 220.0, 146.83


def music_intro():
    notes = [(0.0, D4, 3.0, 0.8), (0.35, A4, 3.0, 0.6), (0.7, D5, 3.0, 0.6), (1.05, Fs5, 3.0, 0.55), (1.8, E5, 2.6, 0.45), (2.4, D5, 2.8, 0.5)]
    return _music(notes, 5.5, (D3, A3, Fs4), "music_intro")


def music_transition():
    notes = [(0.0, A4, 2.0, 0.55), (0.28, D5, 2.2, 0.5)]
    return _music(notes, 2.6, None, "music_transition")


def music_outro():
    notes = [(0.0, Fs5, 2.5, 0.45), (0.35, D5, 2.5, 0.45), (0.7, A4, 2.8, 0.45), (1.1, D4, 3.2, 0.6)]
    return _music(notes, 4.5, (D3, A3, D4), "music_outro")


LIBRARY: dict[str, Sound] = {
    s.name: s
    for s in [
        Sound("river", "bed", "flowing river with bubbling water", bed_river),
        Sound("waterfall", "bed", "roaring waterfall", bed_waterfall),
        Sound("lake", "bed", "gentle lake lapping, light breeze", bed_lake),
        Sound("ocean", "bed", "ocean waves on a shore", bed_ocean),
        Sound("harbor", "bed", "harbor water, gulls, creaking ropes", bed_harbor),
        Sound("forest", "bed", "wind in trees with birdsong", bed_forest),
        Sound("birds", "bed", "lively birdsong", bed_birds),
        Sound("meadow", "bed", "open meadow: crickets, birds, breeze", bed_meadow),
        Sound("wind", "bed", "mountain wind with gusts", bed_wind),
        Sound("cave", "bed", "echoing cave with water drips", bed_cave),
        Sound("hot_spring", "bed", "boiling, hissing hot spring", bed_hot_spring),
        Sound("mudpot", "bed", "bubbling, plopping mud", bed_mudpot),
        Sound("train", "bed", "steam train chuffing along the rails", bed_train),
        Sound("mine", "bed", "mine tunnel: distant pickaxes, drips, echo", bed_mine),
        Sound("construction", "bed", "road/rail building crews: hammers on steel, shovels", bed_construction),
        Sound("town", "bed", "frontier town: murmur, hooves, blacksmith", bed_town),
        Sound("horses", "bed", "horses on a dirt trail", bed_horses),
        Sound("crowd", "bed", "crowd of people talking", bed_crowd),
        Sound("campfire", "bed", "crackling campfire", bed_campfire),
        Sound("city", "bed", "distant city traffic", bed_city),
        Sound("solemn", "bed", "low reflective drone with wind", bed_solemn),
        Sound("pad", "bed", "warm musical pad for intros", bed_pad),
        Sound("geyser_eruption", "sfx", "a geyser erupting", sfx_geyser_eruption),
        Sound("train_whistle", "sfx", "steam train whistle echoing", sfx_train_whistle),
        Sound("blast", "sfx", "distant dynamite blast echoing", sfx_blast),
        Sound("bell", "sfx", "church bell tolling", sfx_bell),
        Sound("raptor", "sfx", "hawk cry overhead", sfx_raptor),
        Sound("gulls", "sfx", "seagulls calling", sfx_gulls),
        Sound("wolf_howl", "sfx", "a lone wolf howling", sfx_wolf_howl),
        Sound("splash", "sfx", "water splash", sfx_splash),
        Sound("thunder", "sfx", "distant thunder", sfx_thunder),
        Sound("hoofbeats", "sfx", "horses galloping past", sfx_hoofbeats),
        Sound("intro", "music", "warm opening sting", music_intro),
        Sound("transition", "music", "soft two-note chime between stories", music_transition),
        Sound("outro", "music", "gentle closing sting", music_outro),
    ]
}

# Theme (from classify.py) -> (ambience bed, accent one-shot)
THEME_SOUNDS: dict[str, tuple[str | None, str | None]] = {
    "geyser": ("hot_spring", "geyser_eruption"), "hot_spring": ("hot_spring", None), "mudpot": ("mudpot", None),
    "waterfall": ("waterfall", None), "river": ("river", None), "lake": ("lake", None), "ocean": ("ocean", "gulls"),
    "harbor": ("harbor", None), "forest": ("forest", None), "birds": ("birds", None), "meadow": ("meadow", None),
    "mountain": ("wind", "raptor"), "wind": ("wind", None), "cave": ("cave", None),
    "train": ("train", "train_whistle"), "mining": ("mine", "blast"), "construction": ("construction", "blast"),
    "town": ("town", None), "horses": ("horses", "hoofbeats"), "crowd": ("crowd", None), "bells": (None, "bell"),
    "campfire": ("campfire", None), "city": ("city", None), "solemn": ("solemn", None),
}


def names(kind: str | None = None) -> list[str]:
    return [s.name for s in LIBRARY.values() if kind is None or s.kind == kind]
