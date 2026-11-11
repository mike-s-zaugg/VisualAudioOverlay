"""Unit tests for capture_state.py: the capture status the dashboard shows,
the mono feedback decision and the capture_device setting.

Plain Python (no Qt, soundcard or numpy), so they run on Linux CI. They guard
a mono-hearing player's audio: a wrong same-device answer either replays the
mix into the captured device (an echo into the working ear) or turns mono
off for nothing.
"""

import pytest

import capture_state as cs

KEYS = {"active", "source", "device", "channels", "layout", "mode", "note",
        "note_short", "mono_paused"}


# ── coerce_capture_device ──────────────────────────────────────────────

@pytest.mark.parametrize("raw, value", [
    (None, ""), ("", ""), (42, ""), (1.5, ""), (True, ""), ([], ""),
    ({"name": "x"}, ""), ("x" * 257, ""), ("x" * 256, "x" * 256),
    ("Headset Earphone (USB)", "Headset Earphone (USB)"),
    (" Spaced ", " Spaced ")])      # exact names: Windows names are not trimmed
def test_coerce_capture_device(raw, value):
    assert cs.coerce_capture_device(raw) == value


# ── same_device (mono feedback guard) ──────────────────────────────────

def test_ids_decide_when_both_known():
    assert cs.same_device("{a}", "Headset", "{a}", "Headset")
    assert cs.same_device("{a}", "Headset", "{a}", "Renamed")
    # Two identical headsets: same name, different endpoints. No false
    # positive, mono keeps playing to the other one.
    assert not cs.same_device("{a}", "Headset", "{b}", "Headset")


def test_names_decide_without_ids():
    assert cs.same_device(None, "Headset", None, "Headset")
    assert cs.same_device("{a}", "Headset", "", "Headset")
    assert not cs.same_device(None, "Headset", None, "Speakers")


def test_nothing_known_is_not_a_match():
    assert not cs.same_device(None, None, None, None)
    assert not cs.same_device("", "", "", "")
    assert not cs.same_device("{a}", None, None, None)


# ── Notes ──────────────────────────────────────────────────────────────

def test_note_priority_and_short_forms():
    pick = cs.pick_note
    assert pick(True, True, "system", 2) == (cs.NOTE_MONO_FEEDBACK, "Mono output paused.")
    assert pick(False, True, "system", 2) == (cs.NOTE_DEVICE_NOT_FOUND, "Using Windows default.")
    assert pick(False, False, "app", 2) == (cs.NOTE_APP_STEREO, "")
    assert pick(False, False, "system", 2) == (cs.NOTE_DEVICE_STEREO, "")
    assert pick(False, False, "system", 6) == ("", "")
    assert pick(False, False, "system", 8) == ("", "")
    assert pick(False, False, "system", 1) == ("", "")


def test_unresolved_mono_device_has_its_own_note():
    # Fail closed: the mix is paused because its device is unknown, and the
    # note says that instead of claiming a feedback loop.
    assert cs.pick_note(True, True, "system", 2, cs.MONO_PAUSE_UNRESOLVED) == (
        cs.NOTE_MONO_UNRESOLVED, "Mono output paused.")
    assert cs.pick_note(True, False, "system", 8, cs.MONO_PAUSE_FEEDBACK)[0] == \
        cs.NOTE_MONO_FEEDBACK
    # The reason only matters while mono is actually paused.
    assert cs.pick_note(False, False, "system", 6, cs.MONO_PAUSE_UNRESOLVED) == ("", "")
    info = cs.active_capture_info("system", "Headset", 2, "stereo", "stereo",
                                  mono_paused=True,
                                  mono_reason=cs.MONO_PAUSE_UNRESOLVED)
    assert info["mono_paused"] is True and info["note"] == cs.NOTE_MONO_UNRESOLVED


def test_not_found_only_applies_to_system_capture():
    assert cs.pick_note(False, True, "app", 2) == (cs.NOTE_APP_STEREO, "")


def test_mono_guard_beats_everything():
    for source in ("system", "app"):
        for nf in (False, True):
            for ch in (1, 2, 6, 8):
                assert cs.pick_note(True, nf, source, ch)[0] == cs.NOTE_MONO_FEEDBACK


