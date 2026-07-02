"""Unit tests for direction.py - the pure math behind the radar.

These run without any audio hardware, Qt, or Windows APIs (plain numpy), so
they work in CI on Linux.
"""

import math

import numpy as np
import pytest

from direction import angle_diff, band_rms, stereo_angle, surround_angle

SR = 48000
N = 2400  # one 50ms capture chunk, same as audio_capture.py


def sine(freq, amp=1.0, n=N, sr=SR):
    t = np.arange(n) / sr
    return amp * np.sin(2 * np.pi * freq * t)


# ── band_rms ───────────────────────────────────────────────────────────

def test_full_range_equals_plain_rms():
    rng = np.random.default_rng(0)
    data = rng.standard_normal((N, 2))
    expected = np.sqrt(np.mean(data ** 2, axis=0))
    np.testing.assert_allclose(band_rms(data, SR, 20, 20000), expected)


def test_in_band_sine_keeps_its_rms():
    # 440 Hz sine, amplitude 0.5 -> RMS = 0.5/sqrt(2)
    data = sine(440, amp=0.5)[:, None]
    rms = band_rms(data, SR, 100, 900)
    assert rms[0] == pytest.approx(0.5 / math.sqrt(2), rel=0.05)


def test_out_of_band_sine_is_rejected():
    data = sine(4000, amp=0.5)[:, None]
    rms = band_rms(data, SR, 100, 900)
    assert rms[0] < 0.01


def test_band_separates_mixed_signal():
    # Footstep-band tone + loud out-of-band rumble: the band RMS should see
    # (approximately) only the in-band component.
    in_band = sine(300, amp=0.2)
    rumble = sine(40, amp=1.0)
    data = (in_band + rumble)[:, None]
    rms = band_rms(data, SR, 100, 900)
    assert rms[0] == pytest.approx(0.2 / math.sqrt(2), rel=0.1)


def test_per_channel_independence():
    data = np.stack([sine(300, amp=0.4), sine(4000, amp=0.4)], axis=1)
    rms = band_rms(data, SR, 100, 900)
    assert rms[0] > 10 * rms[1]


# ── stereo_angle ───────────────────────────────────────────────────────

def test_stereo_centre_is_zero():
    assert stereo_angle(0.5, 0.5) == pytest.approx(0.0, abs=0.1)


def test_stereo_hard_right_and_left():
    assert stereo_angle(0.0, 0.5) == pytest.approx(90.0, rel=0.01)
    assert stereo_angle(0.5, 0.0) == pytest.approx(-90.0, rel=0.01)


def test_stereo_slight_pan_is_expanded():
    # The 0.3 exponent should push a mild 60/40 imbalance well off centre.
    angle = stereo_angle(0.4, 0.6)
    assert 30.0 < angle < 90.0


# ── surround_angle ─────────────────────────────────────────────────────

def test_surround_front_is_zero():
    assert surround_angle(1.0, 1.0, 1.0, 0.0, 0.0) == pytest.approx(0.0)


def test_surround_right_is_90():
    assert surround_angle(0.0, 1.0, 0.0, 0.0, 1.0) == pytest.approx(90.0)


def test_surround_left_is_minus_90():
    assert surround_angle(1.0, 0.0, 0.0, 1.0, 0.0) == pytest.approx(-90.0)


def test_surround_rear_is_180():
    assert abs(surround_angle(0.0, 0.0, 0.0, 1.0, 1.0)) == pytest.approx(180.0)


# ── angle_diff ─────────────────────────────────────────────────────────

def test_angle_diff_simple():
    assert angle_diff(10.0, 30.0) == pytest.approx(20.0)
    assert angle_diff(30.0, 10.0) == pytest.approx(20.0)


def test_angle_diff_wraps_at_180():
    # A sound directly behind the player: -179 and +179 are 2 degrees apart,
    # not 358 (the bug that split rear blips in two).
    assert angle_diff(179.0, -179.0) == pytest.approx(2.0)
    assert angle_diff(-170.0, 170.0) == pytest.approx(20.0)


def test_angle_diff_opposites():
    assert angle_diff(90.0, -90.0) == pytest.approx(180.0)
