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
 *   refresh_programs()          - re-enumerate them (after the Program popup closes,
 *                                 or at the first input after the window regains focus)
 *
 *   Every signal is held back while a dropdown popup is open and applied once it
 *   closes (see "Quiet dropdowns" near fillSelect).
 *   appDeactivated()            - the whole application lost activation (alt-tab);
 *                                 ends a dropdown's quiet period. Guarded, not queued
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
 *
 * Toggle hotkey (guarded: the Hotkey row hides itself on an older backend):
 *   set_toggle_hotkey(str)      - bind a combination such as "Ctrl+Shift+H"; "" clears
 *   set_hotkey_capture(bool)    - the dashboard is waiting for a key press (Python
 *                                 releases the current hotkey meanwhile)
 *   hotkeyChanged(jsonStr)      - {combo, state, effective, requested, error,
 *                                  capturing, can_choose}, every key optional
 *   The hotkey starts or stops the radar like the Start/End button; the result
 *   arrives through the normal statusChanged.
 *
 * Capture device (guarded: an older backend keeps the deviceChanged text):
 *   set_capture_device(str)     - output device to listen to, by name; "" follows
 *                                 the Windows default
 *   refresh_capture_devices()   - re-enumerate; the answer is captureDevicesChanged.
 *                                 Asked for after the dropdown's popup closes, never
 *                                 while it is open (see "Quiet dropdowns")
 *   captureDevicesChanged(json) - {devices: [{name, channels, is_default}],
 *                                  selected, available}
 *   captureInfoChanged(json)    - {active, source, device, channels, layout, mode,
 *                                  note, note_short, mono_paused}; once the backend
 *                                  has it, it owns the status line and
 *                                  deviceChanged is ignored. The full note sits
 *                                  in the title; note_short is accepted but not
 *                                  shown (the radar words must stay whole), and
 *                                  mono_paused turns the Mono Output hint to Paused
 *   Every key optional. Profiles store the choice as capture_device.
 * ═══════════════════════════════════════════════════════════════════════
 */

let radarActive = false;
let moveModeActive = false;

// Project links (opened in the real browser via bridge.open_url). The update
// notice overrides updateUrl when a specific release page is known.
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

            // Every signal goes through `listen`, which holds it back while a
            // dropdown is open (see "Quiet dropdowns" below).
            listen("statusChanged", onStatusChanged);
            listen("deviceChanged", onDeviceChanged);
            listen("profilesChanged", onProfilesChanged);
            listen("monitorsChanged", onMonitorsChanged);
            listen("presetsChanged", onPresetsChanged);
            listen("overlayPositionChanged", onOverlayPositionChanged);
            // Optional new signals - only connected if the backend provides them.
            listen("programsChanged", onProgramsChanged);
            listen("monoStateChanged", onMonoStateChanged);
            listen("updateAvailable", onUpdateAvailable);
            listen("appearanceChanged", onAppearanceChanged);
            listen("audioSettingsChanged", onAudioSettingsChanged);
            listen("selectedPresetChanged", onSelectedPresetChanged);
            // Capture device. Without these signals (older backend) the select only
            // offers Windows default and deviceChanged keeps writing the status line.
            listen("captureDevicesChanged", onCaptureDevicesChanged);
            // The application lost activation. Not through listen: it is what
            // ends a dropdown's lifetime. An older backend lacks it.
            if (bridge.appDeactivated && typeof bridge.appDeactivated.connect === "function") {
                bridge.appDeactivated.connect(onAppDeactivated);
            }
            if (listen("captureInfoChanged", onCaptureInfoChanged)) captureInfoOwnsStatus = true;
            // Toggle hotkey. An older backend has neither the signal nor the slot,
            // so the Hotkey row would only show a sample binding: hide it instead.
            if (!(typeof bridge.set_toggle_hotkey === "function" && listen("hotkeyChanged", onHotkeyChanged))) {
                toggleClass("hotkey-field", "is-hidden", true);
            }

            // Show the current version in the footer.
            if (bridge.get_app_version) {
                bridge.get_app_version(whenNoPopup(function (v) { setText("footer-version", "v" + v); }));
            }

            bridge.request_initial_data();

            // The Program list only contains apps that are currently playing audio.
            // Re-enumerate whenever the user returns to this window (e.g. alt-tabs
            // back from the game), so a game launched after startup shows up without
            // having to Start the radar first.
            //
            // Not right away: a click into the window focuses it just before the
            // press, and a dropdown popup steals and returns focus while it opens.
            // The refresh runs at the next quiet moment instead (pointer moved,
            // a key or a click outside a dropdown, or a dropdown closed). See
            // requestRefresh. A focus during a dropdown's lifetime is queued
            // the same way (it runs once that ends), so the list never stays stale.
            window.addEventListener("focus", () => requestRefresh("programs", true));

            resolve();
        });
    });
}

// Backend found a newer GitHub release. Show the notice in the footer, left of
// the version number. It takes no extra height, so it needs no dismiss button.
function onUpdateAvailable(version, url) {
    if (url) updateUrl = url;
    setText("update-notice-text", version ? `Version ${version} is available` : "A new version is available");
    toggleClass("update-notice", "is-hidden", false);
}

// ── Signal handlers (Python → JS) ──────────────────────────────────────
// The only place the radar state is set: the Start/End button, the toggle
// hotkey and the backend itself all end in this signal.
function onStatusChanged(message, isActive) {
    radarActive = isActive;
    // Python announces a start before the capture thread reports, so the status
    // line says "Starting..." until a captureInfoChanged arrives (see
    // refreshCaptureStatus). A stop forgets what arrived. So does a start that
    // follows an ended capture: a device or program change restarts the
    // capture as "active false", this statusChanged, then "active true".
    if (!isActive || (captureInfoSinceStart && !(captureInfoLast && captureInfoLast.active === true))) {
        captureInfoSinceStart = false;
    }
    setText("status-text", message);
    syncToggleUI();
    refreshCaptureStatus();
}

