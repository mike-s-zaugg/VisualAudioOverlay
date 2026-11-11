"""Pure direction/level math shared by the capture thread and the overlay.

No Qt, no audio-device imports - everything here is plain numpy/math so it can
be unit-tested on any platform (see tests/test_direction.py) and reused without
dragging in soundcard/COM.
"""

import math

import numpy as np


def band_rms(data, samplerate, freq_low=20, freq_high=20000):
    """Per-channel RMS of `data` restricted to [freq_low, freq_high] Hz.

    `data` is (frames, channels) float. Returns a 1-D array of one RMS value
    per channel.

    The band restriction is computed in the frequency domain via Parseval's
    theorem on a Hann-windowed FFT, rather than zeroing bins and inverting
    (the old approach): the window kills the spectral leakage that a raw
    rectangular chunk smears across the band edge, and skipping the inverse
    FFT makes it cheaper. The Hann window's power loss is corrected by
    mean(w^2) so results are comparable to a plain time-domain RMS.
    """
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    n = data.shape[0]
    if n == 0:
        return np.zeros(data.shape[1])

    if freq_low <= 20 and freq_high >= 20000:
        # Full audible range: no filtering needed, plain RMS is exact.
        return np.sqrt(np.mean(data ** 2, axis=0))

    window = np.hanning(n)
    spec = np.fft.rfft(data * window[:, None], axis=0)
    freqs = np.fft.rfftfreq(n, d=1.0 / samplerate)
    in_band = (freqs >= freq_low) & (freqs <= freq_high)

    # Parseval for rfft: sum(x^2) = (|X_0|^2 + 2*sum(|X_k|^2) + |X_nyq|^2) / n.
    # The DC and (for even n) Nyquist bins appear once; all others twice.
    weights = np.full(len(freqs), 2.0)
    weights[0] = 1.0
    if n % 2 == 0:
        weights[-1] = 1.0

    band_energy = ((np.abs(spec) ** 2) * (weights * in_band)[:, None]).sum(axis=0) / n
    mean_square = band_energy / n / np.mean(window ** 2)
    return np.sqrt(mean_square)


def stereo_angle(left_rms, right_rms):
    """L/R balance -> angle in degrees, -90 (hard left) .. +90 (hard right).

    The 0.3 exponent expands small balance differences so slightly-panned
    sounds still visibly leave the centre of the radar.
    """
    balance = (right_rms - left_rms) / (right_rms + left_rms + 1e-6)
    sign = 1.0 if balance >= 0 else -1.0
    return sign * (abs(balance) ** 0.3) * 90.0


def surround_angle(fl, fr, c, rl, rr):
    """5.1 channel levels -> angle in degrees, 0 = front, +90 = right,
    +-180 = rear."""
    x = (fr + rr) - (fl + rl)
    y = (fl + fr + c) - (rl + rr)
    return math.degrees(math.atan2(x, y))


# ── Channel layouts ────────────────────────────────────────────────────
# The capture used to read channels 0, 1, 2, 4 and 5 by fixed position and
# nothing else, so in 7.1 the side speakers (6, 7) were never looked at: a
# sound at the player's side was lost or placed wrong, and a quad device was
# treated as stereo. Each layout below lists, per channel index in the order
# Windows delivers them (WAVEFORMATEXTENSIBLE speaker-bit order), a direction
# weight (x = right positive, y = front positive) or None for LFE, which
# carries no direction.
#
# The 5.1 vectors reproduce surround_angle exactly. Sides weigh sqrt(2) so a
# pure side sound reads exactly -90/+90 even though FL/BL also pull on y, and
# FL + SL at equal level reads exactly -67.5 (tan 67.5 = 1 + sqrt(2)).

_SIDE = math.sqrt(2.0)


