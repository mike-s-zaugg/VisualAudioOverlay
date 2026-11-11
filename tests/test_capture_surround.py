"""AudioCaptureThread: direction through the real _analyse_chunk path, the
stereo/surround mode decision, capture device choice, the failure fallback
and the mono feedback guard.

Synthetic chunks and fake soundcard objects only: no device is opened and
nothing is played. Importing audio_capture needs PyQt6 and soundcard, which
CI's test job does not install, so the module skips there and runs on a
Windows dev machine (like test_overlay_hotkey.py).
"""

import numpy as np
import pytest

pytest.importorskip("PyQt6")
try:
    import soundcard  # noqa: F401
except Exception as exc:   # also fails with OSError/RuntimeError without audio
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

import audio_capture  # noqa: E402
import capture_state as cs  # noqa: E402
import mono_output  # noqa: E402
from direction import band_rms, stereo_angle, surround_angle  # noqa: E402

SR = 48000
N = 2400
F_LOW, F_HIGH = 150, 4000


def tone(amp, freq=440.0):
    t = np.arange(N) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def chunk(n, **amps):
    """n-channel chunk with a 440 Hz tone of the given amplitude per channel
    (keys c0..c7)."""
    data = np.zeros((N, n), dtype=np.float32)
    for key, amp in amps.items():
        data[:, int(key[1:])] = tone(amp)
    return data


class Recorder:
    def __init__(self, thread):
        self.blips = []
        self.infos = []
        self.devices = []
        thread.audio_data_signal.connect(lambda a, i: self.blips.append((a, i)))
        thread.capture_info_signal.connect(lambda t, info: self.infos.append(info))
        thread.device_info_signal.connect(lambda n, c: self.devices.append((n, c)))


def make_thread(**kw):
    t = audio_capture.AudioCaptureThread(**kw)
    t.set_freq_range(F_LOW, F_HIGH)
    return t, Recorder(t)


def old_stereo(data, sens=0.005, max_amp=1.0):
    """What the code before this change emitted for stereo mode."""
    rms = band_rms(data, SR, F_LOW, F_HIGH)
    left, right = float(rms[0]), float(rms[1])
    intensity = max(left, right)
    if sens < intensity < max_amp:
        return stereo_angle(left, right), intensity
    return None


def old_surround(data, sens=0.005, max_amp=1.0):
    """What the code before this change emitted once in surround mode."""
    rms = band_rms(data, SR, F_LOW, F_HIGH)
    fl, fr, c, rl, rr = (float(rms[i]) for i in (0, 1, 2, 4, 5))
    intensity = max(fl, fr, c, rl, rr)
    if sens < intensity < max_amp:
        return surround_angle(fl, fr, c, rl, rr), intensity
    return None


# ── Direction through _analyse_chunk ───────────────────────────────────

def test_stereo_input_same_as_before():
    t, rec = make_thread()
    rng = np.random.default_rng(3)
    expected = []
    for _ in range(200):
        data = (rng.standard_normal((N, 2)) * rng.random(2) * 0.2).astype(np.float32)
        t._analyse_chunk(data)
        old = old_stereo(data)
        if old is not None:
            expected.append(old)
    assert rec.blips == expected
    assert len(expected) > 100


def test_51_constant_surround_same_as_before():
    t, rec = make_thread()
    rng = np.random.default_rng(4)
    expected = []
    for _ in range(200):
        data = (rng.standard_normal((N, 6)) * rng.random(6) * 0.2).astype(np.float32)
        t._analyse_chunk(data)
        old = old_surround(data)
        if old is not None:
            expected.append(old)
    assert t._tracker.mode == "surround"
    assert rec.blips == expected        # exact floats, not approx


def test_gate_unchanged_in_surround():
    t, rec = make_thread()
    t.set_max_amplitude(0.1)
    t._analyse_chunk(chunk(6, c4=0.5))          # too loud: max_amp gate
    t._analyse_chunk(chunk(6, c4=0.001))        # too quiet: sensitivity gate
    assert rec.blips == []
    t._analyse_chunk(chunk(6, c4=0.05))
    assert len(rec.blips) == 1


