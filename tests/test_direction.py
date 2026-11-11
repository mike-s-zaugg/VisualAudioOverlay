"""Unit tests for direction.py - the pure math behind the radar.

These run without any audio hardware, Qt, or Windows APIs (plain numpy), so
they work in CI on Linux.
"""

import math

import numpy as np
import pytest

from direction import (LAYOUTS, SURROUND_EVIDENCE_THRESHOLD,
                       SURROUND_HOLD_SECONDS, SurroundModeTracker,
                       angle_diff, band_rms, layout_angle, layout_for_channels,
                       layout_intensity, stereo_angle, surround_angle,
                       surround_evidence)

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


# ── Channel layouts ────────────────────────────────────────────────────

def levels(n, **named):
    """Level list for an n-channel layout, by channel index name."""
    out = [0.0] * n
    for idx, value in named.items():
        out[int(idx[1:])] = value
    return out


def test_layout_names_by_channel_count():
    assert {n: layout_for_channels(n).name for n in (1, 2, 4, 6, 7, 8)} == {
        1: "mono", 2: "stereo", 4: "quad", 6: "5.1", 7: "6.1", 8: "7.1"}


@pytest.mark.parametrize("count, name", [
    (0, "mono"), (-3, "mono"), (3, "stereo"), (5, "stereo"),
    (9, "7.1"), (12, "7.1"), (32, "7.1"), (None, "mono"), ("junk", "mono")])
def test_unexpected_channel_counts_fall_back(count, name):
    assert layout_for_channels(count).name == name


def test_51_matches_old_formula_exactly():
    # The radar must not move for players whose 5.1 setup already worked:
    # same float, not just close.
    rng = np.random.default_rng(42)
    layout = layout_for_channels(6)
    for _ in range(2000):
        fl, fr, c, lfe, rl, rr = rng.random(6) * rng.choice([1e-4, 0.01, 1.0])
        assert layout_angle([fl, fr, c, lfe, rl, rr], layout) == \
            surround_angle(fl, fr, c, rl, rr)


def test_51_with_zeros_matches_old_formula():
    rng = np.random.default_rng(7)
    layout = layout_for_channels(6)
    for _ in range(500):
        vals = rng.random(6) * (rng.random(6) > 0.5)
        fl, fr, c, lfe, rl, rr = vals
        assert layout_angle(list(vals), layout) == surround_angle(fl, fr, c, rl, rr)


@pytest.mark.parametrize("n", [4, 6, 7, 8])
def test_pure_front_pair_and_rear_pair(n):
    layout = layout_for_channels(n)
    rear = (2, 3) if n == 4 else (4, 5)
    assert layout_angle(levels(n, c0=1.0), layout) == pytest.approx(-45.0)
    assert layout_angle(levels(n, c1=1.0), layout) == pytest.approx(45.0)
    assert layout_angle(levels(n, c0=1.0, c1=1.0), layout) == pytest.approx(0.0)
    if n != 7:   # 6.1 has a single back centre instead of a rear pair
        both = levels(n, **{f"c{rear[0]}": 1.0, f"c{rear[1]}": 1.0})
        assert abs(layout_angle(both, layout)) == pytest.approx(180.0)
        assert layout_angle(levels(n, **{f"c{rear[0]}": 1.0}), layout) == pytest.approx(-135.0)
        assert layout_angle(levels(n, **{f"c{rear[1]}": 1.0}), layout) == pytest.approx(135.0)


@pytest.mark.parametrize("n", [7, 8])
def test_side_speakers(n):
    layout = layout_for_channels(n)
    sl, sr = n - 2, n - 1
    assert layout_angle(levels(n, **{f"c{sl}": 0.3}), layout) == pytest.approx(-90.0)
    assert layout_angle(levels(n, **{f"c{sr}": 0.3}), layout) == pytest.approx(90.0)
    assert layout_angle(levels(n, c0=0.5, **{f"c{sl}": 0.5}), layout) == pytest.approx(-67.5)
    assert layout_angle(levels(n, c1=0.5, **{f"c{sr}": 0.5}), layout) == pytest.approx(67.5)


def test_61_back_centre_is_rear():
    layout = layout_for_channels(7)
    assert abs(layout_angle(levels(7, c4=1.0), layout)) == pytest.approx(180.0)