// Older backends only: label is "Name  (Nch)". Once captureInfoChanged exists it
// owns the status line, so this is ignored then.
function onDeviceChanged(label) {
    if (captureInfoOwnsStatus || typeof label !== "string") return;
    const text = deviceLabelText(label);
    renderCaptureStatus([{ text, fit: "cut" }], text);
}

// "Speakers (Realtek(R) Audio)  (2ch)" -> "2 ch: Speakers (Realtek(R) Audio)".
// Channels first, since a long name ends in an ellipsis. Only a trailing
// "(N ch)" counts as the channel suffix, so brackets in the name stay put.
function deviceLabelText(label) {
    const m = String(label).match(/^(.*?)\s*\((\d+)\s*ch\)\s*$/);
    return m ? `${m[2]} ch: ${m[1].trim()}` : String(label).trim();
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
    monoStateLast = s;
    renderMonoHint();

    // Cable status + install button live in the modal (which scrolls, so no clip).
    const status = document.getElementById("mono-status");
    const installBtn = document.getElementById("mono-install-btn");
    if (s.cable) {
        if (status) status.innerHTML =
            `<span class="ok">Virtual cable detected:</span> ${escapeHtml(s.cable)}`;
        if (installBtn) installBtn.classList.add("is-hidden");
    } else {
        if (status) status.innerHTML =
            `<span class="warn">No virtual cable found.</span> Install VB-CABLE to ` +
            `route your game's audio without hearing it twice.`;
        if (installBtn) installBtn.classList.remove("is-hidden");
    }
}

// ── Capture device (HARDWARE card) ─────────────────────────────────────
// The select picks the output VAO listens to, by name ("" = follow the Windows
// default). The line under it says what is being captured and whether the radar
// shows full 360 or only left and right. The card has room for that one line
// only (183 px wide), so the visible text stays short and the title carries the
// full explanation plus Python's note.

let captureInfoOwnsStatus = false;   // backend sends captureInfoChanged
let captureDevices = [];             // last device list from captureDevicesChanged
let captureSelected = "";            // the saved choice ("" = Windows default)
let captureDevicesSig = null;        // last list + choice put into the select

const CAPTURE_TEXT = {
    windowsDefault: "Windows default",
    defaultTitle: "Windows default. Follows the sound output set in Windows.",
    isDefault: "default",
    notConnected: "Not connected",
    stopped: "Starts when the radar runs.",
    starting: "Starting...",          // radar on, the capture has not reported yet
    notListening: "Not listening.",   // radar on, but the capture ended
    listening: "Listening.",
    program: "This program",
    // Second half of the visible line.
    full: "Full 360.",
    leftRight: "Left and right only.",
    waiting: "Waiting for surround.",
    noDirection: "No direction.",
};
// The same, spelled out for the title.
const CAPTURE_TITLE = {
    full: "The radar shows full 360 direction.",
    leftRight: "The radar shows left and right only.",
    waiting: "No surround sound yet, so the radar shows left and right for now.",
    noDirection: "Mono sound has no direction, so every sound shows in front.",
};
// head: visible name, short: name before "Waiting for surround.", word: name in
// the title ("Receiving 7.1 sound."), multi: has surround channels. Unknown
// layouts fall back to the channel count.
const CAPTURE_LAYOUTS = {
    mono: { head: "Mono", short: "Mono", word: "mono", multi: false },
    stereo: { head: "Stereo", short: "Stereo", word: "stereo", multi: false },
    quad: { head: "Quad", short: "Quad", word: "quad", multi: true },
    "5.1": { head: "5.1 surround", short: "5.1", word: "5.1", multi: true },
    "6.1": { head: "6.1 surround", short: "6.1", word: "6.1", multi: true },
    "7.1": { head: "7.1 surround", short: "7.1", word: "7.1", multi: true },
};

// A JSON string or object as a plain object, or null. Never throws.
function parseCapturePayload(raw) {
    let p = raw;
    if (typeof raw === "string") {
        try { p = JSON.parse(raw); } catch (e) { return null; }
    }
    return p && typeof p === "object" && !Array.isArray(p) ? p : null;
}

// A positive whole channel count, else 0 (true, "8", 2.5 and junk are 0).
function captureChannels(v) {
    return typeof v === "number" && Number.isInteger(v) && v > 0 ? v : 0;
}

// Option text, e.g. "SteelSeries Sonar - Gaming (8 ch)" or "Speakers (2 ch, default)".
function captureDeviceLabel(d) {
    const extra = [];
    const ch = captureChannels(d.channels);
    if (ch) extra.push(`${ch} ch`);
    if (d.is_default === true) extra.push(CAPTURE_TEXT.isDefault);
    return extra.length ? `${d.name} (${extra.join(", ")})` : d.name;
}

// Options for the select: Windows default first, then the devices in the order
// Python sent them, then the saved choice if it is not among them right now
// ("Not connected: <name>", so the player sees the choice is kept).
// Membership in the list decides "not connected", so it also covers a payload
// whose `available` flag is missing.
function captureDeviceOptions(devices, selected) {
    const sel = typeof selected === "string" ? selected : "";
    const opts = [{ value: "", label: CAPTURE_TEXT.windowsDefault }];
    const seen = new Set([""]);
    for (const d of Array.isArray(devices) ? devices : []) {
        if (!d || typeof d.name !== "string" || d.name === "" || seen.has(d.name)) continue;
        seen.add(d.name);
        opts.push({ value: d.name, label: captureDeviceLabel(d) });
    }
    // Marker first: the closed select cuts long names, and this must show.
    if (!seen.has(sel)) opts.push({ value: sel, label: `${CAPTURE_TEXT.notConnected}: ${sel}` });
    return { opts, selected: sel };
}

function onCaptureDevicesChanged(raw) {
    const p = parseCapturePayload(raw);
    if (!p) return;
    if (Array.isArray(p.devices)) captureDevices = p.devices;
    if (typeof p.selected === "string") captureSelected = p.selected;
    renderCaptureDevices();
}

