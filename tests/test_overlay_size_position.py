"""Where the overlay window goes when the size setting changes
(AudioRadarApp.set_overlay_size in main.py).

Runs the real method against a stand-in app and overlay, so no QApplication
and no window is created. Importing main.py still needs PyQt6 (with
QtWebEngine) and the audio stack, which CI's test job does not install, so the
module skips there and runs on a Windows dev machine.
"""

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")
try:
    import soundcard  # noqa: F401  (imported by audio_capture via main)
except Exception as exc:   # also fails with OSError/RuntimeError without audio
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

import look  # noqa: E402
import main  # noqa: E402


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeOverlay:
    """Just the geometry of OverlayRadar, with the same keep_center rule.
    move() never changes visibility, like QWidget.move()."""

    def __init__(self, x=0, y=0, visible=False):
        self._x, self._y = x, y
        self.overlay_size = 100
        self.side = look.overlay_side_px(100)
        self.visible = visible
        self.drag_enabled = False
        self.keep_center_calls = []

    def set_overlay_size(self, percent, keep_center=True):
        self.keep_center_calls.append(keep_center)
        new_side = look.overlay_side_px(percent)
        if keep_center:
            self._x, self._y = look.centred_top_left(self._x, self._y, self.side, new_side)
        self.side = new_side
        self.overlay_size = percent

    def isVisible(self):
        return self.visible

    def pos(self):
        return _Point(self._x, self._y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def move(self, x, y):
        self._x, self._y = x, y


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, payload):
        self.emitted.append(payload)


class _Bridge:
    def __init__(self):
        self.overlayPositionChanged = _Signal()
        self.appearanceChanged = _Signal()


class FakeApp:
    # The real methods under test, borrowed from AudioRadarApp.
    set_overlay_size = main.AudioRadarApp.set_overlay_size
    _saved_overlay_xy = main.AudioRadarApp._saved_overlay_xy
    emit_overlay_position = main.AudioRadarApp.emit_overlay_position

    def __init__(self, overlay, settings=None):
        self.overlay = overlay
        self.settings = settings or {}
        self.overlay_size = 100
        self.overlay_hidden = False     # hidden by the toggle hotkey
        self.bridge = _Bridge()
        self.saves_queued = 0

    def _queue_settings_save(self):
        self.saves_queued += 1

    def emit_appearance(self):
        self.bridge.appearanceChanged.emit("{}")

    # Screen checks need a QApplication; every position used here is on screen.
    def _overlay_rect_is_visible(self, x, y):
        return True

    def _selected_monitor_center_position(self):
        raise AssertionError("no test position is off screen")

    def readout(self):
        import json
        state = json.loads(self.bridge.overlayPositionChanged.emitted[-1])
        return state["x"], state["y"]


def test_never_placed_hidden_overlay_is_not_shifted():
    # Before the first Start with nothing saved the window sits unplaced at
    # (0, 0). Shifting it used to show -150, -150 in the position readout.
    app = FakeApp(FakeOverlay(0, 0, visible=False))
    app.set_overlay_size(200)
    assert app.overlay.keep_center_calls == [False]
    assert (app.overlay.x(), app.overlay.y()) == (0, 0)
    assert app.readout() == (0, 0)
    assert "overlay_position" not in app.settings
    assert app.settings["overlay_size"] == 200 and app.saves_queued == 1


def test_never_placed_hidden_overlay_round_trip():
    app = FakeApp(FakeOverlay(0, 0, visible=False))
    for size in [50, 200, 130, 100]:
        app.set_overlay_size(size)
        assert (app.overlay.x(), app.overlay.y()) == (0, 0)
    assert app.overlay.side == 300


def test_hidden_overlay_with_saved_position_parks_on_saved_spot():
    # Never shown, so the widget is still at (0, 0). It is not shifted around
    # that meaningless spot (that read -150, -150); it is parked on the shifted
    # saved top-left instead, so the readout and a D-pad nudge start there.
    app = FakeApp(FakeOverlay(0, 0, visible=False),
                  {"overlay_position": {"x": 210, "y": 90}})
    app.set_overlay_size(200)
    assert app.overlay.keep_center_calls == [False]
    assert app.settings["overlay_position"] == {"x": 60, "y": -60}
    assert (app.overlay.x(), app.overlay.y()) == (60, -60)
    assert app.readout() == (60, -60)
    assert not app.overlay.isVisible()
    app.set_overlay_size(100)
    assert app.settings["overlay_position"] == {"x": 210, "y": 90}
    assert app.readout() == (210, 90)


def test_hidden_overlay_ignores_its_stale_position_when_saved_exists():
    # Shown once, then stopped: the widget's last spot differs from the saved
    # one (for example the saved spot was off screen at Start, so it was
    # centred). The saved spot is what the next Start uses, so it wins.
    app = FakeApp(FakeOverlay(250, 250, visible=False),
                  {"overlay_position": {"x": 210, "y": 90}})
    app.set_overlay_size(50)
    assert app.overlay.keep_center_calls == [False]
    assert app.settings["overlay_position"] == {"x": 285, "y": 165}
    assert app.readout() == (285, 165)


def test_visible_overlay_keeps_centre():
    app = FakeApp(FakeOverlay(300, 150, visible=True),
                  {"overlay_position": {"x": 300, "y": 150}})
    app.set_overlay_size(50)
    assert app.overlay.keep_center_calls == [True]
    assert (app.overlay.x(), app.overlay.y()) == (375, 225)
    assert app.readout() == (375, 225)
    assert app.settings["overlay_position"] == {"x": 375, "y": 225}


def test_visible_overlay_without_saved_position_keeps_centre():
    # Running, centred on the monitor at Start, never moved: still a real
    # on-screen position, so the centre stays put while it is visible.
    app = FakeApp(FakeOverlay(250, 250, visible=True))
    app.set_overlay_size(200)
    assert app.overlay.keep_center_calls == [True]
    assert (app.overlay.x(), app.overlay.y()) == (100, 100)
    assert "overlay_position" not in app.settings


def test_stopped_overlay_without_saved_position_is_not_shifted():
    # Shown once (centred at Start), then stopped. Nothing saved, so the next
    # Start centres it again with the new size; the hidden window stays put.
    app = FakeApp(FakeOverlay(250, 250, visible=False))
    app.set_overlay_size(200)
    assert app.overlay.keep_center_calls == [False]
    assert app.readout() == (250, 250)


def test_unchanged_size_does_nothing():
    app = FakeApp(FakeOverlay(0, 0, visible=False))
    app.set_overlay_size(100)
    assert app.overlay.keep_center_calls == []
    assert app.saves_queued == 0
    assert app.bridge.overlayPositionChanged.emitted == []
