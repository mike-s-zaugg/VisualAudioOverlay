// Capture device tests for dashboard_v2/script.js: the Audio Device select in the
// HARDWARE card (filled from captureDevicesChanged, safe while its popup is
// open), the status line under it (captureInfoChanged), the old deviceChanged
// fallback, profiles (capture_device) and running without a bridge.
//
// Run:  node tests/test_capture_device_ui.js   (exit 0 = pass)
//
// Same harness as test_hotkey_ui.js: loads the real script.js against a minimal
// DOM stub and records every bridge call. Each load() is a fresh script context,
// so a scenario can start against an older or a newer backend.
//
// The contract (slot names, payload keys, profile key) is shared with main.py;
// see the "Capture device" block at the top of script.js.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class ClassList {
    constructor() { this.set = new Set(); }
    toggle(c, force) {
        const on = force === undefined ? !this.set.has(c) : !!force;
        if (on) this.set.add(c); else this.set.delete(c);
        return on;
    }
    add(c) { this.set.add(c); }
    remove(c) { this.set.delete(c); }
    contains(c) { return this.set.has(c); }
}

class Option {
    constructor() { this.value = ""; this.textContent = ""; }
}

class Generic {
    constructor(id) {
        this.id = id; this.value = ""; this.textContent = ""; this.checked = false;
        this.innerHTML = ""; this.options = []; this.attrs = {};
        this.style = { setProperty() {} };
        this.classList = new ClassList();
        this.dataset = { min: "20", max: "20000" };
    }
    addEventListener() {}
    appendChild(o) { this.options.push(o); }
    querySelector() { return { style: {} }; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    focus() {}
}

// A <select> with real value semantics (as in test_dropdowns.js) plus attributes.
class Select {
    constructor(id) {
        this.id = id; this.tagName = "SELECT"; this.options = []; this._idx = -1;
        this.listeners = {}; this.attrs = {}; this.rebuilds = 0;
    }
    set innerHTML(v) { if (v === "") { this.options = []; this._idx = -1; this.rebuilds++; } }
    get innerHTML() { return ""; }
    appendChild(o) { this.options.push(o); if (this._idx === -1) this._idx = 0; }
    get value() { return this._idx >= 0 && this.options[this._idx] ? this.options[this._idx].value : ""; }
    set value(v) { this._idx = this.options.findIndex(o => String(o.value) === String(v)); }
    get selectedIndex() { return this._idx; }
    set selectedIndex(i) { this._idx = i; }
    addEventListener(ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); }
    fire(ev) { const l = this.listeners[ev] || []; this.listeners[ev] = []; l.forEach(cb => cb()); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    labels() { return this.options.map(o => o.textContent); }
    values() { return this.options.map(o => o.value); }
}

const SELECT_IDS = ["capture-device-select", "program-select", "monitor-select", "preset-select",
    "mono-output-select"];
const src = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "script.js"), "utf8");

// Fresh script context. `signals` lists the backend signals that exist (for
// initBridge); `slots` limits which slots exist (default: every slot exists).
// Returns {W, els, calls, sandbox, signal(name, payload)}.
function load(opts = {}) {
    const els = {};
    for (const id of SELECT_IDS) els[id] = new Select(id);
    // Mirror index.html: the status line starts with one keep part.
    els["capture-status"] = Object.assign(new Generic("capture-status"),
        { innerHTML: '<span class="capture-part capture-part--keep">Starts when the radar runs.</span>' });
    const calls = [];
    const slots = opts.slots || null;
    const bridgeTarget = {};
    const handlers = {};
    for (const s of opts.signals || []) {
        bridgeTarget[s] = { connect(fn) { handlers[s] = fn; } };
    }
    const bridge = new Proxy(bridgeTarget, {
        get(t, name) {
            if (name === "then") return undefined;
            if (name in t) return t[name];
            if (opts.signals && /Changed$|Available$/.test(String(name))) return undefined;
            if (slots && !slots.includes(String(name))) return undefined;
            return (...args) => calls.push([String(name), ...args]);
        },
        has() { return true; },
    });
    const domReady = [];
    const sandbox = {
        console,
        document: {
            activeElement: null,
            getElementById(id) {
                if (id === "preview-canvas") return null;          // skip canvas painting
                if (!(id in els)) els[id] = new Generic(id);
                return els[id];
            },
            createElement() { return new Option(); },
            addEventListener(type, fn) { if (type === "DOMContentLoaded") domReady.push(fn); },
            removeEventListener() {},
        },
        window: null,
        bridge: opts.noBridge ? undefined : bridge,
        addEventListener() {},
        removeEventListener() {},
        prompt: () => "Capture test",
    };
    if (opts.signals) {
        // initBridge: the channel hands over the bridge object synchronously.
        sandbox.qt = { webChannelTransport: {} };
        sandbox.QWebChannel = function (_t, cb) { cb({ objects: { bridge } }); };
    }
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    if (opts.signals) {
        sandbox.initBridge();
        calls.length = 0;                       // drop request_initial_data and friends
    }
    if (opts.domReady) {
        // The bootstrap needs a few more elements; give it plain stubs.
        for (const id of ["freq-low", "freq-high"]) els[id] = new Generic(id);
        domReady.forEach(fn => { try { fn(); } catch (e) { /* bridge init without Qt */ } });
    }
    return {
        W: sandbox, els, calls, sandbox,
        signal(name, ...args) { if (handlers[name]) handlers[name](...args); return !!handlers[name]; },
        connected: name => name in handlers,
    };
}

// ── Assertions ─────────────────────────────────────────────────────────
let failed = 0;
function check(name, cond, extra) {
    if (cond) { console.log("  PASS  " + name); }
    else { failed++; console.log("  FAIL  " + name + (extra ? "  -> " + extra : "")); }
}
function section(t) { console.log("\n" + t); }
const J = JSON.stringify;

// The status line is a row of spans (see captureStatus). parts() reads them back
// as [fit, text]; shown() is the text a player sees when nothing is cut.
const unescape = s => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&amp;/g, "&");
function parts(el) {
    const out = [];
    const re = /<span class="capture-part capture-part--(keep|cut|fill)">([^<]*)<\/span>/g;
    let m;
    while ((m = re.exec(el.innerHTML))) out.push([m[1], unescape(m[2]).trim()]);
    return out;
}
const shown = el => unescape(el.innerHTML.replace(/<[^>]*>/g, ""));