// Put the device list into the select. Identical lists arrive on every refresh
// (each time the dropdown has been opened), so an unchanged list with the right
// value showing is skipped; anything else goes through fillSelect, which waits
// while any dropdown is open (see "Quiet dropdowns").
function renderCaptureDevices() {
    const { opts, selected } = captureDeviceOptions(captureDevices, captureSelected);
    const sel = document.getElementById("capture-device-select");
    const sig = JSON.stringify([opts, selected]);
    if (sig === captureDevicesSig && sel && sel.value === selected) return;
    captureDevicesSig = sig;
    fillSelect("capture-device-select", opts, selected);
}

// The closed select cuts long names, so its title holds the full choice.
// Registered as the select's settled hook, so it also runs after a deferred fill.
function syncCaptureSelectTitle() {
    const sel = document.getElementById("capture-device-select");
    if (!sel || !sel.setAttribute) return;
    const opt = sel.options && sel.options[sel.selectedIndex];
    const title = !opt || opt.value === "" ? CAPTURE_TEXT.defaultTitle : opt.textContent;
    sel.setAttribute("title", title);
}

// {parts, text, title} for the status line. Pure, never throws. A payload
// replaces the previous one: a missing note means no note, a missing `active`
// means not capturing. `radarRunning` is the radar state from statusChanged.
//
// The line is 183 px, so it is built from parts that say what may be cut:
//   keep  never cut (what the radar can show)
//   cut   shortened with an ellipsis when the line is full (layout, program)
// The full `note` sentence is always in the title. note_short is not shown:
// measured in the real font, a short note plus the radar words does not fit
// ("Mono output paused. Full 360." is 190 px), and the radar words must stay
// whole. Its two cases show elsewhere: Paused in the Mono Output hint and
// "Not connected: <name>" in the select.
function captureStatus(raw, radarRunning) {
    const p = parseCapturePayload(raw) || {};
    const note = typeof p.note === "string" ? p.note.trim() : "";
    const done = (parts, title) => ({ parts, text: parts.map(x => x.text).join(" "), title });

    if (p.active !== true) {
        const idle = radarRunning === true ? CAPTURE_TEXT.notListening : CAPTURE_TEXT.stopped;
        return done([{ text: idle, fit: "keep" }], note);
    }

    const isApp = p.source === "app";
    const device = typeof p.device === "string" ? p.device.trim() : "";
    const ch = captureChannels(p.channels);
    const layout = typeof p.layout === "string" && Object.prototype.hasOwnProperty.call(CAPTURE_LAYOUTS, p.layout)
        ? CAPTURE_LAYOUTS[p.layout]
        : ch === 1 ? CAPTURE_LAYOUTS.mono
        : ch === 2 ? CAPTURE_LAYOUTS.stereo
        : ch > 2 ? { head: `${ch} ch`, short: `${ch} ch`, word: `${ch} channel`, multi: true }
        : null;

    // What the radar can show right now.
    let kind = "";
    if (p.mode === "surround") kind = "full";
    else if (layout === CAPTURE_LAYOUTS.mono) kind = "noDirection";
    else if (layout && layout.multi) kind = "waiting";
    else if (layout || p.mode === "stereo") kind = "leftRight";

    let head;
    if (isApp) head = device || CAPTURE_TEXT.program;
    else if (!layout) head = "";
    else head = kind === "waiting" ? layout.short : layout.head;

    const radar = kind ? CAPTURE_TEXT[kind] : "";
    const parts = [];
    if (head) parts.push({ text: `${head}.`, fit: radar ? "cut" : "keep" });
    if (radar) parts.push({ text: radar, fit: "keep" });
    if (!parts.length) parts.push({ text: CAPTURE_TEXT.listening, fit: "keep" });

    const titleParts = [];
    if (device) titleParts.push(`Listening to ${device}.`);
    else if (isApp) titleParts.push("Listening to one program.");
    if (layout) titleParts.push(`Receiving ${layout.word} sound.`);
    if (kind) titleParts.push(CAPTURE_TITLE[kind]);
    if (note) titleParts.push(note);
    const s = done(parts, "");
    s.title = titleParts.join(" ") || s.text;
    return s;
}

// Last captureInfoChanged payload, re-read when the radar starts or stops.
let captureInfoLast = null;
// A payload arrived while the radar was active, since it last started.
let captureInfoSinceStart = false;

function onCaptureInfoChanged(raw) {
    captureInfoOwnsStatus = true;
    const p = parseCapturePayload(raw);
    if (!p) return;                             // junk: keep what is showing
    captureInfoLast = p;
    if (radarActive) captureInfoSinceStart = true;
    monoPaused = p.mono_paused === true;
    monoPausedNote = typeof p.note === "string" ? p.note.trim() : "";
    renderMonoHint();
    refreshCaptureStatus();
}

// Repaint the status line from the last capture info and the radar state:
// stopped, "Starting..." until the capture reports, then its status, or
// "Not listening." once it reports that it ended. Event driven, no timers.
// Leaves the line to deviceChanged on an older backend.
function refreshCaptureStatus() {
    if (!captureInfoOwnsStatus) return;
    if (radarActive && !captureInfoSinceStart) {
        renderCaptureStatus([{ text: CAPTURE_TEXT.starting, fit: "keep" }], "");
        return;
    }
    const s = captureStatus(captureInfoLast, radarActive);
    renderCaptureStatus(s.parts, s.title);
}

// One span per part (see captureStatus); the trailing space is part of the
// span text, so the words sit exactly as in plain text.
function renderCaptureStatus(parts, title) {
    const el = document.getElementById("capture-status");
    if (!el) return;
    el.innerHTML = parts.map((x, i) =>
        `<span class="capture-part capture-part--${x.fit}">` +
        `${escapeHtml(x.text)}${i < parts.length - 1 ? " " : ""}</span>`).join("");
    setAttr("capture-status", "title", title);
}

