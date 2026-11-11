"""popup_snap: the snap is a fixed point, so a move() from inside a Move event
cannot loop, at any scale (whole positions exist or they do not)."""

import pytest

from popup_snap import SNAP_REACH, snap_coordinate, snapped_position

SCALES = [1.0, 1.1, 1.25, 1.33, 1.5, 1.75, 2.0, 2.25, 3.0]


@pytest.mark.parametrize("dpr", SCALES)
def test_snapping_twice_changes_nothing(dpr):
    for v in range(-60, 400):
        once = snap_coordinate(v, dpr)
        assert snap_coordinate(once, dpr) == once, (v, dpr, once)


def test_scale_without_reachable_whole_position_never_moves():
    # 1.33 needs a multiple of 100 logical pixels: out of reach, so left alone.
    for v in range(-30, 300):
        assert snap_coordinate(v, 1.33) in (v, snap_coordinate(v, 1.33))
    assert snapped_position(61, 79, 1.33) == (61, 79)


def test_scale_1_1_moves_at_most_five_pixels():
    for v in range(0, 500):
        assert abs(snap_coordinate(v, 1.1) - v) <= 5


def test_move_never_exceeds_reach():
    for dpr in SCALES:
        for v in range(0, 500):
            assert abs(snap_coordinate(v, dpr) - v) <= SNAP_REACH
