import numpy as np
import pytest

from cairn.audio import dsp
from cairn.audio.library import LIBRARY, THEME_SOUNDS
from cairn.audio.render import encode_mp3, render_sound


def test_theme_sounds_exist_with_right_kinds():
    for bed, accent in THEME_SOUNDS.values():
        assert bed is None or LIBRARY[bed].kind == "bed"
        assert accent is None or LIBRARY[accent].kind == "sfx"


@pytest.mark.parametrize("name", ["river", "train", "mine", "town", "geyser_eruption", "intro"])
def test_sounds_are_clean(name):
    x = LIBRARY[name].make()
    assert x.ndim == 2 and x.shape[1] == 2 and np.isfinite(x).all()
    assert np.abs(x).max() < 1.0
    assert dsp.rms_db(x) > -45  # not silent
    if LIBRARY[name].kind == "bed":
        m = x.mean(axis=1)
        # Seamless loop: the jump across the seam is no bigger than a typical sample step.
        assert abs(m[0] - m[-1]) < 8 * np.median(np.abs(np.diff(m))) + 1e-4


def test_deterministic():
    assert np.array_equal(LIBRARY["birds"].make(), LIBRARY["birds"].make())


def test_mp3_encode_and_cache():
    data, dur = render_sound("splash")
    assert data[:3] == b"ID3" or data[0] == 0xFF
    assert 0.5 < dur < 3
    data2, _ = render_sound("splash")
    assert data == data2
    assert len(encode_mp3(np.zeros(4410), 44100)) > 0
