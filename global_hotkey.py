"""System-wide hotkeys behind a small backend interface.

main.py talks to GlobalHotkeyBackend only. Today there is a Windows backend
(RegisterHotKey) and a null backend for everything else; a Linux port adds a
backend (xdg-desktop-portal GlobalShortcuts, KDE KGlobalAccel) without touching
main.py. That is why results always arrive through statusChanged and never only
as a return value: those services answer asynchronously and may assign a
different combination than the one requested.

Anti-cheat note: no keyboard hooks, no polling, no extra thread. Windows posts
WM_HOTKEY for the registered combination only; this app never sees any other
key press. The combination itself is swallowed (the game does not get it).

Importing this module on Linux must not touch ctypes.windll: the Windows parts
load lazily inside WindowsHotkeyBackend.
"""

import sys

from PyQt6.QtCore import (QAbstractNativeEventFilter, QCoreApplication, QObject,
                          Qt, pyqtSignal)

import hotkeys


class GlobalHotkeyBackend(QObject):
    """Interface. All methods must be called on the GUI thread.

    activated(action_id) is emitted on the GUI thread when a bound combination
    is pressed. statusChanged(action_id, hotkeys.BindingStatus) is emitted after
    every call below (also when nothing changed, so every request gets an
    answer) and whenever the system changes a binding on its own."""

    activated = pyqtSignal(str)
    statusChanged = pyqtSignal(str, object)

    def capabilities(self):
        """can_choose_combo: the app picks the combination itself.
        managed_by_system: the desktop owns bindings (the player edits them in
        system settings, the app only proposes one)."""
        raise NotImplementedError

    def bind(self, action_id, combo, initial=False):
        """Bind action_id to combo ("" clears it). initial=True is the startup
        load of the saved preference; otherwise it is a change by the player."""
        raise NotImplementedError

    def unbind(self, action_id):
        self.bind(action_id, "")

    def suspend(self):
        """Release every registration so key presses reach the dashboard."""
        raise NotImplementedError

    def resume(self):
        """Restore what should be active after suspend()."""
        raise NotImplementedError

    def shutdown(self):
        """Release everything for good (app exit). Idempotent."""
        raise NotImplementedError

    def status(self, action_id):
        """The last reported status for action_id."""
        raise NotImplementedError


class NullHotkeyBackend(GlobalHotkeyBackend):
    """No hotkey support on this platform: every bind reports unsupported.
    The saved preference is kept as is, so it comes back on a platform that
    does support it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preferred = {}

    def capabilities(self):
        return {"can_choose_combo": False, "managed_by_system": False}

    def _report(self, action_id, requested=""):
        status = hotkeys.BindingStatus(
            state=hotkeys.STATE_UNSUPPORTED,
            preferred=self._preferred.get(action_id, ""),
            requested=requested,
            error=hotkeys.MSG_UNSUPPORTED,
        )
        self.statusChanged.emit(action_id, status)
        return status

    def bind(self, action_id, combo, initial=False):
        if initial:
            self._preferred[action_id] = hotkeys.normalize(combo) or ""
            self._report(action_id)
        else:
            self._report(action_id, hotkeys.normalize(combo) or "")

    def suspend(self):
        for action_id in self._preferred:
            self._report(action_id)

    resume = suspend

    def shutdown(self):
        pass

    def status(self, action_id):
        return hotkeys.BindingStatus(
            state=hotkeys.STATE_UNSUPPORTED,
            preferred=self._preferred.get(action_id, ""),
            error=hotkeys.MSG_UNSUPPORTED,
        )


class BinderBackend(GlobalHotkeyBackend):
    """A synchronous backend over a registrar (see hotkeys.HotkeyBinder). The
    Windows backend is one; tests drive it with a fake registrar."""

    # Internal, connected queued: the platform callback (a native event filter
    # on Windows) only posts, and the overlay toggle runs from the event loop
    # like any other slot rather than inside the native message dispatch.
    _pressed = pyqtSignal(str)

    def __init__(self, registrar, parent=None):
        super().__init__(parent)
        self._registrar = registrar
        self._binders = {}
        self._pressed.connect(self.activated, Qt.ConnectionType.QueuedConnection)

    def capabilities(self):
        return {"can_choose_combo": True, "managed_by_system": False}

    def _binder(self, action_id):
        binder = self._binders.get(action_id)
        if binder is None:
            binder = hotkeys.HotkeyBinder(action_id, self._registrar)
            self._binders[action_id] = binder
        return binder

    def _registrations_changed(self):
        """Hook for subclasses: called after anything was (un)registered."""

    def _publish(self, action_id, status):
        self._registrations_changed()
        self.statusChanged.emit(action_id, status)

    def bind(self, action_id, combo, initial=False):
        binder = self._binder(action_id)
        status = binder.load(combo) if initial else binder.change(combo)
        self._publish(action_id, status)

    def suspend(self):
        for action_id, binder in self._binders.items():
            self._publish(action_id, binder.suspend())

    def resume(self):
        for action_id, binder in self._binders.items():
            self._publish(action_id, binder.resume())

    def shutdown(self):
        for binder in self._binders.values():
            binder.shutdown()
        self._registrations_changed()

    def status(self, action_id):
        return self._binder(action_id).status()

    def _emit_pressed(self, action_id):
        self._pressed.emit(action_id)


# ── Windows ────────────────────────────────────────────────────────────────
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
# Thread hotkeys (hWnd = NULL) may use ids 0x0000..0xBFFF. Start somewhere
# recognisable so a WM_HOTKEY from anything else is easy to tell apart.
_ID_FIRST = 0x5641
_ID_LAST = 0xBFFF


class _WindowsRegistrar:
    """RegisterHotKey/UnregisterHotKey for the calling (GUI) thread."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._register = user32.RegisterHotKey
        self._register.argtypes = [wintypes.HWND, ctypes.c_int,
                                   wintypes.UINT, wintypes.UINT]
        self._register.restype = wintypes.BOOL
        self._unregister = user32.UnregisterHotKey
        self._unregister.argtypes = [wintypes.HWND, ctypes.c_int]
        self._unregister.restype = wintypes.BOOL
        self._next_id = _ID_FIRST
        # hotkey id -> action id. Read by the message filter.
        self.actions = {}

    def _new_id(self):
        # A fresh id per registration: a change outside a capture registers the
        # new combination before releasing the old one, so both exist for a
        # moment. (In the dashboard flow capture already released the old one.)
        for _ in range(_ID_LAST - _ID_FIRST + 1):
            hk_id = self._next_id
            self._next_id = _ID_FIRST if hk_id >= _ID_LAST else hk_id + 1
            if hk_id not in self.actions:
                return hk_id
        raise hotkeys.RegistrationFailed(False, 0)

    def register(self, action_id, combo):
        mods, vk = hotkeys.windows_binding(combo)
        hk_id = self._new_id()
        # hWnd NULL: WM_HOTKEY goes to this thread's queue, so no window has to
        # exist and re-creating the overlay window (Move mode) cannot drop it.
        # MOD_NOREPEAT: holding the keys down toggles once, not 30 times a second.
        if not self._register(None, hk_id, mods | hotkeys.MOD_NOREPEAT, vk):
            code = self._ctypes.get_last_error()
            raise hotkeys.RegistrationFailed(
                code == ERROR_HOTKEY_ALREADY_REGISTERED, code)
        self.actions[hk_id] = action_id
        return hk_id

    def unregister(self, hk_id):
        self.actions.pop(hk_id, None)
        # Failure here only means it was not registered; nothing to report.
        self._unregister(None, hk_id)