def test_lfe_alone_does_not_blip_in_surround():
    t, rec = make_thread()
    t._analyse_chunk(chunk(6, c4=0.05))         # into surround mode
    rec.blips.clear()
    t._analyse_chunk(chunk(6, c3=0.5))          # bass only
    assert rec.blips == []


@pytest.mark.parametrize("side, angle", [(6, -90.0), (7, 90.0)])
def test_71_pure_side_channel(side, angle):
    t, rec = make_thread()
    t._analyse_chunk(chunk(8, **{f"c{side}": 0.2}))
    assert rec.blips[-1][0] == pytest.approx(angle)
    assert rec.infos[-1]["layout"] == "7.1"
    assert rec.infos[-1]["mode"] == "surround"


def test_quad_is_no_longer_stereo():
    t, rec = make_thread()
    t._analyse_chunk(chunk(4, c2=0.2, c3=0.2))  # BL + BR
    assert abs(rec.blips[-1][0]) == pytest.approx(180.0)


def test_switches_on_first_surround_chunk_after_silence():
    t, rec = make_thread()
    t._begin_stream("system", "Speakers", 6, None)
    assert rec.infos[-1]["mode"] == "stereo"
    for _ in range(100):                        # 5 s of quiet menu
        t._analyse_chunk(chunk(6))
    assert t._tracker.mode == "stereo"
    t._analyse_chunk(chunk(6, c5=0.2))          # rear right footstep
    assert rec.infos[-1]["mode"] == "surround"
    assert rec.devices[-1] == ("Speakers", 6)
    assert rec.blips[-1][0] == pytest.approx(135.0)


def test_returns_to_stereo_after_30s_front_only():
    t, rec = make_thread()
    t._begin_stream("system", "Speakers", 8, None)
    t._analyse_chunk(chunk(8, c4=0.2))
    assert rec.infos[-1]["mode"] == "surround"
    front = chunk(8, c0=0.1, c1=0.2)
    for _ in range(599):
        t._analyse_chunk(front)
    assert rec.infos[-1]["mode"] == "surround"
    # Surround math for a front-only sound during the hold (not stereo 90-ish).
    assert 0.0 < rec.blips[-1][0] < 45.0
    t._analyse_chunk(front)                     # 600 chunks = 30 s
    assert rec.infos[-1]["mode"] == "stereo"
    assert rec.devices[-1] == ("Speakers", 2)
    assert rec.blips[-1] == old_stereo(front)


def test_stereo_device_never_switches():
    t, rec = make_thread()
    t._begin_stream("system", "Headset", 2, None)
    for _ in range(50):
        t._analyse_chunk(chunk(2, c0=0.2, c1=0.1))
    assert [i["mode"] for i in rec.infos] == ["stereo"]


# ── captureInfo payload and notes ──────────────────────────────────────

KEYS = {"active", "source", "device", "channels", "layout", "mode", "note",
        "note_short", "mono_paused"}


def test_payload_shape_and_notes():
    t, rec = make_thread()
    t._begin_stream("system", "Speakers", 6, None)
    info = rec.infos[-1]
    assert set(info) == KEYS
    assert info == {"active": True, "source": "system", "device": "Speakers",
                    "channels": 6, "layout": "5.1", "mode": "stereo", "note": "",
                    "note_short": "", "mono_paused": False}
    t._begin_stream("system", "Headset", 2, None)
    assert rec.infos[-1]["note"] == cs.NOTE_DEVICE_STEREO
    assert rec.infos[-1]["note_short"] == ""
    t._begin_stream("app", "cs2.exe", 2, None)
    assert rec.infos[-1]["note"] == cs.NOTE_APP_STEREO
    assert rec.infos[-1]["note_short"] == ""
    assert rec.infos[-1]["source"] == "app"
    assert rec.devices[-1] == ("cs2.exe (per-app)", 2)


