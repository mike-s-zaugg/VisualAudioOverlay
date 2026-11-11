import soundcard as sc
from PyQt6.QtCore import QThread, pyqtSignal
import time

from capture_state import (MONO_PAUSE_FEEDBACK, MONO_PAUSE_UNRESOLVED,
                           active_capture_info, inactive_capture_info,
                           order_capture_devices, same_device)
from direction import (SurroundModeTracker, band_rms, layout_angle,
                       layout_for_channels, layout_intensity, stereo_angle,
                       surround_evidence)


def _device_key(dev):
    """(id, name) of a soundcard device, for capture_state.same_device. Name
    lookups go to COM and can fail for a device that just vanished."""
    try:
        name = dev.name
    except Exception:
        name = None
    return getattr(dev, "id", None), name


def _same_device(a, b):
    """True if a speaker and a loopback microphone are the same endpoint."""
    return same_device(*_device_key(a), *_device_key(b))


def list_capture_devices():
    """Output devices that can be captured (every active output has a
    loopback), ordered and de-duplicated by capture_state. Names are the
    identity (like mono_device).

    Returns [{"name", "channels", "is_default"}]; [] if enumeration fails.
    "channels" is 0 when Windows does not report a format for a device."""
    try:
        speakers = sc.all_speakers()
    except Exception as e:
        print(f"Capture device enumeration failed: {e}")
        return []
    try:
        default_id = sc.default_speaker().id
    except Exception:
        default_id = None

    devices = []
    for spk in speakers:
        try:
            name = spk.name
        except Exception:
            continue                    # vanished mid-enumeration
        try:
            channels = int(spk.channels)
        except Exception:
            channels = 0
        devices.append({"name": name, "channels": channels,
                        "is_default": default_id is not None and spk.id == default_id})
    return order_capture_devices(devices)


