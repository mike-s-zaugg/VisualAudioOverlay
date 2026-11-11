"""appDeactivated: main.py tells the dashboard that the application lost activation.

The dashboard ends a dropdown's quiet period on this signal (see "Quiet
dropdowns" in dashboard_v2/script.js), so it must fire when the application
becomes inactive and never while a Qt popup is up.

Runs the real AudioRadarApp.on_application_state_changed against a stand-in
with a fake bridge, so no window is created, no device is opened and no
settings file is touched. Importing main.py needs PyQt6 (with QtWebEngine) and
the audio stack, which CI's test job does not install, so the module skips
there and runs on a Windows dev machine (like test_capture_device_app.py).
"""

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")
try:
    import soundcard  # noqa: F401  (imported by audio_capture via main)
except Exception as exc:   # also fails with OSError/RuntimeError without audio
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

from PyQt6.QtCore import Qt  # noqa: E402

import main  # noqa: E402

State = Qt.ApplicationState


class FakeSignal:
    def __init__(self):
        self.count = 0

    def emit(self):
        self.count += 1


class FakeBridge:
    def __init__(self):
        self.appDeactivated = FakeSignal()


class Stand:
    """What on_application_state_changed needs from the app."""

    def __init__(self):
        self.bridge = FakeBridge()


def handle(stand, state):
    main.AudioRadarApp.on_application_state_changed(stand, state)


@pytest.fixture
def no_popup(monkeypatch):
    monkeypatch.setattr(main.QApplication, "activePopupWidget", staticmethod(lambda: None))


def test_bridge_declares_the_signal():
    assert "appDeactivated" in dir(main.Bridge)


def test_inactive_emits_once(no_popup):
    s = Stand()
    handle(s, State.ApplicationInactive)
    assert s.bridge.appDeactivated.count == 1


def test_active_does_not_emit(no_popup):
    s = Stand()
    handle(s, State.ApplicationActive)
    assert s.bridge.appDeactivated.count == 0


@pytest.mark.parametrize("state", [State.ApplicationHidden, State.ApplicationSuspended])
def test_other_inactive_states_emit(no_popup, state):
    s = Stand()
    handle(s, state)
    assert s.bridge.appDeactivated.count == 1


def test_not_while_a_popup_is_up(monkeypatch):
    monkeypatch.setattr(main.QApplication, "activePopupWidget", staticmethod(lambda: object()))
    s = Stand()
    handle(s, State.ApplicationInactive)
    handle(s, State.ApplicationHidden)
    assert s.bridge.appDeactivated.count == 0