def test_lfe_leak_never_switches_a_stereo_game():
    # A driver leaking bass/DC onto LFE and faint hiss onto the rears while a
    # stereo game plays: the radar must stay in (wider) stereo math.
    t, rec = make_thread()
    t._begin_stream("system", "Speakers", 6, None)
    for _ in range(100):
        t._analyse_chunk(chunk(6, c0=0.2, c1=0.1, c3=0.9, c4=0.0005, c5=0.0005))
    assert [i["mode"] for i in rec.infos] == ["stereo"]
    t._analyse_chunk(chunk(6, c0=0.2, c1=0.1, c4=0.002))     # a real rear sound
    assert rec.infos[-1]["mode"] == "surround"


def test_inactive_payload_shape():
    info = audio_capture.inactive_capture_info()
    assert set(info) == KEYS and info["active"] is False
    assert info["mono_paused"] is False and info["note_short"] == ""


# ── Fakes for soundcard ────────────────────────────────────────────────

class FakeDevice:
    def __init__(self, name, dev_id, channels=2, chunks=0, fail=False):
        self.name = name
        self.id = dev_id
        self.channels = channels
        self.isloopback = True
        self.chunks = chunks        # how many chunks to deliver before stopping
        self.fail = fail
        self.fail_after = None      # raise like a device unplugged mid-stream
        self.opened = 0

    def recorder(self, samplerate):
        self.opened += 1
        if self.fail:
            raise RuntimeError("device gone")
        return FakeRecorder(self)


class FakeRecorder:
    thread = None               # the capture thread to stop after `chunks`

    def __init__(self, dev):
        self.dev = dev
        self.count = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        self.count += 1
        if self.dev.fail_after is not None and self.count > self.dev.fail_after:
            raise RuntimeError("device unplugged")
        if self.count >= self.dev.chunks and FakeRecorder.thread is not None:
            FakeRecorder.thread.running = False
        return np.zeros((numframes, self.dev.channels), dtype=np.float32)


class FakeSC:
    def __init__(self, devices, default):
        self.devices = devices
        self.default = default

    def all_microphones(self, include_loopback=False):
        return list(self.devices)

    def all_speakers(self):
        return list(self.devices)

    def default_speaker(self):
        return self.default


@pytest.fixture
def devices(monkeypatch):
    headset = FakeDevice("Headset Earphone (USB)", "{id-headset}", 2, chunks=3)
    sonar = FakeDevice("SteelSeries Sonar - Gaming", "{id-sonar}", 8, chunks=3)
    realtek = FakeDevice("Speakers (Realtek)", "{id-realtek}", 6, chunks=3)
    fake = FakeSC([realtek, headset, sonar], default=headset)
    monkeypatch.setattr(audio_capture, "sc", fake)
    return fake


def run_system(thread):
    FakeRecorder.thread = thread
    thread.running = True
    try:
        thread.run()
    finally:
        FakeRecorder.thread = None


def test_follows_windows_default_when_no_choice(devices):
    t, rec = make_thread()
    run_system(t)
    assert rec.infos[0]["device"] == "Headset Earphone (USB)"
    assert rec.infos[0]["note"] == cs.NOTE_DEVICE_STEREO
    assert rec.infos[-1] == audio_capture.inactive_capture_info()


def test_chosen_device_is_captured(devices):
    t, rec = make_thread()
    t.set_capture_device("SteelSeries Sonar - Gaming")
    run_system(t)
    assert rec.infos[0]["device"] == "SteelSeries Sonar - Gaming"
    assert rec.infos[0]["channels"] == 8 and rec.infos[0]["layout"] == "7.1"
    assert rec.infos[0]["note"] == ""
    assert devices.devices[1].opened == 0      # default not touched


def test_chosen_device_needs_exact_name(devices):
    t, rec = make_thread()
    t.set_capture_device("Sonar")               # no fuzzy matching
    run_system(t)
    assert rec.infos[0]["device"] == "Headset Earphone (USB)"
    assert rec.infos[0]["note"] == cs.NOTE_DEVICE_NOT_FOUND


def test_unplugged_choice_falls_back_to_default_with_note(devices):
    t, rec = make_thread()
    t.set_capture_device("Old USB Headset")
    run_system(t)
    assert rec.infos[0]["device"] == "Headset Earphone (USB)"
    assert rec.infos[0]["note"] == cs.NOTE_DEVICE_NOT_FOUND


