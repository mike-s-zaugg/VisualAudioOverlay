import sys
import json
import os

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl
from PyQt6.QtGui import QColor

from audio_capture import AudioCaptureThread
from overlay import OverlayRadar

# RESOURCE_DIR = where bundled, read-only assets live. When packaged by
# PyInstaller (onefile), datas are extracted to sys._MEIPASS; in dev it's the
# script folder. DATA_DIR = a writable location for user data (profiles): next
# to the .exe when frozen, else the script folder.
if getattr(sys, "frozen", False):
    RESOURCE_DIR = sys._MEIPASS
    DATA_DIR = os.path.dirname(sys.executable)
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = RESOURCE_DIR

PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

# Prefer the new dashboard_v2 UI, then index.html next to main.py, then the
# original dashboard/ (kept as a fallback). Resolved against RESOURCE_DIR so it
# works both in dev and inside the packaged .exe.
_candidates = [
    os.path.join(RESOURCE_DIR, "dashboard_v2", "index.html"),
    os.path.join(RESOURCE_DIR, "index.html"),
    os.path.join(RESOURCE_DIR, "dashboard", "index.html"),
]
DASHBOARD_FILE = next((p for p in _candidates if os.path.exists(p)), _candidates[0])
print(f"Dashboard: {DASHBOARD_FILE}  (exists: {os.path.exists(DASHBOARD_FILE)})")

SOUND_PRESETS = {
    "All Sounds":           {"freq_low": 20,  "freq_high": 20000, "max_amp": 1.0},
    "Footsteps — CS2":      {"freq_low": 100, "freq_high": 900,   "max_amp": 0.15},
    "Footsteps — Valorant": {"freq_low": 80,  "freq_high": 1000,  "max_amp": 0.12},
    "Footsteps — Fortnite": {"freq_low": 120, "freq_high": 800,   "max_amp": 0.18},
    "Footsteps — General":  {"freq_low": 100, "freq_high": 800,   "max_amp": 0.15},
    "Custom":               {"freq_low": 100, "freq_high": 800,   "max_amp": 1.0},
}


# ── Bridge ─────────────────────────────────────────────────────────────────
# This object is injected into the JS context as `window.bridge`.
# JS calls Python methods via:  bridge.start_radar()
# Python pushes updates to JS via signals, which JS subscribes to:
#   bridge.statusChanged.connect(function(msg, isActive) { ... })

class Bridge(QObject):
    # Signals → pushed to JS
    statusChanged   = pyqtSignal(str, bool)   # (message, isActive)
    deviceChanged   = pyqtSignal(str)          # detected device name
    profilesChanged = pyqtSignal(str)          # full profiles dict as JSON
    monitorsChanged = pyqtSignal(str)          # list of monitors as JSON
    presetsChanged  = pyqtSignal(str)          # list of preset names as JSON

    def __init__(self, app: "AudioRadarApp"):
        super().__init__()
        self._app = app

    # ── Lifecycle ─────────────────────────────────────────────────────
    @pyqtSlot()
    def start_radar(self):
        self._app.start_radar()

    @pyqtSlot()
    def stop_radar(self):
        self._app.stop_radar()

    # ── Audio Settings ────────────────────────────────────────────────
    @pyqtSlot(float)
    def set_sensitivity(self, val: float):
        self._app.audio_thread.set_sensitivity(val)

    @pyqtSlot(float)
    def set_gain(self, val: float):
        self._app.audio_thread.set_gain(val)

    @pyqtSlot(int, int)
    def set_freq_range(self, low: int, high: int):
        self._app.audio_thread.set_freq_range(low, high)

    @pyqtSlot(float)
    def set_max_amplitude(self, val: float):
        self._app.audio_thread.set_max_amplitude(val)

    @pyqtSlot(str)
    def apply_preset(self, name: str):
        p = SOUND_PRESETS.get(name, SOUND_PRESETS["All Sounds"])
        self._app.audio_thread.set_freq_range(p["freq_low"], p["freq_high"])
        self._app.audio_thread.set_max_amplitude(p["max_amp"])

    @pyqtSlot(bool)
    def set_invert(self, invert: bool):
        self._app.invert_direction = invert

    @pyqtSlot(int)
    def set_monitor(self, idx: int):
        self._app.selected_monitor = idx

    # ── Overlay Appearance ────────────────────────────────────────────
    @pyqtSlot(str)
    def set_accent_color(self, hex_color: str):
        self._app.overlay.set_accent_color(hex_color)

    @pyqtSlot(int)
    def set_stroke_width(self, width: int):
        self._app.overlay.set_stroke_width(width)

    # ── Profiles ──────────────────────────────────────────────────────
    @pyqtSlot(str)
    def save_profile(self, json_str: str):
        """Expects JSON: { name, sensitivity, gain, preset, freq_low, freq_high, max_amp, invert }"""
        try:
            data = json.loads(json_str)
            name = data.get("name", "").strip()
            if not name:
                return
            self._app.profiles[name] = data
            self._app._save_profiles()
            self.profilesChanged.emit(json.dumps(self._app.profiles))
        except Exception as e:
            print(f"save_profile error: {e}")

    @pyqtSlot(str, result=str)
    def get_profile(self, name: str) -> str:
        """Returns a single profile as JSON string (for loading into UI)."""
        p = self._app.profiles.get(name, {})
        return json.dumps(p)

    @pyqtSlot(str)
    def delete_profile(self, name: str):
        if name in self._app.profiles:
            del self._app.profiles[name]
            self._app._save_profiles()
            self.profilesChanged.emit(json.dumps(self._app.profiles))

    # ── Init Data Request ─────────────────────────────────────────────
    @pyqtSlot()
    def request_initial_data(self):
        """
        JS calls this once on page load.
        Python responds by emitting all initial state signals.
        """
        # Monitors
        screens = QApplication.screens()
        monitors = [{"idx": i, "name": s.name(), "resolution": f"{s.geometry().width()}×{s.geometry().height()}"}
                    for i, s in enumerate(screens)]
        self.monitorsChanged.emit(json.dumps(monitors))

        # Profiles
        self.profilesChanged.emit(json.dumps(self._app.profiles))

        # Presets
        self.presetsChanged.emit(json.dumps(list(SOUND_PRESETS.keys())))