const DEVICES = [
    { name: "Speakers (Realtek(R) Audio)", channels: 2, is_default: true },
    { name: "SteelSeries Sonar - Gaming", channels: 8, is_default: false },
    { name: "Voicemeeter Input (VB-Audio Voicemeeter VAIO)", channels: 6, is_default: false },
];
const devs = over => J(Object.assign({ devices: DEVICES, selected: "", available: true }, over || {}));
const info = over => J(Object.assign({ active: true, source: "system", device: "SteelSeries Sonar - Gaming",
    channels: 8, layout: "7.1", mode: "surround", note: "", note_short: "", mono_paused: false }, over || {}));

// ── 1. Filling the select ──────────────────────────────────────────────
section("captureDevicesChanged fills the select");
let t = load();
let sel = t.els["capture-device-select"];
t.W.onCaptureDevicesChanged(devs());
check("Windows default first, then the devices in order",
    J(sel.values()) === J(["", "Speakers (Realtek(R) Audio)", "SteelSeries Sonar - Gaming",
        "Voicemeeter Input (VB-Audio Voicemeeter VAIO)"]), J(sel.values()));
check("labels carry the channel count, the default is marked",
    J(sel.labels()) === J(["Windows default", "Speakers (Realtek(R) Audio) (2 ch, default)",
        "SteelSeries Sonar - Gaming (8 ch)", "Voicemeeter Input (VB-Audio Voicemeeter VAIO) (6 ch)"]),
    J(sel.labels()));
check("selected \"\" shows Windows default", sel.value === "" && sel.selectedIndex === 0, String(sel.selectedIndex));
check("select title explains Windows default", /Follows the sound output set in Windows/.test(sel.attrs.title || ""),
    sel.attrs.title);
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
check("saved device selected", sel.value === "SteelSeries Sonar - Gaming", sel.value);
check("select title holds the full label", sel.attrs.title === "SteelSeries Sonar - Gaming (8 ch)", sel.attrs.title);
check("no extra option for a connected choice", sel.options.length === 4, String(sel.options.length));

section("saved device not connected");
t.W.onCaptureDevicesChanged(devs({ selected: "Sonar Gaming", available: false }));
check("kept as an extra option at the end", sel.values()[4] === "Sonar Gaming" && sel.options.length === 5, J(sel.values()));
check("marked as not connected, marker first", sel.labels()[4] === "Not connected: Sonar Gaming", sel.labels()[4]);
check("and selected", sel.value === "Sonar Gaming", sel.value);
check("title says so too", sel.attrs.title === "Not connected: Sonar Gaming", sel.attrs.title);
t.W.onCaptureDevicesChanged(devs({ selected: "Sonar Gaming", available: true }));
check("a choice missing from the list is shown even if available says true",
    sel.value === "Sonar Gaming" && sel.labels()[4] === "Not connected: Sonar Gaming", J(sel.labels()));
t.W.onCaptureDevicesChanged(devs({ devices: DEVICES.concat([{ name: "Sonar Gaming", channels: 8 }]),
    selected: "Sonar Gaming" }));
check("plugged back in: a normal option again", sel.labels()[4] === "Sonar Gaming (8 ch)" && sel.options.length === 5,
    J(sel.labels()));

section("odd device entries");
t.W.onCaptureDevicesChanged(J({ devices: [null, 5, {}, { name: "" }, { name: 7 },
    { name: "A" }, { name: "A", channels: 2 }, { name: "B", channels: "8" }, { name: "C", channels: 0 },
    { name: "D", channels: 2.5 }, { name: "E", channels: true, is_default: "yes" }], selected: "" }));
check("junk skipped, duplicates once, bad channel counts left out",
    J(sel.labels()) === J(["Windows default", "A", "B", "C", "D", "E"]), J(sel.labels()));
check("still Windows default", sel.value === "", sel.value);

section("partial and broken captureDevicesChanged payloads never throw");
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
for (const bad of ["", "{", "null", "[]", "42", "\"x\"", undefined, null, "{}", '{"devices":"no"}',
    '{"selected":5}', '{"devices":null,"selected":null}']) {
    let threw = null;
    try { t.W.onCaptureDevicesChanged(bad); } catch (e) { threw = e; }
    check(`${J(bad)} does not throw`, threw === null, String(threw));
}
check("junk keeps the list and the choice", sel.options.length === 4 && sel.value === "SteelSeries Sonar - Gaming",
    J(sel.values()) + " / " + sel.value);
t.W.onCaptureDevicesChanged(J({ selected: "" }));
check("selected only: list kept, choice follows", sel.options.length === 4 && sel.value === "", sel.value);
t.W.onCaptureDevicesChanged(J({ devices: [] }));
check("empty list: Windows default only", J(sel.labels()) === J(["Windows default"]), J(sel.labels()));

section("an unchanged list is not rebuilt");
t = load();
sel = t.els["capture-device-select"];
t.W.onCaptureDevicesChanged(devs());
let n = sel.rebuilds;
t.W.onCaptureDevicesChanged(devs());
check("identical payload: no rebuild", sel.rebuilds === n, String(sel.rebuilds - n));
sel.value = "SteelSeries Sonar - Gaming";             // user picked, Python kept ""
t.W.onCaptureDevicesChanged(devs());
check("same list but a different value showing: rebuilt to Python's choice", sel.value === "", sel.value);

// ── 2. Popup open ──────────────────────────────────────────────────────
section("no refill while the popup is open");
t = load();
sel = t.els["capture-device-select"];
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
n = sel.rebuilds;
t.sandbox.document.activeElement = sel;               // popup open
t.W.onCaptureDevicesChanged(devs({ devices: DEVICES.concat([{ name: "USB Headset", channels: 2 }]),
    selected: "SteelSeries Sonar - Gaming" }));
check("options untouched while open", sel.options.length === 4 && sel.rebuilds === n, String(sel.options.length));
check("selection untouched while open", sel.value === "SteelSeries Sonar - Gaming", sel.value);
t.W.onCaptureDevicesChanged(devs({ devices: DEVICES.concat([{ name: "USB Headset", channels: 2 }]),
    selected: "SteelSeries Sonar - Gaming" }));
check("a second reply while open still waits", sel.options.length === 4 && sel.rebuilds === n);
sel.fire("blur");
t.sandbox.document.activeElement = null;
check("deferred fill applied on blur", sel.options.length === 5 && sel.labels()[4] === "USB Headset (2 ch)",
    J(sel.labels()));