def test_71_side_plus_back_is_between():
    layout = layout_for_channels(8)
    angle = layout_angle(levels(8, c4=0.5, c6=0.5), layout)   # BL + SL
    assert -135.0 < angle < -90.0


def test_centre_is_front():
    for n in (6, 7, 8):
        assert layout_angle(levels(n, c2=1.0), layout_for_channels(n)) == pytest.approx(0.0)


@pytest.mark.parametrize("n", [1, 2, 4, 6, 7, 8])
def test_silence_does_not_raise(n):
    layout = layout_for_channels(n)
    assert layout_angle([0.0] * n, layout) == pytest.approx(0.0, abs=1e-6)
    assert layout_intensity([0.0] * n, layout) == 0.0


@pytest.mark.parametrize("n", [6, 7, 8])
def test_lfe_never_moves_angle_or_intensity(n):
    layout = layout_for_channels(n)
    rng = np.random.default_rng(n)
    for _ in range(200):
        base = list(rng.random(n) * 0.2)
        loud_lfe = list(base)
        loud_lfe[3] = 5.0
        assert layout_angle(loud_lfe, layout) == layout_angle(base, layout)
        assert layout_intensity(loud_lfe, layout) == layout_intensity(base, layout)


def test_intensity_is_loudest_direction_channel():
    layout = layout_for_channels(8)
    assert layout_intensity(levels(8, c0=0.1, c7=0.4, c3=0.9), layout) == 0.4


def test_stereo_layout_uses_stereo_angle():
    layout = layout_for_channels(2)
    assert layout_angle([0.4, 0.6], layout) == stereo_angle(0.4, 0.6)
    assert layout_angle([0.7], layout_for_channels(1)) == 0.0


def test_extra_channels_beyond_layout_are_ignored():
    layout = layout_for_channels(10)
    vals = levels(10, c6=1.0, c8=5.0, c9=5.0)
    assert layout_angle(vals, layout) == pytest.approx(-90.0)
    assert layout_intensity(vals, layout) == 1.0


def test_every_layout_has_one_vector_per_channel():
    for n, layout in LAYOUTS.items():
        assert layout.channels == n
        assert len(layout.vectors) == n


# ── Surround evidence ──────────────────────────────────────────────────

def test_evidence_ignores_front_pair_and_lfe():
    chunk = np.zeros((N, 6))
    chunk[:, 0] = chunk[:, 1] = 0.9
    chunk[:, 3] = 0.8               # LFE: bass or DC leaking from a driver
    assert surround_evidence(chunk, layout_for_channels(6)) == 0.0
    chunk[10, 5] = -0.25            # rear right, negative peak
    assert surround_evidence(chunk, layout_for_channels(6)) == 0.25


@pytest.mark.parametrize("n, channels", [
    (4, (2, 3)), (6, (2, 4, 5)), (7, (2, 4, 5, 6)), (8, (2, 4, 5, 6, 7))])
def test_evidence_channels_per_layout(n, channels):
    layout = layout_for_channels(n)
    for ch in range(n):
        chunk = np.zeros((N, n))
        chunk[100, ch] = 0.5
        expected = 0.5 if ch in channels else 0.0
        assert surround_evidence(chunk, layout) == expected, ch


def test_threshold_is_about_minus_60_dbfs():
    assert SURROUND_EVIDENCE_THRESHOLD == 0.001


def test_evidence_is_zero_for_stereo_and_fallback_layouts():
    assert surround_evidence(np.ones((N, 2)), layout_for_channels(2)) == 0.0
    assert surround_evidence(np.ones((N, 5)), layout_for_channels(5)) == 0.0
    assert surround_evidence(np.ones((N, 1)), layout_for_channels(1)) == 0.0


def test_evidence_only_reads_channels_of_the_layout():
    chunk = np.zeros((N, 10))
    chunk[:, 9] = 1.0               # beyond the 7.1 the layout uses
    assert surround_evidence(chunk, layout_for_channels(10)) == 0.0
    chunk[:, 7] = 0.5
    assert surround_evidence(chunk, layout_for_channels(10)) == 0.5


# ── Mode tracker ───────────────────────────────────────────────────────

CHUNK_S = N / SR       # 0.05 s, as in the capture loop


