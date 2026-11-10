"""Overlay visibility and the toggle hotkey in main.py (AudioRadarApp).

Runs the real methods against a stand-in app and overlay, with the real
BinderBackend over a fake registrar, so no window is created and nothing is
registered with the system. Importing main.py needs PyQt6 (with QtWebEngine)
and the audio stack, which CI's test job does not install, so the module skips
there and runs on a Windows dev machine.
"""

import json

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")
try:
    import soundcard  # noqa: F401  (imported by audio_capture via main)
except Exception as exc:   # also fails with OSError/RuntimeError without audio
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

import global_hotkey  # noqa: E402
import hotkeys  # noqa: E402
import main  # noqa: E402

A = main.AudioRadarApp


class _Point:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeOverlay:
    """Visibility and Move mode like OverlayRadar: set_drag_enabled re-shows a
    visible window (native re-create), hide() keeps drag_enabled."""

    def __init__(self):
        self.visible = False
        self.drag_enabled = False
        self._x, self._y = 100, 200
        self.shows = 0
        self.hides = 0

    def isVisible(self):
        return self.visible

    def show(self):
        if not self.visible:
            self.shows += 1
        self.visible = True

    def hide(self):
        if self.visible:
            self.hides += 1
        self.visible = False

    def set_drag_enabled(self, enabled):
        self.drag_enabled = bool(enabled)

    def pos(self):
        return _Point(self._x, self._y)

    def move(self, x, y):
        self._x, self._y = x, y


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args if len(args) > 1 else args[0])


class _Bridge:
    def __init__(self):
        for name in ("overlayPositionChanged", "statusChanged", "hotkeyChanged",
                     "overlayVisibilityChanged"):
            setattr(self, name, _Signal())


class FakeAudioThread:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class FakeRegistrar:
    def __init__(self, taken=()):
        self.taken = set(taken)
        self.held = {}
        self.next = 1

    def register(self, action_id, combo):
        if combo.text in self.taken:
            raise hotkeys.RegistrationFailed(True, 1409)
        self.held[self.next] = combo.text
        self.next += 1
        return self.next - 1

    def unregister(self, handle):
        self.held.pop(handle, None)


class FakeApp:
    # The real methods under test, borrowed from AudioRadarApp.
    start_radar = A.start_radar
    stop_radar = A.stop_radar
    set_overlay_drag_enabled = A.set_overlay_drag_enabled
    move_overlay = A.move_overlay
    nudge_overlay = A.nudge_overlay
    on_overlay_position_changed = A.on_overlay_position_changed
    emit_overlay_position = A.emit_overlay_position
    _set_overlay_hidden = A._set_overlay_hidden
    emit_overlay_visibility = A.emit_overlay_visibility
    _sync_overlay_visibility = A._sync_overlay_visibility
    set_toggle_hotkey = A.set_toggle_hotkey
    set_hotkey_capture = A.set_hotkey_capture
    on_hotkey_status = A.on_hotkey_status
    emit_hotkey_state = A.emit_hotkey_state
    on_hotkey_activated = A.on_hotkey_activated
    toggle_overlay_hidden = A.toggle_overlay_hidden

    def __init__(self, settings=None, backend=None, taken=()):
        self.settings = dict(settings or {})
        self.overlay = FakeOverlay()
        self.bridge = _Bridge()
        self.audio_thread = FakeAudioThread()
        self.radar_active = False
        self.overlay_hidden = False
        self._published_visibility = None
        self.toggle_hotkey = hotkeys.preference_from_setting(
            self.settings.get("toggle_hotkey"))
        self.saves_queued = 0
        self.disk_writes = 0
        self.capture_starts = 0
        self.placements = 0
        self.hotkey_status = hotkeys.BindingStatus(state=hotkeys.STATE_PENDING)
        self.registrar = FakeRegistrar(taken)
        self.hotkey_backend = backend or global_hotkey.BinderBackend(self.registrar)
        self.hotkey_backend.statusChanged.connect(self.on_hotkey_status)
        self.hotkey_backend.bind(hotkeys.TOGGLE_OVERLAY, self.toggle_hotkey, initial=True)

    # Stubs for what is not under test.
    def _place_overlay_for_start(self):
        self.placements += 1

    def _start_capture_thread(self):
        self.capture_starts += 1

    def emit_programs(self):
        pass

    def _new_audio_thread(self):
        pass

    def _save_settings(self):
        self.disk_writes += 1

    def _queue_settings_save(self):
        self.saves_queued += 1

    # Helpers
    def press(self):
        self.on_hotkey_activated(hotkeys.TOGGLE_OVERLAY)

    def hotkey_payload(self):
        return json.loads(self.bridge.hotkeyChanged.emitted[-1])

    def visibility_payloads(self):
        return [json.loads(p) for p in self.bridge.overlayVisibilityChanged.emitted]


