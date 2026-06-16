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
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

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
    "Footsteps - CS2":      {"freq_low": 100, "freq_high": 900,   "max_amp": 0.15},
    "Footsteps - Valorant": {"freq_low": 80,  "freq_high": 1000,  "max_amp": 0.12},
    "Footsteps - Fortnite": {"freq_low": 120, "freq_high": 800,   "max_amp": 0.18},
    "Footsteps - General":  {"freq_low": 100, "freq_high": 800,   "max_amp": 0.15},
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
    programsChanged = pyqtSignal(str)          # running audio programs as JSON
    overlayPositionChanged = pyqtSignal(str)   # overlay position/state as JSON
    monoStateChanged = pyqtSignal(str)         # mono-output devices + cable state as JSON

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

    @pyqtSlot(str)
    def set_program(self, value: str):
        """Capture target chosen in the UI. 'all' (or empty) = whole-system audio."""
        self._app.selected_program = None if value in ("", "all") else value

    # ── Mono output (single-sided listeners) ──────────────────────────
    @pyqtSlot(bool)
    def set_mono_enabled(self, enabled: bool):
        """Turn the in-app mono down-mix on/off. Applied on the next Start."""
        self._app.set_mono_enabled(enabled)

    @pyqtSlot(str)
    def set_mono_output(self, device: str):
        """Choose which real device the mono mix plays to. '' = system default."""
        self._app.set_mono_output(device)

    @pyqtSlot()
    def refresh_mono_devices(self):
        self._app.emit_mono_state()

    @pyqtSlot()
    def install_vbcable(self):
        """Launch the bundled VB-CABLE installer (UAC-elevated) so mono output can
        route the game away from the headphones. Falls back to the download page
        if the installer isn't bundled in this build."""
        self._app.install_vbcable()

    @pyqtSlot(bool)
    def set_overlay_drag_enabled(self, enabled: bool):
        self._app.set_overlay_drag_enabled(enabled)

    @pyqtSlot(int, int)
    def set_overlay_position(self, x: int, y: int):
        self._app.move_overlay(x, y)

    @pyqtSlot(int, int)
    def nudge_overlay(self, dx: int, dy: int):
        self._app.nudge_overlay(dx, dy)

    @pyqtSlot()
    def reset_overlay_position(self):
        self._app.reset_overlay_position()

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

        # Running audio programs (for per-app capture)
        self._app.emit_programs()

        # Overlay position
        self._app.emit_overlay_position()

        # Mono-output devices + VB-CABLE detection
        self._app.emit_mono_state()


# ── Main Application ────────────────────────────────────────────────────────

class AudioRadarApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Visual Audio Overlay")
        self.resize(1100, 720)

        self.invert_direction = False
        self.selected_monitor = 0
        self.selected_program = None   # None = whole-system audio; else a program name
        self.profiles = self._load_profiles()
        self.settings = self._load_settings()
        self.radar_active = False

        # Mono output (single-sided listeners). Persisted in settings.json so the
        # user's choice survives restarts; applied to the audio thread on Start.
        self.mono_enabled = bool(self.settings.get("mono_enabled", False))
        self.mono_device = self.settings.get("mono_device") or None

        # Overlay (PyQt6 transparent window - unchanged)
        self.overlay = OverlayRadar()

        # Audio thread (starts idle, no capture yet)
        self.audio_thread = AudioCaptureThread()
        self.audio_thread.audio_data_signal.connect(self.on_audio_data)
        self.audio_thread.device_info_signal.connect(self.on_device_info)

        # Bridge object exposed to JS
        self.bridge = Bridge(self)
        self.overlay.positionChanged.connect(self.on_overlay_position_changed)
        self.overlay.positionPreview.connect(self.on_overlay_position_preview)

        # WebEngine view
        self.view = QWebEngineView()

        # WebChannel - registers `bridge` as `window.bridge` in JS
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

    def on_overlay_position_changed(self, x: int, y: int):
        self.settings["overlay_position"] = {"x": int(x), "y": int(y)}
        self._save_settings()
        self.emit_overlay_position()

    def on_overlay_position_preview(self, x: int, y: int):
        """Live drag frames: refresh the UI readout only, no disk write.
        The final position is persisted once on mouse release via
        on_overlay_position_changed (issue #3)."""
        state = {
            "x": int(x),
            "y": int(y),
            "drag_enabled": bool(self.overlay.drag_enabled),
        }
        self.bridge.overlayPositionChanged.emit(json.dumps(state))

    def emit_overlay_position(self):
        pos = self.overlay.pos()
        state = {
            "x": int(pos.x()),
            "y": int(pos.y()),
            "drag_enabled": bool(self.overlay.drag_enabled),
        }
        self.bridge.overlayPositionChanged.emit(json.dumps(state))

    # ── Programs (per-app capture) ────────────────────────────────────
    def list_programs(self):
        """Running programs with an audio session. Safe to call on the GUI thread."""
        try:
            from process_loopback import list_audio_programs
            return list_audio_programs()
        except Exception as e:
            print(f"Program enumeration failed: {e}")
            return []

    def emit_programs(self):
        names = [p["name"] for p in self.list_programs()]
        self.bridge.programsChanged.emit(json.dumps(names))

    # ── Mono output (single-sided listeners) ──────────────────────────
    def set_mono_enabled(self, enabled: bool):
        self.mono_enabled = bool(enabled)
        self.settings["mono_enabled"] = self.mono_enabled
        self._save_settings()
        self.emit_mono_state()

    def set_mono_output(self, device: str):
        self.mono_device = device or None
        self.settings["mono_device"] = self.mono_device
        self._save_settings()
        self.emit_mono_state()

    def emit_mono_state(self):
        """Push the playback-device list + VB-CABLE detection + current selection
        to the UI so it can render the mono setup card."""
        try:
            from mono_output import (list_output_devices, default_output_name,
                                     detect_virtual_cable)
            devices = list_output_devices()
            default = default_output_name()
            cable = detect_virtual_cable()
        except Exception as e:
            print(f"Mono device enumeration failed: {e}")
            devices, default, cable = [], None, None

        state = {
            "devices": devices,
            "default": default,
            "cable": cable,            # None until VB-CABLE is installed
            "enabled": self.mono_enabled,
            "selected": self.mono_device,
        }
        self.bridge.monoStateChanged.emit(json.dumps(state))

    def install_vbcable(self):
        """Launch the bundled VB-CABLE installer with a UAC prompt. If the build
        doesn't bundle it, open the official download page instead. The installer
        shows its own UI on purpose (donationware terms + trust for the
        anti-cheat-wary audience)."""
        installer = os.path.join(RESOURCE_DIR, "vendor", "VBCABLE",
                                 "VBCABLE_Setup_x64.exe")
        if os.path.exists(installer):
            try:
                import shutil
                import tempfile
                import ctypes
                # Copy out of the (onefile) bundle first: _MEIPASS is wiped when
                # this app exits, which could break the installer mid-run.
                tmp = os.path.join(tempfile.gettempdir(), "VBCABLE_Setup_x64.exe")
                shutil.copyfile(installer, tmp)
                ctypes.windll.shell32.ShellExecuteW(None, "runas", tmp, None, None, 1)
                return
            except Exception as e:
                print(f"VB-CABLE launch failed: {e}")
        import webbrowser
        webbrowser.open("https://vb-audio.com/Cable/")

    def _resolve_target(self):
        """Map the selected program name to a live PID. Returns (pid, name) or
        (None, None) for whole-system capture / if the program is gone."""
        if not self.selected_program:
            return None, None
        try:
            from process_loopback import resolve_pid
            pid = resolve_pid(self.selected_program)
        except Exception:
            pid = None
        if pid is None:
            return None, None
        return pid, self.selected_program

    # ── Radar Control ─────────────────────────────────────────────────
    def start_radar(self):
        self.radar_active = True
        self.overlay.show()
        self._place_overlay_for_start()

        # Resolve the capture target fresh (PIDs change between launches), then
        # configure the idle thread before starting it.
        pid, name = self._resolve_target()
        self.audio_thread.set_target(pid, name)
        # Re-apply mono config (the thread is recreated fresh on each Start).
        self.audio_thread.set_mono(self.mono_enabled, self.mono_device)

        if not self.audio_thread.isRunning():
            self.audio_thread.start()

        if self.selected_program and pid is None:
            self.bridge.statusChanged.emit(
                f"'{self.selected_program}' has no audio - using system audio", True)
        elif pid is not None:
            self.bridge.statusChanged.emit(f"Radar active - capturing {name}", True)
        else:
            self.bridge.statusChanged.emit("Radar is active", True)

        # Refresh the program list so newly launched apps show up next time.
        self.emit_programs()

    def stop_radar(self):
        self.radar_active = False
        self.overlay.set_drag_enabled(False)
        self.overlay.hide()
        self.audio_thread.stop()

        # Create a fresh thread ready for next start
        self.audio_thread = AudioCaptureThread()
        self.audio_thread.audio_data_signal.connect(self.on_audio_data)
        self.audio_thread.device_info_signal.connect(self.on_device_info)

        self.bridge.statusChanged.emit("Radar stopped", False)
        self.emit_overlay_position()

    def _selected_monitor_center_position(self):
        screens = QApplication.screens()
        idx = self.selected_monitor if self.selected_monitor < len(screens) else 0
        geo = screens[idx].geometry()
        return (
            geo.x() + (geo.width()  - self.overlay.width())  // 2,
            geo.y() + (geo.height() - self.overlay.height()) // 2,
        )

    def _saved_overlay_position_is_visible(self, pos):
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            return False

        x = int(pos["x"])
        y = int(pos["y"])
        width = self.overlay.width()
        height = self.overlay.height()

        for screen in QApplication.screens():
            geo = screen.geometry()
            visible_x = x + width > geo.x() and x < geo.x() + geo.width()
            visible_y = y + height > geo.y() and y < geo.y() + geo.height()
            if visible_x and visible_y:
                return True
        return False

    def _place_overlay_for_start(self):
        pos = self.settings.get("overlay_position")
        if self._saved_overlay_position_is_visible(pos):
            self.overlay.move(int(pos["x"]), int(pos["y"]))
        else:
            x, y = self._selected_monitor_center_position()
            self.overlay.move(x, y)
        self.emit_overlay_position()

    def set_overlay_drag_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled and not self.overlay.isVisible():
            self.overlay.show()
            self._place_overlay_for_start()

        self.overlay.set_drag_enabled(enabled)

        if enabled:
            self.emit_overlay_position()
            return

        pos = self.overlay.pos()
        self.on_overlay_position_changed(pos.x(), pos.y())
        if not self.radar_active:
            self.overlay.hide()

    def move_overlay(self, x: int, y: int):
        if not self.overlay.isVisible():
            self.overlay.show()
            if not self.radar_active:
                self.overlay.set_drag_enabled(True)

        self.overlay.move(int(x), int(y))
        self.on_overlay_position_changed(int(x), int(y))

    def nudge_overlay(self, dx: int, dy: int):
        pos = self.overlay.pos()
        self.move_overlay(pos.x() + int(dx), pos.y() + int(dy))

    def reset_overlay_position(self):
        x, y = self._selected_monitor_center_position()
        self.move_overlay(x, y)

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

    def _load_settings(self) -> dict:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.settings, f, indent=2)

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
