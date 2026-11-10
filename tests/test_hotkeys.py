"""hotkeys.py: combination parsing, the settings rule, the Windows key table
and the binding state machine (driven by a fake registrar). Pure Python, runs
on Linux CI."""

import pytest

import hotkeys
from hotkeys import (DEFAULT_TOGGLE_HOTKEY, HotkeyBinder, InvalidCombo,
                     RegistrationFailed, normalize, parse_combo,
                     preference_from_setting, try_parse)


# ── Canonical form ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, canonical", [
    ("Ctrl+Shift+H", "Ctrl+Shift+H"),
    ("ctrl+shift+h", "Ctrl+Shift+H"),
    ("SHIFT+CTRL+H", "Ctrl+Shift+H"),
    ("  shift +  ctrl+ h ", "Ctrl+Shift+H"),
    ("Shift+Alt+Ctrl+x", "Ctrl+Alt+Shift+X"),
    ("alt+shift+pageup", "Alt+Shift+PageUp"),
    ("ctrl + page down", "Ctrl+PageDown"),
    ("ctrl+NUM5", "Ctrl+Num5"),
    ("f9", "F9"),
    ("Shift+f24", "Shift+F24"),
    ("alt+1", "Alt+1"),
])
def test_canonical_form(raw, canonical):
    assert parse_combo(raw).text == canonical
    assert normalize(raw) == canonical


def test_canonical_text_is_stable():
    for raw in ["ctrl+shift+h", "Alt+Up", "F1"]:
        text = parse_combo(raw).text
        assert parse_combo(text).text == text


def test_default_is_valid_and_canonical():
    assert normalize(DEFAULT_TOGGLE_HOTKEY) == DEFAULT_TOGGLE_HOTKEY
    # The maintainer's rule: the default is not a function key.
    assert parse_combo(DEFAULT_TOGGLE_HOTKEY).key not in hotkeys.FUNCTION_KEYS


# ── Every key class ────────────────────────────────────────────────────────
def test_vocabulary():
    keys = set(hotkeys.KEYS)
    assert len(keys) == len(hotkeys.KEYS) == 26 + 10 + 24 + 10 + 10
    for k in ["A", "Z", "0", "9", "F1", "F24", "Num0", "Num9", "Insert",
              "Delete", "Home", "End", "PageUp", "PageDown", "Up", "Down",
              "Left", "Right"]:
        assert k in keys


@pytest.mark.parametrize("key", hotkeys.KEYS)
def test_every_key_with_ctrl(key):
    assert parse_combo(f"Ctrl+{key}").text == f"Ctrl+{key}"
    if f"Alt+{key}" in hotkeys.RESERVED_COMBOS:
        return                  # covered by test_alt_f4_is_reserved
    assert parse_combo(f"alt+{key.lower()}").text == f"Alt+{key}"


@pytest.mark.parametrize("n", range(1, 25))
def test_function_keys_alone_and_with_shift(n):
    assert parse_combo(f"F{n}").text == f"F{n}"
    assert parse_combo(f"shift+f{n}").text == f"Shift+F{n}"


# ── Validity rules ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["A", "5", "Num5", "Up", "PageDown", "Insert",
                                 "Shift+A", "Shift+7", "shift+home"])
def test_needs_ctrl_or_alt_unless_function_key(raw):
    with pytest.raises(InvalidCombo) as e:
        parse_combo(raw)
    assert e.value.message == hotkeys.MSG_INVALID_NEEDS_MODIFIER
    # Named back in canonical form so the UI can show it.
    assert e.value.display == normalize_display(raw)


def normalize_display(raw):
    parts = [p.strip() for p in raw.split("+")]
    return "+".join(p[0].upper() + p[1:] if len(p) > 1 else p.upper() for p in parts)


@pytest.mark.parametrize("raw", ["Alt+A", "Ctrl+A", "Ctrl+Shift+Num0",
                                 "Alt+Left", "Ctrl+Alt+Delete"])
def test_ctrl_or_alt_makes_any_key_valid(raw):
    assert try_parse(raw) is not None


