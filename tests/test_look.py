"""Unit tests for look.py, the overlay look settings (size, alert opacity,
outline, stroke width).

Standard library only (no Qt), so they run on Linux CI.
"""

import pytest

import look


# Values JSON (or a hand-edited settings.json) can realistically hold that are
# not usable numbers. Each must fall back to the default, never raise.
JUNK = [None, "", "abc", "12px", [], [100], {}, {"x": 1}, True, False,
        float("nan"), "nan", object()]


# ── overlay_size ───────────────────────────────────────────────────────

def test_size_default():
    assert look.coerce_overlay_size(None) == 100
    assert look.OVERLAY_SIZE_DEFAULT == 100


@pytest.mark.parametrize("value, expected", [
    (50, 50), (100, 100), (200, 200),
    (49, 50), (0, 50), (-30, 50), (201, 200), (1000, 200),
    (10 ** 400, 200), (-(10 ** 400), 50),
    (float("inf"), 200), (float("-inf"), 50), ("inf", 200),
])
def test_size_clamps_to_range(value, expected):
    assert look.coerce_overlay_size(value) == expected


@pytest.mark.parametrize("value, expected", [
    (54, 50), (55, 60), (56, 60), (64, 60), (65, 70), (75, 80),
    (124.9, 120), (125, 130), (195, 200), (199.99, 200),
    ("130", 130), (" 87 ", 90), ("150.0", 150), (149.5, 150),
])
def test_size_snaps_half_up_to_step(value, expected):
    assert look.coerce_overlay_size(value) == expected


@pytest.mark.parametrize("value", JUNK)
def test_size_junk_is_default(value):
    assert look.coerce_overlay_size(value) == 100


def test_every_valid_size_is_a_fixed_point_and_an_int():
    for size in range(50, 201, 10):
        result = look.coerce_overlay_size(size)
        assert result == size and type(result) is int


def test_side_px():
    assert look.overlay_side_px(100) == 300
    assert look.overlay_side_px(50) == 150
    assert look.overlay_side_px(200) == 600
    assert look.overlay_side_px(130) == 390
    # Coerced first, so junk or out-of-range input still gives a valid side.
    assert look.overlay_side_px("junk") == 300
    assert look.overlay_side_px(5000) == 600


def test_every_side_is_a_multiple_of_30():
    # The no-drift guarantee rests on this: the side difference between any
    # two sizes is even, so half of it is a whole pixel.
    for size in range(50, 201, 10):
        side = look.overlay_side_px(size)
        assert side % 30 == 0 and type(side) is int


# ── centre-preserving resize ───────────────────────────────────────────

def test_centred_top_left_keeps_centre():
    for old in range(50, 201, 10):
        for new in range(50, 201, 10):
            old_side, new_side = look.overlay_side_px(old), look.overlay_side_px(new)
            x, y = 1234, -567
            nx, ny = look.centred_top_left(x, y, old_side, new_side)
            # Centre = top-left + side / 2, compared doubled to stay in ints.
            assert 2 * nx + new_side == 2 * x + old_side
            assert 2 * ny + new_side == 2 * y + old_side


@pytest.mark.parametrize("sequence", [
    [100, 50, 200, 130, 100],
    [100, 200, 50, 200, 50, 100],
    [100] + list(range(110, 201, 10)) + list(range(190, 49, -10)) + [60, 70, 80, 90, 100],
    [100, 170, 60, 150, 90, 200, 110, 100],
])
def test_no_drift_over_a_run_of_size_changes(sequence):
    start = (800, 400)
    x, y = start
    for old, new in zip(sequence, sequence[1:]):
        x, y = look.centred_top_left(x, y, look.overlay_side_px(old),
                                     look.overlay_side_px(new))
    assert (x, y) == start


def test_slider_drag_burst_does_not_drift():
    # A player dragging the slider back and forth produces many steps; the
    # position after returning to the start size must be exactly the start.
    x, y = 17, 23
    size = 100
    for target in [200, 50, 180, 70, 100]:
        step = 10 if target > size else -10
        while size != target:
            nxt = size + step
            x, y = look.centred_top_left(x, y, look.overlay_side_px(size),
                                         look.overlay_side_px(nxt))
            size = nxt
    assert (x, y) == (17, 23)