check("applied once", sel.rebuilds === n + 1, String(sel.rebuilds - n));
check("selection kept", sel.value === "SteelSeries Sonar - Gaming", sel.value);

section("user picks while a fill is waiting");
t.calls.length = 0;
t.sandbox.document.activeElement = sel;
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));   // USB Headset gone
sel.value = "Speakers (Realtek(R) Audio)";
t.W.AR.setCaptureDevice(sel.value);                   // inline onchange
sel.fire("change");                                   // fillSelect's flush listener
t.sandbox.document.activeElement = null;
check("the pick wins over the queued fill", sel.value === "Speakers (Realtek(R) Audio)", sel.value);
check("queued options landed", sel.options.length === 4, String(sel.options.length));
check("the pick went to Python", J(t.calls) === J([["set_capture_device", "Speakers (Realtek(R) Audio)"]]), J(t.calls));
check("title follows the pick", sel.attrs.title === "Speakers (Realtek(R) Audio) (2 ch, default)", sel.attrs.title);

section("another select open does not block this one");
t.sandbox.document.activeElement = t.els["program-select"];
t.W.onCaptureDevicesChanged(devs({ selected: "" }));
t.sandbox.document.activeElement = null;
check("filled at once", sel.value === "", sel.value);

// ── 3. JS -> Python ────────────────────────────────────────────────────
section("setCaptureDevice and refreshCaptureDevices");
t = load();
t.W.AR.setCaptureDevice("SteelSeries Sonar - Gaming");
t.W.AR.setCaptureDevice("");
t.W.AR.setCaptureDevice(null);
t.W.AR.setCaptureDevice(undefined);
t.W.AR.setCaptureDevice(5);
check("set_capture_device always gets one string",
    J(t.calls) === J([["set_capture_device", "SteelSeries Sonar - Gaming"], ["set_capture_device", ""],
        ["set_capture_device", ""], ["set_capture_device", ""], ["set_capture_device", "5"]]), J(t.calls));
t.calls.length = 0;
t.W.AR.refreshCaptureDevices();
check("refresh_capture_devices with no arguments", J(t.calls) === J([["refresh_capture_devices"]]), J(t.calls));

section("index.html wiring");
const html = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "index.html"), "utf8");
const selTag = (html.match(/<select id="capture-device-select"[^>]*>/) || [""])[0];
check("the select exists with the Monitor select's classes", /class="select"/.test(selTag), selTag);
check("it sits in a select-wrap", /<div class="select-wrap">\s*<select id="capture-device-select"/.test(html));
check("onchange calls AR.setCaptureDevice(this.value)", /onchange="AR\.setCaptureDevice\(this\.value\)"/.test(selTag), selTag);
check("onmousedown calls AR.refreshCaptureDevices()", /onmousedown="AR\.refreshCaptureDevices\(\)"/.test(selTag), selTag);
check("it has an accessible name and points at the status line",
    /aria-label="Audio device"/.test(selTag) && /aria-describedby="capture-status"/.test(selTag), selTag);
check("Windows default is there before any data", /<option value="">Windows default<\/option>/.test(html));
check("status line reuses .device-sub", /id="capture-status" class="device-sub capture-status"/.test(html));
check("the old device name line is gone", !html.includes('id="device-name"') && !html.includes('id="device-channels"')
    && !src.includes("device-name") && !src.includes("device-channels"));

// ── 4. Incoming signals never call the bridge ──────────────────────────
section("no echo loop");
t = load();
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
t.W.onCaptureDevicesChanged(devs({ selected: "Gone", available: false }));
t.W.onCaptureInfoChanged(info());
t.W.onCaptureInfoChanged(info({ active: false }));
t.W.onDeviceChanged("Speakers  (2ch)");
check("no bridge call from any incoming signal", t.calls.length === 0, J(t.calls));

// ── 5. captureInfoChanged -> status line ───────────────────────────────
section("status line");
t = load();
const st = () => shown(t.els["capture-status"]);
const stParts = () => parts(t.els["capture-status"]);
const stTitle = () => t.els["capture-status"].attrs.title;
function infoCase(name, payload, text, titleCheck) {
    t.W.onCaptureInfoChanged(payload);
    check(`${name}: "${text}"`, st() === text, st());
    if (titleCheck) check(`${name}: title`, titleCheck(stTitle() || ""), stTitle());
}
infoCase("stopped", J({ active: false }), "Starts when the radar runs.", s => s === "");
infoCase("stopped with stale fields", info({ active: false }), "Starts when the radar runs.", s => s === "");
infoCase("stereo", info({ device: "Speakers (Realtek(R) Audio)", channels: 2, layout: "stereo", mode: "stereo" }),
    "Stereo. Left and right only.",
    s => s === "Listening to Speakers (Realtek(R) Audio). Receiving stereo sound. The radar shows left and right only.");
infoCase("5.1 surround", info({ channels: 6, layout: "5.1" }), "5.1 surround. Full 360.",
    s => /Receiving 5\.1 sound\. The radar shows full 360 direction\.$/.test(s));
infoCase("6.1 surround", info({ channels: 7, layout: "6.1" }), "6.1 surround. Full 360.");
infoCase("7.1 surround", info(), "7.1 surround. Full 360.",
    s => s === "Listening to SteelSeries Sonar - Gaming. Receiving 7.1 sound. The radar shows full 360 direction.");
infoCase("quad surround", info({ channels: 4, layout: "quad" }), "Quad. Full 360.");
infoCase("7.1 waiting in stereo mode", info({ mode: "stereo" }), "7.1. Waiting for surround.",
    s => /No surround sound yet, so the radar shows left and right for now\./.test(s));
infoCase("5.1 waiting in stereo mode", info({ channels: 6, layout: "5.1", mode: "stereo" }), "5.1. Waiting for surround.");
infoCase("quad waiting in stereo mode", info({ channels: 4, layout: "quad", mode: "stereo" }), "Quad. Waiting for surround.");
infoCase("mono", info({ channels: 1, layout: "mono", mode: "stereo" }), "Mono. No direction.",
    s => /every sound shows in front/.test(s));
infoCase("per-app", info({ source: "app", device: "cs2.exe", channels: 2, layout: "stereo", mode: "stereo" }),
    "cs2.exe. Left and right only.", s => s.startsWith("Listening to cs2.exe."));