def test_failure_fallback_chain_still_works(devices, monkeypatch):
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail = True              # the default headset dies
    t, rec = make_thread()
    statuses = []
    t.status_signal.connect(statuses.append)
    run_system(t)
    assert "failed - trying another output" in statuses[0]
    assert rec.infos[0]["device"] == "Speakers (Realtek)"
    assert rec.infos[0]["layout"] == "5.1"


def test_list_capture_devices_default_first_sorted(devices):
    devices.devices.append(FakeDevice("Speakers (Realtek)", "{id-dup}", 2))
    listed = audio_capture.list_capture_devices()
    assert listed == [
        {"name": "Headset Earphone (USB)", "channels": 2, "is_default": True},
        {"name": "Speakers (Realtek)", "channels": 6, "is_default": False},
        {"name": "SteelSeries Sonar - Gaming", "channels": 8, "is_default": False},
    ]


def test_list_capture_devices_survives_errors(monkeypatch):
    class Broken:
        def all_speakers(self):
            raise RuntimeError("COM error")

    monkeypatch.setattr(audio_capture, "sc", Broken())
    assert audio_capture.list_capture_devices() == []


# ── Mono feedback guard ────────────────────────────────────────────────

class FakeMono:
    """Stands in for MonoMixThread: resolves like it, never plays."""
    made = []
    resolve_failures = 0        # how many resolutions raise (COM error) first
    thread = None               # capture thread, to log what each chunk was from
    construct_fails = False

    def __init__(self, device_name=None, samplerate=48000):
        if FakeMono.construct_fails:
            raise RuntimeError("no audio stack")
        self.device_name = device_name
        self.started = False
        self.stopped = False
        self.fed = 0
        self.fed_from = []      # captured device label for every fed chunk
        FakeMono.made.append(self)

    class _Sig:
        def connect(self, fn):
            pass

    failed = _Sig()

    def _resolve_speaker(self):
        if FakeMono.resolve_failures > 0:
            FakeMono.resolve_failures -= 1
            raise RuntimeError("COM error while resolving")
        sc = audio_capture.sc
        for d in sc.all_speakers():
            if self.device_name and self.device_name in d.name:
                return d
        return sc.default_speaker()

    def start(self):
        self.started = True

    def feed(self, data):
        self.fed += 1
        if FakeMono.thread is not None:
            self.fed_from.append(FakeMono.thread._device_label)

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_mono(monkeypatch):
    FakeMono.made = []
    FakeMono.resolve_failures = 0
    FakeMono.thread = None
    FakeMono.construct_fails = False
    monkeypatch.setattr(mono_output, "MonoMixThread", FakeMono)
    yield FakeMono
    FakeMono.thread = None
    FakeMono.resolve_failures = 0
    FakeMono.construct_fails = False


def test_mono_to_default_while_capturing_default_is_blocked(devices, fake_mono):
    t, rec = make_thread()
    t.set_mono(True, None)                      # mono to "default device"
    run_system(t)
    assert not any(m.started for m in fake_mono.made)
    assert rec.infos[0]["note"] == cs.NOTE_MONO_FEEDBACK


def test_mono_to_captured_device_by_name_is_blocked(devices, fake_mono):
    t, rec = make_thread()
    t.set_capture_device("SteelSeries Sonar - Gaming")
    t.set_mono(True, "SteelSeries Sonar")       # resolves to the same device
    run_system(t)
    assert not any(m.started for m in fake_mono.made)
    assert rec.infos[0]["note"] == cs.NOTE_MONO_FEEDBACK


def test_mono_to_other_device_plays_as_before(devices, fake_mono):
    t, rec = make_thread()
    t.set_capture_device("SteelSeries Sonar - Gaming")
    t.set_mono(True, "Headset Earphone (USB)")
    run_system(t)
    assert len(fake_mono.made) == 1
    m = fake_mono.made[0]
    assert m.started and m.stopped and m.fed == 3
    assert rec.infos[0]["note"] == ""


