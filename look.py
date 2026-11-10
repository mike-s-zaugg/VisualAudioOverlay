"""Overlay look settings: ranges, defaults and the small bits of math behind them.

Standard library only (no Qt, no numpy) so the tests run on Linux CI. main.py
and overlay.py both use these helpers instead of repeating the arithmetic, so
the ranges live in exactly one place.

Every coerce_* function accepts whatever JSON can hold (None, strings, floats,
bools, NaN, huge numbers) and always returns a valid value. settings.json and
profiles.json are hand-editable, and a typo there must never stop the app from
starting.
"""

import math

# Window side at 100 %. The radar radius and every pen width scale from this.
BASE_SIDE_PX = 300

# overlay_size: a zoom in percent. Step 10 keeps the window side a multiple of
# 30 px, so (old_side - new_side) / 2 is always a whole number of pixels and the
# centre cannot drift over repeated size changes.
OVERLAY_SIZE_DEFAULT = 100
OVERLAY_SIZE_MIN = 50
OVERLAY_SIZE_MAX = 200
OVERLAY_SIZE_STEP = 10

# alert_opacity: maximum arc opacity in percent. The floor stops a player from
# making the alerts invisible by accident.
ALERT_OPACITY_DEFAULT = 100
ALERT_OPACITY_MIN = 20
ALERT_OPACITY_MAX = 100

# outline_enabled: show the faint white base ring.
OUTLINE_ENABLED_DEFAULT = True

# stroke_width: arc pen width in pixels at 100 % size (the dashboard slider).
STROKE_WIDTH_DEFAULT = 6
STROKE_WIDTH_MIN = 1
STROKE_WIDTH_MAX = 20

# Fixed decorations, in pixels at 100 % size.
BASE_RING_WIDTH_PX = 2
DRAG_RING_OFFSET_PX = 8

_TRUE_WORDS = {"true", "1", "yes", "on"}
_FALSE_WORDS = {"false", "0", "no", "off"}


def _as_number(value):
    """A usable number from a JSON value, or None.

    Bools are rejected on purpose: True is an int in Python, and a stray
    `"overlay_size": true` should read as junk, not as 1 %.
    Ints are returned as-is (not via float) so a huge int cannot overflow.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return None if math.isnan(number) else number
    return None


def _round_half_up(number):
    # Python's round() rounds halves to even (round(6.5) == 6), which would
    # make 65 snap down but 75 snap up. Half-up is what a player expects.
    return int(math.floor(number + 0.5))


def _clamp(number, low, high):
    return low if number < low else high if number > high else number


def coerce_overlay_size(value):
    """overlay_size in percent: clamped to 50..200, snapped to a multiple of 10."""
    number = _as_number(value)
    if number is None:
        return OVERLAY_SIZE_DEFAULT
    # Clamp before snapping: the bounds are multiples of the step, so the
    # snapped value stays in range, and inf or 10**400 never reach floor().
    number = _clamp(number, OVERLAY_SIZE_MIN, OVERLAY_SIZE_MAX)
    return _round_half_up(number / OVERLAY_SIZE_STEP) * OVERLAY_SIZE_STEP


def coerce_alert_opacity(value):
    """alert_opacity in percent: clamped to 20..100, rounded to a whole number."""
    number = _as_number(value)
    if number is None:
        return ALERT_OPACITY_DEFAULT
    return _round_half_up(_clamp(number, ALERT_OPACITY_MIN, ALERT_OPACITY_MAX))


def coerce_stroke_width(value):
    """stroke_width in pixels at 100 %: clamped to 1..20, rounded.

    Replaces a bare int() at startup that crashed the app on a hand-edited
    value such as "thick" or null.
    """
    number = _as_number(value)
    if number is None:
        return STROKE_WIDTH_DEFAULT
    return _round_half_up(_clamp(number, STROKE_WIDTH_MIN, STROKE_WIDTH_MAX))


def coerce_outline_enabled(value):
    """outline_enabled as a real bool. Anything unrecognised means the default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return OUTLINE_ENABLED_DEFAULT


def overlay_side_px(size_percent):
    """Window side in pixels for a size percent (150..600 px)."""
    return BASE_SIDE_PX * coerce_overlay_size(size_percent) // 100


def scaled_width(px_at_100, size_percent):
    """A pen width or offset defined at 100 %, zoomed with the overlay size.

    Returns a float: at 50 % a 1 px stroke becomes 0.5 px instead of rounding
    to 0, which Qt would draw as a 1 px cosmetic pen.
    """
    return px_at_100 * size_percent / 100


def centred_top_left(x, y, old_side, new_side):
    """New top-left that keeps the window centre in place after a resize.

    With sides that are multiples of 30 (see OVERLAY_SIZE_STEP) the shift is
    exact, so a run of changes that returns to the start size also returns to
    the start position.
    """
    shift = (old_side - new_side) // 2
    return x + shift, y + shift


def arc_alpha(life, opacity_percent):
    """Arc alpha (0..255) for a blip's life (0..1) at the given maximum opacity.

    255 * percent / 100 is computed first so at 100 % the factor is exactly
    255.0 and the result matches the old int(life * 255) bit for bit.
    """
    if not life > 0:          # also catches NaN
        return 0
    if life > 1:              # keeps inf away from int()
        life = 1
    alpha = int(life * (255 * opacity_percent / 100))
    return _clamp(alpha, 0, 255)