infoCase("per-app without a name", info({ source: "app", device: "", channels: 2, layout: "stereo", mode: "stereo" }),
    "This program. Left and right only.", s => s.startsWith("Listening to one program."));
infoCase("per-app in surround", info({ source: "app", device: "game.exe" }), "game.exe. Full 360.");

section("what the radar shows is never the part that gets cut");
t.W.onCaptureInfoChanged(info({ source: "app", device: "Counter-Strike 2 Global Offensive.exe", channels: 2,
    layout: "stereo", mode: "stereo" }));
check("long program name: name may be cut, radar words kept",
    J(stParts()) === J([["cut", "Counter-Strike 2 Global Offensive.exe."], ["keep", "Left and right only."]]), J(stParts()));
for (const [over, words] of [[{}, "Full 360."], [{ mode: "stereo" }, "Waiting for surround."],
    [{ channels: 2, layout: "stereo", mode: "stereo" }, "Left and right only."],
    [{ channels: 1, layout: "mono", mode: "stereo" }, "No direction."]]) {
    t.W.onCaptureInfoChanged(info(over));
    const ps = stParts();
    check(`${words} is a keep part`, ps.some(p => p[0] === "keep" && p[1] === words) && ps[0][0] === "cut", J(ps));
}
t.W.onCaptureInfoChanged(J({ active: true, device: "Speakers" }));
check("nothing known: Listening. kept whole", J(stParts()) === J([["keep", "Listening."]]), J(stParts()));
t.W.onCaptureInfoChanged(J({ active: true, layout: "stereo", channels: 2, source: "app", device: "x.exe", mode: "x" }));
check("words are spaced like plain text", t.els["capture-status"].innerHTML.includes("x.exe. </span>"),
    t.els["capture-status"].innerHTML);
t.W.onCaptureInfoChanged(info({ device: "<b>&\"", source: "app" }));
check("program names are escaped in the line", t.els["capture-status"].innerHTML.includes("&lt;b&gt;&amp;\"")
    && !t.els["capture-status"].innerHTML.includes("<b>"), t.els["capture-status"].innerHTML);

// The notes and short notes exactly as capture_state.py sends them. A short
// note plus the radar words does not fit 183 px, so note_short is never in the
// line: the line is the normal text, the full note is only in the title.
section("notes: the normal text with the radar words whole, the full note only in the title");
const MONO_NOTE = "Mono output is paused because it would play into the device the radar is listening to, "
    + "so pick a different mono output device.";
const SAVED_NOTE = "Your chosen audio device is not connected, so the radar is listening to the Windows "
    + "default device instead.";
const APP_NOTE = "Per-app capture is stereo only, so choose All (system audio) for full 360 degree direction.";
const STEREO_NOTE = "This device is set to stereo (2 channels) in Windows, so the radar can only show left and right.";
const SYS_STEREO = { source: "system", device: "Speakers (Realtek(R) Audio)", channels: 2, layout: "stereo", mode: "stereo" };
const NOTE_CASES = [
    // [name, payload fields, note, note_short, normal parts]
    ["mono guard on a stereo device", Object.assign({ mono_paused: true }, SYS_STEREO), MONO_NOTE, "Mono output paused.",
        [["cut", "Stereo."], ["keep", "Left and right only."]]],
    ["mono guard on 7.1 surround", { mono_paused: true }, MONO_NOTE, "Mono output paused.",
        [["cut", "7.1 surround."], ["keep", "Full 360."]]],
    ["saved device not connected", SYS_STEREO, SAVED_NOTE, "Using Windows default.",
        [["cut", "Stereo."], ["keep", "Left and right only."]]],
    ["per-app", { source: "app", device: "cs2.exe", channels: 2, layout: "stereo", mode: "stereo" }, APP_NOTE, "",
        [["cut", "cs2.exe."], ["keep", "Left and right only."]]],
    ["device set to stereo", SYS_STEREO, STEREO_NOTE, "",
        [["cut", "Stereo."], ["keep", "Left and right only."]]],
];
t = load();
t.W.onStatusChanged("Radar is active", true);
for (const [name, over, note, short, want] of NOTE_CASES) {
    t.W.onCaptureInfoChanged(info(Object.assign({ note, note_short: short }, over)));
    check(`${name}: normal text, "${want.map(x => x[1]).join(" ")}"`, J(stParts()) === J(want), J(stParts()));
    check(`${name}: radar words are a whole keep part`, stParts().some(p => p[0] === "keep" && p[1] === want[1][1]),
        J(stParts()));
    if (short) check(`${name}: note_short is not in the line`, !st().includes(short), st());
    check(`${name}: the long note is not in the line`, !st().includes(note), st());
    check(`${name}: full note last in the title`, stTitle().endsWith(" " + note), stTitle());
    check(`${name}: no fill part`, !t.els["capture-status"].innerHTML.includes("capture-part--fill"),
        t.els["capture-status"].innerHTML);
}
t.W.onCaptureInfoChanged(J({ active: true, note: SAVED_NOTE, note_short: "Using Windows default." }));
check("note_short with nothing else known: Listening., note in the title",
    J(stParts()) === J([["keep", "Listening."]]) && stTitle() === SAVED_NOTE, J(stParts()) + " / " + stTitle());
t.W.onCaptureInfoChanged(J({ active: false, note: SAVED_NOTE, note_short: "Using Windows default." }));
check("capture ended with a note_short: Not listening. alone, note in the title",
    J(stParts()) === J([["keep", "Not listening."]]) && stTitle() === SAVED_NOTE, J(stParts()) + " / " + stTitle());
t.W.onStatusChanged("Radar stopped", false);
check("stopped with a note_short: the stopped text alone, note in the title",
    J(stParts()) === J([["keep", "Starts when the radar runs."]]) && stTitle() === SAVED_NOTE, J(stParts()));
for (const [name, over, note, short] of NOTE_CASES) {
    t.W.onCaptureInfoChanged(J({ active: false, note, note_short: short, mono_paused: !!over.mono_paused }));
    check(`stopped, ${name}: stopped text, note in the title`,
        st() === "Starts when the radar runs." && stTitle() === note, st() + " / " + stTitle());
}
t.W.onCaptureInfoChanged(info({ note: "Some note", note_short: "Short." }));
t.W.onCaptureInfoChanged(info());
check("a payload without notes clears the title note", st() === "7.1 surround. Full 360." && !stTitle().includes("Some note"),
    st());