def test_scaled_width():
    assert look.scaled_width(6, 100) == 6.0
    assert look.scaled_width(2, 100) == 2.0
    assert look.scaled_width(8, 100) == 8.0
    assert look.scaled_width(6, 50) == 3.0
    assert look.scaled_width(1, 50) == 0.5          # not rounded to 0
    assert look.scaled_width(20, 200) == 40.0
    assert look.scaled_width(6, 130) == pytest.approx(7.8)


# ── alert_opacity ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, 100), (100, 100), (20, 20), (19, 20), (0, 20), (-5, 20),
    (101, 100), (10 ** 400, 100), (float("inf"), 100), (float("-inf"), 20),
    (55, 55), (55.5, 56), (54.4, 54), ("75", 75),
])
def test_opacity_coerce(value, expected):
    result = look.coerce_alert_opacity(value)
    assert result == expected and type(result) is int


@pytest.mark.parametrize("value", JUNK)
def test_opacity_junk_is_default(value):
    assert look.coerce_alert_opacity(value) == 100


# ── arc alpha ──────────────────────────────────────────────────────────

def test_alpha_at_100_matches_old_formula_exactly():
    # At 100 % the overlay must draw exactly what it drew before the setting
    # existed, which used int(life * 255). Check every life value the decay
    # can produce plus a fine sweep.
    lives = [1.0 - k * 0.04 for k in range(26)]
    lives += [i / 10000 for i in range(1, 10001)]
    for life in lives:
        if life > 0:
            assert look.arc_alpha(life, 100) == int(life * 255)


@pytest.mark.parametrize("life, opacity, expected", [
    (1.0, 100, 255),
    (1.0, 20, 51),
    (1.0, 50, 127),
    (0.5, 100, 127),
    (0.5, 50, 63),
    (0.0, 100, 0),
    (-0.2, 100, 0),
    (float("nan"), 100, 0),
    (1.5, 100, 255),                # life above 1 never overflows the alpha
    (float("inf"), 100, 255),
    (0.001, 20, 0),
])
def test_alpha_edges(life, opacity, expected):
    assert look.arc_alpha(life, opacity) == expected


def test_alpha_is_always_in_range():
    for opacity in range(20, 101):
        for i in range(0, 101):
            alpha = look.arc_alpha(i / 100, opacity)
            assert 0 <= alpha <= 255 and type(alpha) is int


def test_alpha_scales_down_never_up():
    for i in range(1, 101):
        life = i / 100
        assert look.arc_alpha(life, 40) <= look.arc_alpha(life, 100)


# ── outline_enabled ────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), (None, True),
    (1, True), (0, False), (1.0, True), (0.0, False),
    ("true", True), ("false", False), (" FALSE ", False), ("off", False),
    ("on", True), ("1", True), ("0", False), ("no", False), ("yes", True),
])
def test_outline_coerce(value, expected):
    assert look.coerce_outline_enabled(value) is expected


@pytest.mark.parametrize("value", ["", "maybe", 2, -1, 0.5, float("nan"),
                                   [], {}, [False], object()])
def test_outline_junk_is_default(value):
    assert look.coerce_outline_enabled(value) is True


# ── stroke_width (the startup crash fix) ───────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, 6), (6, 6), (1, 1), (20, 20), (0, 1), (-3, 1), (21, 20),
    (10 ** 400, 20), (6.4, 6), (6.5, 7), ("12", 12), ("thick", 6),
    (True, 6), (float("nan"), 6), (float("inf"), 20), ([], 6), ({}, 6),
])
def test_stroke_coerce(value, expected):
    result = look.coerce_stroke_width(value)
    assert result == expected and type(result) is int


def test_constants_match_the_contract():
    assert (look.OVERLAY_SIZE_MIN, look.OVERLAY_SIZE_MAX, look.OVERLAY_SIZE_STEP) == (50, 200, 10)
    assert (look.ALERT_OPACITY_MIN, look.ALERT_OPACITY_MAX) == (20, 100)
    assert look.ALERT_OPACITY_DEFAULT == 100
    assert look.OUTLINE_ENABLED_DEFAULT is True
    assert look.BASE_SIDE_PX == 300
    # The pre-setting pixel values; at 100 % the overlay must still use them.
    assert (look.BASE_RING_WIDTH_PX, look.DRAG_RING_OFFSET_PX) == (2, 8)
    assert (look.STROKE_WIDTH_MIN, look.STROKE_WIDTH_MAX, look.STROKE_WIDTH_DEFAULT) == (1, 20, 6)