def test_mono_stopped_when_fallback_moves_onto_its_device(devices, fake_mono, monkeypatch):
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail = True              # captured default headset dies
    t, rec = make_thread()
    t.set_mono(True, "Speakers (Realtek)")      # ...and the fallback is Realtek
    run_system(t)
    # The headset failed before the mix started; Realtek is then captured,
    # so the mix must not start on it.
    assert not any(m.started for m in fake_mono.made)
    assert rec.infos[0]["device"] == "Speakers (Realtek)"
    assert rec.infos[0]["note"] == cs.NOTE_MONO_FEEDBACK


def test_running_mono_is_stopped_if_capture_moves_onto_it(devices, fake_mono):
    t, rec = make_thread()
    t.set_mono(True, "Speakers (Realtek)")
    headset, realtek = devices.devices[1], devices.devices[0]
    t._start_mono(headset)
    assert fake_mono.made[0].started
    t._start_mono(realtek)                      # e.g. a later fallback
    assert fake_mono.made[0].stopped and t._mono is None
    assert t._mono_blocked


def test_payload_reports_mono_paused(devices, fake_mono):
    t, rec = make_thread()
    t.set_mono(True, None)
    run_system(t)
    assert rec.infos[0]["mono_paused"] is True
    assert rec.infos[0]["note_short"] == "Mono output paused."
    assert rec.infos[-1]["mono_paused"] is False        # inactive at the end


def test_not_found_note_cleared_when_fallback_leaves_the_default(devices, monkeypatch):
    # Chosen device missing -> Windows default (headset) with the note. The
    # headset is then unplugged and the fallback captures Realtek: the note
    # must no longer claim the Windows default is being captured.
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail_after = 2
    t, rec = make_thread()
    t.set_capture_device("Old USB Headset")
    run_system(t)
    active = [i for i in rec.infos if i["active"]]
    assert [i["device"] for i in active] == ["Headset Earphone (USB)", "Speakers (Realtek)"]
    assert active[0]["note"] == cs.NOTE_DEVICE_NOT_FOUND
    assert active[0]["note_short"] == "Using Windows default."
    assert active[1]["note"] == "" and active[1]["note_short"] == ""


def test_held_back_mono_resumes_when_fallback_leaves_its_device(devices, fake_mono, monkeypatch):
    # Mono to the default headset while capturing it: held back. The headset
    # dies and capture moves to Realtek, so the mix no longer feeds back and
    # starts (to the headset, as the player chose).
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail_after = 2
    t, rec = make_thread()
    t.set_mono(True, None)
    run_system(t)
    active = [i for i in rec.infos if i["active"]]
    assert active[0]["device"] == "Headset Earphone (USB)"
    assert active[0]["mono_paused"] is True
    assert active[1]["device"] == "Speakers (Realtek)"
    assert active[1]["mono_paused"] is False and active[1]["note"] == ""
    started = [m for m in fake_mono.made if m.started]
    assert len(started) == 1 and started[0].stopped


def test_mono_off_is_untouched(devices, fake_mono):
    t, rec = make_thread()
    run_system(t)
    assert fake_mono.made == []
    assert rec.infos[0]["note"] == cs.NOTE_DEVICE_STEREO


# ── Mono guard fails closed when the mix's device cannot be resolved ───

def test_unresolved_at_start_then_fallback_onto_mono_device_feeds_nothing(
        devices, fake_mono, monkeypatch):
    # The reviewer's sequence: resolving the mix's device raises at start
    # while the headset is captured; the headset is unplugged and capture
    # falls onto Realtek, which is the mono device. Not one chunk may reach
    # a mix while Realtek is captured.
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail_after = 2
    fake_mono.resolve_failures = 1
    t, rec = make_thread()
    fake_mono.thread = t
    t.set_mono(True, "Speakers (Realtek)")
    run_system(t)
    assert all("Speakers (Realtek)" not in m.fed_from for m in fake_mono.made)
    assert not any(m.started for m in fake_mono.made)
    active = [i for i in rec.infos if i["active"]]
    assert [i["device"] for i in active] == ["Headset Earphone (USB)", "Speakers (Realtek)"]
    assert active[0]["mono_paused"] is True
    assert active[0]["note"] == cs.NOTE_MONO_UNRESOLVED
    assert active[0]["note_short"] == "Mono output paused."
    assert active[1]["mono_paused"] is True
    assert active[1]["note"] == cs.NOTE_MONO_FEEDBACK