@pytest.mark.parametrize("raw, message", [
    ("Win+A", hotkeys.MSG_INVALID_WIN_KEY),
    ("Ctrl+Meta+A", hotkeys.MSG_INVALID_WIN_KEY),
    ("Ctrl+Shift", hotkeys.MSG_INVALID_ONE_KEY),
    ("Ctrl", hotkeys.MSG_INVALID_ONE_KEY),
    ("Ctrl+A+B", hotkeys.MSG_INVALID_ONE_KEY),
    ("Ctrl+Ctrl+A", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl+F25", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl+Space", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl+Esc", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl+", hotkeys.MSG_INVALID_UNKNOWN),
    ("+A", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl++A", hotkeys.MSG_INVALID_UNKNOWN),
    ("Control+A", hotkeys.MSG_INVALID_UNKNOWN),
    ("Ctrl+Ä", hotkeys.MSG_INVALID_UNKNOWN),
])
def test_invalid_combinations(raw, message):
    with pytest.raises(InvalidCombo) as e:
        parse_combo(raw)
    assert e.value.message == message
    assert try_parse(raw) is None
    assert normalize(raw) is None


@pytest.mark.parametrize("raw", ["Alt+F4", "alt+f4", " F4 + ALT "])
def test_alt_f4_is_reserved(raw):
    # RegisterHotKey would accept it, and Alt+F4 would then stop closing
    # windows everywhere while VAO runs.
    with pytest.raises(InvalidCombo) as e:
        parse_combo(raw)
    assert e.value.message == hotkeys.MSG_INVALID_ALT_F4
    assert e.value.display == "Alt+F4"
    assert normalize(raw) is None


@pytest.mark.parametrize("raw", ["Ctrl+Alt+F4", "Alt+Shift+F4", "Ctrl+Alt+Shift+F4",
                                 "F4", "Ctrl+F4", "Shift+F4", "Alt+F3", "Alt+F5"])
def test_only_exact_alt_f4_is_reserved(raw):
    assert parse_combo(raw).text == raw


def test_reserved_list_is_canonical_and_friendly():
    for text, message in hotkeys.RESERVED_COMBOS.items():
        combo = _combo_from_parts(text)
        assert combo.text == text and not combo.is_valid
        assert message and "\u2014" not in message and "\u2013" not in message


def _combo_from_parts(text):
    *mods, key = text.split("+")
    return hotkeys.Combo("Ctrl" in mods, "Alt" in mods, "Shift" in mods, key)


def test_alt_f4_change_is_invalid_and_keeps_binding():
    b, reg, _ = loaded()
    s = b.change("alt+F4")
    assert (s.state, s.preferred, s.effective, s.requested, s.error) == \
        ("invalid", "Ctrl+Shift+H", "Ctrl+Shift+H", "Alt+F4", hotkeys.MSG_INVALID_ALT_F4)
    assert reg.combos() == ["Ctrl+Shift+H"]
    assert ("register", "Alt+F4") not in reg.log      # never reaches the system


@pytest.mark.parametrize("junk", [None, 42, 1.5, True, [], {}, ["Ctrl+A"],
                                  "", "   ", "x" * 500, "Ctrl+" * 40 + "A"])
def test_junk_never_raises_anything_but_invalid_combo(junk):
    with pytest.raises(InvalidCombo):
        parse_combo(junk)
    assert try_parse(junk) is None


def test_long_input_is_clipped_for_display():
    with pytest.raises(InvalidCombo) as e:
        parse_combo("x" * 500)
    assert len(e.value.display) <= hotkeys.MAX_DISPLAY_LEN


def test_normalize_empty_means_unbound():
    assert normalize("") == ""
    assert normalize("  ") == ""


def test_messages_have_no_em_dashes():
    texts = [hotkeys.MSG_INVALID_UNKNOWN, hotkeys.MSG_INVALID_NEEDS_MODIFIER,
             hotkeys.MSG_INVALID_WIN_KEY, hotkeys.MSG_INVALID_ONE_KEY,
             hotkeys.MSG_UNSUPPORTED, hotkeys.MSG_INVALID_ALT_F4,
             hotkeys.conflict_message("Ctrl+Shift+H"),
             hotkeys.failed_message("Ctrl+Shift+H", 5)]
    for t in texts:
        assert "\u2014" not in t and "\u2013" not in t
    assert "Ctrl+Shift+H" in hotkeys.conflict_message("Ctrl+Shift+H")
    assert "5" in hotkeys.failed_message("Ctrl+Shift+H", 5)


# ── Settings rule ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, expected", [
    (None, DEFAULT_TOGGLE_HOTKEY),          # key missing
    ("", ""),                               # player cleared it
    ("   ", ""),
    ("ctrl+alt+k", "Ctrl+Alt+K"),
    ("F9", "F9"),
    ("Shift+A", DEFAULT_TOGGLE_HOTKEY),     # not allowed: junk
    ("Alt+F4", DEFAULT_TOGGLE_HOTKEY),      # reserved: junk, not unbound
    ("alt + f4", DEFAULT_TOGGLE_HOTKEY),
    ("nonsense", DEFAULT_TOGGLE_HOTKEY),
    (42, DEFAULT_TOGGLE_HOTKEY),
    (False, DEFAULT_TOGGLE_HOTKEY),
    ({"combo": "Ctrl+A"}, DEFAULT_TOGGLE_HOTKEY),
    (["Ctrl+A"], DEFAULT_TOGGLE_HOTKEY),
])
def test_preference_from_setting(value, expected):
    assert preference_from_setting(value) == expected