def test_note_texts():
    full = (cs.NOTE_MONO_FEEDBACK, cs.NOTE_MONO_UNRESOLVED, cs.NOTE_DEVICE_NOT_FOUND,
            cs.NOTE_APP_STEREO, cs.NOTE_DEVICE_STEREO)
    short = (cs.NOTE_MONO_FEEDBACK_SHORT, cs.NOTE_DEVICE_NOT_FOUND_SHORT)
    for note in full + short:
        assert note.endswith(".") and note.count(".") == 1     # one sentence
        assert "\u2014" not in note and "\u2013" not in note   # no em/en dash
    for note in full:
        assert len(note) < 130
    for note in short:
        assert len(note) <= 24
    assert cs.NOTE_MONO_FEEDBACK_SHORT == "Mono output paused."
    assert cs.NOTE_DEVICE_NOT_FOUND_SHORT == "Using Windows default."


# ── Payloads ───────────────────────────────────────────────────────────

def test_active_payload_exact():
    info = cs.active_capture_info("system", "Speakers", 6, "5.1", "surround")
    assert info == {"active": True, "source": "system", "device": "Speakers",
                    "channels": 6, "layout": "5.1", "mode": "surround",
                    "note": "", "note_short": "", "mono_paused": False}


def test_active_payload_mono_paused():
    info = cs.active_capture_info("system", "Headset", 2, "stereo", "stereo",
                                  mono_paused=True, device_not_found=True)
    assert set(info) == KEYS
    assert info["mono_paused"] is True
    assert info["note"] == cs.NOTE_MONO_FEEDBACK
    assert info["note_short"] == "Mono output paused."


def test_inactive_payload_exact():
    assert cs.inactive_capture_info() == {
        "active": False, "source": "system", "device": "", "channels": 0,
        "layout": "stereo", "mode": "stereo", "note": "", "note_short": "",
        "mono_paused": False}


def test_payloads_are_fresh_dicts():
    a = cs.inactive_capture_info()
    a["active"] = True
    assert cs.inactive_capture_info()["active"] is False


# ── Device list ────────────────────────────────────────────────────────

def test_order_default_first_then_by_name_and_dedupe():
    raw = [
        {"name": "speakers (Realtek)", "channels": 6, "is_default": False},
        {"name": "Headset", "channels": 2, "is_default": False},
        {"name": "Sonar", "channels": 8, "is_default": True},
        {"name": "Headset", "channels": 2, "is_default": False},
        {"name": "", "channels": 2, "is_default": False},
        {"name": None, "channels": 2, "is_default": False},
        {"name": "Odd", "channels": None, "is_default": False},
    ]
    assert cs.order_capture_devices(raw) == [
        {"name": "Sonar", "channels": 8, "is_default": True},
        {"name": "Headset", "channels": 2, "is_default": False},
        {"name": "Odd", "channels": 0, "is_default": False},
        {"name": "speakers (Realtek)", "channels": 6, "is_default": False},
    ]


def test_duplicate_name_keeps_the_default():
    raw = [{"name": "Headset", "channels": 2, "is_default": False},
           {"name": "Headset", "channels": 8, "is_default": True}]
    assert cs.order_capture_devices(raw) == [
        {"name": "Headset", "channels": 8, "is_default": True}]


def test_devices_payload():
    devices = [{"name": "Headset", "channels": 2, "is_default": True}]
    assert cs.capture_devices_payload(devices, "") == {
        "devices": devices, "selected": "", "available": True}
    assert cs.capture_devices_payload(devices, "Headset")["available"] is True
    p = cs.capture_devices_payload(devices, "Unplugged")
    assert p["selected"] == "Unplugged" and p["available"] is False
    assert cs.capture_devices_payload([], "")["available"] is True


def test_module_has_no_platform_imports():
    import sys
    src = open(cs.__file__, encoding="utf-8").read()
    for banned in ("PyQt6", "soundcard", "numpy", "ctypes", "comtypes"):
        assert f"import {banned}" not in src and f"from {banned}" not in src
    assert "capture_state" in sys.modules
