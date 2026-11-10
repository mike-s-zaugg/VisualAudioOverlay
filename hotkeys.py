"""Hotkey combinations: parsing, validation, the Windows key table, and the
binding state machine behind the hotkey that starts and stops the radar.

Standard library only (no Qt, no ctypes) so the tests run on Linux CI.
global_hotkey.py wraps this in a Qt object and adds the platform backends;
main.py only ever sees canonical strings and BindingStatus values.

Combination strings are the stored, platform-neutral format: modifiers in the
order Ctrl, Alt, Shift, then exactly one key, joined with "+", for example
"Ctrl+Shift+H". "" means unbound. settings.json is hand-editable and the
dashboard sends whatever the player pressed, so every function here accepts
any value and never raises on bad input except where documented.
"""

from dataclasses import dataclass

# Default binding for the Start/Stop hotkey. Not a function key (the
# maintainer's call: games bind F-keys a lot) and not Ctrl+Alt, which Windows
# treats as AltGr, so on some layouts the hotkey would swallow typed characters.
DEFAULT_TOGGLE_HOTKEY = "Ctrl+Shift+H"

# The one action bound today: start the radar when stopped, stop it when
# running (the dashboard's Start/End button). Bindings are keyed by action id
# so more actions can be added later without changing the backends.
TOGGLE_RADAR = "toggle_radar"

# Binding states, exactly as the dashboard contract names them.
STATE_OK = "ok"                    # registered and active
STATE_UNBOUND = "unbound"
STATE_CONFLICT = "conflict"        # another app owns the combination
STATE_INVALID = "invalid"          # not a valid combination
STATE_UNSUPPORTED = "unsupported"  # no backend on this platform
STATE_PENDING = "pending"          # backend has not answered yet (async backends)
STATES = (STATE_OK, STATE_UNBOUND, STATE_CONFLICT, STATE_INVALID,
          STATE_UNSUPPORTED, STATE_PENDING)

MODIFIERS = ("Ctrl", "Alt", "Shift")

# Anything longer is junk, not a combination ("Ctrl+Alt+Shift+PageDown" is 23).
MAX_INPUT_LEN = 64
# The longest raw text echoed back as "requested" for input that is not even a
# combination, so a pasted wall of text cannot bloat every payload.
MAX_DISPLAY_LEN = 40


def _build_keys():
    keys = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    keys += [str(d) for d in range(10)]
    keys += [f"F{n}" for n in range(1, 25)]
    keys += [f"Num{d}" for d in range(10)]
    keys += ["Insert", "Delete", "Home", "End", "PageUp", "PageDown",
             "Up", "Down", "Left", "Right"]
    return tuple(keys)


# Closed vocabulary, in canonical spelling.
KEYS = _build_keys()
FUNCTION_KEYS = frozenset(f"F{n}" for n in range(1, 25))
_KEY_BY_LOWER = {k.lower(): k for k in KEYS}
_MOD_BY_LOWER = {m.lower(): m for m in MODIFIERS}
# Recognised only to give a clearer message than "unknown key".
_WIN_KEY_NAMES = frozenset({"win", "windows", "meta", "super", "cmd", "command"})

# ── Player-facing messages (short, friendly, no em dashes) ─────────────────
MSG_INVALID_UNKNOWN = (
    "That is not a key combination VAO can use. Use a letter, a number, F1 to "
    "F24, the number pad, an arrow key or Insert, Delete, Home, End, Page Up "
    "and Page Down.")
MSG_INVALID_NEEDS_MODIFIER = (
    "Hold Ctrl or Alt with that key, or use a function key (F1 to F24), so the "
    "hotkey does not get in the way of typing.")
MSG_INVALID_WIN_KEY = "The Windows key cannot be part of the hotkey."
MSG_INVALID_ONE_KEY = "Use exactly one key together with Ctrl, Alt or Shift."
MSG_UNSUPPORTED = "Global hotkeys are not available on this system yet."
MSG_INVALID_ALT_F4 = "Alt+F4 closes windows, so it cannot be used as the hotkey."