class Layout:
    """One channel layout. `vectors` has one entry per channel used: an
    (x, y) direction weight, or None for a channel without direction (LFE)."""

    __slots__ = ("name", "vectors", "channels", "direction_indices",
                 "surround_capable", "evidence_slices")

    def __init__(self, name, vectors):
        self.name = name
        self.vectors = tuple(vectors)
        self.channels = len(self.vectors)
        self.direction_indices = tuple(
            i for i, v in enumerate(self.vectors) if v is not None)
        # Only layouts with rear or side speakers can ever leave stereo mode.
        self.surround_capable = self.channels >= 4
        # Channels whose content means "this is surround audio": every
        # direction channel except the front pair. As runs of contiguous
        # indices (slices), so the per-chunk check makes no index copies.
        idx = [i for i in self.direction_indices if i >= 2] \
            if self.surround_capable else []
        runs = []
        for i in idx:
            if runs and runs[-1][1] == i:
                runs[-1][1] = i + 1
            else:
                runs.append([i, i + 1])
        self.evidence_slices = tuple(slice(a, b) for a, b in runs)

    def __repr__(self):
        return f"Layout({self.name!r}, {self.channels}ch)"


_FL, _FR, _C = (-1.0, 1.0), (1.0, 1.0), (0.0, 1.0)
_BL, _BR = (-1.0, -1.0), (1.0, -1.0)
_SL, _SR = (-_SIDE, 0.0), (_SIDE, 0.0)

LAYOUTS = {
    1: Layout("mono", [(0.0, 1.0)]),
    # Stereo angles come from stereo_angle (balance), not from these vectors;
    # they are listed so intensity sees both channels.
    2: Layout("stereo", [(-1.0, 0.0), (1.0, 0.0)]),
    4: Layout("quad", [_FL, _FR, _BL, _BR]),
    # Windows' "5.1 back" (BL, BR) and "5.1 side" (SL, SR) both put the
    # surround pair on indices 4 and 5; both are read as rear, as before.
    6: Layout("5.1", [_FL, _FR, _C, None, _BL, _BR]),
    7: Layout("6.1", [_FL, _FR, _C, None, (0.0, -_SIDE), _SL, _SR]),
    8: Layout("7.1", [_FL, _FR, _C, None, _BL, _BR, _SL, _SR]),
}

LAYOUT_NAMES = tuple(layout.name for layout in LAYOUTS.values())


def layout_for_channels(channels):
    """The layout used for a stream with `channels` channels. Never raises.

    Counts without an entry use only the first channels: more than 8 reads
    the first 8 as 7.1. 3 (2.1 or 3.0) and 5 (5.0 or 4.1) read the front pair
    as stereo, because their third to fifth channels differ between the two
    variants and a wrong guess would put sounds in the wrong place, which is
    worse than showing only left and right. 0 or less is mono."""
    try:
        channels = int(channels)
    except (TypeError, ValueError):
        channels = 1
    if channels in LAYOUTS:
        return LAYOUTS[channels]
    if channels > 8:
        return LAYOUTS[8]
    if channels in (3, 5):
        return LAYOUTS[2]
    return LAYOUTS[1]


def layout_angle(levels, layout):
    """Per-channel levels -> angle in degrees, 0 = front, +90 = right,
    +-180 = rear. `levels` may hold more channels than the layout uses.

    Stereo uses stereo_angle and mono is always 0. For the others the
    positive and negative contributions are summed separately, in channel
    order, so 5.1 gives bit-for-bit the same float as surround_angle (the
    radar must not shift for players whose 5.1 setup already worked)."""
    name = layout.name
    if name == "mono":
        return 0.0
    if name == "stereo":
        return stereo_angle(float(levels[0]), float(levels[1]))
    x_pos = x_neg = y_pos = y_neg = 0.0
    for level, vec in zip(levels, layout.vectors):
        if vec is None:
            continue
        vx, vy = vec
        if vx > 0.0:
            x_pos += level if vx == 1.0 else level * vx
        elif vx < 0.0:
            x_neg += level if vx == -1.0 else level * -vx
        if vy > 0.0:
            y_pos += level if vy == 1.0 else level * vy
        elif vy < 0.0:
            y_neg += level if vy == -1.0 else level * -vy
    return math.degrees(math.atan2(x_pos - x_neg, y_pos - y_neg))


