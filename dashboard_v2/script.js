/**
 * Visual Audio Overlay - dashboard_v2 frontend
 * ═══════════════════════════════════════════════════════════════════════
 * Talks to the Python `Bridge` (main.py) over QWebChannel.
 *   JS → Python : window.bridge.method(arg)
 *   Python → JS : window.bridge.signal.connect(cb)
 *
 * Bridge methods used (already in main.py):
 *   start_radar, stop_radar, set_sensitivity(float), set_gain(float),
 *   set_freq_range(int low, int high), set_max_amplitude(float),
 *   apply_preset(str), set_monitor(int), set_accent_color(hex),
 *   set_stroke_width(int), save_profile(jsonStr), delete_profile(str),
 *   request_initial_data()
 *
 *   set_program(str)            - per-app capture target
 *   programsChanged(jsonStr)    - list of running audio programs
 *
 *   Overlay look (guarded, see callBridge):
 *   set_overlay_size(int)       - radar size in percent, 50-200 (100 = 300 px)
 *   set_alert_opacity(int)      - max arc opacity in percent, 20-100
 *   set_outline_enabled(bool)   - show the faint base ring
 *   appearanceChanged(jsonStr)  - {color, thickness, overlay_size, alert_opacity,
 *                                  outline_enabled}, every key optional
 *
 *   set_selected_preset(str)      - persist the dropdown choice to settings.json
 *   selectedPresetChanged(str)    - that choice, restored on load (name only)
 *   audioSettingsChanged(jsonStr) - the live sensitivity/gain/freq/max-amp, so the
 *                                   sliders follow a preset and come back on load
 *
 * Mono output (single-sided listeners) - guarded so the UI works without them:
 *   set_mono_enabled(bool)      - turn the in-app mono down-mix on/off
 *   set_mono_output(str)        - which real device the mono mix plays to
 *   install_vbcable()           - launch the bundled VB-CABLE installer
 *   monoStateChanged(jsonStr)   - {devices, default, cable, enabled, selected}
 * ═══════════════════════════════════════════════════════════════════════
 */

let radarActive = false;
let moveModeActive = false;

// Project links (opened in the real browser via bridge.open_url). The update
// banner overrides updateUrl when a specific release page is known.
const REPO_URL = "https://github.com/mike-s-zaugg/VisualAudioOverlay";
const FEEDBACK_URL = REPO_URL + "/issues/new/choose";
const CONTRIBUTE_URL = REPO_URL + "/blob/main/CONTRIBUTING.md";
const COFFEE_URL = "https://buymeacoffee.com/mikezaugg";
let updateUrl = REPO_URL + "/releases/latest";

function openExternal(url) {
    if (bridge && bridge.open_url) bridge.open_url(url);
}

// ── Bridge init ────────────────────────────────────────────────────────
function initBridge() {
    return new Promise((resolve) => {
        new QWebChannel(qt.webChannelTransport, function (channel) {
            window.bridge = channel.objects.bridge;

            bridge.statusChanged.connect(onStatusChanged);
            bridge.deviceChanged.connect(onDeviceChanged);
            bridge.profilesChanged.connect(onProfilesChanged);
            bridge.monitorsChanged.connect(onMonitorsChanged);
            bridge.presetsChanged.connect(onPresetsChanged);
            bridge.overlayPositionChanged.connect(onOverlayPositionChanged);
            // Optional new signals - only connect if the backend provides them.
            if (bridge.programsChanged) bridge.programsChanged.connect(onProgramsChanged);
            if (bridge.monoStateChanged) bridge.monoStateChanged.connect(onMonoStateChanged);
            if (bridge.updateAvailable) bridge.updateAvailable.connect(onUpdateAvailable);
            if (bridge.appearanceChanged) bridge.appearanceChanged.connect(onAppearanceChanged);
            if (bridge.audioSettingsChanged) bridge.audioSettingsChanged.connect(onAudioSettingsChanged);
            if (bridge.selectedPresetChanged) bridge.selectedPresetChanged.connect(onSelectedPresetChanged);

            // Show the current version in the footer.
            if (bridge.get_app_version) {
                bridge.get_app_version(function (v) { setText("footer-version", "v" + v); });
            }

            bridge.request_initial_data();

            // The Program list only contains apps that are currently playing audio.
            // Re-enumerate whenever the user returns to this window (e.g. alt-tabs
            // back from the game), so a game launched after startup shows up without
            // having to Start the radar first.
            //
            // Skipped while any <select> popup is open - it is the popup itself
            // stealing and returning focus, and answering that churn with a
            // blocking COM enumeration is what grew the list. See isSelectOpen.
            window.addEventListener("focus", () => {
                if (isSelectOpen()) return;
                AR.refreshPrograms();
            });

            resolve();
        });
    });
}

