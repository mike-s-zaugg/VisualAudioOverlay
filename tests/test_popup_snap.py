"""popup_snap: dropdown popups are moved onto whole physical pixels.

The arithmetic needs no Qt, so this runs on CI. What it protects: at Windows
display scaling of 125, 150 or 175 %, a QtWebEngine <select> popup at a
position that is not whole in physical pixels grows by one pixel per frame for
as long as it is open (see popup_snap.py). Whole positions stop that.
"""

import math

import pytest

from popup_snap import SNAP_REACH, is_whole, snap_coordinate, snapped_position

WINDOWS_SCALES = [1.25, 1.5, 1.75, 2.25, 2.5, 3.0, 3.5]


@pytest.mark.parametrize("dpr", [1.0, 2.0, 3.0])
def test_whole_scales_never_move(dpr):
    for v in range(-50, 2000, 7):
        assert snap_coordinate(v, dpr) == v


def test_measured_cases_at_125():
    # The positions QtWebEngine used offscreen at 125 %, and where they go.
    assert snapped_position(61, 79, 1.25) == (60, 80)
    assert snapped_position(64, 199, 1.25) == (64, 200)
    assert snapped_position(857, 309, 1.25) == (856, 308)


def test_already_whole_stays():
    assert snap_coordinate(60, 1.25) == 60
    assert snap_coordinate(64, 1.75) == 64
    assert snap_coordinate(62, 1.5) == 62


def test_ties_go_up_or_left():
    # 62 at 125 %: 60 and 64 are both 2 away; the smaller one wins.
    assert snap_coordinate(62, 1.25) == 60
    # 199 at 150 %: 198 and 200 are both 1 away.
    assert snap_coordinate(199, 1.5) == 198


@pytest.mark.parametrize("dpr", WINDOWS_SCALES)
def test_result_is_whole_and_close(dpr):
    # A whole physical pixel recurs at least every 4 logical pixels at these
    # scales, so no popup moves by more than 2.
    for v in range(-40, 3000):
        s = snap_coordinate(v, dpr)
        assert is_whole(s * dpr), (v, dpr, s)
        assert abs(s - v) <= 2, (v, dpr, s)


def test_negative_positions_on_a_left_monitor():
    assert snap_coordinate(-1919, 1.25) == -1920
    assert is_whole(snap_coordinate(-7, 1.75) * 1.75)


@pytest.mark.parametrize("dpr", [None, "abc", 0, -1.25, math.nan, math.inf])
def test_unusable_scale_leaves_it(dpr):
    assert snap_coordinate(61, dpr) == 61


def test_no_whole_position_within_reach_leaves_it():
    # 1.001: the next whole product is 1000 pixels away.
    assert snap_coordinate(61, 1.001) == 61
    assert SNAP_REACH < 1000


def test_numeric_strings_and_floats_are_accepted():
    assert snap_coordinate(61, "1.25") == 60
    assert snap_coordinate(61.0, 1.25) == 60


# ── The popup's screen ─────────────────────────────────────────────────
# Qt maps physical = screen physical origin + (logical - screen logical origin)
# * dpr, so the offset from the screen's logical origin is what must be whole.

def test_default_origin_is_the_old_behaviour():
    assert snapped_position(61, 79, 1.25) == snapped_position(61, 79, 1.25, (0, 0)) == (60, 80)


def test_1366_wide_primary_left_of_a_125_percent_screen():
    # The 125 % screen starts at logical x = 1366; 1366 * 1.25 = 1707.5, so a
    # globally whole x such as 1368 (1710) is half a pixel off on that screen.
    origin = (1366, 0)
    x, y = snapped_position(1368, 79, 1.25, origin)
    assert ((x - 1366) * 1.25).is_integer(), x
    assert (x, y) == (1366, 80)
    assert snapped_position(1427, 200, 1.25, origin) == (1426, 200)
    # Without the origin the result would not be whole on that screen.
    gx, _ = snapped_position(1368, 79, 1.25)
    assert not ((gx - 1366) * 1.25).is_integer()


@pytest.mark.parametrize("origin", [(0, 0), (1920, 0), (-1920, 0), (2560, -1440), (1536, 864)])
def test_whole_origins_give_the_global_result(origin):
    # Origins whose product with the scale is whole: same answer as before.
    for x in range(1500, 1700, 3):
        for y in (0, 79, 333):
            assert snapped_position(x, y, 1.25, origin) == snapped_position(x, y, 1.25), (x, y, origin)


@pytest.mark.parametrize("dpr", WINDOWS_SCALES)
def test_offset_from_any_origin_is_whole_and_close(dpr):
    for origin in [(1366, 0), (0, 768), (-1366, 37), (1707, 1080)]:
        for v in range(origin[0] - 10, origin[0] + 300, 7):
            x, y = snapped_position(v, v, dpr, origin)
            assert is_whole((x - origin[0]) * dpr) and is_whole((y - origin[1]) * dpr), (v, origin, dpr)
            assert abs(x - v) <= 2 and abs(y - v) <= 2


def test_snap_can_move_either_way():
    assert snap_coordinate(61, 1.25) == 60     # left/up
    assert snap_coordinate(79, 1.25) == 80     # right/down