def test_settings_rule_on_a_dict():
    assert preference_from_setting({}.get("toggle_hotkey")) == "Ctrl+Shift+H"


# ── Windows mapping ────────────────────────────────────────────────────────
def test_windows_mapping_spot_checks():
    wb = hotkeys.windows_binding
    assert wb(parse_combo("Ctrl+Shift+H")) == (0x2 | 0x4, 0x48)
    assert wb(parse_combo("Alt+A")) == (0x1, 0x41)
    assert wb(parse_combo("Ctrl+Alt+Shift+Z")) == (0x7, 0x5A)
    assert wb(parse_combo("F1")) == (0, 0x70)
    assert wb(parse_combo("F24")) == (0, 0x87)
    assert wb(parse_combo("Ctrl+0")) == (0x2, 0x30)
    assert wb(parse_combo("Ctrl+9")) == (0x2, 0x39)
    assert wb(parse_combo("Ctrl+Num0")) == (0x2, 0x60)
    assert wb(parse_combo("Ctrl+Num9")) == (0x2, 0x69)
    assert wb(parse_combo("Ctrl+PageUp"))[1] == 0x21
    assert wb(parse_combo("Ctrl+PageDown"))[1] == 0x22
    assert wb(parse_combo("Ctrl+End"))[1] == 0x23
    assert wb(parse_combo("Ctrl+Home"))[1] == 0x24
    assert wb(parse_combo("Ctrl+Left"))[1] == 0x25
    assert wb(parse_combo("Ctrl+Up"))[1] == 0x26
    assert wb(parse_combo("Ctrl+Right"))[1] == 0x27
    assert wb(parse_combo("Ctrl+Down"))[1] == 0x28
    assert wb(parse_combo("Ctrl+Insert"))[1] == 0x2D
    assert wb(parse_combo("Ctrl+Delete"))[1] == 0x2E


def test_every_key_maps_to_a_distinct_vk():
    assert set(hotkeys.WINDOWS_VK) == set(hotkeys.KEYS)
    vks = list(hotkeys.WINDOWS_VK.values())
    assert len(set(vks)) == len(vks)
    assert all(0 < vk < 0xFF for vk in vks)


def test_mapping_never_includes_norepeat():
    mods, _ = hotkeys.windows_binding(parse_combo("Ctrl+Alt+Shift+A"))
    assert mods & hotkeys.MOD_NOREPEAT == 0


# ── Binding state machine ──────────────────────────────────────────────────
class FakeRegistrar:
    """Combinations in `taken` belong to another app; `fail_codes` maps a
    combination to a non-conflict error code."""

    def __init__(self, taken=(), fail_codes=None):
        self.taken = set(taken)
        self.fail_codes = dict(fail_codes or {})
        self.held = {}          # handle -> combo text
        self.next = 1
        self.log = []

    def register(self, action_id, combo):
        self.log.append(("register", combo.text))
        if combo.text in self.taken or combo.text in self.held.values():
            raise RegistrationFailed(True, 1409)
        if combo.text in self.fail_codes:
            raise RegistrationFailed(False, self.fail_codes[combo.text])
        handle = self.next
        self.next += 1
        self.held[handle] = combo.text
        return handle

    def unregister(self, handle):
        self.log.append(("unregister", self.held.get(handle)))
        self.held.pop(handle)

    def combos(self):
        return sorted(self.held.values())