// The Mono Output hint in the HARDWARE card: "Off", "On - <device>", or "Paused"
// while Python's mono feedback guard blocks mono playback (mono_paused in
// captureInfoChanged). One line: a long device name ends in an ellipsis (full
// text in the title) so Setup stays visible and the fixed-height card never clips.
let monoStateLast = null;   // last monoStateChanged payload
let monoPaused = false;     // mono_paused from the last captureInfoChanged
let monoPausedNote = "";    // the note that came with it

function renderMonoHint() {
    const s = monoStateLast;
    const hint = document.getElementById("mono-hint");
    if (!s || !hint) return;
    const where = s.selected || (s.default ? "default device" : "default");
    let state = s.enabled ? `On - ${where}` : "Off";
    let title = state;
    // Only while mono is on: with the switch off, "Paused" would contradict it.
    if (monoPaused && s.enabled) {
        state = "Paused";
        title = monoPausedNote || "Mono output is paused.";
    }
    hint.innerHTML =
        `<span class="mono-hint-state" title="${escapeAttr(title)}">${escapeHtml(state)}</span> ` +
        `<a href="#" class="mono-setup-link" ` +
        `onclick="AR.openMonoSetup(); return false;">Setup</a>`;
}

function onOverlayPositionChanged(jsonStr) {
    const state = JSON.parse(jsonStr);
    moveModeActive = !!state.drag_enabled;
    setText("position-val", `${state.x}, ${state.y}`);
    syncMoveUI();
}

// ── Toggle hotkey ──────────────────────────────────────────────────────
// The RADAR card has room for one line only (its status line wraps to two lines
// while the radar runs), so the card shows "Hotkey [binding]" and everything
// else - waiting for keys, the hint, errors, Clear - lives in #hotkey-modal,
// the same modal pattern as Mono Output setup.
//
// Combination strings are canonical: Ctrl, Alt, Shift (in that order), then one
// key, joined with "+". Python owns the binding and the default; this side only
// reads key presses and displays what hotkeyChanged reports.

// Last hotkeyChanged payload (merged, so a partial payload keeps the rest).
let hotkeyState = { combo: "", state: "", effective: "", requested: "", error: "",
    capturing: false, can_choose: true };
let hotkeyCapturing = false;   // our keydown listener is attached
let hotkeyCaptureHint = "";    // why the last key press was not accepted
let hotkeyJustSet = "";        // combination we just asked for, to confirm it saved

const HOTKEY_TEXT = {
    capture: "Press keys...",
    notSet: "Not set",
    notActive: "Not active",
    checking: "Checking...",
    unavailable: "Not available",
};
const HOTKEY_HINT = {
    idle: "Starts or stops the radar while you play.",
    saved: "Saved. Press it in game to start or stop the radar.",
    unbound: "No hotkey set. Click the button to pick one.",
    capture: "Press your keys now. Esc cancels, Backspace clears.",
    needModifier: "Add Ctrl or Alt, or use an F key.",
    badKey: "That key can't be used. Try a letter, number or F key.",
    meta: "The Windows key can't be used.",
    altGr: "AltGr can't be used. Try Ctrl or Alt.",
    pending: "Setting up the hotkey...",
    readOnly: "Your system sets this hotkey.",
    invalid: "That hotkey can't be used. Pick another.",
    unsupported: "Hotkeys are not available on this system.",
};

// Windows virtual key codes (event.keyCode in QtWebEngine on Windows, which
// follows the keyboard layout) for the keys the contract allows.
const HOTKEY_NAMED_KEYS = { 45: "Insert", 46: "Delete", 36: "Home", 35: "End",
    33: "PageUp", 34: "PageDown", 37: "Left", 38: "Up", 39: "Right", 40: "Down" };
// Shift, Ctrl, Alt, left/right Win and AltGr on their own: keep waiting.
const HOTKEY_MODIFIER_CODES = [16, 17, 18, 91, 92, 225];

// Key name for a keyCode, or "" when the key is not in the vocabulary.
function hotkeyKeyName(code) {
    code = Number(code);
    if (!Number.isInteger(code)) return "";
    if (code >= 65 && code <= 90) return String.fromCharCode(code);    // A-Z
    if (code >= 48 && code <= 57) return String.fromCharCode(code);    // 0-9
    if (code >= 112 && code <= 135) return "F" + (code - 111);         // F1-F24
    if (code >= 96 && code <= 105) return "Num" + (code - 96);         // Num0-Num9
    return HOTKEY_NAMED_KEYS[code] || "";
}

function isFunctionKeyName(key) {
    return /^F([1-9]|1[0-9]|2[0-4])$/.test(key);
}

// An F key works alone; any other key needs Ctrl or Alt (Shift alone is not enough).
function isValidHotkey(ctrl, alt, shift, key) {
    if (!key) return false;
    return isFunctionKeyName(key) || !!ctrl || !!alt;
}

function hotkeyCombo(ctrl, alt, shift, key) {
    const parts = [];
    if (ctrl) parts.push("Ctrl");
    if (alt) parts.push("Alt");
    if (shift) parts.push("Shift");
    parts.push(key);
    return parts.join("+");
}

// What a keydown means while waiting for keys:
//   {action: "ignore"}            a modifier on its own, keep waiting
//   {action: "cancel"}            Esc
//   {action: "clear"}             Backspace or Delete on its own
//   {action: "reject", hint}      not usable, keep waiting and say why
//   {action: "bind", combo}       a valid canonical combination
function readHotkeyEvent(e) {
    const code = Number(e && e.keyCode);
    if (HOTKEY_MODIFIER_CODES.includes(code)) return { action: "ignore" };
    const ctrl = !!e.ctrlKey, alt = !!e.altKey, shift = !!e.shiftKey, meta = !!e.metaKey;
    const altGr = typeof e.getModifierState === "function" && !!e.getModifierState("AltGraph");
    const bare = !ctrl && !alt && !shift && !meta && !altGr;
    if (code === 27 && bare) return { action: "cancel" };
    if ((code === 8 || code === 46) && bare) return { action: "clear" };
    if (meta) return { action: "reject", hint: HOTKEY_HINT.meta };
    if (altGr) return { action: "reject", hint: HOTKEY_HINT.altGr };
    const key = hotkeyKeyName(code);
    if (!key) return { action: "reject", hint: HOTKEY_HINT.badKey };
    if (!isValidHotkey(ctrl, alt, shift, key)) return { action: "reject", hint: HOTKEY_HINT.needModifier };
    return { action: "bind", combo: hotkeyCombo(ctrl, alt, shift, key) };
}