// Backend found a newer GitHub release. Show the dismissible banner.
function onUpdateAvailable(version, url) {
    if (url) updateUrl = url;
    setText("update-banner-text", "Version " + version + " is available.");
    toggleClass("update-banner", "is-hidden", false);
}

// ── Signal handlers (Python → JS) ──────────────────────────────────────
function onStatusChanged(message, isActive) {
    radarActive = isActive;
    setText("status-text", message);
    syncToggleUI();
}

function onDeviceChanged(label) {
    // label is "Name  (Nch)" - split the channel suffix onto its own line
    const m = label.match(/^(.*?)\s*\((.*)\)\s*$/);
    if (m) { setText("device-name", m[1].trim()); setText("device-channels", m[2].trim()); }
    else { setText("device-name", label); setText("device-channels", ""); }
}

function onMonitorsChanged(jsonStr) {
    fillSelect("monitor-select", JSON.parse(jsonStr).map(m => ({ value: m.idx, label: m.name })));
}

function onPresetsChanged(jsonStr) {
    window._presets = JSON.parse(jsonStr);   // array of built-in preset names
    rebuildPresetSelects();
}

function onProfilesChanged(jsonStr) {
    window._profiles = JSON.parse(jsonStr);  // { name: {settings...} }
    rebuildPresetSelects();
}

// The preset/profile selected in the last session, restored from settings.json.
// Only records the name here - rebuildPresetSelects does the re-selecting, since
// the matching option may not exist in the dropdown yet.
function onSelectedPresetChanged(name) {
    window._savedPreset = name || "";
    rebuildPresetSelects();
}

function onProgramsChanged(jsonStr) {
    const progs = JSON.parse(jsonStr);
    // Diff-guard: refresh fires on dropdown-open and on every window focus, so
    // identical lists arrive repeatedly. Rebuilding a native <select> while its
    // popup is open makes QtWebEngine grow the rendered list (the runaway
    // dropdown bug). Skip the DOM rebuild entirely when nothing changed.
    const sig = progs.join("\u0000");
    if (sig === window._programsSig) return;
    window._programsSig = sig;

    // No preferred value: fillSelect keeps whatever the user has selected, and
    // falls back to the first option ("All (system audio)") if that program has
    // stopped playing and dropped off the list.
    fillSelect("program-select", [{ value: "all", label: "All (system audio)" },
        ...progs.map(p => ({ value: p, label: p }))]);
}

// Saved overlay appearance (accent colour, thickness, size, alert opacity, outline)
// restored from settings.json, or corrected by Python after it clamped a value.
// Setting an input's value programmatically does NOT fire its oninput/onchange, so
// this never loops back into a re-save. Every key is optional: a missing key (older
// backend, partial payload) leaves that control as it is.
function onAppearanceChanged(jsonStr) {
    const a = JSON.parse(jsonStr);
    if (a.color) {
        const ac = document.getElementById("accent-color");
        if (ac) ac.value = a.color;
        updateColorReadout(a.color);
    }
    if (a.thickness != null) {
        const th = document.getElementById("thickness");
        if (th) th.value = a.thickness;
        setText("thickness-val", a.thickness + " px");
        setFill("thickness", a.thickness, 1, 20);
    }
    if (a.overlay_size != null) {
        setSliderValue("overlay-size", a.overlay_size);
        setText("overlay-size-val", overlaySizeText(a.overlay_size));
        setFill("overlay-size", a.overlay_size, 50, 200);
    }
    if (a.alert_opacity != null) {
        setSliderValue("alert-opacity", a.alert_opacity);
        setText("alert-opacity-val", a.alert_opacity + " %");
        setFill("alert-opacity", a.alert_opacity, 20, 100);
    }
    if (a.outline_enabled != null) {
        const cb = document.getElementById("outline-enabled");
        if (cb) cb.checked = !!a.outline_enabled;
    }
    drawPreview();
}