section("the short-note cases stay visible outside the line");
t = load();
sel = t.els["capture-device-select"];
t.W.onMonoStateChanged(J({ devices: ["Headphones (HyperX Cloud II)"], default: "Speakers", cable: "CABLE Input",
    enabled: true, selected: "Headphones (HyperX Cloud II)" }));
t.W.onStatusChanged("Radar is active", true);
t.W.onCaptureInfoChanged(info(Object.assign({ note: MONO_NOTE, note_short: "Mono output paused.", mono_paused: true },
    SYS_STEREO)));
const pausedState = () => (t.els["mono-hint"].innerHTML.match(/<span class="mono-hint-state" title="([^"]*)">([^<]*)<\/span>/)
    || []).slice(1);
check("mono guard: Paused in the Mono Output hint", pausedState()[1] === "Paused", J(pausedState()));
check("  with the full note as its title", unescape(pausedState()[0] || "") === MONO_NOTE, J(pausedState()));
check("  while the line keeps the radar words", st() === "Stereo. Left and right only.", st());
t.W.onCaptureInfoChanged(info(Object.assign({ note: MONO_NOTE, note_short: "Mono output paused.", mono_paused: true })));
check("mono guard on 7.1: Paused, line says Full 360.", pausedState()[1] === "Paused" && st() === "7.1 surround. Full 360.",
    J(pausedState()) + " / " + st());
t.W.onCaptureDevicesChanged(devs({ selected: "Sonar Gaming Headset", available: false }));
t.W.onCaptureInfoChanged(info(Object.assign({ note: SAVED_NOTE, note_short: "Using Windows default." }, SYS_STEREO)));
check("saved device missing: Not connected: <name> in the select",
    sel.value === "Sonar Gaming Headset" && sel.labels().includes("Not connected: Sonar Gaming Headset"), J(sel.labels()));
check("  while the line keeps the radar words", st() === "Stereo. Left and right only.", st());
check("  and the guard is over: the hint is On again", pausedState()[1] === "On - Headphones (HyperX Cloud II)",
    J(pausedState()));

section("older backend: note without note_short");
t = load();
const OLD = "Per-app capture is stereo only.";
t.W.onCaptureInfoChanged(info({ source: "app", device: "cs2.exe", channels: 2, layout: "stereo", mode: "stereo",
    note: OLD, note_short: undefined }));
// After the normal text only about 10 px are left, which showed a stray first
// letter; so the line keeps the normal text and the note stays in the title.
check("normal text, radar words kept whole",
    J(stParts()) === J([["cut", "cs2.exe."], ["keep", "Left and right only."]]), J(stParts()));
check("note in the title", stTitle().endsWith(" " + OLD), stTitle());
t.W.onCaptureInfoChanged(info({ note: "  Padded.  ", note_short: undefined }));
check("note is trimmed in the title", stTitle().endsWith(" Padded.") && st() === "7.1 surround. Full 360.", stTitle());
t.W.onCaptureInfoChanged(J({ active: false, note: OLD }));
check("stopped: stopped text, note in the title",
    J(stParts()) === J([["keep", "Starts when the radar runs."]]) && stTitle() === OLD, J(stParts()));
t.W.onCaptureInfoChanged(info({ note_short: 5, note: OLD }));
check("a non-string note_short counts as missing", st() === "7.1 surround. Full 360." && stTitle().endsWith(OLD), st());

// Python sends statusChanged(true) before the capture thread reports, so the
// line waits for the first captureInfoChanged since the start (no timers).
section("starting, running, not listening, stopped");
t = load();
t.W.onCaptureInfoChanged(info({ active: false }));
check("stopped: Starts when the radar runs.", st() === "Starts when the radar runs.", st());
t.W.onStatusChanged("Radar is active", true);
check("Start before any payload: Starting...", J(stParts()) === J([["keep", "Starting..."]]), J(stParts()));
check("  three plain dots", st() === "Starting..." && !st().includes(String.fromCharCode(0x2026)), st());
check("  with no stale note in the title", stTitle() === "", stTitle());
t.W.onCaptureInfoChanged("{");
check("a junk payload does not count as the capture reporting", st() === "Starting...", st());
t.W.onCaptureInfoChanged(info());
check("first active: true shows the status", st() === "7.1 surround. Full 360.", st());
t.W.onCaptureInfoChanged(info({ mode: "stereo" }));
check("later payloads update it", st() === "7.1. Waiting for surround.", st());
t.W.onStatusChanged("Audio device 'Sonar' failed - trying another output", true);
check("a status message while the capture runs keeps the status", st() === "7.1. Waiting for surround.", st());
t.W.onCaptureInfoChanged(info(SYS_STEREO));
check("the fallback device reports: its status", st() === "Stereo. Left and right only.", st());
t.W.onStatusChanged("All audio devices failed - stop and restart the radar", true);
t.W.onCaptureInfoChanged(J({ active: false, note: "", note_short: "" }));
check("active: false after that: Not listening.", J(stParts()) === J([["keep", "Not listening."]]), J(stParts()));
t.W.onStatusChanged("Radar stopped", false);
check("Stop: Starts when the radar runs.", st() === "Starts when the radar runs.", st());
t.W.onStatusChanged("Radar is active", true);
check("a later Start: Starting... again", st() === "Starting...", st());
t.W.onCaptureInfoChanged(info());
check("  then the status", st() === "7.1 surround. Full 360.", st());
t.W.onStatusChanged("Radar stopped", false);                   // stop without a final payload
t.W.onStatusChanged("Radar is active", true);
check("Start after a stop that left an active payload behind: Starting...", st() === "Starting...", st());
t.W.onStatusChanged("Radar stopped", false);
t.W.onCaptureInfoChanged(info());
check("a payload while stopped shows as before", st() === "7.1 surround. Full 360.", st());
t.W.onStatusChanged("Radar is active", true);
check("  but does not count for the next Start", st() === "Starting...", st());
t.W.onCaptureInfoChanged(J({ active: false }));
check("the capture fails before it ever ran: Not listening.", st() === "Not listening.", st());
check("statusChanged never calls the bridge", t.calls.length === 0, J(t.calls));