# ── Hotkey toggles the running radar ───────────────────────────────────────
def test_press_hides_and_shows_running_radar_without_touching_capture():
    app = FakeApp()
    app.start_radar()
    assert app.overlay.visible and app.capture_starts == 1
    app.press()
    assert not app.overlay.visible and app.overlay_hidden
    app.press()
    assert app.overlay.visible and not app.overlay_hidden
    assert not app.overlay.drag_enabled
    assert (app.overlay.pos().x(), app.overlay.pos().y()) == (100, 200)   # same place
    assert app.capture_starts == 1 and app.audio_thread.stops == 0
    assert app.visibility_payloads() == [
        {"hidden": False, "radar_active": True},
        {"hidden": True, "radar_active": True},
        {"hidden": False, "radar_active": True},
    ]


def test_press_does_nothing_while_stopped():
    app = FakeApp()
    app.press()
    assert not app.overlay.visible and not app.overlay_hidden
    assert app.bridge.overlayVisibilityChanged.emitted == []


def test_press_does_nothing_in_move_mode_while_stopped():
    app = FakeApp()
    app.set_overlay_drag_enabled(True)
    app.press()
    assert app.overlay.visible and app.overlay.drag_enabled


def test_start_and_stop_reset_hidden():
    app = FakeApp()
    app.start_radar()
    app.press()
    app.start_radar()                      # Start always shows
    assert app.overlay.visible and not app.overlay_hidden
    app.press()
    app.stop_radar()
    assert not app.overlay.visible and not app.overlay_hidden
    assert app.visibility_payloads()[-1] == {"hidden": False, "radar_active": False}
    app.start_radar()
    assert app.overlay.visible


def test_press_in_move_mode_leaves_move_mode_then_hides():
    app = FakeApp()
    app.start_radar()
    app.set_overlay_drag_enabled(True)
    app.overlay.move(300, 400)             # dragged
    writes = app.disk_writes
    app.press()
    assert not app.overlay.drag_enabled and not app.overlay.visible
    assert app.overlay_hidden
    # Position saved and the dashboard told Move mode is off.
    assert app.settings["overlay_position"] == {"x": 300, "y": 400}
    assert app.disk_writes == writes + 1
    last_pos = json.loads(app.bridge.overlayPositionChanged.emitted[-1])
    assert last_pos == {"x": 300, "y": 400, "drag_enabled": False}
    app.press()
    assert app.overlay.visible and not app.overlay.drag_enabled


def test_move_mode_while_hidden_unhides_in_place():
    app = FakeApp()
    app.start_radar()
    placements = app.placements
    app.press()
    app.set_overlay_drag_enabled(True)
    assert app.overlay.visible and app.overlay.drag_enabled and not app.overlay_hidden
    assert app.placements == placements    # comes back where it was
    app.set_overlay_drag_enabled(False)
    assert app.overlay.visible             # radar running: stays up


def test_dpad_while_hidden_unhides_without_move_mode():
    app = FakeApp()
    app.start_radar()
    app.press()
    app.nudge_overlay(10, 0)
    assert app.overlay.visible and not app.overlay_hidden
    assert not app.overlay.drag_enabled
    assert app.overlay.pos().x() == 110


def test_move_mode_and_dpad_unchanged_while_stopped():
    app = FakeApp()
    app.set_overlay_drag_enabled(True)
    assert app.overlay.visible and app.overlay.drag_enabled and app.placements == 1
    app.set_overlay_drag_enabled(False)
    assert not app.overlay.visible and not app.overlay.drag_enabled
    app.move_overlay(5, 6)                 # D-pad while stopped: Move mode on
    assert app.overlay.visible and app.overlay.drag_enabled
    app.stop_radar()
    assert not app.overlay.visible and not app.overlay.drag_enabled


def test_hide_shows_no_extra_window_ops():
    app = FakeApp()
    app.start_radar()
    app.press()
    app.press()
    assert (app.overlay.shows, app.overlay.hides) == (2, 1)


# ── Hidden resets when the key goes away ───────────────────────────────────
def test_unbind_unhides():
    app = FakeApp()
    app.start_radar()
    app.press()
    app.set_toggle_hotkey("")
    assert app.overlay.visible and not app.overlay_hidden
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"]) == ("", "unbound", "")
    assert app.settings["toggle_hotkey"] == "" and app.saves_queued == 1
    app.press()                            # nothing bound: ignored
    assert app.overlay.visible


def test_capture_unhides_and_presses_are_ignored_meanwhile():
    app = FakeApp()
    app.start_radar()
    app.press()
    app.set_hotkey_capture(True)
    assert app.overlay.visible and not app.overlay_hidden
    assert app.hotkey_payload()["capturing"] is True
    app.press()                            # late queued press: ignored
    assert app.overlay.visible
    app.set_hotkey_capture(False)
    assert app.hotkey_payload()["effective"] == "Ctrl+Shift+H"
    app.press()
    assert not app.overlay.visible