// The overlay window is 300 px square at 100 % (overlay.py); Size scales it.
const OVERLAY_BASE_PX = 300;

// Size readout, e.g. "100 % · 300 px".
function overlaySizeText(pct) {
    const p = parseInt(pct);
    return `${p} % · ${Math.round(OVERLAY_BASE_PX * p / 100)} px`;
}

// Call a bridge slot only when the backend is connected and has it, so the look
// controls still update their readout and preview in local preview mode (no Qt)
// and against an older backend that lacks the slot.
function callBridge(slot, ...args) {
    const b = window.bridge;
    if (b && typeof b[slot] === "function") b[slot](...args);
}

// Move the audio sliders and their readouts to match the backend. Sent on load
// (the values restored from settings.json) and after a built-in preset is applied
// - the backend is already authoritative in both cases, and setting an input's
// value in JS does not fire its oninput, so this never loops back into the bridge.
// Keys are optional, so a caller can push a subset.
function onAudioSettingsChanged(jsonStr) {
    const p = JSON.parse(jsonStr);
    if (p.sensitivity != null) {
        const slider = Math.round(p.sensitivity * 10000);   // slider units = f * 10000
        setSliderValue("sensitivity", slider);
        setText("sensitivity-val", p.sensitivity.toFixed(4));
        setFill("sensitivity", slider);
    }
    if (p.gain != null) {
        const slider = Math.round(p.gain * 10);             // slider units = f * 10
        setSliderValue("gain", slider);
        setText("gain-val", p.gain.toFixed(1) + "x");
        setFill("gain", slider);
    }
    if (p.freq_low != null && p.freq_high != null) {
        document.getElementById("freq-low").value = p.freq_low;
        document.getElementById("freq-high").value = p.freq_high;
        setText("freq-val", `${p.freq_low}-${p.freq_high} Hz`);
        updateDualFill();
    }
    if (p.max_amp != null) {
        const slider = Math.round(p.max_amp * 100);   // slider units = max_amp * 100
        setSliderValue("max-amp", slider);
        setText("max-amp-val", p.max_amp.toFixed(2));
        setFill("max-amp", slider);
    }
}

// Mono-output state: device list + VB-CABLE detection + current selection.
function onMonoStateChanged(jsonStr) {
    const s = JSON.parse(jsonStr);

    const opts = [
        { value: "", label: "System default" + (s.default ? ` (${s.default})` : "") },
        ...(s.devices || []).map(d => ({ value: d, label: d })),
    ];
    // "" is a real option value here (System default), so pass it through rather
    // than letting it fall back to the current pick.
    fillSelect("mono-output-select", opts, s.selected || "");

    const cb = document.getElementById("mono-enabled");
    if (cb) cb.checked = !!s.enabled;

    // Compact hint shown in the HARDWARE card (the full setup lives in the modal).
    const hint = document.getElementById("mono-hint");
    if (hint) {
        const where = s.selected || (s.default ? "default device" : "default");
        const state = s.enabled ? `On - ${where}` : "Off";
        hint.innerHTML =
            `${state} <a href="#" class="mono-setup-link" ` +
            `onclick="AR.openMonoSetup(); return false;">Setup</a>`;
    }

    // Cable status + install button live in the modal (which scrolls, so no clip).
    const status = document.getElementById("mono-status");
    const installBtn = document.getElementById("mono-install-btn");
    if (s.cable) {
        if (status) status.innerHTML =
            `<span class="ok">Virtual cable detected:</span> ${s.cable}`;
        if (installBtn) installBtn.classList.add("is-hidden");
    } else {
        if (status) status.innerHTML =
            `<span class="warn">No virtual cable found.</span> Install VB-CABLE to ` +
            `route your game's audio without hearing it twice.`;
        if (installBtn) installBtn.classList.remove("is-hidden");
    }
}

function onOverlayPositionChanged(jsonStr) {
    const state = JSON.parse(jsonStr);
    moveModeActive = !!state.drag_enabled;
    setText("position-val", `${state.x}, ${state.y}`);
    syncMoveUI();
}

