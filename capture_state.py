"""Pure decisions behind the capture status the dashboard shows.

No Qt, no soundcard, no numpy: these guard a mono-hearing player's audio (the
mono feedback decision) and what the dashboard tells them, so they are
unit-tested on Linux CI (tests/test_capture_state.py). audio_capture.py and
main.py use them; the Windows device objects are reduced to plain ids and
names before they get here.
"""

# One short sentence each for captureInfoChanged "note", and the short form
# for the one-line status ("note_short", about 24 characters at most). Per-app
# and stereo-device have no short form: the dashboard status already reads
# "Left and right only." for those, and the full note is in the tooltip.
NOTE_MONO_FEEDBACK = ("Mono output is paused because it would play into the "
                      "device the radar is listening to, so pick a different "
                      "mono output device.")
NOTE_MONO_FEEDBACK_SHORT = "Mono output paused."
# The mono guard fails closed: if the mix's playback device cannot be
# resolved, nobody can tell whether it is the captured device, so the mix is
# held back too. Same short form; the dashboard only needs "paused".
NOTE_MONO_UNRESOLVED = ("Mono output is paused because its output device "
                        "could not be found, so check the mono output device "
                        "and restart the radar.")
NOTE_DEVICE_NOT_FOUND = ("Your chosen audio device is not connected, so the "
                         "radar is listening to the Windows default device "
                         "instead.")
NOTE_DEVICE_NOT_FOUND_SHORT = "Using Windows default."
NOTE_APP_STEREO = ("Per-app capture is stereo only, so choose All (system "
                   "audio) for full 360 degree direction.")
NOTE_DEVICE_STEREO = ("This device is set to stereo (2 channels) in Windows, "
                      "so the radar can only show left and right.")

# Windows friendly names are short; soundcard reads at most 256 characters.
CAPTURE_DEVICE_MAX_LEN = 256


def coerce_capture_device(value):
    """The capture_device setting: an output device NAME, "" = follow the
    Windows default. Anything else (hand-edited settings.json, a profile from
    a future version, an oversized string from JS) becomes "" instead of a
    name that can never match a device."""
    if not isinstance(value, str) or len(value) > CAPTURE_DEVICE_MAX_LEN:
        return ""
    return value


def same_device(id_a, name_a, id_b, name_b):
    """True if two output endpoints are the same device. This decides whether
    the mono mix would play into the captured device (an echo that never
    fades, into the player's working ear).

    A loopback shares the WASAPI id of its speaker, so ids decide when both
    are known: two identical headsets can carry the same name. Only without
    ids do names decide. Nothing known on either side is not a match."""
    if id_a and id_b:
        return id_a == id_b
    if name_a and name_b:
        return name_a == name_b
    return False


MONO_PAUSE_FEEDBACK = "feedback"      # the mix would play into the captured device
MONO_PAUSE_UNRESOLVED = "unresolved"  # the mix's device could not be resolved


def pick_note(mono_paused, device_not_found, source, channels,
              mono_reason=MONO_PAUSE_FEEDBACK):
    """(note, note_short) for the player, most important first:
    mono guard (feedback, or the mix's device unknown), saved device missing,
    per-app is stereo, device is stereo in Windows, else nothing."""
    if mono_paused:
        if mono_reason == MONO_PAUSE_UNRESOLVED:
            return NOTE_MONO_UNRESOLVED, NOTE_MONO_FEEDBACK_SHORT
        return NOTE_MONO_FEEDBACK, NOTE_MONO_FEEDBACK_SHORT
    if device_not_found and source == "system":
        return NOTE_DEVICE_NOT_FOUND, NOTE_DEVICE_NOT_FOUND_SHORT
    if source == "app":
        return NOTE_APP_STEREO, ""
    if channels == 2:
        return NOTE_DEVICE_STEREO, ""
    return "", ""


def active_capture_info(source, device, channels, layout, mode,
                        mono_paused=False, device_not_found=False,
                        mono_reason=MONO_PAUSE_FEEDBACK):
    """captureInfoChanged payload while capturing. `device_not_found` must
    only be true while the device captured is the Windows default that
    replaced a missing choice (see audio_capture._begin_stream)."""
    note, note_short = pick_note(mono_paused, device_not_found, source, channels,
                                 mono_reason)
    return {
        "active": True,
        "source": source,
        "device": device,
        "channels": int(channels),
        "layout": layout,
        "mode": mode,
        "note": note,
        "note_short": note_short,
        "mono_paused": bool(mono_paused),
    }


def inactive_capture_info():
    """captureInfoChanged payload while nothing is being captured."""
    return {"active": False, "source": "system", "device": "", "channels": 0,
            "layout": "stereo", "mode": "stereo", "note": "", "note_short": "",
            "mono_paused": False}


def order_capture_devices(devices):
    """Windows default first, the rest sorted by name so the dropdown does
    not reshuffle when Windows enumerates in another order. A duplicate name
    is listed once (the first, so the default wins) because a name could
    never select the second one. `devices` holds dicts with name, channels,
    is_default; entries without a name are dropped."""
    seen = set()
    unique = []
    for d in sorted(devices, key=lambda d: not d.get("is_default")):
        name = d.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        unique.append({"name": name, "channels": int(d.get("channels") or 0),
                       "is_default": bool(d.get("is_default"))})
    unique.sort(key=lambda d: (not d["is_default"], d["name"].lower(), d["name"]))
    return unique


def capture_devices_payload(devices, selected):
    """captureDevicesChanged payload. A saved device that is unplugged right
    now is kept, not cleared, so plugging it back in restores it."""
    names = {d["name"] for d in devices}
    return {
        "devices": list(devices),
        "selected": selected,
        "available": selected == "" or selected in names,
    }
