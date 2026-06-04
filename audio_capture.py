import numpy as np
import soundcard as sc
from PyQt6.QtCore import QThread, pyqtSignal
import math
import time

class AudioCaptureThread(QThread):
    audio_data_signal = pyqtSignal(float, float)
    device_info_signal = pyqtSignal(str, int)

    def __init__(self, sensitivity=0.005, gain=1.0, freq_low=20, freq_high=20000, max_amplitude=1.0):
        super().__init__()
        self.sensitivity = sensitivity
        self.gain = gain
        self.freq_low = freq_low
        self.freq_high = freq_high
        self.max_amplitude = max_amplitude  # ignore sounds louder than this (1.0 = no limit)
        self.running = True
        self.samplerate = 48000

    def set_sensitivity(self, sensitivity):
        self.sensitivity = sensitivity
    
    def set_gain(self, gain):
        self.gain = gain
    
    def set_freq_range(self, low, high):
        self.freq_low = low
        self.freq_high = high
    
    def set_max_amplitude(self, max_amp):
        self.max_amplitude = max_amp

    def _bandpass(self, data):
        """Apply frequency-domain bandpass filter to audio data."""
        if self.freq_low <= 20 and self.freq_high >= 20000:
            return data  # No filtering needed
        
        filtered = np.copy(data)
        for ch in range(data.shape[1]):
            fft = np.fft.rfft(data[:, ch])
            freqs = np.fft.rfftfreq(len(data[:, ch]), d=1.0/self.samplerate)
            
            # Zero out frequencies outside the band
            mask = (freqs >= self.freq_low) & (freqs <= self.freq_high)
            fft[~mask] = 0
            
            filtered[:, ch] = np.fft.irfft(fft, n=len(data[:, ch]))
        
        return filtered

    def run(self):
        try:
            mics = sc.all_microphones(include_loopback=True)
            loopbacks = [m for m in mics if m.isloopback]
            
            if not loopbacks:
                print("No loopback device found.")
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
                
                self._process_chunk(first_data, use_surround)
                
                while self.running:
                    data = mic.record(numframes=2400)
                    self._process_chunk(data, use_surround)
                    
        except RuntimeError as e:
            print(f"Device '{device.name}' failed: {e}")
            for lb in all_loopbacks:
                if lb.name == device.name or "Microphone" in lb.name:
                    continue
                try:
                    time.sleep(0.5)
                    self._capture_loop(lb, [])
                    return
                except:
                    continue
    
    def _process_chunk(self, data, use_surround):
        # Apply bandpass filter
        data = self._bandpass(data)
        
        gain = self.gain
        intensity = 0.0
        angle_deg = 0.0
        
        if use_surround and data.shape[1] >= 6:
            fl = float(np.sqrt(np.mean(data[:, 0]**2))) * gain
            fr = float(np.sqrt(np.mean(data[:, 1]**2))) * gain
            c  = float(np.sqrt(np.mean(data[:, 2]**2))) * gain
            rl = float(np.sqrt(np.mean(data[:, 4]**2))) * gain
            rr = float(np.sqrt(np.mean(data[:, 5]**2))) * gain
            
            x = (fr + rr) - (fl + rl)
            y = (fl + fr + c) - (rl + rr)
            
            intensity = max(fl, fr, c, rl, rr)
            if intensity > self.sensitivity and intensity < self.max_amplitude:
                angle_deg = math.degrees(math.atan2(x, y))
        
        elif data.shape[1] >= 2:
            left_rms = float(np.sqrt(np.mean(data[:, 0]**2))) * gain
            right_rms = float(np.sqrt(np.mean(data[:, 1]**2))) * gain
            
            intensity = max(left_rms, right_rms)
            
            if intensity > self.sensitivity and intensity < self.max_amplitude:
                balance = (right_rms - left_rms) / (right_rms + left_rms + 1e-6)
                sign = 1.0 if balance >= 0 else -1.0
                amplified = sign * (abs(balance) ** 0.3)
                angle_deg = amplified * 90.0
        else:
            mono_rms = float(np.sqrt(np.mean(data[:, 0]**2))) * gain
            intensity = mono_rms
            angle_deg = 0.0
            
        if intensity > self.sensitivity and intensity < self.max_amplitude:
            self.audio_data_signal.emit(float(angle_deg), float(intensity))

    def stop(self):
        self.running = False
        self.wait()