// ── Preset / profile dropdowns ─────────────────────────────────────────
// Built-in presets (read-only) + user profiles (addable/deletable) share
// the dropdown. See handoff.md: merging presets+profiles is the intended
// data model; true editable presets need a backend change.
function rebuildPresetSelects() {
    const presets = window._presets || [];
    const profiles = Object.keys(window._profiles || {});
    const opts = [
        ...presets.map(n => ({ value: n, label: n })),
        ...profiles.map(n => ({ value: n, label: n + "  ★" })),  // ★ = saved profile
    ];
    // Until the selection saved in settings.json has been restored, it outranks
    // whatever the dropdown currently shows: presets and profiles arrive as two
    // separate signals, so the first rebuild can land on an arbitrary entry
    // simply because the list holding the saved one has not arrived yet. A saved
    // name that no longer exists (a profile deleted since) falls back to the
    // current pick rather than dropping the dropdown to its first entry.
    const saved = window._presetRestored ? null : window._savedPreset;
    const keep = document.getElementById("preset-select")?.value;
    const want = (saved && opts.some(o => o.value === saved)) ? saved : keep;
    // The restore replay is registered as this select's settled hook, so it also
    // runs when the rebuild had to be deferred past an open popup.
    fillSelect("preset-select", opts, want);
}

// Latch the restore once the saved name is actually showing in the dropdown,
// which hands control of the selection back to the user (see rebuildPresetSelects).
//
// It deliberately does NOT re-apply the entry. A saved profile is a snapshot of
// the colour, thickness, program and monitor as they were when it was saved, so
// replaying it on every launch overwrote anything changed since. Those settings
// are already restored from settings.json, one value at a time. The label can
// therefore disagree with the sliders once you tune them by hand - that is
// intended: the sliders are the truth, the name is a bookmark.
function maybeRestorePreset() {
    if (window._presetRestored) return;
    const name = window._savedPreset;
    if (!name) return;
    const sel = document.getElementById("preset-select");
    if (!sel || sel.value !== name) return;   // not in the dropdown (yet)
    window._presetRestored = true;
}