// Merge a hotkeyChanged payload into `prev`. Never throws: junk or missing keys
// keep the previous value, wrong types are ignored.
function mergeHotkeyPayload(prev, raw) {
    let p = raw;
    if (typeof raw === "string") {
        try { p = JSON.parse(raw); } catch (e) { p = null; }
    }
    const next = Object.assign({}, prev);
    if (!p || typeof p !== "object") return next;
    for (const k of ["combo", "state", "effective", "requested", "error"]) {
        if (typeof p[k] === "string") next[k] = p[k];
    }
    for (const k of ["capturing", "can_choose"]) {
        if (typeof p[k] === "boolean") next[k] = p[k];
    }
    return next;
}

// The player can pick a binding here (not unsupported, not system managed).
function hotkeyCanChoose(s) {
    return s.state !== "unsupported" && s.can_choose !== false;
}

// Text of the binding button (and the read-only readout).
function hotkeyButtonText(s, capturing) {
    if (capturing) return HOTKEY_TEXT.capture;
    if (s.state === "unsupported") return HOTKEY_TEXT.unavailable;
    if (s.effective) return s.effective;
    if (s.state === "pending" && (s.requested || s.combo)) return HOTKEY_TEXT.checking;
    if (s.combo) return HOTKEY_TEXT.notActive;     // saved but not working (startup conflict)
    return HOTKEY_TEXT.notSet;
}

// Hint line under the binding button. `ctx` = {capturing, captureHint, justSet}.
function hotkeyHintText(s, ctx) {
    ctx = ctx || {};
    if (ctx.capturing) return ctx.captureHint || HOTKEY_HINT.capture;
    if (s.state === "unsupported") return s.error || HOTKEY_HINT.unsupported;
    if (s.state === "conflict") {
        return s.error || `${s.requested || s.combo || "That hotkey"} is already in use. Pick another.`;
    }
    if (s.state === "invalid") return s.error || HOTKEY_HINT.invalid;
    if (s.state === "pending") return HOTKEY_HINT.pending;
    if (s.can_choose === false) return HOTKEY_HINT.readOnly;
    if (!s.combo && !s.effective) return HOTKEY_HINT.unbound;
    if (ctx.justSet && (s.effective === ctx.justSet || s.combo === ctx.justSet)) return HOTKEY_HINT.saved;
    return HOTKEY_HINT.idle;
}

function isHotkeyCapturing() { return hotkeyCapturing; }

function onHotkeyChanged(jsonStr) {
    hotkeyState = mergeHotkeyPayload(hotkeyState, jsonStr);
    // The binding can no longer be picked here: stop waiting for keys.
    if (hotkeyCapturing && !hotkeyCanChoose(hotkeyState)) AR.cancelHotkeyCapture();
    renderHotkey();
}

function setHidden(id, hidden) { toggleClass(id, "is-hidden", hidden); }
function setAttr(id, name, value) {
    const el = document.getElementById(id);
    if (el && el.setAttribute) el.setAttribute(name, value);
}

// Paint the card row and the modal from hotkeyState + the local capture state.
function renderHotkey() {
    const s = hotkeyState;
    const canChoose = hotkeyCanChoose(s);
    const text = hotkeyButtonText(s, hotkeyCapturing);
    const hint = hotkeyHintText(s, { capturing: hotkeyCapturing, captureHint: hotkeyCaptureHint,
        justSet: hotkeyJustSet });
    const bound = !!(s.combo || s.effective);

    // RADAR card: a button when the player can change it, plain text otherwise.
    setHidden("hotkey-btn", !canChoose);
    setHidden("hotkey-readout", canChoose);
    setText("hotkey-btn", text);
    setText("hotkey-readout", text);
    setAttr("hotkey-btn", "title", hint + " Click to change.");
    setAttr("hotkey-btn", "aria-label", `Hotkey: ${text}. Click to change.`);
    setAttr("hotkey-readout", "title", hint);
    setAttr("hotkey-readout", "aria-label", `Hotkey: ${text}. ${hint}`);

    // Modal.
    setHidden("hotkey-capture-btn", !canChoose);
    setText("hotkey-capture-btn", text);
    setAttr("hotkey-capture-btn", "aria-label", hotkeyCapturing
        ? "Waiting for keys. Click to stop." : `Hotkey: ${text}. Click to change.`);
    setAttr("hotkey-capture-btn", "aria-pressed", hotkeyCapturing ? "true" : "false");
    toggleClass("hotkey-capture-btn", "is-active", hotkeyCapturing);
    setHidden("hotkey-clear", !canChoose || !bound);
    setText("hotkey-hint", hint);
}

function onHotkeyCaptureKey(e) {
    // Every key is ours while waiting, so Ctrl+R, F5 and friends do nothing.
    e.preventDefault();
    e.stopPropagation();
    const r = readHotkeyEvent(e);
    if (r.action === "ignore") return;
    if (r.action === "cancel") { AR.cancelHotkeyCapture(); return; }
    if (r.action === "clear") { AR.clearHotkey(); return; }
    if (r.action === "reject") { hotkeyCaptureHint = r.hint; renderHotkey(); return; }
    // Valid: Python ends capture itself when it gets the new binding.
    endHotkeyCapture();
    hotkeyJustSet = r.combo;
    callBridge("set_toggle_hotkey", r.combo);
    renderHotkey();
}

// The window lost focus or got hidden: the keys go elsewhere now, so stop.
function onHotkeyCaptureBlur() { AR.cancelHotkeyCapture(); }
function onHotkeyCaptureVisibility() { if (document.hidden) AR.cancelHotkeyCapture(); }

