import numpy as np
import soundcard as sc
from PyQt6.QtCore import QThread, pyqtSignal
import time

from direction import band_rms, stereo_angle, surround_angle

class AudioCaptureThread(QThread):
    audio_data_signal = pyqtSignal(float, float)
    device_info_signal = pyqtSignal(str, int)
    # Human-readable capture problems (device gone, no loopback, fallbacks).
    # The app forwards these to the dashboard status line so failures are
    # visible to the user, not just printed to a console nobody sees.
    status_signal = pyqtSignal(str)

    def __init__(self, sensitivity=0.005, gain=1.0, freq_low=20, freq_high=20000, max_amplitude=1.0,
                 target_pid=None, target_name=None):
        super().__init__()
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

    def set_target(self, pid, name=None):
        """Choose the capture source. Only takes effect before the thread starts."""
        self.target_pid = pid
        self.target_name = name

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
        # whole system mix the way we always have.
        self._start_mono()
        try:
            if self.target_pid:
                self._run_process_loopback()
            else:
                self._run_system_loopback()
        finally:
            self._stop_mono()

    # ── Mono down-mix output ───────────────────────────────────────────
    def _start_mono(self):
        if not self.mono_enabled:
            return
        try:
            from mono_output import MonoMixThread
            self._mono = MonoMixThread(device_name=self.mono_device,
                                       samplerate=self.samplerate)
            self._mono.failed.connect(lambda msg: print(f"Mono output error: {msg}"))
            self._mono.start()
            print(f"Mono output on -> {self.mono_device or 'default device'}")
        except Exception as e:
            print(f"Mono output unavailable ({e}); continuing without it.")
            self._mono = None

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
        self.device_info_signal.emit(f"{label} (per-app)", 2)
        try:
            while self.running:
                data = cap.read(2400)
                self._feed_mono(data)
                self._process_chunk(data, use_surround=False)
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
            
            # Pick device by priority
            device = None
            try:
                default_name = sc.default_speaker().name
                for lb in loopbacks:
                    if default_name in lb.name:
                        device = lb
                        break
            except:
                pass
            
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
                raw_channels = first_data.shape[1]
                
                use_surround = False
                if raw_channels >= 6:
                    surround_max = max(
                        float(np.max(np.abs(first_data[:, ch])))
                        for ch in range(2, min(raw_channels, 6))
                    )
                    if surround_max > 0.0001:
                        use_surround = True
                
                effective = raw_channels if use_surround else min(raw_channels, 2)
                mode = "360° Surround" if use_surround else "Stereo L/R"
                print(f"Channels: {raw_channels} | Mode: {mode}")
                self.device_info_signal.emit(device.name, effective)
                
                self._feed_mono(first_data)
                self._process_chunk(first_data, use_surround)

                while self.running:
                    data = mic.record(numframes=2400)
                    self._feed_mono(data)
                    self._process_chunk(data, use_surround)
                    
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
    
    def _process_chunk(self, data, use_surround):
        # Band-limited per-channel RMS (Hann-windowed FFT + Parseval; the
        # direction math only ever needs levels, never a filtered waveform).
        rms = band_rms(data, self.samplerate, self.freq_low, self.freq_high) * self.gain

        angle_deg = 0.0

        if use_surround and data.shape[1] >= 6:
            fl, fr, c = float(rms[0]), float(rms[1]), float(rms[2])
            rl, rr = float(rms[4]), float(rms[5])
            intensity = max(fl, fr, c, rl, rr)
            if intensity > self.sensitivity and intensity < self.max_amplitude:
                angle_deg = surround_angle(fl, fr, c, rl, rr)

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