def feed(tracker, evidence, seconds):
    """Feed `seconds` of chunks; return the chunk indices that changed mode."""
    changes = []
    for i in range(int(round(seconds / CHUNK_S))):
        if tracker.update(evidence, CHUNK_S):
            changes.append(i)
    return changes


def test_tracker_starts_stereo():
    t = SurroundModeTracker(layout_for_channels(8))
    assert not t.surround and t.mode == "stereo"


def test_tracker_switches_on_first_surround_chunk_after_silence():
    t = SurroundModeTracker(layout_for_channels(6))
    assert feed(t, 0.0, 10.0) == []           # quiet menu: stays stereo
    assert t.update(0.01, CHUNK_S) is True    # first chunk with surround content
    assert t.mode == "surround"
    assert t.update(0.01, CHUNK_S) is False   # no repeated change report


def test_tracker_threshold_is_strict():
    t = SurroundModeTracker(layout_for_channels(6))
    assert t.update(0.001, CHUNK_S) is False
    assert t.update(0.0011, CHUNK_S) is True


def make_chunk(n, ch=None, peak=0.0):
    chunk = np.zeros((N, n), dtype=np.float32)
    chunk[:, 0] = chunk[:, 1] = 0.3        # a stereo game in the front pair
    if ch is not None:
        chunk[:, ch] = peak * np.sin(np.linspace(0, 40 * np.pi, N))
    return chunk


def test_lfe_only_signal_never_switches():
    for n in (6, 7, 8):
        layout = layout_for_channels(n)
        t = SurroundModeTracker(layout)
        for _ in range(200):
            assert t.update(surround_evidence(make_chunk(n, 3, 0.9), layout), CHUNK_S) is False
        assert t.mode == "stereo"


def test_quiet_leak_on_rear_does_not_switch():
    # Hiss at -66 dBFS on a rear channel while a stereo game plays.
    layout = layout_for_channels(6)
    t = SurroundModeTracker(layout)
    for _ in range(200):
        assert t.update(surround_evidence(make_chunk(6, 4, 0.0005), layout), CHUNK_S) is False


def test_single_rear_chunk_above_threshold_switches_at_once():
    layout = layout_for_channels(8)
    t = SurroundModeTracker(layout)
    t.update(surround_evidence(make_chunk(8), layout), CHUNK_S)
    assert t.update(surround_evidence(make_chunk(8, 4, 0.002), layout), CHUNK_S) is True
    t2 = SurroundModeTracker(layout)
    assert t2.update(surround_evidence(make_chunk(8, 7, 0.002), layout), CHUNK_S) is True


def test_tracker_holds_surround_then_returns_after_30s():
    t = SurroundModeTracker(layout_for_channels(8))
    t.update(0.5, CHUNK_S)
    chunks_for_hold = int(round(SURROUND_HOLD_SECONDS / CHUNK_S))
    assert SURROUND_HOLD_SECONDS == 30.0
    for i in range(chunks_for_hold - 1):
        assert t.update(0.0, CHUNK_S) is False, i
        assert t.surround
    assert t.update(0.0, CHUNK_S) is True     # exactly 30 s of front-only audio
    assert t.mode == "stereo"


def test_tracker_evidence_during_hold_restarts_it():
    t = SurroundModeTracker(layout_for_channels(6))
    t.update(0.5, CHUNK_S)
    assert feed(t, 0.0, 29.0) == []
    t.update(0.5, CHUNK_S)                    # surround sound again
    assert feed(t, 0.0, 29.0) == []           # a full new hold is needed
    assert t.surround
    assert feed(t, 0.0, 1.0) == [19]          # 30 s after the last evidence
    assert not t.surround


def test_tracker_returns_to_surround_after_going_stereo():
    t = SurroundModeTracker(layout_for_channels(4))
    t.update(0.5, CHUNK_S)
    feed(t, 0.0, 30.0)
    assert not t.surround
    assert t.update(0.5, CHUNK_S) is True and t.surround


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_tracker_never_switches_without_surround_layout(n):
    t = SurroundModeTracker(layout_for_channels(n))
    assert t.update(1.0, CHUNK_S) is False
    assert feed(t, 1.0, 5.0) == []
    assert t.mode == "stereo"