def test_never_resolvable_mix_never_starts_on_system_capture(devices, fake_mono, monkeypatch):
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail_after = 2
    fake_mono.resolve_failures = 99
    t, rec = make_thread()
    t.set_mono(True, "Speakers (Realtek)")
    run_system(t)
    assert not any(m.started or m.fed for m in fake_mono.made)
    active = [i for i in rec.infos if i["active"]]
    assert len(active) == 2
    assert all(i["mono_paused"] and i["note"] == cs.NOTE_MONO_UNRESOLVED for i in active)


def test_unresolved_at_start_resumes_once_resolvable_and_not_captured(
        devices, fake_mono, monkeypatch):
    # Retry on the next device change: resolvable now, plays to Sonar while
    # Realtek is captured, so it starts and only Realtek chunks reach it.
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    devices.devices[1].fail_after = 2
    fake_mono.resolve_failures = 1
    t, rec = make_thread()
    fake_mono.thread = t
    t.set_mono(True, "SteelSeries Sonar")
    run_system(t)
    started = [m for m in fake_mono.made if m.started]
    assert len(started) == 1
    assert started[0].fed_from and set(started[0].fed_from) == {"Speakers (Realtek)"}
    active = [i for i in rec.infos if i["active"]]
    assert active[0]["mono_paused"] is True
    assert active[1]["mono_paused"] is False and active[1]["note"] == ""


def test_mix_started_unresolved_in_per_app_is_rechecked_on_loopback(devices, fake_mono):
    # Per-app capture cannot pick the mix up, so an unresolved mix may start;
    # once a loopback device is captured it is resolved again, and stopped
    # if that still fails.
    t, rec = make_thread()
    t.set_mono(True, "Speakers (Realtek)")
    headset = devices.devices[1]
    fake_mono.resolve_failures = 1
    t._start_mono(None)
    mono = fake_mono.made[0]
    assert mono.started and t._mono_speaker is None
    fake_mono.resolve_failures = 1
    t._start_mono(headset)
    assert mono.stopped and t._mono is None
    assert t._mono_blocked and t._mono_pause_reason == cs.MONO_PAUSE_UNRESOLVED


def test_mix_started_unresolved_keeps_playing_when_retry_shows_other_device(devices, fake_mono):
    t, rec = make_thread()
    t.set_mono(True, "Speakers (Realtek)")
    fake_mono.resolve_failures = 1
    t._start_mono(None)
    t._start_mono(devices.devices[1])           # headset captured, mix on Realtek
    assert t._mono is fake_mono.made[0] and not fake_mono.made[0].stopped
    assert not t._mono_blocked
    t._start_mono(devices.devices[0])           # now Realtek itself is captured
    assert fake_mono.made[0].stopped and t._mono_blocked
    assert t._mono_pause_reason == cs.MONO_PAUSE_FEEDBACK


def test_mono_construction_failure_is_not_reported_as_guard(devices, fake_mono):
    t, rec = make_thread()
    t.set_mono(True, None)
    headset, realtek = devices.devices[1], devices.devices[0]
    t._start_mono(headset)                      # guard: mix to default = captured
    assert t._mono_blocked
    fake_mono.construct_fails = True
    t._start_mono(realtek)                      # capture moved, but no mono stack
    assert not t._mono_blocked and t._mono is None
    t._begin_stream("system", realtek.name, 6, realtek)
    assert rec.infos[-1]["mono_paused"] is False
    assert rec.infos[-1]["note"] == ""


def test_block_mono_survives_a_name_error(fake_mono):
    class Vanished:
        id = "{gone}"

        @property
        def name(self):
            raise RuntimeError("COM error")

    t, rec = make_thread()
    t._block_mono(Vanished())                   # must not raise
    assert t._mono_blocked