// ── AR namespace (JS → Python) ─────────────────────────────────────────
window.AR = {
    toggleRadar() {
        if (radarActive) bridge.stop_radar();
        else bridge.start_radar();
        // UI syncs authoritatively via statusChanged
    },

    setSensitivity(val) {
        const f = val / 10000;
        setText("sensitivity-val", f.toFixed(4));
        setFill("sensitivity", val);
        bridge.set_sensitivity(f);
    },

    setGain(val) {
        const f = val / 10;
        setText("gain-val", f.toFixed(1) + "x");
        setFill("gain", val);
        bridge.set_gain(f);
    },

    setMaxAmp(val) {
        const f = val / 100;
        setText("max-amp-val", f.toFixed(2));
        setFill("max-amp", val);
        bridge.set_max_amplitude(f);
    },

    // Frequency is in real Hz (locked decision). Dual handles.
    setFreqRange() {
        const lowEl = document.getElementById("freq-low");
        const highEl = document.getElementById("freq-high");
        let low = parseInt(lowEl.value);
        let high = parseInt(highEl.value);
        if (low > high) { [low, high] = [high, low]; }   // keep ordered
        setText("freq-val", `${low}-${high} Hz`);
        updateDualFill();
        bridge.set_freq_range(low, high);
    },

    applyPreset(name) {
        if (!name) return;
        // Remember the choice so it survives a restart. Also stops a stale saved
        // name (e.g. a profile deleted since) from overriding this pick on the
        // next dropdown rebuild.
        window._presetRestored = true;
        if (bridge.set_selected_preset) bridge.set_selected_preset(name);
        const profiles = window._profiles || {};
        if (profiles[name]) { applyProfileValues(profiles[name]); return; }   // saved profile
        bridge.apply_preset(name);                                            // built-in preset
    },

    addPreset() {
        const name = (window.prompt("Name this preset:") || "").trim();
        if (!name) return;
        const data = {
            name,
            sensitivity: intVal("sensitivity", 50),
            gain: intVal("gain", 10),
            freq_low: intVal("freq-low", 150),
            freq_high: intVal("freq-high", 4000),
            max_amp: intVal("max-amp", 100),
            preset: "Custom",
            // Richer profiles: also capture the target program, monitor, mono
            // state, and overlay appearance, so "CS2" restores everything.
            program: strVal("program-select", "all"),
            monitor: intVal("monitor-select", 0),
            mono_enabled: !!document.getElementById("mono-enabled")?.checked,
            mono_device: strVal("mono-output-select", ""),
            accent_color: strVal("accent-color", "#9751F2"),
            thickness: intVal("thickness", 6),
            overlay_size: intVal("overlay-size", 100),
            alert_opacity: intVal("alert-opacity", 100),
            outline_enabled: document.getElementById("outline-enabled")?.checked ?? true,
        };
        // Select the new profile as soon as it comes back in profilesChanged, by
        // re-arming the restore latch: it selects a name the moment its option
        // exists, which is exactly the problem here (the option does not exist
        // yet). Python selects it on its side in save_profile, so both agree.
        window._savedPreset = name;
        window._presetRestored = false;
        bridge.save_profile(JSON.stringify(data));   // emits profilesChanged
    },

    deletePreset() {
        const sel = document.getElementById("preset-select");
        const name = sel?.value;
        if (!name) return;
        if (!(window._profiles || {})[name]) {
            window.alert("Built-in presets can't be deleted - only saved presets (★).");
            return;
        }
        bridge.delete_profile(name);
    },

    setMonitor(idx) { bridge.set_monitor(parseInt(idx)); },

    setProgram(value) {
        // Backend method may not exist yet - guard it.
        if (bridge.set_program) bridge.set_program(value);
        else console.log("set_program not wired yet; selected:", value);
    },

    refreshPrograms() {
        // Re-enumerate live audio programs when the dropdown is opened, so the
        // game shows up even if it started playing after the app launched.
        if (bridge.refresh_programs) bridge.refresh_programs();
    },

    // ── Mono output ────────────────────────────────────────────────
    setMonoEnabled(on) {
        if (bridge.set_mono_enabled) bridge.set_mono_enabled(!!on);
        if (on) AR.openMonoSetup();      // first enable: walk them through setup
    },

    setMonoOutput(value) {
        if (bridge.set_mono_output) bridge.set_mono_output(value);
    },

    openMonoSetup() { toggleClass("mono-modal", "is-hidden", false); },
    closeMonoSetup() { toggleClass("mono-modal", "is-hidden", true); },

    installCable() {
        if (bridge.install_vbcable) bridge.install_vbcable();
    },

    toggleMoveMode() {
        bridge.set_overlay_drag_enabled(!moveModeActive);
    },

    nudgeOverlay(dx, dy) {
        bridge.nudge_overlay(parseInt(dx), parseInt(dy));
    },

    resetOverlay() {
        bridge.reset_overlay_position();
    },

    setAccentColor(hex) {
        bridge.set_accent_color(hex);
        updateColorReadout(hex);
        drawPreview();
    },

    setThickness(val) {
        setText("thickness-val", val + " px");
        setFill("thickness", val, 1, 20);
        bridge.set_stroke_width(parseInt(val));
        drawPreview();
    },

    // Whole-radar size in percent. The preview deliberately keeps its size:
    // ring and arcs scale together, so it already shows the look, and the
    // readout carries the size.
    setOverlaySize(val) {
        const pct = parseInt(val);
        setText("overlay-size-val", overlaySizeText(pct));
        setFill("overlay-size", pct, 50, 200);
        callBridge("set_overlay_size", pct);
        drawPreview();
    },

    // Maximum arc opacity in percent (a full-strength sound). The ring is unaffected.
    setAlertOpacity(val) {
        const pct = parseInt(val);
        setText("alert-opacity-val", pct + " %");
        setFill("alert-opacity", pct, 20, 100);
        callBridge("set_alert_opacity", pct);
        drawPreview();
    },

    // Faint base ring on/off. Arcs are unaffected.
    setOutlineEnabled(on) {
        callBridge("set_outline_enabled", !!on);
        drawPreview();
    },

    // ── Footer support links + update banner ──────────────────────────
    openRepo() { openExternal(REPO_URL); },
    openFeedback() { openExternal(FEEDBACK_URL); },
    openContribute() { openExternal(CONTRIBUTE_URL); },
    openCoffee() { openExternal(COFFEE_URL); },
    openUpdate() { openExternal(updateUrl); },
    dismissUpdate() { toggleClass("update-banner", "is-hidden", true); },
};