// main.py restarts the capture in place when the device or program changes:
// captureInfoChanged(active false), statusChanged(true), captureInfoChanged(active true).
section("capture restart while the radar stays active");
t = load();
t.W.onStatusChanged("Radar is active", true);
t.W.onCaptureInfoChanged(info());
t.W.onCaptureInfoChanged(J({ active: false }));
check("the old capture ends: Not listening. (one event, before the restart's statusChanged)",
    st() === "Not listening.", st());
t.W.onStatusChanged("Radar active - capturing cs2.exe", true);
check("the restart's statusChanged: Starting...", st() === "Starting...", st());
t.W.onCaptureInfoChanged(info({ source: "app", device: "cs2.exe", channels: 2, layout: "stereo", mode: "stereo" }));
check("the new capture reports: its status", st() === "cs2.exe. Left and right only.", st());
t.W.onStatusChanged("Radar is active", true);
check("a statusChanged(true) while the capture runs is not a restart", st() === "cs2.exe. Left and right only.", st());
t.W.onCaptureInfoChanged(J({ active: false }));
t.W.onStatusChanged("Radar is active", true);
t.W.onCaptureInfoChanged(J({ active: false }));
check("a restart whose capture fails: Not listening.", st() === "Not listening.", st());

section("the bridge path: capture signals, no payload yet");
t = load({ signals: ["statusChanged", "deviceChanged", "profilesChanged", "monitorsChanged", "presetsChanged",
    "overlayPositionChanged", "captureDevicesChanged", "captureInfoChanged"] });
t.signal("statusChanged", "Radar is active", true);
check("capture signals but no info yet: Starting...", st() === "Starting...", st());
t.signal("captureInfoChanged", info());
check("first payload: the status", st() === "7.1 surround. Full 360.", st());
t.signal("captureInfoChanged", J({ active: false }));
check("then active: false: Not listening.", st() === "Not listening.", st());
t.signal("statusChanged", "Radar stopped", false);
t.signal("statusChanged", "Radar is active", true);
check("Stop and Start: Starting...", st() === "Starting...", st());

section("older backend: the deviceChanged text is unaffected");
t = load();
t.W.onDeviceChanged("Speakers  (2ch)");
t.W.onStatusChanged("Radar active", true);
check("statusChanged(true) leaves the deviceChanged text", st() === "2 ch: Speakers", st());
t.W.onStatusChanged("Radar stopped", false);
check("statusChanged(false) leaves it too", st() === "2 ch: Speakers", st());
t = load({ signals: ["statusChanged", "deviceChanged", "profilesChanged", "monitorsChanged", "presetsChanged",
    "overlayPositionChanged"], slots: ["request_initial_data"] });
t.signal("statusChanged", "Radar active", true);
check("no capture signals: Start does not show Starting...", st() === "Starts when the radar runs.", st());
t.signal("deviceChanged", "Speakers (Realtek(R) Audio)  (2ch)");
check("deviceChanged writes the line while running", st() === "2 ch: Speakers (Realtek(R) Audio)", st());
t.signal("statusChanged", "Radar stopped", false);
t.signal("statusChanged", "Radar active", true);
check("Stop and Start keep the deviceChanged text", st() === "2 ch: Speakers (Realtek(R) Audio)", st());

section("partial and unknown captureInfoChanged payloads");
t = load();
infoCase("unknown layout, 12 channels, surround", info({ layout: "9.1.4", channels: 12 }), "12 ch. Full 360.",
    s => s.includes("Receiving 12 channel sound."));
infoCase("unknown layout, 12 channels, stereo mode", info({ layout: "9.1.4", channels: 12, mode: "stereo" }),
    "12 ch. Waiting for surround.");
infoCase("no layout, 2 channels", info({ layout: undefined, channels: 2, mode: "stereo" }), "Stereo. Left and right only.");
infoCase("no layout, 1 channel", info({ layout: undefined, channels: 1, mode: "stereo" }), "Mono. No direction.");
infoCase("unknown mode treated as not surround", info({ mode: "atmos" }), "7.1. Waiting for surround.");
infoCase("nothing but active", J({ active: true }), "Listening.", s => s === "Listening.");
infoCase("active and mode only", J({ active: true, mode: "surround" }), "Full 360.");
infoCase("active and a device only", J({ active: true, device: "Speakers" }), "Listening.",
    s => s === "Listening to Speakers.");
infoCase("unknown source treated as system", info({ source: "cloud" }), "7.1 surround. Full 360.");
infoCase("active as a string is not active", info({ active: "true" }), "Starts when the radar runs.");
infoCase("wrong types everywhere", J({ active: true, source: 1, device: 2, channels: "8", layout: 7.1, mode: null,
    note: 5, note_short: 6, mono_paused: "yes" }), "Listening.");
infoCase("layout name that is an Object key", info({ layout: "toString", channels: 0, mode: "stereo" }),
    "Left and right only.");
t.W.onCaptureInfoChanged(info());
for (const bad of ["", "{", "null", "[]", "42", "\"x\"", undefined, null, true]) {
    let threw = null;
    try { t.W.onCaptureInfoChanged(bad); } catch (e) { threw = e; }
    check(`${J(bad)} does not throw`, threw === null, String(threw));
}
check("junk keeps what is showing", st() === "7.1 surround. Full 360.", st());
check("pure helper never throws on junk", (() => {
    try {
        return [null, undefined, 5, "{", [], {}].every(x => typeof t.W.captureStatus(x).text === "string"
            && Array.isArray(t.W.captureStatus(x, true).parts));
    } catch (e) { return false; }
})());

// ── 6. deviceChanged fallback ──────────────────────────────────────────
section("deviceChanged against an older backend (no capture signals)");
t = load({ signals: ["statusChanged", "deviceChanged", "profilesChanged", "monitorsChanged", "presetsChanged",
    "overlayPositionChanged"], slots: ["request_initial_data"] });
check("capture signals not connected", !t.connected("captureInfoChanged") && !t.connected("captureDevicesChanged"));
t.signal("deviceChanged", "Speakers (Realtek(R) Audio)  (2ch)");
check("status line shows channels and name", shown(t.els["capture-status"]) === "2 ch: Speakers (Realtek(R) Audio)",
    shown(t.els["capture-status"]));
check("brackets inside the name survive", t.els["capture-status"].attrs.title === "2 ch: Speakers (Realtek(R) Audio)",
    t.els["capture-status"].attrs.title);
