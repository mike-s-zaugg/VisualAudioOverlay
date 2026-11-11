import sys
import json
import os

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl, QThread, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon

import hotkeys
import look
import popup_snap
from audio_capture import AudioCaptureThread, list_capture_devices
from capture_state import (capture_devices_payload, coerce_capture_device,
                           inactive_capture_info)
from global_hotkey import create_backend as create_hotkey_backend
from overlay import OverlayRadar
from perf_stats import LOG_NAME as PERF_LOG_NAME, PerfLog, PerfStats, enabled_from_env

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

# Opt-in performance log (set VAO_PERF=1 before launching). Read once here; when
# off, no counters exist and the overlay/capture hot paths pay one None check.
PERF_ENABLED = enabled_from_env()
PERF_LOG_FILE = os.path.join(DATA_DIR, PERF_LOG_NAME)

# Resolved against RESOURCE_DIR so it works both in dev and inside the
# packaged .exe.
DASHBOARD_FILE = os.path.join(RESOURCE_DIR, "dashboard_v2", "index.html")
print(f"Dashboard: {DASHBOARD_FILE}  (exists: {os.path.exists(DASHBOARD_FILE)})")

# App icon (window/taskbar). Use the PNG, not the .ico: QIcon(".ico") needs Qt's
# qico imageformat plugin, which PyInstaller does not reliably bundle - when it is
# missing the .ico loads as an empty icon and the taskbar shows no icon. PNG is
# handled by Qt core (no plugin), so it works in the packaged .exe. The .ico is
# still used for the .exe file icon via the PyInstaller spec (independent of this).
APP_ICON = os.path.join(RESOURCE_DIR, "assets", "icon.png")

# ── Version + project links ─────────────────────────────────────────────────
# APP_VERSION must match the GitHub release tag (without the leading "v") for the
# update check to compare correctly. Bump this for every release you tag.
APP_VERSION = "0.2.2"
REPO_URL = "https://github.com/mike-s-zaugg/VisualAudioOverlay"
# Latest-release JSON (no auth needed; 60 req/hr per IP is plenty for one check
# per launch). Used by the in-app update check to reach users who already have
# the app installed - we have no telemetry/emails, so this is the only channel.
REPO_LATEST_RELEASE_API = (
    "https://api.github.com/repos/mike-s-zaugg/VisualAudioOverlay/releases/latest"
)
# Where the footer "Send feedback" link goes: a prefilled new-issue form.
FEEDBACK_URL = REPO_URL + "/issues/new/choose"

# Footstep bands rev. 2026-07-02, based on spectral analysis of the actual CS2
# footstep assets (extracted from pak01_dir.vpk) plus published EQ guidance for
# the other games. Key findings: footstep energy spans ~150Hz-4kHz (soft/wet
# surfaces and cloth movement sit at 900Hz-8kHz, which the old 800-1000Hz caps
# cut off entirely), while rifle fire concentrates ABOVE 4kHz - so a 4kHz cap
# keeps gunshots out of band and max_amp handles their loudness. The 150Hz low
# cut sheds rumble/explosion low end.
#
# Every preset must list ALL five audio parameters. A preset that sets only some
# of them inherits the rest from whatever was selected before it, which leaks in
# one direction only: saved profiles do set all five, so switching from a profile
# to a preset used to carry the profile's sensitivity along with it.
#
# Sensitivity and gain are the same in every entry on purpose. The spectral work
# above measured frequency bands and the loudness gate; it says nothing about
# level, and how loud you run your system is a property of your headset, not of
# the game. They are spelled out per preset anyway so a future entry can override
# them without reintroducing the leak.
_PRESET_LEVEL_DEFAULTS = {"sensitivity": 0.005, "gain": 1.0}
SOUND_PRESETS = {
    name: {**_PRESET_LEVEL_DEFAULTS, **band} for name, band in {
        "All Sounds":           {"freq_low": 20,  "freq_high": 20000, "max_amp": 1.0},
        "Footsteps - CS2":      {"freq_low": 150, "freq_high": 4000,  "max_amp": 0.15},
        "Footsteps - Valorant": {"freq_low": 150, "freq_high": 4000,  "max_amp": 0.12},
        "Footsteps - Fortnite": {"freq_low": 150, "freq_high": 5000,  "max_amp": 0.18},
        "Footsteps - General":  {"freq_low": 150, "freq_high": 4000,  "max_amp": 0.15},
        "Custom":               {"freq_low": 150, "freq_high": 4000,  "max_amp": 1.0},
    }.items()
}

# Saved profiles store SLIDER POSITIONS (that is what AR.addPreset reads out of
# the DOM), while audio_settings stores the real values the capture thread uses.
# These factors are the inverse of the conversions in dashboard_v2/script.js -
# setSensitivity divides by 10000, setGain by 10, setMaxAmp by 100 - so keep the
# two in step. The frequency sliders are already in real Hz.
PROFILE_SLIDER_SCALE = {
    "sensitivity": 10000,
    "gain": 10,
    "max_amp": 100,
    "freq_low": 1,
    "freq_high": 1,
}


# ── Bridge ─────────────────────────────────────────────────────────────────
# This object is injected into the JS context as `window.bridge`.
# JS calls Python methods via:  bridge.start_radar()
# Python pushes updates to JS via signals, which JS subscribes to:
#   bridge.statusChanged.connect(function(msg, isActive) { ... })

def _parse_version(tag: str):
    """'v0.2.1' or '0.2.1' -> (0, 2, 1). Non-numeric parts become 0 so a weird
    tag never crashes the check. Returns () if nothing parseable."""
    nums = []
    for part in tag.lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if digits == "":
            break
        nums.append(int(digits))
    return tuple(nums)