// Apply a saved profile's values to the controls and push to Python. Note that
// each AR.* call below goes through the bridge, so the values land back in the
// profile via the auto-update path - harmless, since they are the profile's own
// values and the sync skips a write when nothing changed.
// Richer fields (program/monitor/mono/appearance) are applied only when the
// profile has them, so profiles saved by older versions still load fine and
// simply leave those settings as they are.
function applyProfileValues(p) {
    setSliderValue("sensitivity", p.sensitivity ?? 50);
    setSliderValue("gain", p.gain ?? 10);
    setSliderValue("max-amp", p.max_amp ?? 100);
    document.getElementById("freq-low").value = p.freq_low ?? 150;
    document.getElementById("freq-high").value = p.freq_high ?? 4000;
    AR.setSensitivity(intVal("sensitivity", 50));
    AR.setGain(intVal("gain", 10));
    AR.setMaxAmp(intVal("max-amp", 100));
    AR.setFreqRange();

    if (p.program != null) {
        const sel = document.getElementById("program-select");
        if (sel) {
            // The saved game may not be running (yet) - inject its option so
            // the dropdown shows the choice; the backend falls back to system
            // audio with a status message until the program plays audio.
            if (!Array.from(sel.options).some(o => o.value === p.program)) {
                const opt = document.createElement("option");
                opt.value = p.program;
                opt.textContent = p.program;
                sel.appendChild(opt);
            }
            sel.value = p.program;
        }
        AR.setProgram(p.program);
    }

    if (p.monitor != null) {
        const sel = document.getElementById("monitor-select");
        if (sel) sel.value = p.monitor;
        AR.setMonitor(p.monitor);
    }

    // Device before enable, so turning mono on starts on the right output.
    // monoStateChanged echoes back and re-syncs the checkbox/select/hint.
    if (p.mono_device != null && bridge.set_mono_output) {
        bridge.set_mono_output(p.mono_device);
    }
    if (p.mono_enabled != null && bridge.set_mono_enabled) {
        bridge.set_mono_enabled(!!p.mono_enabled);
    }

    if (p.accent_color) {
        const ac = document.getElementById("accent-color");
        if (ac) ac.value = p.accent_color;
        AR.setAccentColor(p.accent_color);
    }
    if (p.thickness != null) {
        setSliderValue("thickness", p.thickness);
        AR.setThickness(p.thickness);
    }
    // Size and opacity are applied only when they are numbers (a hand-edited
    // profile can hold "abc" or true, which a range input would turn into its
    // midpoint). They are read back from the slider, which snaps an out-of-range
    // or off-step number to what the control can show.
    const size = profileNumber(p.overlay_size);
    if (size !== null) {
        setSliderValue("overlay-size", size);
        AR.setOverlaySize(intVal("overlay-size", 100));
    }
    const opacity = profileNumber(p.alert_opacity);
    if (opacity !== null) {
        setSliderValue("alert-opacity", opacity);
        AR.setAlertOpacity(intVal("alert-opacity", 100));
    }
    // Only a real boolean: anything else leaves the outline as it is.
    if (typeof p.outline_enabled === "boolean") {
        const cb = document.getElementById("outline-enabled");
        if (cb) cb.checked = p.outline_enabled;
        AR.setOutlineEnabled(p.outline_enabled);
    }
}

// A profile value as a finite number, or null to skip it. Accepts real numbers
// and numeric strings ("120"); booleans, NaN, empty strings and other junk are null.
function profileNumber(v) {
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    if (typeof v === "string" && v.trim() !== "") {
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    }
    return null;
}