def loaded(pref="Ctrl+Shift+H", **kw):
    reg = FakeRegistrar(**kw)
    b = HotkeyBinder(hotkeys.TOGGLE_OVERLAY, reg)
    status = b.load(pref)
    return b, reg, status


def test_load_ok():
    b, reg, s = loaded()
    assert (s.state, s.preferred, s.effective, s.requested, s.error, s.capturing) == \
        ("ok", "Ctrl+Shift+H", "Ctrl+Shift+H", "", "", False)
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_load_unbound():
    b, reg, s = loaded("")
    assert (s.state, s.preferred, s.effective) == ("unbound", "", "")
    assert reg.combos() == []


def test_startup_conflict_keeps_preference_but_nothing_active():
    b, reg, s = loaded(taken={"Ctrl+Shift+H"})
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("conflict", "Ctrl+Shift+H", "", "")
    assert "Ctrl+Shift+H" in s.error
    assert reg.combos() == []


def test_change_ok_registers_new_before_releasing_old():
    b, reg, _ = loaded()
    s = b.change("alt + k")
    assert (s.state, s.preferred, s.effective, s.requested, s.error) == \
        ("ok", "Alt+K", "Alt+K", "", "")
    assert reg.combos() == ["Alt+K"]
    assert reg.log[-2:] == [("register", "Alt+K"), ("unregister", "Ctrl+Shift+H")]


def test_change_conflict_keeps_old_binding():
    b, reg, _ = loaded(taken={"Ctrl+K"})
    s = b.change("ctrl+k")
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("conflict", "Ctrl+Shift+H", "Ctrl+Shift+H", "Ctrl+K")
    assert s.error == hotkeys.conflict_message("Ctrl+K")
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_change_generic_failure_reports_code_and_keeps_old_binding():
    b, reg, _ = loaded(fail_codes={"Ctrl+K": 87})
    s = b.change("Ctrl+K")
    assert (s.state, s.effective, s.requested) == ("conflict", "Ctrl+Shift+H", "Ctrl+K")
    assert "87" in s.error
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_change_invalid_keeps_old_binding():
    b, reg, _ = loaded()
    s = b.change("shift+a")
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("invalid", "Ctrl+Shift+H", "Ctrl+Shift+H", "Shift+A")
    assert s.error == hotkeys.MSG_INVALID_NEEDS_MODIFIER
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_change_non_string_does_not_clear():
    b, reg, _ = loaded()
    s = b.change(None)
    assert s.state == "invalid" and s.effective == "Ctrl+Shift+H"


def test_same_combination_again_is_a_noop():
    b, reg, _ = loaded()
    n = len(reg.log)
    s = b.change("ctrl+shift+h")
    assert (s.state, s.effective, s.requested, s.error) == ("ok", "Ctrl+Shift+H", "", "")
    assert len(reg.log) == n


def test_failure_message_cleared_by_next_success():
    b, reg, _ = loaded(taken={"Ctrl+K"})
    b.change("Ctrl+K")
    s = b.change("Ctrl+J")
    assert (s.state, s.requested, s.error, s.effective) == ("ok", "", "", "Ctrl+J")


def test_clear_binding():
    b, reg, _ = loaded()
    s = b.change("")
    assert (s.state, s.preferred, s.effective) == ("unbound", "", "")
    assert reg.combos() == []
    s = b.change("  ")
    assert s.state == "unbound"


def test_retry_after_startup_conflict():
    b, reg, _ = loaded(taken={"Ctrl+Shift+H"})
    reg.taken.clear()                      # the other app exited
    s = b.change("Ctrl+Shift+H")
    assert (s.state, s.effective, s.requested) == ("ok", "Ctrl+Shift+H", "")