def layout_intensity(levels, layout):
    """Loudest direction channel (LFE excluded: bass rumble alone must not
    open the gate for a blip whose direction it cannot tell)."""
    return max(float(levels[i]) for i in layout.direction_indices)


def surround_evidence(chunk, layout):
    """Peak absolute RAW sample over every channel of the layout that is
    neither the front pair nor LFE (centre, rear, back and side). 0.0 for
    mono and stereo layouts, which can never be surround.

    LFE is left out on purpose (the old one-shot check counted it): it has
    no direction in the math, and a driver that leaks bass or DC onto it
    while a stereo game plays would otherwise switch the radar to surround
    math and narrow the display."""
    peak = 0.0
    if chunk.shape[0] == 0:
        return peak
    for sl in layout.evidence_slices:
        part = chunk[:, sl]
        if part.shape[1] == 0:
            continue
        # max/min instead of abs(): no temporary array the size of the chunk.
        peak = max(peak, float(part.max()), -float(part.min()))
    return peak


# ── Stereo/surround mode decision ──────────────────────────────────────
# About -60 dBFS. The old one-shot check switched on anything above 0.0001
# (-80 dBFS), so driver hiss or a DC offset on an unused surround channel
# was enough to put a stereo game into surround math for good. Real
# surround content is far louder; a single chunk above this still switches
# at once, so a short sound that exists only in a rear or side speaker is
# not missed.
SURROUND_EVIDENCE_THRESHOLD = 0.001
# How long a surround-capable stream must stay front-only before the radar
# goes back to stereo math. Long on purpose: in a surround game a sound that
# is momentarily only in the front speakers must keep the surround math (it
# would otherwise jump from about 45 to 90 degrees), while a player who moves
# from a surround game to stereo music without restarting gets the wider
# stereo display back.
SURROUND_HOLD_SECONDS = 30.0


class SurroundModeTracker:
    """Decides, chunk by chunk, whether the direction math runs in stereo or
    surround mode. Replaces the old one-shot check on the first 50 ms chunk,
    which left a session started in a quiet menu stuck in stereo until the
    next Start.

    No clock and no Qt: time advances only by the chunk durations passed to
    update(), so tests are deterministic."""

    def __init__(self, layout, threshold=SURROUND_EVIDENCE_THRESHOLD,
                 hold_seconds=SURROUND_HOLD_SECONDS):
        self.layout = layout
        self.threshold = threshold
        self.hold_seconds = hold_seconds
        self.surround = False
        self._quiet_seconds = 0.0

    @property
    def mode(self):
        return "surround" if self.surround else "stereo"

    def update(self, evidence, duration):
        """Feed one chunk's surround evidence and its length in seconds.
        Returns True when the mode changed with this chunk."""
        if not self.layout.surround_capable:
            return False
        if evidence > self.threshold:
            self._quiet_seconds = 0.0
            if not self.surround:
                self.surround = True
                return True
            return False
        if not self.surround:
            return False
        self._quiet_seconds += duration
        # Tolerance: 600 chunks of 0.05 s add up to a hair under 30.0 in floats.
        if self._quiet_seconds >= self.hold_seconds - 1e-9:
            self.surround = False
            self._quiet_seconds = 0.0
            return True
        return False


def angle_diff(a, b):
    """Smallest absolute difference between two angles in degrees.

    Wraps at +-180 so e.g. angle_diff(179, -179) == 2 - without this, blips
    for a sound directly behind the player (surround mode) split into two.
    """
    return abs((a - b + 180.0) % 360.0 - 180.0)