class UpdateCheckThread(QThread):
    """Fetches the latest GitHub release tag on a background thread and, if it is
    newer than APP_VERSION, emits (version, html_url). Fails silently on any
    error (offline, rate-limited, GitHub down) so it is never intrusive."""

    updateFound = pyqtSignal(str, str)   # (latest_version, release_page_url)

    def run(self):
        try:
            import json as _json
            import urllib.request

            req = urllib.request.Request(
                REPO_LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"VisualAudioOverlay/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = _json.loads(resp.read().decode("utf-8"))

            tag = (data.get("tag_name") or "").strip()
            url = data.get("html_url") or REPO_URL + "/releases/latest"
            if tag and _parse_version(tag) > _parse_version(APP_VERSION):
                self.updateFound.emit(tag.lstrip("vV"), url)
        except Exception as e:
            # Silent by design - a failed update check must never bother the user.
            print(f"Update check skipped: {e}")


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
    updateAvailable = pyqtSignal(str, str)     # (latest_version, release_page_url)
    appearanceChanged = pyqtSignal(str)        # overlay look (colour, thickness, size, opacity, outline) as JSON
    selectedPresetChanged = pyqtSignal(str)    # preset/profile name to restore in the dropdown
    audioSettingsChanged = pyqtSignal(str)     # all five live audio params, so JS moves the sliders
    hotkeyChanged = pyqtSignal(str)            # Start/Stop hotkey binding state as JSON
    captureDevicesChanged = pyqtSignal(str)    # capturable output devices + saved choice as JSON
    captureInfoChanged = pyqtSignal(str)       # what is captured right now (device, layout, mode, note) as JSON
    appDeactivated = pyqtSignal()              # the whole application lost activation (alt-tab away)

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
        self._app.set_audio_param("sensitivity", val)

    @pyqtSlot(float)
    def set_gain(self, val: float):
        self._app.set_audio_param("gain", val)

    @pyqtSlot(int, int)
    def set_freq_range(self, low: int, high: int):
        self._app.set_audio_param("freq_low", low)
        self._app.set_audio_param("freq_high", high)

    @pyqtSlot(float)
    def set_max_amplitude(self, val: float):
        self._app.set_audio_param("max_amp", val)

    @pyqtSlot(str)
    def apply_preset(self, name: str):
        self._app.apply_preset(name)

    @pyqtSlot(str)
    def set_selected_preset(self, name: str):
        self._app.set_selected_preset(name)

    @pyqtSlot(bool)
    def set_invert(self, invert: bool):
        self._app.invert_direction = invert

    @pyqtSlot(int)
    def set_monitor(self, idx: int):
        self._app.selected_monitor = idx
        self._app._queue_settings_save()

    @pyqtSlot(str)
    def set_program(self, value: str):
        """Capture target chosen in the UI. 'all' (or empty) = whole-system audio."""
        self._app.set_program(None if value in ("", "all") else value)

    @pyqtSlot()
    def refresh_programs(self):
        """Re-enumerate running audio programs. JS calls this when the user opens
        the Program dropdown, so the list is live (a program only appears once it
        is actually playing audio)."""
        self._app.emit_programs()

    # ── Capture device (which output is looped back) ──────────────────
    @pyqtSlot(str)
    def set_capture_device(self, name: str):
        """Output device to capture, by name ("" = follow the Windows
        default). Restarts a running system capture when it changed. Always
        answered with captureDevicesChanged."""
        self._app.set_capture_device(name)

    @pyqtSlot()
    def refresh_capture_devices(self):
        """Re-enumerate output devices. JS calls this when the Audio Device
        dropdown is opened, so a headset plugged in later shows up."""
        self._app.emit_capture_devices()

    # ── Mono output (single-sided listeners) ──────────────────────────
    @pyqtSlot(bool)
    def set_mono_enabled(self, enabled: bool):
        """Turn the in-app mono down-mix on/off. Applies live while running."""
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

    # ── Project links / about ─────────────────────────────────────────
    @pyqtSlot(result=str)
    def get_app_version(self) -> str:
        return APP_VERSION

    @pyqtSlot(str)
    def open_url(self, url: str):
        """Open an external link (footer: star/feedback/contribute, or the update
        banner) in the user's real browser - not inside this QWebEngine window.
        Guarded to https:// only so JS can't be coaxed into launching anything."""
        if isinstance(url, str) and url.startswith("https://"):
            import webbrowser
            webbrowser.open(url)

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
        self._app.set_accent_color(hex_color)

    @pyqtSlot(int)
    def set_stroke_width(self, width: int):
        self._app.set_stroke_width(width)

    @pyqtSlot(int)
    def set_overlay_size(self, percent: int):
        """Radar size in percent (50..200, step 10). Clamped and snapped here."""
        self._app.set_overlay_size(percent)

    @pyqtSlot(int)
    def set_alert_opacity(self, percent: int):
        """Maximum arc opacity in percent (20..100)."""
        self._app.set_alert_opacity(percent)

    @pyqtSlot(bool)
    def set_outline_enabled(self, enabled: bool):
        """Show or hide the faint white base ring."""
        self._app.set_outline_enabled(enabled)

    # ── Start/Stop hotkey (global app setting, not part of profiles) ──
    @pyqtSlot(str)
    def set_toggle_hotkey(self, combo: str):
        """Bind the hotkey that starts and stops the radar ("" clears it).
        Python validates and normalises; the answer comes back through
        hotkeyChanged. Also ends a capture started with
        set_hotkey_capture(true)."""
        self._app.set_toggle_hotkey(combo)

    @pyqtSlot(bool)
    def set_hotkey_capture(self, capturing: bool):
        """true: the dashboard waits for the player to press a combination, so
        the current one is released (it would be swallowed otherwise). false:
        restore it. Safe to call repeatedly."""
        self._app.set_hotkey_capture(capturing)

    # ── Profiles ──────────────────────────────────────────────────────
    @pyqtSlot(str)
    def save_profile(self, json_str: str):
        """Expects JSON with at least { name } plus whatever the UI captures:
        sensitivity, gain, preset, freq_low, freq_high, max_amp, invert, and
        (since richer profiles) program, monitor, mono_enabled, mono_device,
        capture_device, accent_color, thickness, overlay_size, alert_opacity,
        outline_enabled. Older profiles missing keys still load."""
        try:
            data = json.loads(json_str)
            name = data.get("name", "").strip()
            if not name:
                return
            # Look values from JS are coerced like the live setters do, so a
            # profile never stores a value the overlay would refuse. Missing ones
            # (an older dashboard) are filled from the live look: the profile is
            # meant to capture the whole setup.
            live_look = self._app._look_values()
            for key, coerce in (("overlay_size", look.coerce_overlay_size),
                                ("alert_opacity", look.coerce_alert_opacity),
                                ("outline_enabled", look.coerce_outline_enabled)):
                data[key] = coerce(data[key]) if key in data else live_look[key]
            data["capture_device"] = (coerce_capture_device(data["capture_device"])
                                      if "capture_device" in data
                                      else self._app.capture_device)
            self._app.profiles[name] = data
            self._app._save_profiles()
            # The profile you just made is the one you are working in - selecting
            # it is what puts later changes on the auto-update path. The dashboard
            # selects it in the dropdown off the same name (see AR.addPreset).
            self._app.set_selected_preset(name)
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
            # Deleting the profile that is currently selected would leave a name
            # in settings.json that no dropdown entry can ever match, so nothing
            # would be restored on the next launch and the label would drift from
            # the running band. Fall back to the neutral preset.
            if self._app.selected_preset == name:
                self._app.set_selected_preset("All Sounds")
                self._app.emit_selected_preset()
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

        # Preset/profile selection saved from the last session. Emitted BEFORE the
        # two lists that build the dropdown, so JS knows what to re-select as soon
        # as the matching option appears (JS also re-checks on every rebuild, so
        # the order here is a convenience, not a requirement).
        self._app.emit_selected_preset()

        # Live audio parameters (sliders). Restored values, not a preset's - the
        # saved name is only re-selected in the dropdown, never re-applied, so a
        # profile cannot overwrite settings changed after it was chosen.
        self._app.emit_audio_settings()

        # Profiles
        self.profilesChanged.emit(json.dumps(self._app.profiles))

        # Presets
        self.presetsChanged.emit(json.dumps(list(SOUND_PRESETS.keys())))

        # Running audio programs (for per-app capture)
        self._app.emit_programs()

        # Overlay appearance (colour, thickness, size, alert opacity, outline),
        # restored from settings
        self._app.emit_appearance()

        # Overlay position
        self._app.emit_overlay_position()

        # Mono-output devices + VB-CABLE detection
        self._app.emit_mono_state()

        # Capture device list + saved choice, and what is captured right now
        # (active false while the radar is stopped).
        self._app.emit_capture_devices()
        self._app.emit_capture_info()

        # Start/Stop hotkey. A freshly loaded page is never waiting for a key
        # press: if the old page went away mid-capture (reload, crash), its
        # "false" never came and the hotkey would stay released, so restore it.
        if self._app.hotkey_status.capturing:
            self._app.set_hotkey_capture(False)
        self._app.emit_hotkey_state()


# ── Main Application ────────────────────────────────────────────────────────

class AudioRadarApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Visual Audio Overlay")
        if os.path.exists(APP_ICON):
            self.setWindowIcon(QIcon(APP_ICON))
        self.resize(1100, 720)

        self.invert_direction = False
        self.selected_monitor = 0
        self.selected_program = None   # None = whole-system audio; else a program name
        self.profiles = self._load_profiles()
        self.settings = self._load_settings()
        self.radar_active = False

        # Start/Stop hotkey preference ("" = unbound). Missing or junk in
        # settings.json means the default; nothing is written back until the
        # player changes it, so an old settings.json stays untouched.
        self.toggle_hotkey = hotkeys.preference_from_setting(
            self.settings.get("toggle_hotkey"))

        # Live audio parameters, held here as the source of truth so they survive
        # thread recreation. The capture thread is rebuilt fresh (with library
        # defaults) on every Stop and on every mid-session restart, so these are
        # re-applied in _start_capture_thread - otherwise a preset would silently
        # revert to "all frequencies" after the first stop/start. Defaults match
        # the dashboard's initial slider positions.
        self.audio_settings = {
            "sensitivity": 0.005,   # sens slider 50 / 10000
            "gain": 1.0,            # gain slider 10 / 10
            "freq_low": 150,        # freq slider default (matches SOUND_PRESETS)
            "freq_high": 4000,
            "max_amp": 1.0,         # max-amp slider 100 / 100
        }
        # ...and restored from settings.json on top of those defaults, so a tuned
        # slider survives a restart. Without this the only thing that came back
        # was the preset NAME, which then had to be re-applied to mean anything -
        # and re-applying a saved profile overwrote the colour and thickness the
        # user had changed since.
        self._load_audio_settings()

        # Slider drags fire one bridge call per pixel of movement, so writing
        # settings.json on every one would hammer the disk the way persisting
        # every drag frame did for the overlay (issue #3). Coalesce into a single
        # write once the user stops moving.
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(600)
        self._settings_save_timer.timeout.connect(self._write_pending_saves)

        # Which entry of the preset/profile dropdown was last selected. Persisted
        # in settings.json and re-selected by the dashboard on load, so the choice
        # survives a restart instead of silently falling back to the first option.
        # It is a LABEL only - the values it stands for are restored separately via
        # audio_settings above, because re-applying the entry would clobber
        # anything the user changed after picking it. Defaults to "All Sounds",
        # which is what an unrestored dropdown already displays.
        self.selected_preset = self.settings.get("selected_preset") or "All Sounds"

        # Mono output (single-sided listeners). Persisted in settings.json so the
        # user's choice survives restarts; applied to the audio thread on Start.
        self.mono_enabled = bool(self.settings.get("mono_enabled", False))
        self.mono_device = self.settings.get("mono_device") or None

        # Which output device is looped back, by name ("" = the Windows
        # default). Missing or junk = "", and nothing is written back at
        # startup. What is really being captured (it can differ: unplugged,
        # per-app, fallback) is tracked in capture_info from the thread.
        self.capture_device = coerce_capture_device(self.settings.get("capture_device"))
        self.capture_info = inactive_capture_info()
        self._capture_devices = None    # last enumerated list (emit_capture_devices)

        # Perf counters (None unless VAO_PERF=1). Created before the overlay and
        # the capture thread because both take it as a constructor argument.
        self.perf = None
        self._perf_log = None
        if PERF_ENABLED:
            self._start_perf_log()

        # Overlay (PyQt6 transparent window - unchanged)
        self.overlay = OverlayRadar(perf=self.perf)

        # Overlay appearance (accent colour, stroke, size, alert opacity, outline).
        # Persisted in settings.json so the look survives restarts; applied to the
        # overlay now and pushed to the dashboard via emit_appearance() on load.
        # Default colour matches the UI swatch. The look.coerce_* helpers turn a
        # missing or hand-edited bad value into the default or the nearest valid
        # one; a bare int() here used to crash the app on start. Nothing is
        # written back, so starting with an old settings.json leaves it as is.
        self.accent_color = self.settings.get("accent_color") or "#9751F2"
        self.stroke_width = look.coerce_stroke_width(self.settings.get("stroke_width"))
        self.overlay_size = look.coerce_overlay_size(self.settings.get("overlay_size"))
        self.alert_opacity = look.coerce_alert_opacity(self.settings.get("alert_opacity"))
        self.outline_enabled = look.coerce_outline_enabled(self.settings.get("outline_enabled"))
        self.overlay.set_accent_color(self.accent_color)
        self.overlay.set_stroke_width(self.stroke_width)
        # keep_center=False: the window has never been placed yet. Start and Move
        # mode position it (saved spot or monitor centre) using its new size.
        self.overlay.set_overlay_size(self.overlay_size, keep_center=False)
        self.overlay.set_alert_opacity(self.alert_opacity)
        self.overlay.set_outline_enabled(self.outline_enabled)

        # Audio thread (starts idle, no capture yet)
        self._new_audio_thread()

        # Bridge object exposed to JS
        self.bridge = Bridge(self)

        # Tell the dashboard when the whole application stops being the active
        # one (alt-tab to the game). It ends a dropdown's quiet period on this:
        # a <select> popup is a window of this same application, so the popup's
        # own focus churn never makes the application inactive.
        qt_app = QApplication.instance()
        if qt_app is not None:
            qt_app.applicationStateChanged.connect(self.on_application_state_changed)

        self.overlay.positionChanged.connect(self.on_overlay_position_changed)
        self.overlay.positionPreview.connect(self.on_overlay_position_preview)

        # Global Start/Stop hotkey. After the bridge: the backend answers
        # through statusChanged, synchronously on Windows, and the handler
        # emits to JS. A conflict at startup is reported, never fatal.
        self.hotkey_status = hotkeys.BindingStatus(state=hotkeys.STATE_PENDING)
        self.hotkey_backend = create_hotkey_backend(parent=self)
        self.hotkey_backend.statusChanged.connect(self.on_hotkey_status)
        self.hotkey_backend.activated.connect(self.on_hotkey_activated)
        self.hotkey_backend.bind(hotkeys.TOGGLE_RADAR, self.toggle_hotkey,
                                 initial=True)

        # WebEngine view
        self.view = QWebEngineView()
        # Dropdown popups grew by a pixel per frame at 125/150/175 % display
        # scaling (a Qt/Chromium rounding loop): keep them on whole pixels.
        # See popup_snap.py.
        self._popup_snap = popup_snap.install(self.view)

        # WebChannel - registers `bridge` as `window.bridge` in JS
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.setCentralWidget(self.view)

        # Load the dashboard HTML
        self.view.setUrl(QUrl.fromLocalFile(DASHBOARD_FILE))

        # Check GitHub for a newer release in the background. If found, the bridge
        # forwards it to JS, which shows a small dismissible "update available"
        # banner. Silent on any failure - never blocks or nags. Kept as an
        # attribute so the QThread isn't garbage-collected mid-run.
        self.update_thread = UpdateCheckThread()
        self.update_thread.updateFound.connect(self.bridge.updateAvailable)
        self.update_thread.start()

    # ── Perf log (VAO_PERF=1 only) ────────────────────────────────────
    def _start_perf_log(self):
        try:
            stats = PerfStats()
            log = PerfLog(PERF_LOG_FILE, stats)
        except OSError as e:
            print(f"VAO_PERF: cannot write {PERF_LOG_FILE} ({e}); perf log off.")
            return
        self.perf = stats
        self._perf_log = log
        # Periodic wake-up, but only when the user asked for the perf log: one
        # summary line per second is what makes before/after runs comparable.
        self._perf_timer = QTimer(self)
        self._perf_timer.setInterval(1000)
        self._perf_timer.timeout.connect(log.flush)
        self._perf_timer.start()
        print(f"VAO_PERF on: writing {PERF_LOG_FILE}")

    def _stop_perf_log(self):
        if self._perf_log is not None:
            self._perf_timer.stop()
            self._perf_log.flush()
            self._perf_log.close()
            self._perf_log = None

    # ── Audio Callbacks ───────────────────────────────────────────────
    def on_audio_data(self, angle: float, intensity: float):
        if self.invert_direction:
            angle = -angle
        # The overlay drops events itself while hidden (cheap isVisible check),
        # so nothing piles up between Stop and the next Start.
        self.overlay.update_audio_data(angle, intensity)

    def on_device_info(self, name: str, channels: int):
        label = f"{name}  ({channels}ch)"
        self.bridge.deviceChanged.emit(label)

    def on_application_state_changed(self, state):
        """Any state other than active (inactive, hidden, suspended) means no
        window of this application has focus, so no dropdown popup is open.

        Never while a Qt popup is up: if a popup ever made the application
        look inactive for a moment, the dashboard would apply its held-back
        updates onto that open popup."""
        if QApplication.activePopupWidget() is not None:
            return
        if state != Qt.ApplicationState.ApplicationActive:
            self.bridge.appDeactivated.emit()

    def on_capture_status(self, message: str):
        """Capture-thread problems (device lost, fallback taken) surfaced on the
        dashboard status line instead of only the console."""
        self.bridge.statusChanged.emit(message, self.radar_active)

    def _new_audio_thread(self):
        """QThreads aren't restartable, so a fresh (idle) thread is created here
        on init, after every Stop, and on every mid-session capture restart."""
        self.audio_thread = AudioCaptureThread(perf=self.perf)
        self.audio_thread.audio_data_signal.connect(self.on_audio_data)
        self.audio_thread.device_info_signal.connect(self.on_device_info)
        self.audio_thread.status_signal.connect(self.on_capture_status)
        self.audio_thread.capture_info_signal.connect(self.on_capture_info)
        # The old thread has ended (every caller stopped it first). Its own
        # "active false" may still be queued, and on_capture_info ignores a
        # replaced thread, so report the end here.
        if self.capture_info.get("active"):
            self.capture_info = inactive_capture_info()
            self.emit_capture_info()

    def on_capture_info(self, thread, info):
        """Live capture facts from the capture thread (queued). Facts from a
        thread that was already replaced are dropped: after a quick restart
        its last messages arrive late and would overwrite the new session's
        device, mode and note."""
        if thread is not self.audio_thread or not isinstance(info, dict):
            return
        self.capture_info = info
        self.emit_capture_info()

    def emit_capture_info(self):
        self.bridge.captureInfoChanged.emit(json.dumps(self.capture_info))

    # ── Capture device ────────────────────────────────────────────────
    def set_capture_device(self, value):
        name = coerce_capture_device(value)
        if name != self.capture_device:
            self.capture_device = name
            self.settings["capture_device"] = name
            self._queue_settings_save()
            # Only a system capture uses the device. Per-app capture (also
            # one that fell back to system audio) restarts too when the
            # thread reports system, so the choice applies right away.
            if self.audio_thread.target_pid is None \
                    or self.capture_info.get("source") == "system":
                self._restart_capture_if_active()
        # Answer with the last enumerated list: applying a profile calls this
        # setter every time, and re-enumerating on the GUI thread for each
        # call would buy nothing (the dropdown refreshes when opened).
        self.emit_capture_devices(refresh=False)

    def emit_capture_devices(self, refresh=True):
        """Push the capturable output devices and the saved choice.

        refresh=True enumerates (GUI thread, about 5 to 9 ms here); only
        initial data and refresh_capture_devices do that, never anything
        periodic. refresh=False reuses the last list, enumerating only if
        there is none yet."""
        if refresh or self._capture_devices is None:
            self._capture_devices = list_capture_devices()
        self.bridge.captureDevicesChanged.emit(json.dumps(
            capture_devices_payload(self._capture_devices, self.capture_device)))

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
    def set_program(self, program):
        """Change the capture target. Applies live when the radar is running."""
        if program == self.selected_program:
            return
        self.selected_program = program
        self._queue_settings_save()
        self._restart_capture_if_active()

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

    # ── Overlay appearance (colour, stroke, size, opacity, outline) ───
    def set_accent_color(self, hex_color: str):
        self.accent_color = hex_color or "#9751F2"
        self.overlay.set_accent_color(self.accent_color)
        self.settings["accent_color"] = self.accent_color
        self._queue_settings_save()

    def set_stroke_width(self, width: int):
        self.stroke_width = look.coerce_stroke_width(width)
        self.overlay.set_stroke_width(self.stroke_width)
        self.settings["stroke_width"] = self.stroke_width
        self._queue_settings_save()
        if width != self.stroke_width:
            self.emit_appearance()      # clamped: show the real value

    # The three look setters below share one pattern. Python owns the ranges, so
    # each value from JS is coerced first. An unchanged value does nothing (no
    # repaint, no save), because slider drags repeat the same value a lot. If
    # coercion changed what JS sent, appearanceChanged echoes the real value so
    # the slider does not show a number the overlay is not using.

    def set_overlay_size(self, value):
        size = look.coerce_overlay_size(value)
        if size != self.overlay_size:
            old_side = look.overlay_side_px(self.overlay_size)
            new_side = look.overlay_side_px(size)
            self.overlay_size = size
            visible = self.overlay.isVisible()
            # Only a visible radar keeps its centre on the same screen pixel. A
            # hidden window's position can be meaningless: before the first Start
            # it sits unplaced at (0, 0), and shifting that sent it to negative
            # coordinates, so the position readout showed -150, -150 and a D-pad
            # nudge put the radar partly off screen.
            # Geometry only: the native window is not re-created.
            self.overlay.set_overlay_size(size, keep_center=visible)

            # The saved top-left moves by the same amount, so the next Start puts
            # the centre in the same place. With nothing saved the overlay is
            # centred on the monitor at Start, which already uses the new size.
            saved = self._saved_overlay_xy()
            if saved is not None:
                saved = look.centred_top_left(saved[0], saved[1], old_side, new_side)
                if not visible:
                    # Park the hidden window on the saved spot, so the readout and
                    # a D-pad nudge before the next Start begin from where the
                    # radar will really appear. move() does not show a widget.
                    self.overlay.move(*saved)

            # Shrinking around a centre that sits off screen can leave no pixel of
            # the radar on any monitor. Same rule as at Start: if nothing is
            # reachable, centre it on the selected monitor. (Growing never
            # triggers this: the bigger window still contains the old one.)
            if visible and not self._overlay_rect_is_visible(
                    self.overlay.x(), self.overlay.y()):
                x, y = self._selected_monitor_center_position()
                self.overlay.move(x, y)
                if saved is not None:
                    saved = (x, y)

            if saved is not None:
                self.settings["overlay_position"] = {"x": saved[0], "y": saved[1]}
            self.settings["overlay_size"] = size
            self._queue_settings_save()
            self.emit_overlay_position()
        if value != size:
            self.emit_appearance()

    def set_alert_opacity(self, value):
        opacity = look.coerce_alert_opacity(value)
        if opacity != self.alert_opacity:
            self.alert_opacity = opacity
            self.overlay.set_alert_opacity(opacity)
            self.settings["alert_opacity"] = opacity
            self._queue_settings_save()
        if value != opacity:
            self.emit_appearance()

    def set_outline_enabled(self, value):
        enabled = look.coerce_outline_enabled(value)
        if enabled != self.outline_enabled:
            self.outline_enabled = enabled
            self.overlay.set_outline_enabled(enabled)
            self.settings["outline_enabled"] = enabled
            self._queue_settings_save()
        if value is not enabled:
            self.emit_appearance()

    def _look_values(self):
        """The three newer look settings under their settings/profile/payload
        keys. One list so emit_appearance, save_profile and the profile
        auto-update cannot drift apart."""
        return {
            "overlay_size": self.overlay_size,
            "alert_opacity": self.alert_opacity,
            "outline_enabled": self.outline_enabled,
        }

    def emit_appearance(self):
        """Push the overlay look to the dashboard on load (and after Python had to
        correct a value) so the pickers, sliders and preview match the overlay."""
        self.bridge.appearanceChanged.emit(json.dumps({
            "color": self.accent_color,
            "thickness": self.stroke_width,
            **self._look_values(),
        }))

    # ── Start/Stop hotkey ─────────────────────────────────────────────
    # A global app setting (settings.json "toggle_hotkey"), deliberately not in
    # profiles: switching profiles must never move the key a player relies on
    # mid-match. global_hotkey.py does the binding; this only routes requests
    # and turns its answers into the hotkeyChanged payload.

    def set_toggle_hotkey(self, value):
        # Validated, normalised and length-capped in hotkeys.HotkeyBinder; a
        # failed change keeps the old binding and reports why.
        self.hotkey_backend.bind(hotkeys.TOGGLE_RADAR, value)

    def set_hotkey_capture(self, capturing):
        if capturing:
            self.hotkey_backend.suspend()
        else:
            self.hotkey_backend.resume()

    def on_hotkey_status(self, action_id, status):
        if action_id != hotkeys.TOGGLE_RADAR:
            return
        self.hotkey_status = status
        if status.preferred != self.toggle_hotkey:
            # The backend accepted a new preference (a failed change never
            # moves it). Debounced like every other setting.
            self.toggle_hotkey = status.preferred
            self.settings["toggle_hotkey"] = status.preferred
            self._queue_settings_save()
        self.emit_hotkey_state()

    def emit_hotkey_state(self):
        s = self.hotkey_status
        caps = self.hotkey_backend.capabilities()
        self.bridge.hotkeyChanged.emit(json.dumps({
            "combo": self.toggle_hotkey,
            "state": s.state,
            "effective": s.effective,
            "requested": s.requested,
            "error": s.error,
            "capturing": bool(s.capturing),
            "can_choose": bool(caps.get("can_choose_combo")),
        }))

    def on_hotkey_activated(self, action_id):
        # Delivered through a queued signal, so a press can arrive just after
        # the binding was released (capture start). Ignore it then: the player
        # is picking a new key in the dashboard, not asking to start or stop.
        if action_id == hotkeys.TOGGLE_RADAR and self.hotkey_status.effective:
            self.toggle_radar()

    def toggle_radar(self):
        """The hotkey: the dashboard's Start/End button, from inside a game.
        Runs the very same start_radar/stop_radar the button does, so the
        dashboard follows through statusChanged like after a click.

        Presses are handled one at a time on the GUI thread. stop_radar waits
        for the capture thread to finish, and a press made meanwhile stays
        queued until it has, so a quick stop-start never runs two capture
        threads at once and no press is lost."""
        if self.overlay.drag_enabled:
            # Leave Move mode exactly like the dashboard's Move button: saves
            # the position and tells the dashboard (overlayPositionChanged).
            # Start then shows the radar click-through, not draggable.
            self.set_overlay_drag_enabled(False)
        if self.radar_active:
            self.stop_radar()
        else:
            self.start_radar()

    # ── Mono output (single-sided listeners) ──────────────────────────
    def set_mono_enabled(self, enabled: bool):
        enabled = bool(enabled)
        changed = enabled != self.mono_enabled
        self.mono_enabled = enabled
        self.settings["mono_enabled"] = self.mono_enabled
        self._queue_settings_save()
        self.emit_mono_state()
        if changed:
            self._restart_capture_if_active()

    def set_mono_output(self, device: str):
        device = device or None
        changed = device != self.mono_device
        self.mono_device = device
        self.settings["mono_device"] = self.mono_device
        self._queue_settings_save()
        self.emit_mono_state()
        if changed:
            self._restart_capture_if_active()

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

    # ── Audio parameters (source of truth, survive thread recreation) ──
    def _apply_audio_settings_to_thread(self):
        """Push the current audio parameters onto the (possibly freshly created)
        capture thread. Called on every start so a recreated thread doesn't come
        up with library defaults instead of the user's preset/sliders."""
        s = self.audio_settings
        self.audio_thread.set_sensitivity(s["sensitivity"])
        self.audio_thread.set_gain(s["gain"])
        self.audio_thread.set_freq_range(s["freq_low"], s["freq_high"])
        self.audio_thread.set_max_amplitude(s["max_amp"])

    def _load_audio_settings(self):
        """Overlay the saved audio parameters onto the defaults. settings.json is
        hand-editable, so every value is coerced individually and skipped when it
        is not a usable number - a corrupt file costs you your tuning, never a
        crash on launch."""
        saved = self.settings.get("audio_settings")
        if not isinstance(saved, dict):
            return
        for key, cast in (("sensitivity", float), ("gain", float),
                          ("freq_low", int), ("freq_high", int), ("max_amp", float)):
            if key in saved:
                try:
                    self.audio_settings[key] = cast(saved[key])
                except (TypeError, ValueError):
                    pass

    def _queue_settings_save(self):
        """Stage the live audio parameters and debounce the write (see the timer).
        Also the entry point for the profile auto-update, so every setter that
        changes something a profile stores should call this."""
        self.settings["audio_settings"] = dict(self.audio_settings)
        self._settings_save_timer.start()

    def _write_pending_saves(self):
        """The debounced write itself: settings.json, then the selected profile."""
        self._save_settings()
        self._sync_selected_profile()

    def _flush_pending_saves(self):
        """Write a pending change now instead of waiting the debounce out. Used on
        close and before switching profiles - a slider moved in the last 600ms
        belongs to the profile it was moved in, not the one being switched to."""
        if self._settings_save_timer.isActive():
            self._settings_save_timer.stop()
            self._write_pending_saves()

    def _sync_selected_profile(self):
        """Auto-update: while one of the user's own profiles is selected, live
        changes are written back into it, so switching away and back returns the
        values you last had rather than the snapshot taken when it was created.

        Built-in presets are read-only and fall straight through - `profiles` only
        ever holds user-made entries, so the lookup is the whole check."""
        prof = self.profiles.get(self.selected_preset)
        if prof is None:
            return
        updated = {k: round(self.audio_settings[k] * scale)
                   for k, scale in PROFILE_SLIDER_SCALE.items()}
        updated.update({
            "program": self.selected_program or "all",
            "monitor": self.selected_monitor,
            "mono_enabled": self.mono_enabled,
            "mono_device": self.mono_device or "",
            "capture_device": self.capture_device,
            "accent_color": self.accent_color,
            "thickness": self.stroke_width,
            **self._look_values(),
        })
        if all(prof.get(k) == v for k, v in updated.items()):
            return                      # nothing moved: no disk write, no rebuild
        prof.update(updated)
        self._save_profiles()
        # The dashboard caches profiles to feed applyProfileValues, so it has to
        # see the new values or switching back would replay the stale ones.
        self.bridge.profilesChanged.emit(json.dumps(self.profiles))

    def emit_audio_settings(self):
        """Push the live audio parameters to the dashboard so the sliders show what
        the capture thread is actually using."""
        self.bridge.audioSettingsChanged.emit(json.dumps(self.audio_settings))

    def set_audio_param(self, key: str, value):
        """Update one live audio parameter. Stored on the app (so it survives a
        thread restart), applied to the running thread immediately, and queued for
        persistence so it also survives a restart."""
        self.audio_settings[key] = value
        self._apply_audio_settings_to_thread()
        self._queue_settings_save()

    def set_selected_preset(self, name: str):
        """Remember which dropdown entry (built-in preset or saved profile) is
        selected. Written straight to settings.json: this only fires when the
        user picks an entry, not on slider drags, so it is not a hot path. The
        no-op guard matters anyway - the dashboard replays the restored name
        through the same path a click takes, which would otherwise rewrite the
        file with identical contents on every launch."""
        name = name or ""
        if name == self.selected_preset:
            return
        self._flush_pending_saves()
        self.selected_preset = name
        self.settings["selected_preset"] = name
        self._save_settings()

    def emit_selected_preset(self):
        self.bridge.selectedPresetChanged.emit(self.selected_preset)

    def apply_preset(self, name: str):
        """Apply a built-in preset and echo the values back to the dashboard so the
        sliders and readouts actually move (otherwise switching presets looks like
        it does nothing).

        Every parameter is overwritten, never just the ones the preset cares about
        - see the note on SOUND_PRESETS for why a partial apply leaks."""
        p = SOUND_PRESETS.get(name, SOUND_PRESETS["All Sounds"])
        for key in self.audio_settings:
            self.audio_settings[key] = p[key]
        self._apply_audio_settings_to_thread()
        self._queue_settings_save()
        self.emit_audio_settings()

    # ── Radar Control ─────────────────────────────────────────────────
    def _start_capture_thread(self):
        """Configure the idle thread (target/mono/params are read at thread start)
        and start it. Shared by Start and by mid-session capture restarts."""
        # Resolve the capture target fresh (PIDs change between launches).
        pid, name = self._resolve_target()
        self.audio_thread.set_target(pid, name)
        self.audio_thread.set_mono(self.mono_enabled, self.mono_device)
        self.audio_thread.set_capture_device(self.capture_device)
        self._apply_audio_settings_to_thread()

        if not self.audio_thread.isRunning():
            self.audio_thread.start()

        if self.selected_program and pid is None:
            self.bridge.statusChanged.emit(
                f"'{self.selected_program}' has no audio - using system audio", True)
        elif pid is not None:
            self.bridge.statusChanged.emit(f"Radar active - capturing {name}", True)
        else:
            self.bridge.statusChanged.emit("Radar is active", True)

    # ── Overlay visibility ────────────────────────────────────────────
    # One rule decides whether the overlay is on screen:
    #     visible = radar_active or Move mode
    # Start, Stop, Move mode and the D-pad change the inputs, then call
    # _sync_overlay_visibility() instead of showing or hiding it themselves.

    def _sync_overlay_visibility(self, move_mode=None):
        """Show or hide the overlay to match the rule above. Returns True if it
        was just shown (callers place it then).

        move_mode: the Move mode state about to be applied, for the callers
        that show the window first and switch Move mode on afterwards (the
        order they always had). Defaults to the overlay's current state."""
        if move_mode is None:
            move_mode = self.overlay.drag_enabled
        want = self.radar_active or move_mode
        if want == self.overlay.isVisible():
            return False
        if want:
            # Tool window with WA_ShowWithoutActivating and
            # WindowDoesNotAcceptFocus (overlay.py): a Start from the hotkey
            # never takes focus from the game.
            self.overlay.show()
            return True
        self.overlay.hide()     # hideEvent stops the timer and drops the arcs
        return False

    # start_radar and stop_radar are the only start and stop routines: the
    # dashboard button (Bridge) and the hotkey (toggle_radar) both call them.
    def start_radar(self):
        self.radar_active = True
        self._sync_overlay_visibility()
        self._place_overlay_for_start()
        self._start_capture_thread()
        # Refresh the program list so newly launched apps show up next time.
        self.emit_programs()

    def _restart_capture_if_active(self):
        """Program/mono choices only take effect when the capture thread starts,
        so apply a mid-session change by restarting the thread in place. The
        overlay stays up; only the capture source blips out for a moment."""
        if not self.radar_active:
            return
        self.audio_thread.stop()
        self._new_audio_thread()
        self._start_capture_thread()

    def stop_radar(self):
        self.radar_active = False
        # Move mode off first: hide() alone keeps drag_enabled, so the next
        # show would come back draggable instead of click-through.
        self.overlay.set_drag_enabled(False)
        self._sync_overlay_visibility()
        self.audio_thread.stop()

        # Create a fresh thread ready for next start
        self._new_audio_thread()

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

    def _saved_overlay_xy(self):
        """The saved overlay top-left as (x, y), or None if there is none or it
        is not usable. settings.json is hand-editable; a bad value here used to
        raise from int() on Start."""
        pos = self.settings.get("overlay_position")
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            return None
        try:
            return int(pos["x"]), int(pos["y"])
        except (TypeError, ValueError, OverflowError):
            return None

    def _overlay_rect_is_visible(self, x, y):
        """True if the overlay, at top-left (x, y) and its current size, overlaps
        any screen by at least one pixel."""
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
        saved = self._saved_overlay_xy()
        if saved is not None and self._overlay_rect_is_visible(*saved):
            self.overlay.move(*saved)
        else:
            x, y = self._selected_monitor_center_position()
            self.overlay.move(x, y)
        self.emit_overlay_position()

    def set_overlay_drag_enabled(self, enabled: bool):
        enabled = bool(enabled)
        # Radar stopped: first show, place it like Start would. A running
        # radar is already on screen and stays where it is.
        if enabled and self._sync_overlay_visibility(move_mode=True):
            self._place_overlay_for_start()

        self.overlay.set_drag_enabled(enabled)

        if enabled:
            self.emit_overlay_position()
            return

        pos = self.overlay.pos()
        self.on_overlay_position_changed(pos.x(), pos.y())
        self._sync_overlay_visibility()     # hides it again if the radar is stopped

    def move_overlay(self, x: int, y: int):
        # Only a stopped radar can be off screen here. The D-pad then works in
        # Move mode, switched on right after the first show (the order it
        # always had).
        if self._sync_overlay_visibility(move_mode=True):
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
        self._flush_pending_saves()
        # Give the combination back to the system, also mid-capture (the
        # backend tracks that). Windows would drop it when the thread ends too,
        # but not if something keeps the process alive a moment longer.
        try:
            self.hotkey_backend.shutdown()
        except Exception as e:
            print(f"Hotkey shutdown failed: {e}")
        try:
            self.stop_radar()
        except Exception:
            pass
        self.overlay.close()
        self._stop_perf_log()
        event.accept()


if __name__ == "__main__":
    # Windows groups taskbar buttons (and picks their icon) by AppUserModelID.
    # Without an explicit ID, a `python main.py` launch shows the generic Python
    # icon in the taskbar even though setWindowIcon is set. Declaring our own ID
    # makes Windows treat this as a standalone app and use our icon there too.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "VisualAudioOverlay.App"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON):
        app.setWindowIcon(QIcon(APP_ICON))
    window = AudioRadarApp()
    window.show()
    sys.exit(app.exec())