# Combinations the player must not bind even though they pass the rules above,
# keyed by canonical text. The one place to extend this list.
# - Alt+F4: RegisterHotKey accepts it, and while VAO runs it would then stop
#   closing windows everywhere. Only the exact set Alt+F4 is blocked;
#   Ctrl+Alt+F4, Alt+Shift+F4 and plain F4 do not close windows.
# Ctrl+Alt+Delete is not listed: Windows reserves it, so RegisterHotKey refuses
# it and the player gets the "system refused" message instead.
RESERVED_COMBOS = {
    "Alt+F4": MSG_INVALID_ALT_F4,
}


def conflict_message(combo_text):
    return (f"Another program is already using {combo_text}. "
            "Pick a different combination.")


def failed_message(combo_text, code):
    # Not a known conflict: say so honestly and give the code, so a bug report
    # can name the real cause.
    return (f"The system refused {combo_text} (error {code}). "
            "Pick a different combination.")


# ── Parsing ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Combo:
    """A parsed combination. `key` is canonical; build with parse_combo()."""
    ctrl: bool
    alt: bool
    shift: bool
    key: str

    @property
    def text(self):
        parts = [m for m, on in zip(MODIFIERS, (self.ctrl, self.alt, self.shift)) if on]
        return "+".join(parts + [self.key])

    @property
    def is_valid(self):
        # A function key is safe alone. Anything else needs Ctrl or Alt: Shift+A
        # or a bare letter would be swallowed system-wide and break typing.
        if self.text in RESERVED_COMBOS:
            return False
        return self.key in FUNCTION_KEYS or self.ctrl or self.alt

    def __str__(self):
        return self.text


class InvalidCombo(ValueError):
    """Raised by parse_combo. `message` is the player-facing sentence and
    `display` the best readable form of what was asked for."""

    def __init__(self, message, display):
        super().__init__(message)
        self.message = message
        self.display = display


def _clip(text):
    text = " ".join(text.split())
    if len(text) > MAX_DISPLAY_LEN:
        return text[:MAX_DISPLAY_LEN - 3] + "..."
    return text


def parse_combo(text):
    """Parse a combination string into a valid Combo.

    Case-insensitive; spaces anywhere are ignored ("ctrl + page up" works).
    Modifiers may come in any order. Raises InvalidCombo for anything that is
    not a valid, non-empty combination (empty input included: callers treat
    "" as "unbound" before calling this)."""
    if not isinstance(text, str):
        raise InvalidCombo(MSG_INVALID_UNKNOWN, "")
    if len(text) > MAX_INPUT_LEN:
        raise InvalidCombo(MSG_INVALID_UNKNOWN, _clip(text))
    parts = ["".join(p.split()).lower() for p in text.split("+")]
    raw_display = _clip(text)
    if not text.strip() or any(p == "" for p in parts):
        raise InvalidCombo(MSG_INVALID_UNKNOWN, raw_display)

    mods = set()
    keys = []
    for part in parts:
        if part in _MOD_BY_LOWER:
            mod = _MOD_BY_LOWER[part]
            if mod in mods:
                raise InvalidCombo(MSG_INVALID_UNKNOWN, raw_display)
            mods.add(mod)
        elif part in _KEY_BY_LOWER:
            keys.append(_KEY_BY_LOWER[part])
        elif part in _WIN_KEY_NAMES:
            raise InvalidCombo(MSG_INVALID_WIN_KEY, raw_display)
        else:
            raise InvalidCombo(MSG_INVALID_UNKNOWN, raw_display)

    if len(keys) != 1:
        raise InvalidCombo(MSG_INVALID_ONE_KEY, raw_display)
    combo = Combo("Ctrl" in mods, "Alt" in mods, "Shift" in mods, keys[0])
    if not combo.is_valid:
        # Well-formed, just not allowed: name it in canonical form.
        message = RESERVED_COMBOS.get(combo.text, MSG_INVALID_NEEDS_MODIFIER)
        raise InvalidCombo(message, combo.text)
    return combo


def try_parse(text):
    """parse_combo() that returns None instead of raising."""
    try:
        return parse_combo(text)
    except InvalidCombo:
        return None


