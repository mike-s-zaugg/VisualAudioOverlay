"""
Mono audio output for single-sided listeners.

The overlay shows the *direction* of a sound, but mono-hearing users also need
to *hear* the game - and to hear everything through their one working ear they
normally switch on Windows "mono audio". That setting sums L+R *before* our
loopback tap, which destroys the very stereo the overlay needs. So instead of
relying on the Windows setting, this module lets the app do the mono down-mix
itself: the captured stereo is still analysed in full for direction, while a
copy is summed to mono here and played to the user's real headphones.

Why a virtual cable is required
-------------------------------
If the game keeps playing to the headphones *and* we replay a mono mix, the user
hears it twice (doubling). The 2026-06-09 spike proved you cannot silence the
original without also silencing the capture (muting the session drops the
loopback to RMS 0 - the tap is after the mute node). The only fix is to route
the game's original output away from the ears, into a virtual sink such as
VB-CABLE, so *only* our mono mix reaches the headphones. See the
audioradar-mono-vbcable-plan memory for the full plan.

This module stays dependency-free beyond what the app already uses (numpy +
soundcard). It does not know about the cable routing itself - that is a one-time
Windows setup the user performs; here we only produce and play the mono signal.
"""

import queue

import numpy as np
import soundcard as sc
from PyQt6.QtCore import QThread, pyqtSignal

# Substrings that identify a virtual-audio-cable playback endpoint. VB-CABLE is
# the one we bundle/recommend; Voicemeeter users have an equivalent sink.
_CABLE_HINTS = ("cable", "vb-audio", "voicemeeter", "vac")


def list_output_devices() -> list[str]:
    """Names of available playback devices, for the mono-output picker."""
    try:
        return [s.name for s in sc.all_speakers()]
    except Exception:
        return []


def default_output_name() -> str | None:
    try:
        return sc.default_speaker().name
    except Exception:
        return None


def detect_virtual_cable() -> str | None:
    """Return the name of an installed virtual-cable playback endpoint, or None.

    Note this looks for the cable among *speakers* (somewhere audio can be sent).
    Its presence means the driver is installed; it does not prove the user has
    actually routed the game into it - that is surfaced as guidance in the UI.
    """
    for name in list_output_devices():
        low = name.lower()
        if any(h in low for h in _CABLE_HINTS):
            return name
    return None


class MonoMixThread(QThread):
    """Plays a mono down-mix of the captured audio to a chosen output device.

    The capture thread pushes raw (unfiltered) stereo chunks via `feed()`; this
    thread sums them to mono and writes them to the player. It runs on its own
    thread because `soundcard` players are thread-affine (must be created and
    used on the same thread) and so playback never blocks the capture/FFT loop.
    A small bounded queue keeps latency in check: if playback falls behind, the
    oldest chunk is dropped rather than building an ever-growing delay.
    """

    failed = pyqtSignal(str)

    def __init__(self, device_name: str | None = None, samplerate: int = 48000,
                 out_channels: int = 2, queue_max: int = 8):
        super().__init__()
        self.device_name = device_name or None
        self.samplerate = samplerate
        self.out_channels = out_channels
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._running = True

    def feed(self, stereo_chunk: np.ndarray):
        """Hand a raw capture chunk to the player. Non-blocking; drops the oldest
        queued chunk if the buffer is full so latency stays bounded."""
        if stereo_chunk is None or len(stereo_chunk) == 0:
            return
        try:
            self._q.put_nowait(stereo_chunk)
        except queue.Full:
            try:
                self._q.get_nowait()       # drop oldest
                self._q.put_nowait(stereo_chunk)
            except queue.Empty:
                pass

    def _resolve_speaker(self):
        if self.device_name:
            try:
                spk = sc.get_speaker(self.device_name)
                if spk is not None:
                    return spk
            except Exception:
                pass
        return sc.default_speaker()

    def run(self):
        try:
            speaker = self._resolve_speaker()
            if speaker is None:
                self.failed.emit("no output device for mono playback")
                return
            with speaker.player(samplerate=self.samplerate,
                                channels=self.out_channels) as player:
                while self._running:
                    try:
                        chunk = self._q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    player.play(self._to_mono(chunk))
        except Exception as e:
            self.failed.emit(str(e))

    def _to_mono(self, data: np.ndarray) -> np.ndarray:
        """Average every captured channel into one mono signal, then duplicate it
        across the output channels so it plays in both cups - whichever ear is
        the working one hears the full mix. 0.5-style averaging (mean) keeps the
        level from clipping versus a raw sum."""
        if data.ndim == 1:
            mono = data
        else:
            mono = data.mean(axis=1)
        out = np.repeat(mono[:, None], self.out_channels, axis=1)
        return np.ascontiguousarray(out, dtype=np.float32)

    def stop(self):
        self._running = False
        self.wait()