class _WmHotkeyFilter(QAbstractNativeEventFilter):
    """While installed, this small Python callback runs for every native
    message of this app's GUI thread, so the check is one integer read for
    the message type and nothing else (measured at about 0.3 microseconds per
    message). It is installed while a hotkey is registered, which is the
    default; with nothing registered it is removed (see WindowsHotkeyBackend)."""

    def __init__(self, actions, on_press):
        super().__init__()
        import ctypes
        from ctypes import wintypes

        self._uint = ctypes.c_uint
        self._uptr = ctypes.c_size_t
        self._message_offset = wintypes.MSG.message.offset
        self._wparam_offset = wintypes.MSG.wParam.offset
        self._actions = actions
        self._on_press = on_press

    def nativeEventFilter(self, event_type, message):
        # On Windows every event type Qt passes here carries a MSG*.
        addr = int(message)
        if self._uint.from_address(addr + self._message_offset).value != WM_HOTKEY:
            return False, 0
        action_id = self._actions.get(
            self._uptr.from_address(addr + self._wparam_offset).value)
        if action_id is None:
            return False, 0     # not ours (another library in this process)
        self._on_press(action_id)
        return True, 0


class WindowsHotkeyBackend(BinderBackend):
    """RegisterHotKey for the GUI thread plus a native event filter for
    WM_HOTKEY. Must be created after the QApplication, on the GUI thread."""

    def __init__(self, parent=None):
        registrar = _WindowsRegistrar()
        super().__init__(registrar, parent)
        self._app = QCoreApplication.instance()
        if self._app is None:
            raise RuntimeError("WindowsHotkeyBackend needs a QApplication")
        self._filter = _WmHotkeyFilter(registrar.actions, self._emit_pressed)
        self._filter_installed = False

    def _registrations_changed(self):
        # Install the filter only while something is registered. Then (the
        # default) a tiny Python callback runs for each native message of the
        # GUI thread. Unbound, in capture, or after a startup conflict, no
        # filter is installed and no Python runs per native message.
        want = bool(self._registrar.actions)
        if want and not self._filter_installed:
            self._app.installNativeEventFilter(self._filter)
        elif not want and self._filter_installed:
            self._app.removeNativeEventFilter(self._filter)
        self._filter_installed = want


def create_backend(parent=None, platform=None):
    """The backend for this platform. Never raises: if the Windows backend
    cannot start, the app runs on without hotkeys (state unsupported)."""
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        try:
            return WindowsHotkeyBackend(parent)
        except Exception as e:
            print(f"Global hotkeys unavailable: {e}")
    return NullHotkeyBackend(parent)