def test_failed_change_keeps_binding_and_hidden_state():
    app = FakeApp(taken={"Ctrl+K"})
    app.start_radar()
    app.press()
    app.set_toggle_hotkey("Ctrl+K")
    p = app.hotkey_payload()
    assert p == {"combo": "Ctrl+Shift+H", "state": "conflict",
                 "effective": "Ctrl+Shift+H", "requested": "Ctrl+K",
                 "error": hotkeys.conflict_message("Ctrl+K"),
                 "capturing": False, "can_choose": True}
    # The old key still works, so the radar may stay hidden.
    assert app.overlay_hidden
    assert "toggle_hotkey" not in app.settings and app.saves_queued == 0


def test_failed_change_during_capture_shows_radar():
    app = FakeApp(taken={"Ctrl+K"})
    app.start_radar()
    app.press()
    app.set_hotkey_capture(True)
    app.set_toggle_hotkey("Ctrl+K")
    assert app.overlay.visible
    assert app.hotkey_payload()["effective"] == "Ctrl+Shift+H"


# ── Settings ───────────────────────────────────────────────────────────────
def test_startup_with_old_settings_uses_default_and_writes_nothing():
    app = FakeApp(settings={"accent_color": "#123456"})
    p = app.hotkey_payload()
    assert p == {"combo": "Ctrl+Shift+H", "state": "ok", "effective": "Ctrl+Shift+H",
                 "requested": "", "error": "", "capturing": False, "can_choose": True}
    assert "toggle_hotkey" not in app.settings
    assert app.saves_queued == 0 and app.disk_writes == 0


@pytest.mark.parametrize("junk", [42, None, "Shift+A", "nope", ["x"], {"a": 1}])
def test_junk_setting_falls_back_to_default(junk):
    app = FakeApp(settings={"toggle_hotkey": junk})
    assert app.hotkey_payload()["combo"] == "Ctrl+Shift+H"
    assert app.saves_queued == 0
    assert app.settings["toggle_hotkey"] == junk   # not rewritten


def test_saved_binding_and_unbound_are_restored():
    app = FakeApp(settings={"toggle_hotkey": "alt+k"})
    assert app.hotkey_payload()["effective"] == "Alt+K"
    app = FakeApp(settings={"toggle_hotkey": ""})
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"]) == ("", "unbound", "")


def test_change_is_saved_debounced_and_canonical():
    app = FakeApp()
    app.set_toggle_hotkey(" shift+ctrl + j ")
    assert app.settings["toggle_hotkey"] == "Ctrl+Shift+J"
    assert app.saves_queued == 1 and app.disk_writes == 0
    app.set_toggle_hotkey("Ctrl+Shift+J")           # same again: no save
    assert app.saves_queued == 1
    assert app.hotkey_payload()["state"] == "ok"


def test_invalid_change_reported():
    app = FakeApp()
    app.set_toggle_hotkey("shift+a")
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"], p["requested"]) == \
        ("Ctrl+Shift+H", "invalid", "Ctrl+Shift+H", "Shift+A")
    assert p["error"]


def test_startup_conflict():
    app = FakeApp(taken={"Ctrl+Shift+H"})
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"], p["requested"]) == \
        ("Ctrl+Shift+H", "conflict", "", "")
    assert "Ctrl+Shift+H" in p["error"]
    assert app.saves_queued == 0
    app.start_radar()
    app.press()                                     # no key active: ignored
    assert app.overlay.visible


# ── Null backend (no hotkey support) ───────────────────────────────────────
def test_null_backend_app_still_works():
    app = FakeApp(backend=global_hotkey.NullHotkeyBackend())
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"], p["can_choose"]) == \
        ("Ctrl+Shift+H", "unsupported", "", False)
    assert p["error"] == hotkeys.MSG_UNSUPPORTED
    app.set_toggle_hotkey("Alt+K")
    assert app.hotkey_payload()["requested"] == "Alt+K"
    assert app.saves_queued == 0
    app.start_radar()
    app.press()
    assert app.overlay.visible
    app.set_hotkey_capture(True)
    app.set_hotkey_capture(False)
    app.stop_radar()
    assert not app.overlay.visible


def test_payload_keys_are_exactly_the_contract():
    app = FakeApp()
    assert set(app.hotkey_payload()) == {"combo", "state", "effective", "requested",
                                         "error", "capturing", "can_choose"}
    app.start_radar()
    assert set(app.visibility_payloads()[-1]) == {"hidden", "radar_active"}


def test_size_change_while_hidden_keeps_centre():
    # Hidden by the hotkey with nothing saved: the window keeps its centre, so
    # the next show is not off by half the size change.
    import look
    from test_overlay_size_position import FakeApp as SizeApp, FakeOverlay as SizeOverlay
    app = SizeApp(SizeOverlay(250, 250, visible=False))
    app.overlay_hidden = True
    app.set_overlay_size(200)
    assert app.overlay.keep_center_calls == [True]
    assert (app.overlay.x(), app.overlay.y()) == look.centred_top_left(250, 250, 300, 600)