// ── Preview canvas - mirrors overlay.py rendering ──────────────────────
// overlay.py: faint white base circle + accent-coloured arc "blips",
// 35° span, round cap, stroke width = thickness. Here we draw one static
// sample blip so the user sees the chosen colour + thickness style.
// The blip is a full-strength sound, so it is drawn at the alert opacity
// (overlay: alpha = strength * alert opacity). The base circle is only drawn
// while the outline is on. Overlay size is NOT drawn: ring and arcs scale
// together, so the preview stays a normalised picture at its designed size.
function drawPreview() {
    const canvas = document.getElementById("preview-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const radius = Math.min(W, H) / 2 * 0.8;

    ctx.clearRect(0, 0, W, H);

    // Base circle (matches overlay: white @ ~12% alpha, 2px)
    const outline = document.getElementById("outline-enabled")?.checked ?? true;
    if (outline) {
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(255,255,255,0.18)";
        ctx.lineWidth = 2;
        ctx.stroke();
    }

    // Sample blip arc
    const accent = document.getElementById("accent-color")?.value || "#9751F2";
    const thickness = parseInt(document.getElementById("thickness")?.value || 6);
    const opacity = (parseInt(document.getElementById("alert-opacity")?.value) || 100) / 100;
    const sampleAngleDeg = -35;             // up-and-to-the-right, like the mockup
    const spanDeg = 35;
    // canvas 0° = +x axis, clockwise; overlay angle 0 = up. Convert:
    const centerDeg = -90 + sampleAngleDeg;
    const start = (centerDeg - spanDeg / 2) * Math.PI / 180;
    const end = (centerDeg + spanDeg / 2) * Math.PI / 180;

    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, end);
    ctx.strokeStyle = accent;
    ctx.lineWidth = thickness;
    ctx.lineCap = "round";
    ctx.globalAlpha = opacity;
    ctx.stroke();
    ctx.globalAlpha = 1;
}

// ── Dual-range fill geometry ───────────────────────────────────────────
function updateDualFill() {
    const wrap = document.getElementById("freq-range");
    const fill = wrap.querySelector(".dualrange-fill");
    const min = +wrap.dataset.min, max = +wrap.dataset.max;
    let lo = +document.getElementById("freq-low").value;
    let hi = +document.getElementById("freq-high").value;
    if (lo > hi) [lo, hi] = [hi, lo];
    const pct = v => ((v - min) / (max - min)) * 100;
    fill.style.left = pct(lo) + "%";
    fill.style.right = (100 - pct(hi)) + "%";
}

// ── Small helpers ──────────────────────────────────────────────────────
function setText(id, txt) { const el = document.getElementById(id); if (el) el.textContent = txt; }
function toggleClass(id, cls, on) { const el = document.getElementById(id); if (el) el.classList.toggle(cls, on); }
function intVal(id, def) { const el = document.getElementById(id); return el ? parseInt(el.value) : def; }
function strVal(id, def) { const el = document.getElementById(id); return el ? el.value : def; }

// Rebuilds queued because their <select> was open at the time:
// { id: {opts, preferred} }.
const _pendingFills = {};

// Follow-up work to run once a select's options have actually landed in the DOM.
// Registered per id so a *deferred* fill still triggers what its caller expected
// to happen right after the rebuild.
const _fillSettled = { "preset-select": maybeRestorePreset };

// QtWebEngine draws <select> popups natively (that is why the open list is white
// instead of following our dark CSS - the popup is a Qt widget, not DOM). Two
// consequences, and both feed the runaway-dropdown bug:
//
//   1. Swapping the options while the popup is open makes Qt GROW the rendered
//      list instead of redrawing it, leaving a blank strip under the real
//      entries that gets taller with every rebuild.
//   2. The popup is a separate window, so opening it takes focus off the web
//      view and hands it straight back, repeatedly, for as long as it stays
//      open - and the focus handler answered every round with a blocking COM
//      enumeration whose reply rebuilt the very list being displayed.
//
// `document.activeElement` stays on the <select> for as long as its popup is up,
// so it answers both questions. It also stays there after the popup closes until
// focus moves on, which only makes this over-cautious, never wrong.
//
// 49b828f fixed one instance by diff-guarding the program list, but that only
// helps while the list is unchanged - any real change (a game starting or
// stopping audio) still rebuilt an open popup. This is the general fix and
// covers every dropdown, so it protects the preset list too.
function isSelectOpen(sel) {
    const active = document.activeElement;
    if (!active || active.tagName !== "SELECT") return false;
    return sel ? active === sel : true;
}