# ── Main Application ────────────────────────────────────────────────────────

class AudioRadarApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Radar")
        self.resize(1100, 720)

        self.invert_direction = False
        self.selected_monitor = 0
        self.profiles = self._load_profiles()

        # Overlay (PyQt6 transparent window — unchanged)
        self.overlay = OverlayRadar()

        # Audio thread (starts idle, no capture yet)
        self.audio_thread = AudioCaptureThread()
        self.audio_thread.audio_data_signal.connect(self.on_audio_data)
        self.audio_thread.device_info_signal.connect(self.on_device_info)

        # Bridge object exposed to JS
        self.bridge = Bridge(self)

        # WebEngine view
        self.view = QWebEngineView()

        # WebChannel — registers `bridge` as `window.bridge` in JS
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.setCentralWidget(self.view)

        # Load the dashboard HTML
        self.view.setUrl(QUrl.fromLocalFile(DASHBOARD_FILE))

    # ── Audio Callbacks ───────────────────────────────────────────────
    def on_audio_data(self, angle: float, intensity: float):
        if self.invert_direction:
            angle = -angle
        self.overlay.update_audio_data(angle, intensity)

    def on_device_info(self, name: str, channels: int):
        label = f"{name}  ({channels}ch)"
        self.bridge.deviceChanged.emit(label)

    # ── Radar Control ─────────────────────────────────────────────────
    def start_radar(self):
        self.overlay.show()

        # Position overlay on selected monitor
        screens = QApplication.screens()
        idx = self.selected_monitor if self.selected_monitor < len(screens) else 0
        geo = screens[idx].geometry()
        self.overlay.move(
            geo.x() + (geo.width()  - self.overlay.width())  // 2,
            geo.y() + (geo.height() - self.overlay.height()) // 2,
        )

        self.audio_thread.start()
        self.bridge.statusChanged.emit("Radar is active", True)

    def stop_radar(self):
        self.overlay.hide()
        self.audio_thread.stop()

        # Create a fresh thread ready for next start
        self.audio_thread = AudioCaptureThread()
        self.audio_thread.audio_data_signal.connect(self.on_audio_data)
        self.audio_thread.device_info_signal.connect(self.on_device_info)

        self.bridge.statusChanged.emit("Radar stopped", False)

    # ── Profiles ──────────────────────────────────────────────────────
    def _load_profiles(self) -> dict:
        if os.path.exists(PROFILES_FILE):
            try:
                with open(PROFILES_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_profiles(self):
        with open(PROFILES_FILE, "w") as f:
            json.dump(self.profiles, f, indent=2)

    # ── Lifecycle ─────────────────────────────────────────────────────
    def closeEvent(self, event):
        try:
            self.stop_radar()
        except Exception:
            pass
        self.overlay.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioRadarApp()
    window.show()
    sys.exit(app.exec())