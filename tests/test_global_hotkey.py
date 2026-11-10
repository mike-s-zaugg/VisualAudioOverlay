"""global_hotkey.py: the Qt backend layer.

Needs PyQt6 (skips on CI, which installs only numpy and pytest). No widget is
created; the QApplication runs on the offscreen platform.

The Windows tests call the real RegisterHotKey, but only with
Ctrl+Alt+Shift+F24, a combination no keyboard in normal use produces, and
always unregister it. Key presses are never simulated: WM_HOTKEY is posted to
this thread's own queue instead.
"""

import os
import sys

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import global_hotkey  # noqa: E402
import hotkeys  # noqa: E402

SAFE_COMBO = "Ctrl+Alt+Shift+F24"
ACTION = hotkeys.TOGGLE_RADAR


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


class Recorder:
    def __init__(self, backend):
        self.statuses = []
        self.pressed = []
        backend.statusChanged.connect(lambda a, s: self.statuses.append((a, s)))
        backend.activated.connect(self.pressed.append)

    @property
    def last(self):
        return self.statuses[-1][1]


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


def test_binder_backend_reports_every_call(qapp):
    backend = global_hotkey.BinderBackend(FakeRegistrar(taken={"Ctrl+K"}))
    rec = Recorder(backend)
    assert backend.capabilities() == {"can_choose_combo": True, "managed_by_system": False}

    backend.bind(ACTION, "Ctrl+Shift+H", initial=True)
    assert rec.statuses[-1][0] == ACTION
    assert (rec.last.state, rec.last.effective) == ("ok", "Ctrl+Shift+H")

    backend.bind(ACTION, "Ctrl+K")
    assert (rec.last.state, rec.last.effective, rec.last.requested) == \
        ("conflict", "Ctrl+Shift+H", "Ctrl+K")

    backend.suspend()
    assert rec.last.capturing and rec.last.effective == ""
    backend.suspend()
    backend.resume()
    assert not rec.last.capturing and rec.last.effective == "Ctrl+Shift+H"
    backend.resume()
    assert len(rec.statuses) == 6           # every call answered

    backend.unbind(ACTION)
    assert rec.last.state == "unbound"
    assert backend.status(ACTION) == rec.last
    backend.shutdown()


def test_activation_is_queued_onto_the_event_loop(qapp):
    backend = global_hotkey.BinderBackend(FakeRegistrar())
    rec = Recorder(backend)
    backend._emit_pressed(ACTION)
    assert rec.pressed == []                # not inside the native callback
    qapp.processEvents()
    assert rec.pressed == [ACTION]


def test_null_backend_reports_unsupported(qapp):
    backend = global_hotkey.NullHotkeyBackend()
    rec = Recorder(backend)
    assert backend.capabilities()["can_choose_combo"] is False
    backend.bind(ACTION, "Ctrl+Shift+H", initial=True)
    s = rec.last
    assert (s.state, s.preferred, s.effective, s.requested, s.error, s.capturing) == \
        ("unsupported", "Ctrl+Shift+H", "", "", hotkeys.MSG_UNSUPPORTED, False)
    backend.bind(ACTION, "alt+k")
    assert (rec.last.state, rec.last.preferred, rec.last.requested) == \
        ("unsupported", "Ctrl+Shift+H", "Alt+K")
    backend.bind(ACTION, "")
    assert rec.last.state == "unsupported" and rec.last.preferred == "Ctrl+Shift+H"
    backend.suspend()
    backend.resume()
    assert rec.last.state == "unsupported" and not rec.last.capturing
    backend.shutdown()
    assert backend.status(ACTION).state == "unsupported"


def test_factory_picks_null_off_windows(qapp):
    assert isinstance(global_hotkey.create_backend(platform="linux"),
                      global_hotkey.NullHotkeyBackend)
    assert isinstance(global_hotkey.create_backend(platform="darwin"),
                      global_hotkey.NullHotkeyBackend)


# ── Real Windows backend (safe combination only) ───────────────────────────
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _post_hotkey_to_self(hk_id):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    tid = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    assert user32.PostThreadMessageW(tid, global_hotkey.WM_HOTKEY, hk_id, 0)


def _bind_safe_or_skip(backend):
    """Bind SAFE_COMBO once. If another process already holds it (a second
    pytest run, a stray process), the test cannot run here: skip, do not fail.
    Callers unregister in `finally`, which also runs on skip."""
    backend.bind(ACTION, SAFE_COMBO, initial=True)
    status = backend.status(ACTION)
    if status.state == hotkeys.STATE_CONFLICT:
        pytest.skip(f"{SAFE_COMBO} is held by another process right now "
                    "(another test run?); real-backend test skipped")
    return status


@windows_only
def test_windows_backend_register_deliver_conflict_shutdown(qapp):
    first = global_hotkey.create_backend()
    second = global_hotkey.WindowsHotkeyBackend()
    try:
        assert isinstance(first, global_hotkey.WindowsHotkeyBackend)
        rec = Recorder(first)
        _bind_safe_or_skip(first)
        assert (rec.last.state, rec.last.effective) == ("ok", SAFE_COMBO)
        assert first._filter_installed

        # Delivery: a posted WM_HOTKEY with our id reaches `activated`.
        (hk_id,) = first._registrar.actions
        _post_hotkey_to_self(hk_id)
        qapp.processEvents()
        qapp.processEvents()
        assert rec.pressed == [ACTION]

        # A WM_HOTKEY with an id that is not ours is left alone.
        _post_hotkey_to_self(0x0042)
        qapp.processEvents()
        qapp.processEvents()
        assert rec.pressed == [ACTION]

        # Conflict: the same combination registered a second time.
        rec2 = Recorder(second)
        second.bind(ACTION, SAFE_COMBO, initial=True)
        assert (rec2.last.state, rec2.last.effective) == ("conflict", "")
        assert rec2.last.error == hotkeys.conflict_message(SAFE_COMBO)
        assert not second._filter_installed

        # Capture releases it (the second backend can take it now) ...
        first.suspend()
        assert not first._filter_installed
        second.bind(ACTION, SAFE_COMBO)
        assert rec2.last.state == "ok"
        second.shutdown()
        # ... and resume takes it back.
        first.resume()
        assert (rec.last.state, rec.last.effective) == ("ok", SAFE_COMBO)

        # Shutdown unregisters: the combination is free again afterwards.
        first.shutdown()
        assert first._registrar.actions == {} and not first._filter_installed
        probe = global_hotkey.WindowsHotkeyBackend()
        try:
            rec3 = Recorder(probe)
            probe.bind(ACTION, SAFE_COMBO, initial=True)
            assert rec3.last.state == "ok"
        finally:
            probe.shutdown()
    finally:
        first.shutdown()
        second.shutdown()


@windows_only
def test_windows_backend_shutdown_during_capture(qapp):
    backend = global_hotkey.WindowsHotkeyBackend()
    try:
        assert _bind_safe_or_skip(backend).state == "ok"
        backend.suspend()
        backend.shutdown()
        backend.resume()                    # must not register again
        assert backend._registrar.actions == {}
    finally:
        backend.shutdown()
