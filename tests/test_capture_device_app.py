"""The capture device setting and the two capture signals in main.py.

Runs the real AudioRadarApp/Bridge methods against a stand-in app, so no
window is created, no device is opened and no settings file is touched.
Importing main.py needs PyQt6 (with QtWebEngine) and the audio stack, which
CI's test job does not install, so the module skips there and runs on a
Windows dev machine (like test_overlay_hotkey.py).
"""

import json

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")
try:
    import soundcard  # noqa: F401  (imported by audio_capture via main)
except Exception as exc:   # also fails with OSError/RuntimeError without audio
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

import audio_capture  # noqa: E402
import main  # noqa: E402

A = main.AudioRadarApp

DEVICES = [
    {"name": "Headset Earphone (USB)", "channels": 2, "is_default": True},
    {"name": "SteelSeries Sonar - Gaming", "channels": 8, "is_default": False},
]


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args if len(args) > 1 else args[0])

    def last(self):
        return json.loads(self.emitted[-1])


class _Bridge:
    def __init__(self):
        for name in ("captureDevicesChanged", "captureInfoChanged",
                     "profilesChanged", "statusChanged", "deviceChanged"):
            setattr(self, name, _Signal())


class FakeAudioThread:
    def __init__(self, target_pid=None):
        self.target_pid = target_pid
        self.running = False
        self.stops = 0
        self.capture_device = None

    def stop(self):
        self.stops += 1
        self.running = False


class FakeApp:
    set_capture_device = A.set_capture_device
    emit_capture_devices = A.emit_capture_devices
    emit_capture_info = A.emit_capture_info
    on_capture_info = A.on_capture_info
    _restart_capture_if_active = A._restart_capture_if_active
    _sync_selected_profile = A._sync_selected_profile
    _look_values = A._look_values

    def __init__(self, settings=None, running=False, target_pid=None):
        self.settings = dict(settings or {})
        self.capture_device = main.coerce_capture_device(self.settings.get("capture_device"))
        self.capture_info = audio_capture.inactive_capture_info()
        self._capture_devices = None
        self.bridge = _Bridge()
        self.radar_active = running
        self.audio_thread = FakeAudioThread(target_pid)
        self.threads = [self.audio_thread]
        self.saves_queued = 0
        self.restarts = 0
        # For _sync_selected_profile
        self.profiles = {}
        self.selected_preset = "All Sounds"
        self.audio_settings = {"sensitivity": 0.005, "gain": 1.0, "freq_low": 150,
                               "freq_high": 4000, "max_amp": 1.0}
        self.selected_program = None
        self.selected_monitor = 0
        self.mono_enabled = False
        self.mono_device = None
        self.accent_color = "#9751F2"
        self.stroke_width = 3
        self.overlay_size = 100
        self.alert_opacity = 100
        self.outline_enabled = True
        self.profile_writes = 0

    def _queue_settings_save(self):
        self.saves_queued += 1

    def _save_profiles(self):
        self.profile_writes += 1

    def _new_audio_thread(self):
        self.audio_thread = FakeAudioThread(self.audio_thread.target_pid)
        self.threads.append(self.audio_thread)

    def _start_capture_thread(self):
        self.restarts += 1
        self.audio_thread.capture_device = self.capture_device
        self.audio_thread.running = True


ENUMERATIONS = []


@pytest.fixture(autouse=True)
def fake_devices(monkeypatch):
    ENUMERATIONS.clear()

    def enumerate_devices():
        ENUMERATIONS.append(1)
        return [dict(d) for d in DEVICES]

    monkeypatch.setattr(main, "list_capture_devices", enumerate_devices)


# ── Setting ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, value", [
    (None, ""), ("", ""), (42, ""), ([], ""), ({"name": "x"}, ""),
    ("x" * 257, ""), ("Headset Earphone (USB)", "Headset Earphone (USB)"),
    (" Spaced ", " Spaced ")])