// Replace a <select>'s options. `preferred` is the value to end up selected;
// omit it to keep the current pick. See applySelectOptions for the fallbacks.
function fillSelect(id, opts, preferred) {
    const sel = document.getElementById(id);
    if (!sel) return;
    if (isSelectOpen(sel)) {
        // Flush on change AND blur: picking an entry closes the popup without
        // blurring, and clicking away blurs without a change. `once` is safe
        // because we only re-arm when another fill is queued.
        if (!(id in _pendingFills)) {
            sel.addEventListener("change", () => flushSelectFill(id, true), { once: true });
            sel.addEventListener("blur", () => flushSelectFill(id), { once: true });
        }
        _pendingFills[id] = { opts, preferred };
        return;
    }
    delete _pendingFills[id];
    applySelectOptions(sel, opts, preferred);
    _fillSettled[id]?.();
}

// Apply a rebuild that was deferred while the popup was open. `userPicked` marks
// the flush as triggered by the user choosing an entry, so their choice outranks
// whatever value the queued rebuild wanted.
function flushSelectFill(id, userPicked) {
    if (!(id in _pendingFills)) return;
    const { opts, preferred } = _pendingFills[id];
    delete _pendingFills[id];
    const sel = document.getElementById(id);
    if (!sel) return;
    applySelectOptions(sel, opts, userPicked ? sel.value : preferred);
    _fillSettled[id]?.();
}

// Which entry ends up selected: `preferred` when the caller named one, else the
// value already showing. Either way it is only assigned if the new list actually
// contains it - assigning a value with no matching <option> leaves selectedIndex
// at -1, which renders as an empty dropdown. Falling through to the first option
// is what a rebuilt select does on its own; the explicit line just makes that
// contract visible rather than inherited.
function applySelectOptions(sel, opts, preferred) {
    const want = preferred == null ? sel.value : String(preferred);
    sel.innerHTML = "";
    opts.forEach(o => {
        const opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.label;
        sel.appendChild(opt);
    });
    if (opts.some(o => String(o.value) === want)) sel.value = want;
    else if (opts.length) sel.selectedIndex = 0;
}

// Paint the filled portion of a single slider via the --fill CSS var.
function setFill(id, val, min, max) {
    const el = document.getElementById(id);
    if (!el) return;
    const lo = min ?? +el.min, hi = max ?? +el.max;
    const pct = ((val - lo) / (hi - lo)) * 100;
    el.style.setProperty("--fill", pct + "%");
}

function setSliderValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
}

function updateColorReadout(hex) {
    setText("color-hex", hex.toUpperCase());
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    setText("color-rgb", `${r}, ${g}, ${b}`);
}

function syncToggleUI() {
    const btn = document.getElementById("btn-toggle");
    const dot = document.getElementById("status-dot");
    if (btn) {
        btn.textContent = radarActive ? "End" : "Start";
        btn.classList.toggle("is-active", radarActive);
    }
    if (dot) {
        dot.classList.toggle("status-dot--on", radarActive);
        dot.classList.toggle("status-dot--off", !radarActive);
    }
}

function syncMoveUI() {
    const btn = document.getElementById("btn-move");
    if (!btn) return;
    btn.textContent = moveModeActive ? "Done" : "Move";
    btn.classList.toggle("is-active", moveModeActive);
}

// ── Bootstrap ──────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function () {
    // Default program option until/unless backend sends a list
    fillSelect("program-select", [{ value: "all", label: "All (system audio)" }]);

    // Wire dual-range inputs
    ["freq-low", "freq-high"].forEach(id =>
        document.getElementById(id).addEventListener("input", AR.setFreqRange));

    // Initial paint of values/fills/preview
    AR_initLocal();

    initBridge().then(() => console.log("Visual Audio Overlay bridge ready."));
});

// Local (no-bridge) initial UI state so the panel looks right immediately.
function AR_initLocal() {
    setFill("sensitivity", 50); setFill("gain", 10);
    setFill("max-amp", 100); setFill("thickness", 6, 1, 20);
    setFill("overlay-size", 100, 50, 200); setFill("alert-opacity", 100, 20, 100);
    setText("sensitivity-val", (50 / 10000).toFixed(4));
    setText("gain-val", "1.0x");
    setText("max-amp-val", "1.00");
    setText("thickness-val", "6 px");
    setText("overlay-size-val", overlaySizeText(100));
    setText("alert-opacity-val", "100 %");
    setText("position-val", "0, 0");
    syncMoveUI();
    updateDualFill();
    setText("freq-val", "150-4000 Hz");
    updateColorReadout("#9751F2");
    drawPreview();
}