// Detach the capture listeners. Does not tell Python (callers decide).
function endHotkeyCapture() {
    if (!hotkeyCapturing) return;
    hotkeyCapturing = false;
    hotkeyCaptureHint = "";
    window.removeEventListener("keydown", onHotkeyCaptureKey, true);
    window.removeEventListener("blur", onHotkeyCaptureBlur);
    document.removeEventListener("visibilitychange", onHotkeyCaptureVisibility);
}

// Esc closes the modal when it is not waiting for keys (capture stops Esc earlier).
function onHotkeyModalKey(e) {
    if (hotkeyCapturing) return;
    if (e.keyCode === 27) { e.preventDefault(); AR.closeHotkey(); }
}

function focusEl(id) {
    const el = document.getElementById(id);
    if (el && typeof el.focus === "function") el.focus();
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
        if (swallowClick("radar")) return;   // the state changed under this click (CLICK_STATE)
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
            capture_device: strVal("capture-device-select", ""),   // "" = Windows default
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
        if (swallowClick("preset")) return;  // another preset showed when pressed (CLICK_STATE)
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

    // Re-enumerate live audio programs, so the game shows up even if it started
    // playing after the app launched. Pressing the Program dropdown asks for this
    // too, but it runs once the popup has closed (see REFRESH_AFTER_POPUP).
    refreshPrograms() { requestRefresh("programs"); },

    // ── Capture device ─────────────────────────────────────────────
    // A name, or "" for the Windows default. Python answers with
    // captureDevicesChanged and captureInfoChanged.
    setCaptureDevice(value) {
        const name = value == null ? "" : String(value);
        captureSelected = name;
        callBridge("set_capture_device", name);
        syncCaptureSelectTitle();
    },

    // Re-enumerate, so a device plugged in since shows up. Pressing the dropdown
    // asks for this too, once its popup has closed (see REFRESH_AFTER_POPUP).
    refreshCaptureDevices() { requestRefresh("captureDevices"); },

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
        if (swallowClick("move")) return;    // the state changed under this click (CLICK_STATE)
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

    // ── Toggle hotkey ──────────────────────────────────────────────
    // The binding button (card or modal): open the modal and wait for keys.
    // A second click while waiting stops waiting.
    startHotkeyCapture() {
        if (!hotkeyCanChoose(hotkeyState)) return;
        if (hotkeyCapturing) { AR.cancelHotkeyCapture(); return; }
        AR.openHotkey();
        hotkeyCapturing = true;
        hotkeyCaptureHint = "";
        hotkeyJustSet = "";
        window.addEventListener("keydown", onHotkeyCaptureKey, true);
        window.addEventListener("blur", onHotkeyCaptureBlur);
        document.addEventListener("visibilitychange", onHotkeyCaptureVisibility);
        callBridge("set_hotkey_capture", true);
        renderHotkey();
        focusEl("hotkey-capture-btn");
    },

    // Stop waiting for keys without changing the binding.
    cancelHotkeyCapture() {
        if (!hotkeyCapturing) return;
        endHotkeyCapture();
        callBridge("set_hotkey_capture", false);
        renderHotkey();
    },

    // Remove the binding. Also ends capture (Python ends it on its side).
    clearHotkey() {
        endHotkeyCapture();
        hotkeyJustSet = "";
        callBridge("set_toggle_hotkey", "");
        renderHotkey();
    },

    openHotkey() {
        const modal = document.getElementById("hotkey-modal");
        if (modal && modal.classList.contains && !modal.classList.contains("is-hidden")) return;
        toggleClass("hotkey-modal", "is-hidden", false);
        document.addEventListener("keydown", onHotkeyModalKey);
        renderHotkey();
    },

    closeHotkey() {
        AR.cancelHotkeyCapture();
        hotkeyJustSet = "";
        toggleClass("hotkey-modal", "is-hidden", true);
        document.removeEventListener("keydown", onHotkeyModalKey);
        renderHotkey();
        focusEl("hotkey-btn");
    },

    // ── Footer support links + update notice ──────────────────────────
    openRepo() { openExternal(REPO_URL); },
    openFeedback() { openExternal(FEEDBACK_URL); },
    openContribute() { openExternal(CONTRIBUTE_URL); },
    openCoffee() { openExternal(COFFEE_URL); },
    openUpdate() { openExternal(updateUrl); },
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

    // Capture device: only a string ("" = Windows default). The select shows the
    // choice at once, marked "not connected" when it is not plugged in;
    // captureDevicesChanged then echoes Python's view.
    if (typeof p.capture_device === "string") {
        captureSelected = p.capture_device;
        renderCaptureDevices();
        AR.setCaptureDevice(p.capture_device);
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
function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }
function setText(id, txt) { const el = document.getElementById(id); if (el) el.textContent = txt; }
function toggleClass(id, cls, on) { const el = document.getElementById(id); if (el) el.classList.toggle(cls, on); }
function intVal(id, def) { const el = document.getElementById(id); return el ? parseInt(el.value) : def; }
function strVal(id, def) { const el = document.getElementById(id); return el ? el.value : def; }

// ── Quiet dropdowns ────────────────────────────────────────────────────
// QtWebEngine draws a <select> popup as a native Qt window (that is why the
// open list is white instead of following our dark CSS). If the page changes
// while that window is opening or open, Qt grows the rendered list instead of
// redrawing it, leaving blank white space beside and under the entries. The
// popup also takes focus off the page and hands it back while it opens, so
// focus and blur events arrive in the middle of it.
//
// 9f74d4d guarded only the options of the select that had focus. Since then
// more runs while a popup is up: the window focus event (it fires before the
// press, while no select has focus yet) started a blocking program
// enumeration, the Program and Audio Device presses re-enumerated, and the
// replies, the live capture status, the mono hint, the radar status and the
// hotkey row all changed the page underneath an open popup.
//
// So a dropdown's whole lifetime is quiet now:
//   - It starts with the press itself (pointerdown/mousedown, or a key that
//     opens a select), not with focus, so the focus churn is inside it.
//   - While it lasts, every signal from Python is held back in arrival order
//     (listen/whenNoPopup), nothing calls the bridge (requestRefresh waits),
//     and no select is rebuilt (fillSelect waits).
//   - It ends when the player picks an entry (change), or does anything else
//     on the page: a click outside the select, a key press (the page only gets
//     keys once the popup is closed), or focus moving to another element.
//     A blur with nowhere to go is the popup's own focus churn (or the window
//     losing focus) and does not end it.
//   - It also ends when the page is hidden, and when Python reports that the
//     whole application lost activation (appDeactivated: alt-tab to the game).
//     The popup is a window of this application, so its focus churn never
//     deactivates the application.
//   - Then everything held back is applied in order, the player's pick
//     outranking any deferred rebuild of that select, and only after that the
//     refreshes the press asked for run, so their replies land on a closed list.
// A popup closed without a pick (Esc, the same entry, a click on the select
// itself) stays "open" until the next click, key, hidden page or deactivation.
// That delays updates and never changes an open list. While it waits, runs of
// the same "latest state" signal collapse into one (COALESCE), and a click that
// ends it is checked against what the player saw (see CLICK_STATE).
// Event driven, no timers.

let popupSelect = null;           // the <select> whose popup is opening or open
const popupDeferred = [];         // [fn, args] held back while it is, in order
const refreshesDue = new Set();   // names from REFRESHES, run at the next quiet moment
let popupPick = null;             // {id, value} while applying the held-back work
let clickSwallow = new Set();     // CLICK_STATE keys a flush changed under this click

// Bridge refreshes, by name. Each re-enumerates on the GUI thread in Python and
// answers with a signal that rebuilds a select.
const REFRESHES = {
    programs: () => callBridge("refresh_programs"),
    captureDevices: () => callBridge("refresh_capture_devices"),
};
// Pressing these dropdowns asks for a fresh list for the next time it opens.
// It cannot be fresh for this time without a reply landing on the open popup.
const REFRESH_AFTER_POPUP = { "program-select": "programs", "capture-device-select": "captureDevices" };

// True while a dropdown popup is opening, open, or possibly closed without a
// change (see above); with `sel`, only for that one.
function isSelectOpen(sel) {
    return popupSelect !== null && (!sel || popupSelect === sel);
}

// Signals whose handler only shows the latest state: a run of the same one in
// the queue collapses into one item. Cross-signal order is kept (only the item
// right before is merged), and a payload that is not a JSON object is queued as
// is. statusChanged is not here: its stop-then-start pairs matter.
const COALESCE = {
    overlayPositionChanged: (a, b) => b,                     // full state each time
    captureInfoChanged: (a, b) => b,                         // a payload replaces the last one
    appearanceChanged: (a, b) => Object.assign({}, a, b),    // every key optional
    audioSettingsChanged: (a, b) => {                        // every key optional
        // A null or missing value in the newer item never overwrites a real one.
        const m = Object.assign({}, a);
        for (const k of Object.keys(b)) if (b[k] != null) m[k] = b[k];
        // The frequency band only applies as a pair: keep the newest whole pair.
        if (b.freq_low == null || b.freq_high == null) {
            if (a.freq_low != null && a.freq_high != null) { m.freq_low = a.freq_low; m.freq_high = a.freq_high; }
            else { delete m.freq_low; delete m.freq_high; }
        }
        return m;
    },
};

// Merge `args` into the queue's last item when it is the same coalescing signal.
function coalesceDeferred(name, args) {
    const last = popupDeferred[popupDeferred.length - 1];
    if (!name || !COALESCE[name] || !last || last[2] !== name) return false;
    const a = parseCapturePayload(last[1][0]);
    const b = parseCapturePayload(args[0]);
    if (!a || !b) return false;
    last[1] = [JSON.stringify(COALESCE[name](a, b))];
    return true;
}

// `fn`, held back while a dropdown is open and applied in order once it closes.
// `name` is the signal it handles (for COALESCE).
function whenNoPopup(fn, name) {
    return function (...args) {
        if (popupSelect) {
            if (!coalesceDeferred(name, args)) popupDeferred.push([fn, args, name]);
            return;
        }
        fn(...args);
    };
}

// Connect a bridge signal through whenNoPopup. False when the backend lacks it.
function listen(name, fn) {
    const sig = window.bridge && window.bridge[name];
    if (!sig || typeof sig.connect !== "function") return false;
    sig.connect(whenNoPopup(fn, name));
    return true;
}

// Ask Python to re-enumerate. Runs now when no dropdown is open, unless `later`;
// otherwise at the next quiet moment (runDueRefreshes).
function requestRefresh(name, later) {
    if (!REFRESHES[name]) return;
    refreshesDue.add(name);
    if (!later) runDueRefreshes();
}

function runDueRefreshes() {
    if (popupSelect) return;
    for (const name of Array.from(refreshesDue)) {
        refreshesDue.delete(name);
        REFRESHES[name]();
    }
}

// The <select> an event is about, or null.
function eventSelect(e) {
    let el = e && e.target;
    if (el && el.tagName === "OPTION") el = el.parentElement || el.parentNode;
    return el && el.tagName === "SELECT" ? el : null;
}

// Keys that open a closed select in Chromium on Windows. Other keys on a
// closed select (arrows, letters) change it in place and fire change; Enter
// does not open it.
function opensSelect(e) {
    const k = e.key;
    if (k === "F4" && !e.altKey) return true;
    if ((k === "ArrowDown" || k === "ArrowUp") && e.altKey) return true;
    return k === " ";
}

// A dropdown is about to open. Opening another one while the last is still
// counted as open (closed without a pick) keeps the held-back work waiting.
function beginPopup(sel) {
    if (popupSelect === sel) return;
    popupSelect = sel;
    const refresh = REFRESH_AFTER_POPUP[sel.id];
    if (refresh) refreshesDue.add(refresh);
}

// The dropdown has closed: apply the held-back work in order. `picked` keeps
// the entry the player just chose over any deferred rebuild of that select.
// One failing item never drops the rest.
function endPopup(picked) {
    if (!popupSelect) return;
    const sel = popupSelect;
    popupSelect = null;
    popupPick = picked ? { id: sel.id, value: sel.value } : null;
    try {
        while (popupDeferred.length) {
            const [fn, args] = popupDeferred.shift();
            try { fn(...args); } catch (err) { console.error(err); }
        }
    } finally {
        popupPick = null;
    }
}

// Controls whose click acts on state that applying the held-back work can
// change: the radar toggle (Start/End), Move/Done, the two switches, and Delete
// (it deletes the preset showing). If a popup closed without a change, the
// player saw the old state when they pressed; their click must not do the
// opposite of what they saw. So the click that ends such a lifetime is dropped
// for a control whose state changed under it, and the player sees the real
// state and clicks again. The hotkey row needs no entry: its button only opens
// the modal, which reads the state afresh, and Clear lives in that modal.
const CLICK_STATE = {
    radar: () => radarActive,
    move: () => moveModeActive,
    "mono-enabled": () => !!document.getElementById("mono-enabled")?.checked,
    "outline-enabled": () => !!document.getElementById("outline-enabled")?.checked,
    preset: () => document.getElementById("preset-select")?.value,
};

// End a lifetime at a click or key, noting which CLICK_STATE changed.
function endPopupForInput() {
    if (!popupSelect) return;
    const before = {};
    for (const k of Object.keys(CLICK_STATE)) before[k] = CLICK_STATE[k]();
    endPopup();
    for (const k of Object.keys(CLICK_STATE)) if (CLICK_STATE[k]() !== before[k]) clickSwallow.add(k);
}

// True once (and forgets it) when this click must be dropped for `key`.
function swallowClick(key) {
    if (!clickSwallow.has(key)) return false;
    clickSwallow.delete(key);
    return true;
}

function onPagePress(e) {
    // A new press starts with a clean slate (mousedown follows its own pointerdown).
    if (e.type !== "mousedown") clickSwallow.clear();
    const sel = eventSelect(e);
    if (sel && (e.button == null || e.button === 0)) { beginPopup(sel); return; }
    // A press elsewhere (the page closes any popup before it handles a press).
    endPopupForInput();
    runDueRefreshes();
}

function onPageKey(e) {
    clickSwallow.clear();
    const sel = eventSelect(e);
    if (sel && opensSelect(e)) { beginPopup(sel); return; }
    endPopupForInput();
    runDueRefreshes();
}

// A switch whose state changed under the click: cancel the click, which keeps
// the checkbox as it now is and fires no change.
function onPageClickCapture(e) {
    const t = e.target;
    if (t && (t.id === "mono-enabled" || t.id === "outline-enabled") && swallowClick(t.id)) e.preventDefault();
}
// After the click's own handlers: forget what was not used, so it never sticks.
// A click on a switch's label is followed by a click on its input, so that
// first one leaves it for the input.
function onPageClick(e) {
    const t = e.target;
    const inLabel = t && t.tagName !== "INPUT" && typeof t.closest === "function" && t.closest("label");
    if (!inLabel) clickSwallow.clear();
}

// A hidden page, or the whole application losing activation, cannot have a
// popup open: end the lifetime. Python sends appDeactivated directly (not
// through the queue). On deactivation the due refreshes wait: the game is
// taking focus, and the window focus on return asks for them again.
function onPageVisibility() {
    if (!document.hidden) return;
    endPopup();
    runDueRefreshes();
}
function onAppDeactivated() {
    endPopup();
}

// Picking an entry. Capture phase: the held-back work lands before the
// select's own onchange reads the pick, and the pick wins over it.
function onPageChangeCapture(e) {
    if (popupSelect && e.target === popupSelect) endPopup(true);
}
// Bubble phase, after the select's onchange has told Python about the pick:
// now the refreshes, so their replies carry the new choice.
function onPageChange() { runDueRefreshes(); }

// Focus moving to another element of the page ends it; a blur with no new
// target is the popup's focus churn or the window losing focus.
function onPageBlur(e) {
    if (!popupSelect || e.target !== popupSelect || !e.relatedTarget) return;
    endPopup();
    runDueRefreshes();
}

function onPagePointerMove() {
    if (!popupSelect && refreshesDue.size) runDueRefreshes();
}

function installPopupGuard() {
    if (!document.addEventListener) return;
    document.addEventListener("pointerdown", onPagePress, true);
    document.addEventListener("mousedown", onPagePress, true);
    document.addEventListener("keydown", onPageKey, true);
    document.addEventListener("change", onPageChangeCapture, true);
    document.addEventListener("change", onPageChange, false);
    document.addEventListener("blur", onPageBlur, true);
    document.addEventListener("pointermove", onPagePointerMove, true);
    document.addEventListener("click", onPageClickCapture, true);
    document.addEventListener("click", onPageClick, false);
    document.addEventListener("visibilitychange", onPageVisibility);
}
installPopupGuard();

// Follow-up work to run once a select's options have actually landed in the DOM.
// Registered per id so a *deferred* fill still triggers what its caller expected
// to happen right after the rebuild.
const _fillSettled = { "preset-select": maybeRestorePreset,
    "capture-device-select": syncCaptureSelectTitle };

// Replace a <select>'s options. `preferred` is the value to end up selected;
// omit it to keep the current pick. See applySelectOptions for the fallbacks.
// While a dropdown is open (any of them) it waits, in order with the signals.
function fillSelect(id, opts, preferred) {
    const sel = document.getElementById(id);
    if (!sel) return;
    if (popupSelect) { popupDeferred.push([fillSelect, [id, opts, preferred]]); return; }
    if (popupPick && popupPick.id === id) preferred = popupPick.value;
    applySelectOptions(sel, opts, preferred);
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
    // Windows default only, until (or unless) the backend sends the device list
    renderCaptureDevices();

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