class AudioCaptureThread(QThread):
    audio_data_signal = pyqtSignal(float, float)
    device_info_signal = pyqtSignal(str, int)
    # Human-readable capture problems (device gone, no loopback, fallbacks).
    # The app forwards these to the dashboard status line so failures are
    # visible to the user, not just printed to a console nobody sees.
    status_signal = pyqtSignal(str)
    # (this thread, live capture facts as a dict; see _capture_info). Sent
    # when a stream starts, when the stereo/surround mode changes and with
    # active False when capture ends. The thread is included so the app can
    # drop facts from a thread it has already replaced (queued signals from
    # the old thread can arrive after a restart).
    capture_info_signal = pyqtSignal(object, object)

    def __init__(self, sensitivity=0.005, gain=1.0, freq_low=20, freq_high=20000, max_amplitude=1.0,
                 target_pid=None, target_name=None, perf=None):
        super().__init__()
        # Optional perf_stats.PerfStats (VAO_PERF=1); None costs one check per chunk.
        self._perf = perf
        self.sensitivity = sensitivity
        self.gain = gain
        self.freq_low = freq_low
        self.freq_high = freq_high
        self.max_amplitude = max_amplitude  # ignore sounds louder than this (1.0 = no limit)
        self.running = True
        self.samplerate = 48000
        # When target_pid is set, capture only that program (and its children)
        # via WASAPI process loopback. None = whole-system loopback (soundcard).
        self.target_pid = target_pid
        self.target_name = target_name
        # Mono output: when enabled, the raw (unfiltered) captured audio is also
        # summed to mono and played to `mono_device` for single-sided listeners.
        # Read at thread start (like target); toggle via the app before Start.
        self.mono_enabled = False
        self.mono_device = None
        self._mono = None
        self._mono_speaker = None       # resolved playback device of the mix
        # True while the mono mix is held back because it would play into the
        # device being captured. Re-decided whenever the captured device
        # changes (see _start_mono).
        self._mono_blocked = False
        self._mono_pause_reason = MONO_PAUSE_FEEDBACK
        # Output device to loop back, by name. "" = the Windows default.
        self.capture_device = ""
        # The Windows default loopback that replaced a chosen device that is
        # not connected (None otherwise), and whether the device captured
        # right now is that replacement. Only then is the "not connected,
        # using the Windows default" note true: after a failure fallback to
        # a third device it would claim the wrong device.
        self._missing_choice_default = None
        self._device_not_found = False
        # Stream state, set per stream by _begin_stream (or lazily by the
        # first chunk in _analyse_chunk).
        self._source = "system"
        self._device_label = ""
        self._channels = 0
        self._layout = None
        self._tracker = None

    def set_target(self, pid, name=None):
        """Choose the capture source. Only takes effect before the thread starts."""
        self.target_pid = pid
        self.target_name = name

    def set_capture_device(self, name):
        """Choose the output device to loop back by name ("" or None = the
        Windows default). Only takes effect before the thread starts."""
        self.capture_device = name or ""

    def set_mono(self, enabled, device=None):
        """Enable/disable the mono down-mix output and pick its playback device.
        Only takes effect before the thread starts (the thread is recreated on
        each Start, so the app re-applies this in start_radar)."""
        self.mono_enabled = bool(enabled)
        self.mono_device = device or None

    def set_sensitivity(self, sensitivity):
        self.sensitivity = sensitivity
    
    def set_gain(self, gain):
        self.gain = gain
    
    def set_freq_range(self, low, high):
        self.freq_low = low
        self.freq_high = high
    
    def set_max_amplitude(self, max_amp):
        self.max_amplitude = max_amp

    def run(self):
        # Per-app capture takes the process-loopback path; otherwise capture the
        # whole system mix the way we always have. Mono playback starts once
        # the captured device is known (see _start_mono).
        try:
            if self.target_pid:
                self._run_process_loopback()
            else:
                self._run_system_loopback()
        finally:
            self._stop_mono()
            self.capture_info_signal.emit(self, inactive_capture_info())

    # ── Mono down-mix output ───────────────────────────────────────────
    def _start_mono(self, captured=None):
        """Start the mono mix for the stream about to be captured, or hold it
        back.

        `captured` is the loopback device being captured (None for per-app
        capture). If the mono mix would play into that same device, the
        loopback picks the mix up again and it is replayed again: an echo
        that never fades, straight into the player's working ear. The mix is
        then held back and the dashboard note says why.

        Called again whenever the failure fallback moves capture to another
        device, and decided afresh each time: a running mix whose device is
        now captured is stopped, and a held-back mix starts once capture has
        moved off its device (that is safe, because every later move is
        checked the same way before any audio reaches the new device).

        Fails closed: while a loopback device is captured, a mix whose
        playback device cannot be resolved is not started (and a running one
        is stopped), because nobody can tell whether it plays into the
        captured device. The resolution is retried on every device change.
        Per-app capture (captured None) cannot pick up the mix, so there an
        unresolved mix starts as before and the mono thread reports it."""
        if not self.mono_enabled:
            return
        if self._mono is not None:
            if captured is None:
                return
            if self._mono_speaker is None:
                # Started unresolved during per-app capture: retry now.
                self._mono_speaker = self._resolve_mono_speaker(self._mono)
            speaker = self._mono_speaker
            if speaker is None or _same_device(speaker, captured):
                self._stop_mono()
                self._block_mono(captured, unresolved=speaker is None)
            return
        try:
            from mono_output import MonoMixThread
            mono = MonoMixThread(device_name=self.mono_device,
                                 samplerate=self.samplerate)
        except Exception as e:
            # Not the guard's doing: the dashboard must not claim it is.
            print(f"Mono output unavailable ({e}); continuing without it.")
            self._mono = None
            self._mono_blocked = False
            return
        # The device the mix will really play to, resolved the way the mono
        # thread does it (fuzzy name, else the Windows default), so "default
        # device" on both sides is caught too.
        speaker = self._resolve_mono_speaker(mono)
        if captured is not None and (speaker is None or _same_device(speaker, captured)):
            self._block_mono(captured, unresolved=speaker is None)
            return
        try:
            if self._mono_blocked:
                print("Mono output resumes: capture moved off its device.")
            self._mono_blocked = False
            mono.failed.connect(lambda msg: print(f"Mono output error: {msg}"))
            mono.start()
            self._mono = mono
            self._mono_speaker = speaker
            print(f"Mono output on -> {self.mono_device or 'default device'}")
        except Exception as e:
            print(f"Mono output unavailable ({e}); continuing without it.")
            self._mono = None

    @staticmethod
    def _resolve_mono_speaker(mono):
        """The mix's playback device, or None if it cannot be resolved now."""
        try:
            return mono._resolve_speaker()
        except Exception:
            return None

    def _block_mono(self, captured, unresolved=False):
        self._mono_blocked = True
        self._mono_pause_reason = (MONO_PAUSE_UNRESOLVED if unresolved
                                   else MONO_PAUSE_FEEDBACK)
        # Through the guarded helper: a COM error on .name must not end capture.
        name = _device_key(captured)[1] or "?"
        if unresolved:
            print(f"Mono output held back: its device cannot be resolved while "
                  f"'{name}' is captured (it might be the same device).")
        else:
            print(f"Mono output skipped: it would play into the captured device "
                  f"'{name}' (feedback loop).")

    def _feed_mono(self, data):
        """Send the RAW (pre-bandpass) chunk to the mono player so the user hears
        the full game audio, not just the filtered footstep band."""
        if self._mono is not None:
            self._mono.feed(data)

    def _stop_mono(self):
        if self._mono is not None:
            try:
                self._mono.stop()
            except Exception:
                pass
            self._mono = None

    def _run_process_loopback(self):
        """Capture a single program's audio. Falls back to system audio on failure."""
        try:
            from process_loopback import ProcessLoopbackCapture
        except Exception as e:
            print(f"Process loopback unavailable ({e}); using system audio.")
            self.status_signal.emit("Per-app capture unavailable - using system audio")
            self._run_system_loopback()
            return

        cap = ProcessLoopbackCapture(self.target_pid, samplerate=self.samplerate, channels=2)
        try:
            cap.start()
        except Exception as e:
            print(f"Process loopback failed ({e}); using system audio.")
            self.status_signal.emit("Per-app capture failed - using system audio")
            self._run_system_loopback()
            return

        label = self.target_name or f"PID {self.target_pid}"
        print(f"Capturing app audio: {label} (per-app, Stereo L/R)")
        # Process loopback never includes this app's own output, so the mono
        # mix cannot feed back into it: no device to check against.
        self._start_mono(None)
        self._begin_stream("app", label, 2, None)
        try:
            while self.running:
                data = cap.read(2400)
                self._feed_mono(data)
                self._process_chunk(data)
        except Exception as e:
            print(f"Process loopback capture error: {e}")
            if self.running:
                self.status_signal.emit(
                    f"Capture of {label} stopped unexpectedly - restart the radar")
        finally:
            cap.close()

    def _run_system_loopback(self):
        try:
            mics = sc.all_microphones(include_loopback=True)
            loopbacks = [m for m in mics if m.isloopback]
            
            if not loopbacks:
                print("No loopback device found.")
                self.status_signal.emit(
                    "No audio output device found - the radar can't capture anything")
                return
            
            # The player's choice first (exact name, as listed by
            # list_capture_devices). If it is unplugged, use the default as
            # before and say so instead of silently capturing something else.
            device = None
            choice_missing = False
            if self.capture_device:
                device = next((lb for lb in loopbacks
                               if lb.name == self.capture_device), None)
                if device is None:
                    choice_missing = True
                    print(f"Capture device '{self.capture_device}' not found; "
                          f"using the Windows default.")

            # Then the Windows default: by endpoint id (exact), else by the
            # old name match.
            if not device:
                try:
                    default = sc.default_speaker()
                    device = next((lb for lb in loopbacks
                                   if _same_device(lb, default)), None)
                    if device is None:
                        for lb in loopbacks:
                            if default.name in lb.name:
                                device = lb
                                break
                except Exception:
                    pass
                if choice_missing and device is not None:
                    self._missing_choice_default = device
            
            if not device:
                for lb in loopbacks:
                    if "Microphone" not in lb.name:
                        device = lb
                        break
            
            if not device:
                device = loopbacks[0]
            
            print(f"Using loopback device: {device.name}")
            self._capture_loop(device, loopbacks)

        except Exception as e:
            print(f"Error in audio capture: {e}")
            import traceback
            traceback.print_exc()
            if self.running:
                self.status_signal.emit(f"Audio capture error: {e}")

    def _capture_loop(self, device, all_loopbacks):
        try:
            with device.recorder(samplerate=self.samplerate) as mic:
                first_data = mic.record(numframes=2400)
                # Stereo or surround is no longer decided from this first
                # 50 ms chunk: a radar started in a quiet menu stayed stereo
                # for the whole session. The tracker in _analyse_chunk
                # follows the audio on every chunk instead.
                self._start_mono(device)
                self._begin_stream("system", device.name, first_data.shape[1], device)

                self._feed_mono(first_data)
                self._process_chunk(first_data)

                while self.running:
                    data = mic.record(numframes=2400)
                    self._feed_mono(data)
                    self._process_chunk(data)
                    
        except RuntimeError as e:
            print(f"Device '{device.name}' failed: {e}")
            if self.running:
                self.status_signal.emit(
                    f"Audio device '{device.name}' failed - trying another output")
            for lb in all_loopbacks:
                if lb.name == device.name or "Microphone" in lb.name:
                    continue
                try:
                    time.sleep(0.5)
                    self._capture_loop(lb, [])
                    return
                except Exception:
                    continue
            # Fallbacks exhausted (or none to try): tell the user instead of
            # leaving a radar that silently never blips again.
            if self.running and all_loopbacks:
                self.status_signal.emit(
                    "All audio devices failed - stop and restart the radar")
    
    # ── Stream facts (layout, mode) for the dashboard ──────────────────
    def _begin_stream(self, source, device_label, channels, device):
        """A stream has started (also after a failure fallback moved capture
        to another device): pick its layout, start in stereo mode, re-decide
        the facts that depend on the device and tell the app.

        `device` is the loopback being captured, None for per-app capture."""
        self._source = source
        self._device_label = device_label
        self._device_not_found = (
            device is not None and self._missing_choice_default is not None
            and _same_device(device, self._missing_choice_default))
        self._set_layout(channels)
        print(f"Channels: {channels} | Layout: {self._layout.name} | "
              f"Mode: {self._tracker.mode}")
        self._report_stream()

    def _set_layout(self, channels):
        self._layout = layout_for_channels(channels)
        self._channels = int(channels)
        self._tracker = SurroundModeTracker(self._layout)

    def _report_stream(self):
        # The existing "name (Nch)" label keeps working: N is what the
        # direction math uses right now (2 in stereo mode), as before.
        if self._source == "app":
            self.device_info_signal.emit(f"{self._device_label} (per-app)", 2)
        else:
            used = self._channels if self._tracker.surround else min(self._channels, 2)
            self.device_info_signal.emit(self._device_label, used)
        self.capture_info_signal.emit(self, self._capture_info())

    def _capture_info(self):
        return active_capture_info(
            self._source, self._device_label, self._channels,
            self._layout.name, self._tracker.mode,
            mono_paused=self.mono_enabled and self._mono_blocked,
            device_not_found=self._device_not_found,
            mono_reason=self._mono_pause_reason)

    def _process_chunk(self, data):
        if self._perf is None:
            self._analyse_chunk(data)
            return
        t0 = time.perf_counter()
        self._analyse_chunk(data)
        self._perf.record_chunk((time.perf_counter() - t0) * 1000.0)

    def _analyse_chunk(self, data):
        if self._tracker is None or data.shape[1] != self._channels:
            self._set_layout(data.shape[1])       # direct callers (tests)
        layout = self._layout

        # Mode decision on the RAW chunk, before the band filter: the old
        # one-shot check looked at raw samples too, and a band-limited view
        # would miss surround content outside the footstep band.
        if self._tracker.update(surround_evidence(data, layout),
                                data.shape[0] / self.samplerate):
            print(f"Direction mode: {self._tracker.mode}")
            self._report_stream()

        # Band-limited per-channel RMS (Hann-windowed FFT + Parseval; the
        # direction math only ever needs levels, never a filtered waveform).
        rms = band_rms(data, self.samplerate, self.freq_low, self.freq_high) * self.gain

        angle_deg = 0.0

        if self._tracker.surround:
            levels = rms.tolist()
            intensity = layout_intensity(levels, layout)
            if intensity > self.sensitivity and intensity < self.max_amplitude:
                angle_deg = layout_angle(levels, layout)

        elif data.shape[1] >= 2:
            left_rms, right_rms = float(rms[0]), float(rms[1])
            intensity = max(left_rms, right_rms)
            if intensity > self.sensitivity and intensity < self.max_amplitude:
                angle_deg = stereo_angle(left_rms, right_rms)
        else:
            intensity = float(rms[0])

        if intensity > self.sensitivity and intensity < self.max_amplitude:
            self.audio_data_signal.emit(float(angle_deg), float(intensity))

    def stop(self):
        self.running = False
        self.wait()