def normalize(text):
    """Canonical form of a valid combination, "" for empty input, else None."""
    if isinstance(text, str) and not text.strip():
        return ""
    combo = try_parse(text)
    return combo.text if combo else None


def preference_from_setting(value):
    """The toggle_hotkey preference from a raw settings.json value.

    Missing (pass None), non-string, unparsable or not allowed (Shift+A, a
    reserved combination like Alt+F4) means the default binding; "" (or only
    spaces) means the player cleared it. Never raises, and the
    caller does not write the result back, so an old settings.json stays as it
    is until the player changes the hotkey."""
    if not isinstance(value, str):
        return DEFAULT_TOGGLE_HOTKEY
    if not value.strip():
        return ""
    combo = try_parse(value)
    return combo.text if combo else DEFAULT_TOGGLE_HOTKEY


# ── Windows mapping (pure table, so it is testable on Linux) ───────────────
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000   # added by the backend, not part of the mapping


def _build_vk_table():
    vk = {}
    for c in range(ord("A"), ord("Z") + 1):
        vk[chr(c)] = c                      # VK_A..VK_Z = ASCII
    for d in range(10):
        vk[str(d)] = 0x30 + d               # VK_0..VK_9 = ASCII
        vk[f"Num{d}"] = 0x60 + d            # VK_NUMPAD0..9
    for n in range(1, 25):
        vk[f"F{n}"] = 0x70 + n - 1          # VK_F1 (0x70)..VK_F24 (0x87)
    vk.update({
        "PageUp": 0x21,     # VK_PRIOR
        "PageDown": 0x22,   # VK_NEXT
        "End": 0x23,
        "Home": 0x24,
        "Left": 0x25,
        "Up": 0x26,
        "Right": 0x27,
        "Down": 0x28,
        "Insert": 0x2D,
        "Delete": 0x2E,
    })
    return vk


WINDOWS_VK = _build_vk_table()


def windows_binding(combo):
    """(fsModifiers, virtual_key) for RegisterHotKey, without MOD_NOREPEAT."""
    mods = 0
    if combo.ctrl:
        mods |= MOD_CONTROL
    if combo.alt:
        mods |= MOD_ALT
    if combo.shift:
        mods |= MOD_SHIFT
    return mods, WINDOWS_VK[combo.key]


# ── Binding state machine ──────────────────────────────────────────────────
class RegistrationFailed(Exception):
    """Raised by a registrar. conflict=True means another app owns the
    combination; otherwise `code` is the platform error code."""

    def __init__(self, conflict, code=0):
        super().__init__(f"conflict={conflict} code={code}")
        self.conflict = conflict
        self.code = code


@dataclass(frozen=True)
class BindingStatus:
    """What one action's binding looks like right now (the hotkeyChanged
    payload minus can_choose, which is a backend capability)."""
    state: str
    preferred: str = ""   # the saved preference ("" = unbound)
    effective: str = ""   # what is registered right now ("" = nothing)
    requested: str = ""   # what a failed change asked for
    error: str = ""
    capturing: bool = False