def test_retry_after_startup_conflict_still_taken():
    b, reg, _ = loaded(taken={"Ctrl+Shift+H"})
    s = b.change("Ctrl+Shift+H")
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("conflict", "Ctrl+Shift+H", "", "Ctrl+Shift+H")


def test_suspend_releases_and_resume_restores():
    b, reg, _ = loaded()
    s = b.suspend()
    assert s.capturing and s.effective == "" and s.state == "ok"
    assert reg.combos() == []
    again = b.suspend()                    # repeated: harmless
    assert again == s and reg.combos() == []
    s = b.resume()
    assert not s.capturing and (s.state, s.effective) == ("ok", "Ctrl+Shift+H")
    assert reg.combos() == ["Ctrl+Shift+H"]
    again = b.resume()
    assert again == s and reg.combos() == ["Ctrl+Shift+H"]


def test_change_ends_capture():
    b, reg, _ = loaded()
    b.suspend()
    s = b.change("Alt+K")
    assert not s.capturing and (s.state, s.effective) == ("ok", "Alt+K")
    assert reg.combos() == ["Alt+K"]
    # The dashboard's late "false" changes nothing.
    assert b.resume() == s


def test_failed_change_during_capture_restores_old_binding():
    b, reg, _ = loaded(taken={"Ctrl+K"})
    b.suspend()
    s = b.change("Ctrl+K")
    assert not s.capturing
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("conflict", "Ctrl+Shift+H", "Ctrl+Shift+H", "Ctrl+K")
    assert reg.combos() == ["Ctrl+Shift+H"]
    # A later "false" keeps the error visible instead of wiping it.
    assert b.resume() == s


def test_invalid_change_during_capture_restores_old_binding():
    b, reg, _ = loaded()
    b.suspend()
    s = b.change("Q")
    assert (s.state, s.effective, s.requested, s.capturing) == \
        ("invalid", "Ctrl+Shift+H", "Q", False)
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_same_combination_during_capture_re_registers():
    b, reg, _ = loaded()
    b.suspend()
    s = b.change("Ctrl+Shift+H")
    assert (s.state, s.effective, s.capturing) == ("ok", "Ctrl+Shift+H", False)
    assert reg.combos() == ["Ctrl+Shift+H"]


def test_clear_during_capture():
    b, reg, _ = loaded()
    b.suspend()
    s = b.change("")
    assert (s.state, s.preferred, s.effective, s.capturing) == ("unbound", "", "", False)
    assert b.resume().state == "unbound" and reg.combos() == []


def test_resume_when_taken_during_capture_reports_conflict():
    b, reg, _ = loaded()
    b.suspend()
    reg.taken.add("Ctrl+Shift+H")
    s = b.resume()
    assert (s.state, s.preferred, s.effective, s.requested) == \
        ("conflict", "Ctrl+Shift+H", "", "")


def test_suspend_clears_old_error():
    b, reg, _ = loaded(taken={"Ctrl+K"})
    b.change("Ctrl+K")
    s = b.suspend()
    assert (s.requested, s.error) == ("", "")


def test_unbound_capture_round_trip():
    b, reg, _ = loaded("")
    assert b.suspend().state == "unbound"
    assert b.resume().state == "unbound"
    assert reg.combos() == []


def test_shutdown_unregisters():
    b, reg, _ = loaded()
    s = b.shutdown()
    assert s.effective == "" and reg.combos() == []
    # Nothing registers after shutdown, and repeating it is harmless.
    b.change("Alt+K")
    b.resume()
    b.shutdown()
    assert reg.combos() == []


def test_shutdown_during_capture():
    b, reg, _ = loaded()
    b.suspend()
    s = b.shutdown()
    assert not s.capturing and reg.combos() == []
    b.resume()
    assert reg.combos() == []


def test_load_during_capture_does_not_register():
    reg = FakeRegistrar()
    b = HotkeyBinder(hotkeys.TOGGLE_OVERLAY, reg)
    b.suspend()
    s = b.load("Ctrl+Shift+H")
    assert s.capturing and reg.combos() == []
    assert b.resume().effective == "Ctrl+Shift+H"


def test_states_are_from_the_contract():
    assert set(hotkeys.STATES) == {"ok", "unbound", "conflict", "invalid",
                                   "unsupported", "pending"}
