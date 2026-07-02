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


def angle_diff(a, b):
    """Smallest absolute difference between two angles in degrees.

    Wraps at +-180 so e.g. angle_diff(179, -179) == 2 - without this, blips
    for a sound directly behind the player (surround mode) split into two.
    """
    return abs((a - b + 180.0) % 360.0 - 180.0)