t.signal("deviceChanged", "Plain name");
check("no channel suffix: the label as given", shown(t.els["capture-status"]) === "Plain name");
let threw = null;
try { t.W.onDeviceChanged(undefined); t.W.onDeviceChanged(42); } catch (e) { threw = e; }
check("non-string label does not throw", threw === null, String(threw));
t.W.AR.setCaptureDevice("X");
t.W.AR.refreshCaptureDevices();
check("missing slots are skipped quietly", t.calls.length === 0, J(t.calls));

section("Windows default only before any device list");
t = load({ domReady: true });
sel = t.els["capture-device-select"];
check("bootstrap leaves Windows default only", J(sel.labels()) === J(["Windows default"]) && sel.value === "",
    J(sel.labels()));
check("with its title", /Follows the sound output/.test(sel.attrs.title || ""), sel.attrs.title);

section("deviceChanged ignored once the backend has the capture signals");
const NEW_SIGNALS = ["statusChanged", "deviceChanged", "profilesChanged", "monitorsChanged", "presetsChanged",
    "overlayPositionChanged", "captureDevicesChanged", "captureInfoChanged"];
t = load({ signals: NEW_SIGNALS });
check("both signals connected", t.connected("captureInfoChanged") && t.connected("captureDevicesChanged"));
t.signal("deviceChanged", "Speakers  (2ch)");
check("deviceChanged before any capture info: ignored", shown(t.els["capture-status"]) === "Starts when the radar runs.",
    shown(t.els["capture-status"]));
t.signal("captureInfoChanged", info());
t.signal("deviceChanged", "Speakers  (2ch)");
check("deviceChanged after capture info: ignored", shown(t.els["capture-status"]) === "7.1 surround. Full 360.",
    shown(t.els["capture-status"]));
t.signal("captureDevicesChanged", devs({ selected: "SteelSeries Sonar - Gaming" }));
check("captureDevicesChanged reaches the select", t.els["capture-device-select"].value === "SteelSeries Sonar - Gaming");
check("still no bridge call", t.calls.length === 0, J(t.calls));

t = load();
t.W.onCaptureInfoChanged(info({ active: false }));
t.W.onDeviceChanged("Speakers  (2ch)");
check("the first captureInfoChanged takes over even without initBridge",
    shown(t.els["capture-status"]) === "Starts when the radar runs.", shown(t.els["capture-status"]));

// ── 7. Profiles ────────────────────────────────────────────────────────
const AUDIO = { sensitivity: 50, gain: 10, freq_low: 150, freq_high: 4000, max_amp: 100 };
function savedProfile(c) {
    const s = c.filter(x => x[0] === "save_profile");
    return s.length ? JSON.parse(s[s.length - 1][1]) : null;
}
const captureCalls = c => c.filter(x => x[0] === "set_capture_device");

section("addPreset stores capture_device");
t = load();
sel = t.els["capture-device-select"];
t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
t.W.AR.addPreset();
let p = savedProfile(t.calls);
check("stored as the device name", p && p.capture_device === "SteelSeries Sonar - Gaming", p && J(p.capture_device));
t.W.onCaptureDevicesChanged(devs({ selected: "" }));
t.W.AR.addPreset();
p = savedProfile(t.calls);
check("Windows default stored as \"\"", p && p.capture_device === "", p && J(p.capture_device));
t.W.onCaptureDevicesChanged(devs({ selected: "Sonar Gaming", available: false }));
t.W.AR.addPreset();
p = savedProfile(t.calls);
check("a not connected choice is stored by name", p && p.capture_device === "Sonar Gaming", p && J(p.capture_device));

section("applyProfileValues with capture_device");
t = load();
sel = t.els["capture-device-select"];
t.W.onCaptureDevicesChanged(devs());
t.W.applyProfileValues(Object.assign({ capture_device: "SteelSeries Sonar - Gaming" }, AUDIO));
check("sent once as a string", J(captureCalls(t.calls)) === J([["set_capture_device", "SteelSeries Sonar - Gaming"]]),
    J(captureCalls(t.calls)));
check("select shows it", sel.value === "SteelSeries Sonar - Gaming", sel.value);
t.calls.length = 0;
t.W.applyProfileValues(Object.assign({ capture_device: "" }, AUDIO));
check("\"\" is applied (Windows default)", J(captureCalls(t.calls)) === J([["set_capture_device", ""]]) && sel.value === "",
    J(captureCalls(t.calls)));
t.calls.length = 0;
t.W.applyProfileValues(Object.assign({ capture_device: "Old Headset" }, AUDIO));
check("a device not plugged in is applied", J(captureCalls(t.calls)) === J([["set_capture_device", "Old Headset"]]));
check("and shown as not connected", sel.value === "Old Headset" && sel.labels().includes("Not connected: Old Headset"),
    J(sel.labels()));
t.W.onCaptureDevicesChanged(devs({ selected: "Old Headset", available: false }));
check("Python's echo keeps it", sel.value === "Old Headset" && sel.options.length === 5, J(sel.labels()));

section("applyProfileValues without capture_device or with the wrong type");
for (const [label, prof] of [["absent", {}], ["null", { capture_device: null }], ["number", { capture_device: 3 }],
    ["true", { capture_device: true }], ["object", { capture_device: { name: "x" } }], ["array", { capture_device: ["x"] }]]) {
    t = load();
    sel = t.els["capture-device-select"];
    t.W.onCaptureDevicesChanged(devs({ selected: "SteelSeries Sonar - Gaming" }));
    t.calls.length = 0;
    t.W.applyProfileValues(Object.assign(prof, AUDIO));
    check(`${label}: no set_capture_device`, captureCalls(t.calls).length === 0, J(captureCalls(t.calls)));
    check(`${label}: select untouched`, sel.value === "SteelSeries Sonar - Gaming" && sel.options.length === 4, sel.value);
    check(`${label}: the rest still applied`, t.calls.some(c => c[0] === "set_sensitivity"), J(t.calls));
}

// ── 8. Without a bridge (local preview) ────────────────────────────────
section("without a bridge");
t = load({ noBridge: true });
sel = t.els["capture-device-select"];
threw = null;
try {
    t.W.AR.setCaptureDevice("SteelSeries Sonar - Gaming");
    t.W.AR.refreshCaptureDevices();
    t.W.onCaptureDevicesChanged(devs());
    t.W.onCaptureInfoChanged(info());
    t.W.onDeviceChanged("Speakers  (2ch)");
    t.W.renderCaptureDevices();
} catch (e) { threw = e; }
check("no exception", threw === null, String(threw));
check("select and status still render", sel.options.length === 4 && shown(t.els["capture-status"]) === "7.1 surround. Full 360.",
    shown(t.els["capture-status"]));