class HotkeyBinder:
    """Synchronous binding logic for one action, over a registrar with
    register(action_id, combo) -> handle (raises RegistrationFailed) and
    unregister(handle).

    Rules (the dashboard contract):
    - load() adopts the saved preference even if registering it fails, so a
      startup conflict leaves the preference saved but nothing active.
    - change() adopts the new combination only if it registers; a failed or
      invalid change keeps the previous binding and reports the failure with
      `requested` set. change() also ends a capture.
    - suspend() releases the registration so key presses reach the dashboard;
      resume() restores what should be active. Both are safe to repeat.
      While suspended the old combination is free, so another app could take
      it in that window; resume() or a failed change() then reports conflict
      with nothing active.
    - shutdown() releases everything for good.
    """

    def __init__(self, action_id, registrar):
        self.action_id = action_id
        self._registrar = registrar
        self.preferred = ""
        self._active = None        # Combo currently registered
        self._handle = None
        self.state = STATE_UNBOUND
        self.requested = ""
        self.error = ""
        self.capturing = False
        self._closed = False

    def status(self):
        return BindingStatus(
            state=self.state,
            preferred=self.preferred,
            effective=self._active.text if self._active else "",
            requested=self.requested,
            error=self.error,
            capturing=self.capturing,
        )

    # internals
    def _register(self, combo):
        """Register combo under a new handle. Returns (handle, state, error)."""
        try:
            handle = self._registrar.register(self.action_id, combo)
        except RegistrationFailed as e:
            if e.conflict:
                return None, STATE_CONFLICT, conflict_message(combo.text)
            # Not a known conflict, but the result for the player is the same:
            # that combination cannot be used, pick another one.
            return None, STATE_CONFLICT, failed_message(combo.text, e.code)
        return handle, STATE_OK, ""

    def _release(self):
        if self._handle is not None:
            self._registrar.unregister(self._handle)
        self._handle = None
        self._active = None

    def _activate_preferred(self):
        """Register the preference if it is not active yet. Sets state/error
        from the outcome."""
        if not self.preferred:
            self.state, self.error = STATE_UNBOUND, ""
            return
        combo = try_parse(self.preferred)
        if combo is None:
            self.state, self.error = STATE_INVALID, MSG_INVALID_UNKNOWN
            return
        if self._active == combo:
            self.state, self.error = STATE_OK, ""
            return
        handle, state, error = self._register(combo)
        if handle is not None:
            self._release()
            self._handle, self._active = handle, combo
        self.state, self.error = state, error

    # operations
    def load(self, preferred):
        """Startup: adopt the saved preference (canonical or "")."""
        if self._closed:
            return self.status()
        # Callers pass preference_from_setting() output; normalize() only
        # guards against a raw value slipping through.
        self.preferred = normalize(preferred) or ""
        self.requested = ""
        if self.capturing:
            self._release()
            self.state = STATE_OK if self.preferred else STATE_UNBOUND
            self.error = ""
            return self.status()
        self._activate_preferred()
        return self.status()

    def change(self, text):
        """The player picked a new combination ("" clears it)."""
        if self._closed:
            return self.status()
        self.capturing = False
        self.requested = ""
        self.error = ""

        if not isinstance(text, str):
            # The bridge slot is typed str, so this is a programming error. It
            # must not clear the binding the way "" does.
            return self._fail(STATE_INVALID, MSG_INVALID_UNKNOWN, "")
        if not text.strip():
            self._release()
            self.preferred = ""
            self.state = STATE_UNBOUND
            return self.status()

        try:
            combo = parse_combo(text)
        except InvalidCombo as e:
            return self._fail(STATE_INVALID, e.message, e.display)

        if self._active == combo:
            # Same combination again: nothing to do, but answer (state ok).
            self.preferred = combo.text
            self.state = STATE_OK
            return self.status()

        handle, state, error = self._register(combo)
        if handle is None:
            return self._fail(state, error, combo.text)
        # Outside a capture the old binding is still registered here and is
        # released only now that the new one is in place, so a failed change
        # never leaves a gap. In the dashboard flow, though, suspend() already
        # released it when capture started, and _fail() has to re-register it.
        self._release()
        self._handle, self._active = handle, combo
        self.preferred = combo.text
        self.state = STATE_OK
        return self.status()

    def _fail(self, state, error, requested):
        """A change failed: restore the previous binding if a capture had
        released it, then report the failure with the old values."""
        if self._active is None and self.preferred:
            self._activate_preferred()
        self.state, self.error, self.requested = state, error, requested
        return self.status()

    def suspend(self):
        """Capture started: release the registration (the combination would be
        swallowed before it reaches the dashboard otherwise)."""
        if self._closed or self.capturing:
            return self.status()
        self.capturing = True
        self.requested = ""
        self.error = ""
        self._release()
        # While capturing, state describes the binding resume() will restore;
        # effective is "" because nothing is registered.
        self.state = STATE_OK if self.preferred else STATE_UNBOUND
        return self.status()

    def resume(self):
        """Capture ended without a new combination: restore the preference."""
        if self._closed or not self.capturing:
            return self.status()
        self.capturing = False
        self._activate_preferred()
        return self.status()

    def shutdown(self):
        """App exit: unregister, also during a capture. Idempotent."""
        self._release()
        self.capturing = False
        self._closed = True
        return self.status()
