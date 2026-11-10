"""The Start/Stop hotkey and overlay visibility in main.py (AudioRadarApp).

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
    """Visibility and Move mode like OverlayRadar: hide() keeps drag_enabled."""

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
        for name in ("overlayPositionChanged", "statusChanged", "hotkeyChanged"):
            setattr(self, name, _Signal())


class FakeAudioThread:
    """Counts how many capture threads run at once (must never exceed one)."""
    running_now = 0
    max_running = 0

    def __init__(self):
        self.running = False
        self.stops = 0

    def start(self):
        assert not self.running, "a QThread cannot be restarted"
        self.running = True
        FakeAudioThread.running_now += 1
        FakeAudioThread.max_running = max(FakeAudioThread.max_running,
                                          FakeAudioThread.running_now)

    def stop(self):
        # Like AudioCaptureThread.stop(): returns only once the thread ended.
        self.stops += 1
        if self.running:
            self.running = False
            FakeAudioThread.running_now -= 1


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
    toggle_radar = A.toggle_radar
    set_overlay_drag_enabled = A.set_overlay_drag_enabled
    move_overlay = A.move_overlay
    nudge_overlay = A.nudge_overlay
    on_overlay_position_changed = A.on_overlay_position_changed
    emit_overlay_position = A.emit_overlay_position
    _sync_overlay_visibility = A._sync_overlay_visibility
    set_toggle_hotkey = A.set_toggle_hotkey
    set_hotkey_capture = A.set_hotkey_capture
    on_hotkey_status = A.on_hotkey_status
    emit_hotkey_state = A.emit_hotkey_state
    on_hotkey_activated = A.on_hotkey_activated

    def __init__(self, settings=None, backend=None, taken=()):
        FakeAudioThread.running_now = FakeAudioThread.max_running = 0
        self.settings = dict(settings or {})
        self.overlay = FakeOverlay()
        self.bridge = _Bridge()
        self.audio_thread = FakeAudioThread()
        self.threads_made = 1
        self.radar_active = False
        self.toggle_hotkey = hotkeys.preference_from_setting(
            self.settings.get("toggle_hotkey"))
        self.saves_queued = 0
        self.disk_writes = 0
        self.placements = 0
        self.hotkey_status = hotkeys.BindingStatus(state=hotkeys.STATE_PENDING)
        self.registrar = FakeRegistrar(taken)
        self.hotkey_backend = backend or global_hotkey.BinderBackend(self.registrar)
        self.hotkey_backend.statusChanged.connect(self.on_hotkey_status)
        self.hotkey_backend.bind(hotkeys.TOGGLE_RADAR, self.toggle_hotkey, initial=True)

    # Stubs for what is not under test.
    def _place_overlay_for_start(self):
        self.placements += 1

    def _start_capture_thread(self):
        # The real one also resolves the target and applies the audio values;
        # starting the thread and the status line are what matter here.
        self.audio_thread.start()
        self.bridge.statusChanged.emit("Radar is active", True)

    def emit_programs(self):
        pass

    def _new_audio_thread(self):
        self.audio_thread = FakeAudioThread()
        self.threads_made += 1

    def _save_settings(self):
        self.disk_writes += 1

    def _queue_settings_save(self):
        self.saves_queued += 1

    # Helpers
    def press(self):
        self.on_hotkey_activated(hotkeys.TOGGLE_RADAR)

    def hotkey_payload(self):
        return json.loads(self.bridge.hotkeyChanged.emitted[-1])

    def last_status(self):
        return self.bridge.statusChanged.emitted[-1]


# ── The hotkey is the Start/End button ─────────────────────────────────────
def test_press_while_stopped_starts():
    app = FakeApp()
    app.press()
    assert app.radar_active and app.overlay.visible
    assert not app.overlay.drag_enabled
    assert app.audio_thread.running and app.placements == 1
    assert app.last_status() == ("Radar is active", True)


def test_press_while_running_stops():
    app = FakeApp()
    app.start_radar()
    thread = app.audio_thread
    app.press()
    assert not app.radar_active and not app.overlay.visible
    assert thread.stops == 1 and not thread.running
    assert app.audio_thread is not thread and not app.audio_thread.running
    assert app.last_status() == ("Radar stopped", False)


def test_rapid_presses_never_run_two_capture_threads():
    app = FakeApp()
    for n in range(1, 8):
        app.press()
        assert app.radar_active == (n % 2 == 1)
        assert app.overlay.visible == app.radar_active
        assert app.bridge.statusChanged.emitted[-1][1] == app.radar_active
    assert FakeAudioThread.max_running == 1
    # One fresh thread per Stop, so no QThread is ever started twice.
    assert app.threads_made == 1 + 3


def test_press_in_move_mode_while_stopped_leaves_move_mode_then_starts():
    app = FakeApp()
    app.set_overlay_drag_enabled(True)
    app.overlay.move(300, 400)             # dragged
    writes = app.disk_writes
    app.press()
    assert app.radar_active and app.overlay.visible
    assert not app.overlay.drag_enabled     # click-through, not draggable
    # Left Move mode like the Move button: position saved, dashboard told.
    assert app.settings["overlay_position"] == {"x": 300, "y": 400}
    assert app.disk_writes == writes + 1
    moves = [json.loads(p) for p in app.bridge.overlayPositionChanged.emitted]
    assert {"x": 300, "y": 400, "drag_enabled": False} in moves
    assert app.last_status() == ("Radar is active", True)


def test_press_in_move_mode_while_running_leaves_move_mode_then_stops():
    app = FakeApp()
    app.start_radar()
    app.set_overlay_drag_enabled(True)
    app.overlay.move(50, 60)
    app.press()
    assert not app.radar_active and not app.overlay.visible
    assert not app.overlay.drag_enabled
    assert app.settings["overlay_position"] == {"x": 50, "y": 60}
    assert json.loads(app.bridge.overlayPositionChanged.emitted[-1])["drag_enabled"] is False
    assert app.last_status() == ("Radar stopped", False)


def test_button_and_hotkey_share_the_routines():
    calls = []

    class Spy(FakeApp):
        def start_radar(self):
            calls.append("start")
            FakeApp.start_radar(self)

        def stop_radar(self):
            calls.append("stop")
            FakeApp.stop_radar(self)

    app = Spy()
    bridge = main.Bridge(app)
    bridge.start_radar()
    bridge.stop_radar()
    app.press()
    app.press()
    assert calls == ["start", "stop", "start", "stop"]


# ── Presses that must do nothing ───────────────────────────────────────────
def test_press_ignored_while_capturing():
    app = FakeApp()
    app.set_hotkey_capture(True)
    app.press()                            # late queued press: ignored
    assert not app.radar_active
    app.set_hotkey_capture(False)
    app.press()
    assert app.radar_active


def test_press_ignored_after_unbind_and_startup_conflict():
    app = FakeApp()
    app.set_toggle_hotkey("")
    app.press()
    assert not app.radar_active
    app = FakeApp(taken={"Ctrl+Shift+H"})
    app.press()
    assert not app.radar_active


def test_other_action_ids_are_ignored():
    app = FakeApp()
    app.on_hotkey_activated("something_else")
    assert not app.radar_active


# ── Button paths unchanged ─────────────────────────────────────────────────
def test_move_mode_and_dpad_while_stopped():
    app = FakeApp()
    app.set_overlay_drag_enabled(True)
    assert app.overlay.visible and app.overlay.drag_enabled and app.placements == 1
    app.set_overlay_drag_enabled(False)
    assert not app.overlay.visible and not app.overlay.drag_enabled
    app.move_overlay(5, 6)                 # D-pad while stopped: Move mode on
    assert app.overlay.visible and app.overlay.drag_enabled
    assert app.overlay.pos().x() == 5
    app.stop_radar()
    assert not app.overlay.visible and not app.overlay.drag_enabled


def test_move_mode_and_dpad_while_running():
    app = FakeApp()
    app.start_radar()
    placements = app.placements
    app.set_overlay_drag_enabled(True)
    assert app.overlay.visible and app.overlay.drag_enabled
    assert app.placements == placements    # stays where it is
    app.set_overlay_drag_enabled(False)
    assert app.overlay.visible and not app.overlay.drag_enabled
    app.nudge_overlay(10, 0)
    assert app.overlay.visible and not app.overlay.drag_enabled
    assert app.overlay.pos().x() == 110


def test_start_while_in_move_mode_keeps_move_mode():
    # The button path at HEAD: Start does not touch Move mode.
    app = FakeApp()
    app.set_overlay_drag_enabled(True)
    app.start_radar()
    assert app.overlay.visible and app.overlay.drag_enabled
    assert app.overlay.shows == 1          # already visible: not shown again


def test_start_stop_window_ops():
    app = FakeApp()
    app.start_radar()
    app.stop_radar()
    app.start_radar()
    assert (app.overlay.shows, app.overlay.hides) == (2, 1)


# ── Settings and the hotkeyChanged payload ─────────────────────────────────
def test_startup_with_old_settings_uses_default_and_writes_nothing():
    app = FakeApp(settings={"accent_color": "#123456"})
    p = app.hotkey_payload()
    assert p == {"combo": "Ctrl+Shift+H", "state": "ok", "effective": "Ctrl+Shift+H",
                 "requested": "", "error": "", "capturing": False, "can_choose": True}
    assert "toggle_hotkey" not in app.settings
    assert app.saves_queued == 0 and app.disk_writes == 0


@pytest.mark.parametrize("junk", [42, None, "Shift+A", "Alt+F4", "nope", ["x"], {"a": 1}])
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


def test_failed_change_keeps_binding():
    app = FakeApp(taken={"Ctrl+K"})
    app.set_toggle_hotkey("Ctrl+K")
    assert app.hotkey_payload() == {
        "combo": "Ctrl+Shift+H", "state": "conflict", "effective": "Ctrl+Shift+H",
        "requested": "Ctrl+K", "error": hotkeys.conflict_message("Ctrl+K"),
        "capturing": False, "can_choose": True}
    assert "toggle_hotkey" not in app.settings and app.saves_queued == 0
    app.press()                                     # old key still works
    assert app.radar_active


def test_invalid_change_reported():
    app = FakeApp()
    app.set_toggle_hotkey("alt+f4")
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"], p["requested"], p["error"]) == \
        ("Ctrl+Shift+H", "invalid", "Ctrl+Shift+H", "Alt+F4", hotkeys.MSG_INVALID_ALT_F4)


def test_startup_conflict():
    app = FakeApp(taken={"Ctrl+Shift+H"})
    p = app.hotkey_payload()
    assert (p["combo"], p["state"], p["effective"], p["requested"]) == \
        ("Ctrl+Shift+H", "conflict", "", "")
    assert "Ctrl+Shift+H" in p["error"]
    assert app.saves_queued == 0
    app.start_radar()                               # the button still works
    assert app.radar_active and app.overlay.visible


def test_payload_keys_are_exactly_the_contract():
    app = FakeApp()
    assert set(app.hotkey_payload()) == {"combo", "state", "effective", "requested",
                                         "error", "capturing", "can_choose"}


def test_bridge_has_no_visibility_signal():
    assert not hasattr(main.Bridge, "overlayVisibilityChanged")


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
    app.press()                                     # nothing bound: ignored
    assert not app.radar_active
    app.start_radar()
    assert app.overlay.visible
    app.set_hotkey_capture(True)
    app.set_hotkey_capture(False)
    app.stop_radar()
    assert not app.overlay.visible