def test_coerce_capture_device(raw, value):
    assert main.coerce_capture_device(raw) == value


def test_choice_is_stored_and_saved_debounced():
    app = FakeApp()
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.capture_device == "SteelSeries Sonar - Gaming"
    assert app.settings["capture_device"] == "SteelSeries Sonar - Gaming"
    assert app.saves_queued == 1


def test_same_choice_does_nothing_but_still_answers():
    app = FakeApp({"capture_device": "SteelSeries Sonar - Gaming"}, running=True)
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.saves_queued == 0 and app.restarts == 0
    assert app.settings == {"capture_device": "SteelSeries Sonar - Gaming"}
    assert len(app.bridge.captureDevicesChanged.emitted) == 1


def test_junk_from_js_becomes_default():
    app = FakeApp({"capture_device": "SteelSeries Sonar - Gaming"})
    app.set_capture_device("y" * 1000)
    assert app.capture_device == ""


def test_change_while_running_system_restarts_capture():
    app = FakeApp(running=True)
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.restarts == 1
    assert app.threads[0].stops == 1
    assert app.audio_thread.capture_device == "SteelSeries Sonar - Gaming"


def test_change_while_stopped_does_not_start():
    app = FakeApp(running=False)
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.restarts == 0 and app.threads[0].stops == 0


def test_change_during_per_app_capture_does_not_restart():
    app = FakeApp(running=True, target_pid=1234)
    app.capture_info = dict(app.capture_info, active=True, source="app")
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.restarts == 0
    assert app.capture_device == "SteelSeries Sonar - Gaming"


def test_per_app_that_fell_back_to_system_restarts():
    app = FakeApp(running=True, target_pid=1234)
    app.capture_info = dict(app.capture_info, active=True, source="system")
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert app.restarts == 1


# ── captureDevicesChanged ──────────────────────────────────────────────

def test_devices_payload():
    app = FakeApp({"capture_device": "SteelSeries Sonar - Gaming"})
    app.emit_capture_devices()
    p = app.bridge.captureDevicesChanged.last()
    assert set(p) == {"devices", "selected", "available"}
    assert p["devices"] == DEVICES
    assert p["selected"] == "SteelSeries Sonar - Gaming"
    assert p["available"] is True


def test_unplugged_choice_is_kept_and_flagged():
    app = FakeApp({"capture_device": "Old USB Headset"})
    app.emit_capture_devices()
    p = app.bridge.captureDevicesChanged.last()
    assert p["selected"] == "Old USB Headset" and p["available"] is False


def test_default_choice_is_always_available(monkeypatch):
    monkeypatch.setattr(main, "list_capture_devices", lambda: [])
    app = FakeApp()
    app.emit_capture_devices()
    assert app.bridge.captureDevicesChanged.last() == {
        "devices": [], "selected": "", "available": True}


# ── captureInfoChanged ─────────────────────────────────────────────────

def test_info_from_current_thread_is_forwarded():
    app = FakeApp()
    info = {"active": True, "source": "system", "device": "Speakers", "channels": 6,
            "layout": "5.1", "mode": "surround", "note": ""}
    app.on_capture_info(app.audio_thread, info)
    assert app.bridge.captureInfoChanged.last() == info
    assert app.capture_info == info


def test_info_from_replaced_thread_is_dropped():
    app = FakeApp()
    old = app.audio_thread
    app._new_audio_thread()
    app.on_capture_info(old, {"active": True, "source": "system", "device": "Old",
                              "channels": 2, "layout": "stereo", "mode": "stereo",
                              "note": ""})
    assert app.bridge.captureInfoChanged.emitted == []


def test_initial_info_is_inactive():
    app = FakeApp()
    app.emit_capture_info()
    assert app.bridge.captureInfoChanged.last() == {
        "active": False, "source": "system", "device": "", "channels": 0,
        "layout": "stereo", "mode": "stereo", "note": "", "note_short": "",
        "mono_paused": False}