t = load({ noBridge: true, domReady: true });
check("local preview bootstrap: Windows default only",
    J(t.els["capture-device-select"].labels()) === J(["Windows default"]), J(t.els["capture-device-select"].labels()));

// ── 9. Mono hint stays one line ────────────────────────────────────────
section("mono hint");
t = load();
t.W.onMonoStateChanged(J({ devices: ["Headphones <b>"], default: "Speakers", cable: "", enabled: true,
    selected: "Headphones <b>" }));
const hint = t.els["mono-hint"].innerHTML;
check("state in its own span with the full text as title",
    /<span class="mono-hint-state" title="On - Headphones &lt;b&gt;">On - Headphones &lt;b&gt;<\/span>/.test(hint), hint);
check("Setup link still there", /class="mono-setup-link"[^>]*>Setup<\/a>$/.test(hint), hint);
check("index.html starts with the same structure", /<span class="mono-hint-state" title="Off">Off<\/span>/.test(html));

section("mono hint while the mono feedback guard is active");
const MONO_ON = { devices: ["Headphones (HyperX Cloud II)"], default: "Speakers", cable: "CABLE Input",
    enabled: true, selected: "Headphones (HyperX Cloud II)" };
const GUARD = "Windows mono audio is on, so mono playback was skipped to avoid an echo.";
const hintNow = () => t.els["mono-hint"].innerHTML;
const hintState = () => (hintNow().match(/<span class="mono-hint-state" title="([^"]*)">([^<]*)<\/span>/) || []).slice(1);
const SETUP = /<\/span> <a href="#" class="mono-setup-link" onclick="AR\.openMonoSetup\(\); return false;">Setup<\/a>$/;
t = load();
t.W.onMonoStateChanged(J(MONO_ON));
const normalHint = hintNow();
check("before any capture info: On - device",
    J(hintState()) === J(["On - Headphones (HyperX Cloud II)", "On - Headphones (HyperX Cloud II)"]), J(hintState()));
t.W.onCaptureInfoChanged(info({ note: GUARD, note_short: "Mono output paused.", mono_paused: true }));
check("mono_paused true: Paused", hintState()[1] === "Paused", J(hintState()));
check("  with the full note in its title", unescape(hintState()[0]) === GUARD, J(hintState()));
check("  Setup link kept", SETUP.test(hintNow()), hintNow());
t.W.onMonoStateChanged(J(MONO_ON));
check("a monoStateChanged meanwhile keeps Paused", hintState()[1] === "Paused", J(hintState()));
t.W.onCaptureInfoChanged(info({ mono_paused: true, note: "" }));
check("Paused without a note: a plain title", J(hintState()) === J(["Mono output is paused.", "Paused"]), J(hintState()));
t.W.onCaptureInfoChanged(info({ mono_paused: false }));
check("mono_paused false: exactly as before", hintNow() === normalHint, hintNow());
t.W.onCaptureInfoChanged(info({ mono_paused: true, note: GUARD }));
t.W.onCaptureInfoChanged(info({ mono_paused: undefined }));
check("mono_paused missing: exactly as before", hintNow() === normalHint, hintNow());
t.W.onCaptureInfoChanged(info({ mono_paused: "true" }));
check("mono_paused as a string: exactly as before", hintNow() === normalHint, hintNow());
t.W.onCaptureInfoChanged(info({ mono_paused: true, note: GUARD }));
t.W.onMonoStateChanged(J(Object.assign({}, MONO_ON, { enabled: false })));
check("mono switched off: Off, not Paused", hintState()[1] === "Off", J(hintState()));
t.W.onCaptureInfoChanged("{");
t.W.onMonoStateChanged(J(MONO_ON));
check("a junk capture payload keeps the guard state", hintState()[1] === "Paused", J(hintState()));
t = load();
t.W.onCaptureInfoChanged(info({ mono_paused: true, note: GUARD }));
check("guard before any mono state: hint left as it is", hintNow() === "", hintNow());
t.W.onMonoStateChanged(J(MONO_ON));
check("then Paused as soon as the mono state arrives", hintState()[1] === "Paused", J(hintState()));
t.W.onCaptureInfoChanged(info({ mono_paused: true, note: "Quote \" and <tag> & more" }));
check("note in the title is escaped", hintNow().includes('title="Quote &quot; and &lt;tag&gt; &amp; more"'), hintNow());
check("hint calls nothing", t.calls.length === 0, J(t.calls));

section("virtual cable name is escaped");
t = load();
const CABLE = "CABLE <Input> & \"Mix\"";
t.W.onMonoStateChanged(J({ devices: [], default: "", cable: CABLE, enabled: false, selected: "" }));
const ms = t.els["mono-status"].innerHTML;
check("no raw markup from the name", !ms.includes("<Input>") && ms.includes("CABLE &lt;Input&gt; &amp; \"Mix\""), ms);
check("reads back as the name", unescape(ms.replace(/<[^>]*>/g, "")) === "Virtual cable detected: " + CABLE,
    unescape(ms.replace(/<[^>]*>/g, "")));

// ── 10. House rules (all of dashboard_v2) ──────────────────────────────
section("house rules");
const css = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "style.css"), "utf8");
const code = src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
check("no timers or animation loops", !/\b(setTimeout|setInterval|requestAnimationFrame)\s*\(/.test(code));
check("no CSS animations", !/@keyframes|\banimation\s*:/.test(css));
const DASHES = [String.fromCharCode(0x2014), String.fromCharCode(0x2013)];
for (const [name, text] of [["script.js", src], ["index.html", html], ["style.css", css],
    ["this test file", fs.readFileSync(__filename, "utf8")]]) {
    check(`no em or en dashes in ${name}`, !DASHES.some(d => text.includes(d)));
}
check("the fill part is gone from the CSS and the script",
    !css.includes("capture-part--fill") && !/fit:\s*"fill"/.test(code));
check("note_short is never read for the line", !/\.note_short\b/.test(code));
check("no new colours in the capture CSS",
    !/#[0-9a-f]{3,8}\b|rgba?\(/i.test((css.match(/\.capture-status[\s\S]*?\}/) || [""])[0]));

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
