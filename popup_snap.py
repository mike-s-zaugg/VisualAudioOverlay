"""Stop QtWebEngine <select> popups from growing at fractional display scaling.

The dashboard's dropdowns open as native popup windows (a top-level
QQuickWidget that QtWebEngine creates as a child of the QWebEngineView). At a
fractional device pixel ratio (Windows display scaling of 125, 150 or 175 %)
Chromium places that window at a position that is not a whole number of
physical pixels, for example x = 61 at 125 % is 76.25 px. Each time the window
geometry goes round between Qt and Chromium, the rectangle is widened to whole
physical pixels on both sides (floor of the left edge, ceil of the right edge)
and converted back to logical pixels rounded up, so it comes back one logical
pixel larger, and the new size is applied again on the next frame. The window
grows by one pixel per frame, per axis whose position is fractional, for as
long as it stays open, while the list inside keeps its real size: the blank
white area beside and under the entries.

Measured with QtWebEngine 6.11 offscreen: a popup whose position lands on
whole physical pixels does not grow at all, and Chromium does not move it back.
So when a popup is shown or moved, it is moved to the nearest position that is
whole in physical pixels: up, down, left or right, ties going up/left, at most
2 logical pixels at Windows scales. Its size is left alone. Nothing changes at
100 % or 200 %, where every position is already whole.

Whole is measured on the popup's own screen. Qt maps a logical position to
physical pixels as screen physical origin + (logical - screen logical origin)
* dpr, and a screen's logical origin times its dpr need not be whole (a 1366
px wide 100 % monitor left of a 125 % one puts that one at x = 1366, and
1366 * 1.25 = 1707.5), so the offset from the screen's origin is snapped.

The pure arithmetic is at the top and needs no Qt (tests/test_popup_snap.py).
"""

# Windows offers scales in steps of 25 %, so a whole physical pixel recurs at
# least every 4 logical pixels. The search reaches a little further for other
# factors and gives up (leaves the position) when none is close.
SNAP_REACH = 8
_EPSILON = 1e-6


def is_whole(value):
    return abs(value - round(value)) < _EPSILON


def snap_coordinate(value, dpr, reach=SNAP_REACH):
    """The integer nearest to `value` whose product with `dpr` is a whole number.

    The result can be smaller or larger than `value`; ties go to the smaller
    one (up or left). At most 2 away at Windows scales. Returns
    `value` unchanged when it is already whole, when `dpr` is not a usable
    number, or when nothing within `reach` is whole.
    """
    value = int(value)
    try:
        dpr = float(dpr)
    except (TypeError, ValueError):
        return value
    if not dpr > 0 or dpr != dpr or dpr == float("inf"):
        return value
    if is_whole(value * dpr):
        return value
    for distance in range(1, reach + 1):
        for candidate in (value - distance, value + distance):
            if is_whole(candidate * dpr):
                return candidate
    return value


def snapped_position(x, y, dpr, origin=(0, 0)):
    """(x, y) moved to the nearest position that is whole in physical pixels.

    `origin` is the logical top-left of the screen the popup is on: the offset
    from it is what Qt multiplies by `dpr`, so that offset is snapped."""
    ox, oy = int(origin[0]), int(origin[1])
    return (ox + snap_coordinate(int(x) - ox, dpr),
            oy + snap_coordinate(int(y) - oy, dpr))


def install(view):
    """Keep every dropdown popup of `view` (a QWebEngineView) on whole pixels.

    Watches only `view`'s child events and, per popup, its Show and Move
    events, so the rest of the application pays nothing. Returns the filter
    object (parented to the view); keep a reference to it."""
    from PyQt6.QtCore import QEvent, QObject, Qt

    marker = "vaoPixelSnap"
    reported = []          # print a failure once per process, not per event

    def is_popup(widget):
        # windowType, not a flag mask: ToolTip shares the Popup bits.
        return widget.isWindow() and widget.windowType() == Qt.WindowType.Popup

    class PopupPixelSnap(QObject):
        def eventFilter(self, obj, event):
            # An exception escaping a PyQt virtual aborts the application, and
            # ChildAdded arrives while the child is half constructed: never let
            # this filter take the dashboard down.
            try:
                kind = event.type()
                if obj is view:
                    # QtWebEngine creates a new popup widget for every open; it
                    # is a Popup by the time the view hears ChildPolished.
                    if kind in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
                        child = event.child()
                        if child is not None and child.isWidgetType() and is_popup(child) \
                                and not child.property(marker):
                            child.setProperty(marker, True)
                            child.installEventFilter(self)
                    return False
                if kind in (QEvent.Type.Show, QEvent.Type.Move):
                    self.snap(obj)
            except Exception as exc:   # noqa: BLE001  (see above)
                if not reported:
                    reported.append(exc)
                    print(f"popup_snap: dropdown popup not aligned ({exc!r}); continuing.")
            return False

        @staticmethod
        def snap(widget):
            pos = widget.pos()
            screen = widget.screen()
            origin = screen.geometry().topLeft() if screen is not None else None
            origin = (origin.x(), origin.y()) if origin is not None else (0, 0)
            x, y = snapped_position(pos.x(), pos.y(), widget.devicePixelRatioF(), origin)
            if (x, y) != (pos.x(), pos.y()):
                widget.move(x, y)

    snapper = PopupPixelSnap(view)
    view.installEventFilter(snapper)
    return snapper
