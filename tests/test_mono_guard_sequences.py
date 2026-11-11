"""Mono feedback guard across a device failure: no chunk of the new device
may reach a mix that plays into it. Fake soundcard objects only; nothing is
opened or played. Skips where PyQt6/soundcard are unavailable (CI test job).
"""

import numpy as np
import pytest

pytest.importorskip("PyQt6")
try:
    import soundcard  # noqa: F401
except Exception as exc:
    pytest.skip(f"soundcard not usable here: {exc}", allow_module_level=True)

import audio_capture  # noqa: E402
import mono_output  # noqa: E402


class Dev:
    def __init__(self, name, dev_id, chunks=3, fail_after=None):
        self.name, self.id, self.channels = name, dev_id, 2
        self.isloopback = True
        self.chunks, self.fail_after, self.count = chunks, fail_after, 0

    def recorder(self, samplerate):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        self.count += 1
        if self.fail_after is not None and self.count > self.fail_after:
            raise RuntimeError("unplugged")
        if self.count >= self.chunks:
            Dev.thread.running = False
        return np.zeros((numframes, 2), dtype=np.float32)


class SC:
    def __init__(self, devs, default):
        self.devs, self.default = devs, default

    def all_microphones(self, include_loopback=False):
        return list(self.devs)

    def all_speakers(self):
        return list(self.devs)

    def default_speaker(self):
        return self.default


class Mono:
    made = []
    resolve_fails = False

    def __init__(self, device_name=None, samplerate=48000):
        self.device_name, self.fed, self.stopped = device_name, 0, False
        self.log = []
        Mono.made.append(self)

    class _Sig:
        def connect(self, fn):
            pass

    failed = _Sig()

    def _resolve_speaker(self):
        if Mono.resolve_fails:
            raise RuntimeError("no device")
        for d in audio_capture.sc.all_speakers():
            if self.device_name and self.device_name in d.name:
                return d
        return audio_capture.sc.default_speaker()

    def start(self):
        pass

    def feed(self, data):
        self.fed += 1
        self.log.append(self.current())

    def stop(self):
        self.stopped = True


def _run(devs, default, mono_device, monkeypatch):
    Mono.made = []
    Mono.resolve_fails = False
    monkeypatch.setattr(audio_capture, "sc", SC(devs, default))
    monkeypatch.setattr(mono_output, "MonoMixThread", Mono)
    monkeypatch.setattr(audio_capture.time, "sleep", lambda s: None)
    t = audio_capture.AudioCaptureThread()
    t.set_mono(True, mono_device)
    infos = []
    t.capture_info_signal.connect(lambda th, i: infos.append(i))
    Dev.thread = t
    t.running = True
    Mono.current = lambda self: audio_capture.sc and t._device_label
    t.run()
    return t, infos


def test_no_chunk_of_the_mono_device_reaches_a_running_mix(monkeypatch):
    # Mix plays to Realtek while the headset is captured. The headset dies
    # and capture moves onto Realtek: the mix is stopped before the first
    # Realtek chunk is fed, and no Realtek chunk is ever fed.
    headset = Dev("Headset", "{h}", chunks=99, fail_after=2)
    realtek = Dev("Realtek", "{r}", chunks=3)
    t, infos = _run([realtek, headset], headset, "Realtek", monkeypatch)
    assert len(Mono.made) == 1
    mono = Mono.made[0]
    assert mono.stopped
    assert set(mono.log) == {"Headset"}         # never fed while on Realtek
    active = [i for i in infos if i["active"]]
    assert active[0]["mono_paused"] is False
    assert active[-1]["device"] == "Realtek" and active[-1]["mono_paused"] is True


def test_held_back_mix_starts_only_after_capture_left_its_device(monkeypatch):
    headset = Dev("Headset", "{h}", chunks=99, fail_after=2)
    realtek = Dev("Realtek", "{r}", chunks=3)
    t, infos = _run([realtek, headset], headset, None, monkeypatch)
    # Unplugged headset: the fake default stays the headset, so the unset
    # mix device still resolves to it, which is not the captured Realtek.
    fed = [m for m in Mono.made if m.fed]
    assert len(fed) == 1                         # the held-back one never fed
    assert set(fed[0].log) == {"Realtek"}
    active = [i for i in infos if i["active"]]
    assert active[0]["mono_paused"] is True
    assert active[-1]["mono_paused"] is False