def test_real_new_audio_thread_reports_the_end(monkeypatch):
    # The real _new_audio_thread with a stand-in thread class: replacing an
    # active capture reports active false even if the old thread's own
    # message is lost or arrives late.
    class T(FakeAudioThread):
        def __init__(self, perf=None):
            super().__init__()
            for name in ("audio_data_signal", "device_info_signal",
                         "status_signal", "capture_info_signal"):
                setattr(self, name, type("S", (), {"connect": lambda s, fn: None})())

    monkeypatch.setattr(main, "AudioCaptureThread", T)
    app = FakeApp()
    app.perf = None
    app.on_audio_data = app.on_device_info = app.on_capture_status = lambda *a: None
    app.capture_info = dict(app.capture_info, active=True, device="Speakers")
    A._new_audio_thread(app)
    assert app.bridge.captureInfoChanged.last()["active"] is False
    A._new_audio_thread(app)        # already inactive: no repeated message
    assert len(app.bridge.captureInfoChanged.emitted) == 1


# ── Profiles ───────────────────────────────────────────────────────────

def test_selected_profile_auto_update_includes_capture_device():
    app = FakeApp()
    app.profiles = {"Mine": {"name": "Mine"}}
    app.selected_preset = "Mine"
    app.capture_device = "SteelSeries Sonar - Gaming"
    app._sync_selected_profile()
    assert app.profiles["Mine"]["capture_device"] == "SteelSeries Sonar - Gaming"
    assert app.profile_writes == 1
    app._sync_selected_profile()
    assert app.profile_writes == 1          # unchanged: no write


def test_save_profile_stores_coerced_or_live_capture_device():
    app = FakeApp()
    app.capture_device = "Headset Earphone (USB)"
    app.set_selected_preset = lambda name: None
    bridge = main.Bridge(app)
    bridge.save_profile(json.dumps({"name": "Old dashboard"}))
    assert app.profiles["Old dashboard"]["capture_device"] == "Headset Earphone (USB)"
    bridge.save_profile(json.dumps({"name": "New", "capture_device": "SteelSeries Sonar - Gaming"}))
    assert app.profiles["New"]["capture_device"] == "SteelSeries Sonar - Gaming"
    bridge.save_profile(json.dumps({"name": "Junk", "capture_device": 5}))
    assert app.profiles["Junk"]["capture_device"] == ""


def test_bridge_slots_exist_with_contract_names():
    for slot in ("set_capture_device", "refresh_capture_devices"):
        assert callable(getattr(main.Bridge, slot))
    for sig in ("captureDevicesChanged", "captureInfoChanged", "deviceChanged"):
        assert hasattr(main.Bridge, sig)


# ── Enumeration only at initial data and on refresh ────────────────────

def test_setter_reuses_the_last_list():
    app = FakeApp({"capture_device": "Headset Earphone (USB)"})
    app.emit_capture_devices()                  # initial data
    assert len(ENUMERATIONS) == 1
    for _ in range(5):                          # profile applications
        app.set_capture_device("Headset Earphone (USB)")
    app.set_capture_device("SteelSeries Sonar - Gaming")
    assert len(ENUMERATIONS) == 1
    assert len(app.bridge.captureDevicesChanged.emitted) == 7
    last = app.bridge.captureDevicesChanged.last()
    assert last["devices"] == DEVICES
    assert last["selected"] == "SteelSeries Sonar - Gaming" and last["available"]


def test_refresh_enumerates_again():
    app = FakeApp()
    app.emit_capture_devices()
    app.emit_capture_devices()                  # refresh_capture_devices
    assert len(ENUMERATIONS) == 2


def test_setter_before_any_list_enumerates_once():
    app = FakeApp()
    app.set_capture_device("x")
    app.set_capture_device("x")
    assert len(ENUMERATIONS) == 1


def test_bridge_refresh_slot_enumerates():
    app = FakeApp()
    main.Bridge(app).refresh_capture_devices()
    assert len(ENUMERATIONS) == 1
