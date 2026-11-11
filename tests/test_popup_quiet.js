// A dropdown's whole lifetime is quiet: dashboard_v2/script.js, "Quiet dropdowns".
//
// Run:  node tests/test_popup_quiet.js       (exit 0 = pass)
//
// QtWebEngine draws a <select> popup as a native Qt window, and changing the page
// while it is opening or open grows that window (blank white space beside and
// under the entries). These tests load the real script.js against a DOM stub that
// records EVERY write (text, HTML, attributes, classes, styles, values, options,
// canvas) and every bridge call, then walk each dropdown through what the page
// sees while its popup lives: the window focus that comes with the click, the
// press, the popup's focus churn, signals from Python arriving while it is open,
// and a dropdown opened right after another one. While a popup lives there must
// be zero writes and zero bridge calls; once it closes, everything held back
// lands, in order, with the player's pick kept, and only then do the refreshes
// the press asked for run.
//
// A popup closed without a change (Esc) leaves no page event, so the later
// sections cover what keeps that harmless: the click that ends it never does
// the opposite of what the player saw, a hidden page or a deactivated
// application ends it, and runs of "latest state" signals collapse.
//
// Same harness style as test_capture_device_ui.js (Node 22, no npm packages).
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..", "dashboard_v2");
const src = fs.readFileSync(path.join(ROOT, "script.js"), "utf8");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

// ── Recording DOM ──────────────────────────────────────────────────────
let writes = [];          // every DOM write, "id what"
let recording = true;
function rec(id, what) { if (recording) writes.push(`${id} ${what}`); }
// Run `fn` without recording (the test itself acting as the player).
function asPlayer(fn) { recording = false; try { return fn(); } finally { recording = true; } }

class ClassList {
    constructor(owner) { this.owner = owner; this.set = new Set(); }
    toggle(c, force) {
        const on = force === undefined ? !this.set.has(c) : !!force;
        if (on !== this.set.has(c)) rec(this.owner.id, `class ${c}`);
        if (on) this.set.add(c); else this.set.delete(c);
        return on;
    }
    add(c) { this.toggle(c, true); }
    remove(c) { this.toggle(c, false); }
    contains(c) { return this.set.has(c); }
}

function styleOf(owner) {
    const store = {};
    return new Proxy(store, {
        get(t, k) {
            if (k === "setProperty") return (name, v) => { rec(owner.id, `style ${name}`); t[name] = v; };
            return t[k];
        },
        set(t, k, v) { rec(owner.id, `style ${String(k)}`); t[k] = v; return true; },
    });
}

class El {
    constructor(id, tag) {
        this.id = id; this.tagName = tag || "DIV";
        this._text = ""; this._html = ""; this._value = ""; this._checked = false;
        this.attrs = {}; this.classList = new ClassList(this); this.style = styleOf(this);
        this.dataset = { min: "20", max: "20000" };
        this.width = 140; this.height = 140;
    }
    get textContent() { return this._text; }
    set textContent(v) { rec(this.id, "text"); this._text = String(v); }
    get innerHTML() { return this._html; }
    set innerHTML(v) { rec(this.id, "html"); this._html = String(v); }
    get value() { return this._value; }
    set value(v) { rec(this.id, "value"); this._value = String(v); }
    get checked() { return this._checked; }
    set checked(v) { rec(this.id, "checked"); this._checked = !!v; }
    setAttribute(k, v) { rec(this.id, `attr ${k}`); this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k]; }
    appendChild(c) { rec(this.id, "append"); return c; }
    addEventListener() {}
    removeEventListener() {}
    querySelector(q) { return this._sub || (this._sub = new El(`${this.id}>${q}`)); }
    focus() { doc.activeElement = this; }
    getContext() {
        const id = this.id;
        return new Proxy({}, {
            get(_t, k) { return (...a) => rec(id, `draw ${String(k)}`); },
            set(_t, k) { rec(id, `draw ${String(k)}`); return true; },
        });
    }
}

class Opt extends El {
    constructor() { super("option", "OPTION"); this.parentNode = null; }
}

class Select extends El {
    constructor(id) { super(id, "SELECT"); this.options = []; this._idx = -1; }
    set innerHTML(v) { rec(this.id, "html"); if (v === "") { this.options = []; this._idx = -1; } }
    get innerHTML() { return ""; }
    appendChild(o) { rec(this.id, "append"); o.parentNode = this; this.options.push(o); if (this._idx === -1) this._idx = 0; return o; }
    get value() { return this._idx >= 0 && this.options[this._idx] ? this.options[this._idx].value : ""; }
    set value(v) { rec(this.id, "value"); this._idx = this.options.findIndex(o => String(o.value) === String(v)); }
    get selectedIndex() { return this._idx; }
    set selectedIndex(i) { rec(this.id, "selectedIndex"); this._idx = i; }
    values() { return this.options.map(o => o.value); }
    labels() { return this.options.map(o => o.textContent); }
}

// Event listeners with capture flags, fired the way the page would.
function listenerTarget() {
    const list = [];
    const cap = o => (typeof o === "boolean" ? o : !!(o && o.capture));
    return {
        list,
        addEventListener(type, fn, o) { list.push({ type, fn, capture: cap(o) }); },
        removeEventListener(type, fn, o) {
            const i = list.findIndex(l => l.type === type && l.fn === fn && l.capture === cap(o));
            if (i >= 0) list.splice(i, 1);
        },
        fire(type, ev, capture) {
            list.filter(l => l.type === type && (capture === undefined || l.capture === capture))
                .forEach(l => l.fn(ev));
        },
    };
}

// Every select in index.html, with its inline onchange (run like the browser does).
const SELECTS = {};
for (const m of html.matchAll(/<select\b[^>]*>/g)) {
    const tag = m[0];
    const id = (tag.match(/\bid="([^"]+)"/) || [])[1];
    SELECTS[id] = { tag, onchange: (tag.match(/\bonchange="([^"]+)"/) || [])[1] || "" };
}
const SELECT_IDS = Object.keys(SELECTS);

let doc, win, els, bridge, calls, handlers, W, pendingReplies;
function load(opts = {}) {
    els = {};
    for (const id of SELECT_IDS) els[id] = new Select(id);
    els["preview-canvas"] = new El("preview-canvas", "CANVAS");
    doc = listenerTarget();
    win = listenerTarget();
    calls = [];
    handlers = {};
    pendingReplies = [];
    const SIGNALS = ["statusChanged", "deviceChanged", "profilesChanged", "monitorsChanged", "presetsChanged",
        "programsChanged", "overlayPositionChanged", "monoStateChanged", "updateAvailable", "appearanceChanged",
        "selectedPresetChanged", "audioSettingsChanged", "hotkeyChanged", "captureDevicesChanged",
        "captureInfoChanged"].concat(opts.noAppDeactivated ? [] : ["appDeactivated"]);
    const target = {};
    for (const s of SIGNALS) target[s] = { connect(fn) { handlers[s] = fn; } };
    bridge = new Proxy(target, {
        get(t, name) {
            if (name === "then") return undefined;
            if (name in t) return t[name];
            return (...args) => {
                calls.push([String(name), ...args.filter(a => typeof a !== "function")]);
                const cb = args.find(a => typeof a === "function");
                if (cb) cb("9.9.9");
                // What main.py answers (queued: QWebChannel delivers it later).
                if (name === "refresh_programs") pendingReplies.push(["programsChanged", J(PROGRAMS_NOW)]);
                if (name === "refresh_capture_devices") pendingReplies.push(["captureDevicesChanged", devs()]);
            };
        },
        has() { return true; },
    });
    const sandbox = {
        console,
        document: {
            activeElement: null,
            hidden: false,
            getElementById(id) { if (!(id in els)) els[id] = new El(id); return els[id]; },
            createElement() { return new Opt(); },
            addEventListener(type, fn, o) { if (type !== "DOMContentLoaded") doc.addEventListener(type, fn, o); },
            removeEventListener(type, fn, o) { doc.removeEventListener(type, fn, o); },
            hasFocus() { return true; },
        },
        window: null,
        addEventListener: (t, f, o) => win.addEventListener(t, f, o),
        removeEventListener: (t, f, o) => win.removeEventListener(t, f, o),
        qt: { webChannelTransport: {} },
        QWebChannel: function (_t, cb) { cb({ objects: { bridge } }); },
        prompt: () => "Quiet test",
        alert() {},
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    sandbox.initBridge();
    W = sandbox;
    doc.ref = sandbox.document;
    Object.defineProperty(doc, "activeElement", {
        get() { return sandbox.document.activeElement; },
        set(v) { sandbox.document.activeElement = v; },
    });
    // Initial data, as request_initial_data sends it, with no popup open.
    signal("monitorsChanged", J([{ idx: 0, name: "\\\\.\\DISPLAY1" }]));
    signal("selectedPresetChanged", "All Sounds");
    signal("profilesChanged", J({ "My CS2": { freq_low: 200, freq_high: 900 } }));
    signal("presetsChanged", J(["All Sounds", "Footsteps - CS2", "Custom"]));
    PROGRAMS_NOW = ["cs2.exe", "Discord.exe"];
    signal("programsChanged", J(PROGRAMS_NOW));
    signal("monoStateChanged", mono(["Headphones"], false, ""));
    DEVICES_NOW = DEVICES.slice(0, 2);
    signal("captureDevicesChanged", devs());
    signal("captureInfoChanged", J({ active: false }));
    signal("hotkeyChanged", J({ combo: "Ctrl+Shift+H", effective: "Ctrl+Shift+H", state: "ok", can_choose: true }));
    signal("statusChanged", "Radar stopped", false);
    calls.length = 0;
    writes = [];
}

const J = JSON.stringify;
const DEVICES = [
    { name: "Speakers (Realtek(R) Audio)", channels: 2, is_default: true },
    { name: "SteelSeries Sonar - Gaming", channels: 8, is_default: false },
    { name: "Headphones (HyperX Cloud II Wireless Gaming Headset)", channels: 2, is_default: false },
];
let DEVICES_NOW = DEVICES.slice(0, 2);
let PROGRAMS_NOW = ["cs2.exe", "Discord.exe"];
let SELECTED_DEVICE = "";
const devs = () => J({ devices: DEVICES_NOW, selected: SELECTED_DEVICE, available: true });
const mono = (devices, enabled, selected) => J({ devices, default: "Speakers", cable: "", enabled, selected });

function signal(name, ...args) {
    if (!handlers[name]) throw new Error("not connected: " + name);
    handlers[name](...args);
}
// Deliver Python's replies to the slots called so far.
function deliverReplies() {
    const r = pendingReplies.splice(0);
    r.forEach(([name, ...args]) => signal(name, ...args));
    return r.length;
}

// ── What the page sees ─────────────────────────────────────────────────
function windowFocus() { win.fire("focus", {}); }
function windowBlur() { win.fire("blur", {}); }
// The press that opens a select's popup: the window is focused by the click
// first (when it was not active), then pointerdown, mousedown, focus.
function press(sel, opts = {}) {
    if (opts.windowWasInactive) windowFocus();
    doc.fire("pointerdown", { target: sel, button: 0 }, true);
    doc.fire("mousedown", { target: sel, button: 0 }, true);
    doc.activeElement = sel;
    doc.fire("focus", { target: sel }, true);
}
// The popup window taking focus and handing it back, twice.
function churn(sel) {
    for (let i = 0; i < 2; i++) {
        doc.fire("blur", { target: sel, relatedTarget: null }, true);
        windowBlur();
        windowFocus();
        doc.fire("focus", { target: sel }, true);
    }
}
// Opening with the keyboard: Alt+Down on a focused select.
function keyOpen(sel) {
    doc.activeElement = sel;
    doc.fire("keydown", { target: sel, key: "ArrowDown", altKey: true, keyCode: 40 }, true);
}
// The player picks `value`: change (capture), the select's inline onchange, change (bubble).
function pick(sel, value) {
    asPlayer(() => { sel.value = value; });
    doc.fire("change", { target: sel }, true);
    const code = SELECTS[sel.id].onchange;
    if (code) vm.runInContext(`(function () { ${code} })`, W).call(sel);
    doc.fire("change", { target: sel }, false);
}
// The player clicks somewhere else on the page (the page closes a popup first).
function clickElsewhere() {
    const other = els["btn-toggle"] || (els["btn-toggle"] = new El("btn-toggle", "BUTTON"));
    doc.fire("pointerdown", { target: other, button: 0 }, true);
    doc.fire("mousedown", { target: other, button: 0 }, true);
}

// Every signal Python can send, each with a payload that changes something.
function allSignalsChangingEverything() {
    PROGRAMS_NOW = ["cs2.exe", "Discord.exe", "valorant.exe"];
    DEVICES_NOW = DEVICES.slice();
    signal("statusChanged", "Radar is active", true);
    signal("captureInfoChanged", J({ active: true, source: "system", device: "SteelSeries Sonar - Gaming",
        channels: 8, layout: "7.1", mode: "surround", note: "", mono_paused: false }));
    signal("deviceChanged", "SteelSeries Sonar - Gaming  (8ch)");
    signal("monoStateChanged", mono(["Headphones", "CABLE Input"], true, "Headphones"));
    signal("programsChanged", J(PROGRAMS_NOW));
    signal("captureDevicesChanged", devs());
    signal("monitorsChanged", J([{ idx: 0, name: "\\\\.\\DISPLAY1" }, { idx: 1, name: "\\\\.\\DISPLAY2" }]));
    signal("profilesChanged", J({ "My CS2": { freq_low: 200 }, "Valorant": { freq_low: 300 } }));
    signal("presetsChanged", J(["All Sounds", "Footsteps - CS2", "Custom", "Gunshots"]));
    signal("audioSettingsChanged", J({ sensitivity: 0.01, gain: 2, freq_low: 300, freq_high: 3000, max_amp: 0.5 }));
    signal("appearanceChanged", J({ color: "#FF0000", thickness: 9, overlay_size: 120, alert_opacity: 60,
        outline_enabled: false }));
    signal("hotkeyChanged", J({ combo: "F9", effective: "F9", state: "ok" }));
    signal("overlayPositionChanged", J({ x: 10, y: 20, drag_enabled: false }));
    signal("updateAvailable", "9.9.9", "https://example.invalid/release");
}

// ── Assertions ─────────────────────────────────────────────────────────
let failed = 0;
function check(name, cond, extra) {
    if (cond) { console.log("  PASS  " + name); }
    else { failed++; console.log("  FAIL  " + name + (extra ? "  -> " + extra : "")); }
}
function section(t) { console.log("\n" + t); }
const short = a => J(a.slice(0, 12)) + (a.length > 12 ? ` (+${a.length - 12})` : "");

// What a pick sends to Python, per select (from index.html's onchange).
const PICKS = {
    "monitor-select": { value: "0", call: ["set_monitor", 0] },
    "capture-device-select": { value: "SteelSeries Sonar - Gaming",
        call: ["set_capture_device", "SteelSeries Sonar - Gaming"] },
    "program-select": { value: "Discord.exe", call: ["set_program", "Discord.exe"] },
    "preset-select": { value: "Footsteps - CS2", call: ["set_selected_preset", "Footsteps - CS2"] },
    "mono-output-select": { value: "Headphones", call: ["set_mono_output", "Headphones"] },
};
const REFRESH_FOR = { "program-select": "refresh_programs", "capture-device-select": "refresh_capture_devices" };

section("index.html: no select reacts to being opened");
check("every dropdown is covered", J(SELECT_IDS.slice().sort()) === J(Object.keys(PICKS).sort()), J(SELECT_IDS));
for (const id of SELECT_IDS) {
    check(`#${id} has only an onchange handler`, !/\bon(?!change)[a-z]+=/.test(SELECTS[id].tag), SELECTS[id].tag);
}

// ── 1. Every dropdown, every signal, the full opening sequence ─────────
for (const id of SELECT_IDS) {
    section(`#${id}: quiet from press to pick`);
    load();
    const sel = els[id];
    if (id === "mono-output-select") asPlayer(() => W.AR.openMonoSetup());

    press(sel, { windowWasInactive: true });
    churn(sel);
    allSignalsChangingEverything();
    doc.fire("pointermove", { target: sel }, true);     // even if a move got through
    churn(sel);
    check("zero bridge calls while the popup lives", calls.length === 0, J(calls));
    check("zero page writes while the popup lives", writes.length === 0, short(writes));
    check("the select still has its old options", sel.options.length > 0 && writes.length === 0);

    pick(sel, PICKS[id].value);
    // Held-back work landed.
    check("radar status landed", els["status-text"].textContent === "Radar is active", els["status-text"].textContent);
    check("capture status landed", /Full 360/.test(els["capture-status"].innerHTML), els["capture-status"].innerHTML);
    check("Program list landed", els["program-select"].values().includes("valorant.exe"), J(els["program-select"].values()));
    check("Audio Device list landed", els["capture-device-select"].options.length === 4,
        J(els["capture-device-select"].values()));
    check("Monitor list landed", els["monitor-select"].options.length === 2, J(els["monitor-select"].values()));
    check("Preset list landed", els["preset-select"].values().includes("Gunshots")
        && els["preset-select"].values().includes("Valorant"), J(els["preset-select"].values()));
    check("Mono Output list and hint landed", els["mono-output-select"].values().includes("CABLE Input")
        && /On - Headphones/.test(els["mono-hint"].innerHTML), els["mono-hint"].innerHTML);
    check("hotkey row landed", els["hotkey-btn"].textContent === "F9", els["hotkey-btn"].textContent);
    check("update notice landed", /9\.9\.9/.test(els["update-notice-text"].textContent));
    check("look and sliders landed", els["thickness-val"].textContent === "9 px"
        && els["freq-val"].textContent === "300-3000 Hz");
    check("the player's pick is showing", sel.value === PICKS[id].value, sel.value);
    // Python hears the pick first, then (and only then) the refresh the press asked for.
    const first = calls.findIndex(c => J(c) === J(PICKS[id].call));
    check("the pick went to Python", first >= 0, J(calls));
    const refreshes = calls.filter(c => /^refresh_/.test(c[0]));
    if (REFRESH_FOR[id]) {
        const at = calls.findIndex(c => c[0] === REFRESH_FOR[id]);
        check(`${REFRESH_FOR[id]} once, after the pick`, at > first
            && calls.filter(c => c[0] === REFRESH_FOR[id]).length === 1, J(calls));
    }
    // The window focus of the click asked for a program refresh: it runs now too, once.
    check("the click's window focus refresh ran once, after close",
        calls.filter(c => c[0] === "refresh_programs").length === 1, J(refreshes));
    check("no refresh other than those", refreshes.every(c => c[0] === "refresh_programs" || c[0] === REFRESH_FOR[id]),
        J(refreshes));
}

// ── 2. Pick wins over a rebuild of the same select that was held back ──
section("the pick wins over a held-back rebuild of the open select");
load();
{
    const cap = els["capture-device-select"];
    press(cap);
    SELECTED_DEVICE = "";                                   // Python still says Windows default
    DEVICES_NOW = DEVICES.slice();
    signal("captureDevicesChanged", devs());
    check("not rebuilt while open", cap.options.length === 3 && writes.length === 0, short(writes));
    pick(cap, "SteelSeries Sonar - Gaming");
    check("rebuilt after close with the new device", cap.options.length === 4, J(cap.values()));
    check("the pick is kept, not Python's older choice", cap.value === "SteelSeries Sonar - Gaming", cap.value);
    check("Python got the pick", calls.some(c => J(c) === J(["set_capture_device", "SteelSeries Sonar - Gaming"])),
        J(calls));
    check("title follows the pick", /SteelSeries/.test(cap.attrs.title || ""), cap.attrs.title);
}

// ── 3. One select opened right after another one closed ────────────────
section("Audio Device opened right after Program was picked");
load();
{
    const prog = els["program-select"], cap = els["capture-device-select"];
    press(prog);
    churn(prog);
    PROGRAMS_NOW = ["cs2.exe", "Discord.exe", "valorant.exe"];   // a game started since
    pick(prog, "cs2.exe");
    check("Program refresh asked for after its popup closed", calls.some(c => c[0] === "refresh_programs"), J(calls));
    calls.length = 0; writes = [];
    // Before Python answers, the player opens Audio Device.
    press(cap);
    churn(cap);
    check("Python's answer arrives while Audio Device is open", deliverReplies() === 1);
    signal("captureInfoChanged", J({ active: true, source: "app", device: "cs2.exe", channels: 2, layout: "stereo",
        mode: "stereo" }));
    check("zero page writes while Audio Device is open", writes.length === 0, short(writes));
    check("zero bridge calls while Audio Device is open", calls.length === 0, J(calls));
    clickElsewhere();                                       // closed without a pick
    check("the Program reply landed after close", prog.values().includes("valorant.exe"), J(prog.values()));
    check("Program kept its pick", prog.value === "cs2.exe", prog.value);
    check("capture status landed", /cs2\.exe/.test(els["capture-status"].innerHTML), els["capture-status"].innerHTML);
    // The window focus of Audio Device's own focus churn queued a Program refresh too.
    check("Audio Device refresh ran after close, then the queued Program one",
        J(calls) === J([["refresh_capture_devices"], ["refresh_programs"]]), J(calls));
}

section("a popup closed without a pick, then another one opened");
load();
{
    const mon = els["monitor-select"], preset = els["preset-select"];
    press(mon);
    signal("statusChanged", "Radar is active", true);
    // Esc closes the Monitor popup: the key goes to the popup, the page sees nothing.
    press(preset);                                          // next dropdown
    churn(preset);
    signal("monitorsChanged", J([{ idx: 0, name: "A" }, { idx: 1, name: "B" }]));
    signal("profilesChanged", J({ "New": {} }));
    check("still quiet while the second one is open", writes.length === 0 && calls.length === 0,
        short(writes) + " " + J(calls));
    pick(preset, "Custom");
    check("everything held back across both landed", els["status-text"].textContent === "Radar is active"
        && mon.options.length === 2 && preset.values().includes("New"), J(preset.values()));
    check("in order: the second preset list includes the new profile, pick kept", preset.value === "Custom",
        preset.value);
}

// ── 4. Keyboard ────────────────────────────────────────────────────────
section("opened with Alt+Down, closed with Tab");
load();
{
    const prog = els["program-select"];
    keyOpen(prog);
    churn(prog);
    signal("programsChanged", J(["a.exe"]));
    check("quiet while open", writes.length === 0 && calls.length === 0, short(writes) + " " + J(calls));
    doc.fire("keydown", { target: prog, key: "Tab", keyCode: 9 }, true);   // the page gets keys only once it is closed
    check("landed after close", prog.values().includes("a.exe"), J(prog.values()));
    check("the refresh the opening asked for ran", calls.some(c => c[0] === "refresh_programs"), J(calls));
}

section("arrow keys on a closed select do not count as opening it");
load();
{
    const mon = els["monitor-select"];
    doc.activeElement = mon;
    doc.fire("keydown", { target: mon, key: "ArrowDown", keyCode: 40 }, true);
    signal("statusChanged", "Radar is active", true);
    check("signals apply at once", els["status-text"].textContent === "Radar is active");
}

// ── 5. Window focus without a dropdown ─────────────────────────────────
section("returning to the window refreshes the Program list at the next quiet moment");
load();
{
    windowFocus();
    check("not on the focus event itself", calls.length === 0, J(calls));
    doc.fire("pointermove", { target: els["status-text"] }, true);
    check("on the first pointer move", J(calls) === J([["refresh_programs"]]), J(calls));
    doc.fire("pointermove", { target: els["status-text"] }, true);
    check("once", calls.length === 1, J(calls));
    deliverReplies();
}

section("returning to the window by clicking straight into a dropdown");
load();
{
    const prog = els["program-select"];
    press(prog, { windowWasInactive: true });
    churn(prog);
    check("no refresh while it opens", calls.length === 0, J(calls));
    pick(prog, "Discord.exe");
    check("one refresh after the pick", calls.filter(c => c[0] === "refresh_programs").length === 1, J(calls));
}

section("a focus move inside the page ends it, focus churn does not");
load();
{
    const mon = els["monitor-select"];
    press(mon);
    doc.fire("blur", { target: mon, relatedTarget: null }, true);
    signal("statusChanged", "Radar is active", true);
    check("blur with nowhere to go: still held back", els["status-text"].textContent === "Radar stopped");
    doc.fire("blur", { target: mon, relatedTarget: els["preset-select"] }, true);
    check("focus moved to another element: applied", els["status-text"].textContent === "Radar is active");
}

section("no dropdown open: nothing is held back");
load();
{
    signal("statusChanged", "Radar is active", true);
    check("applied at once", els["status-text"].textContent === "Radar is active");
    W.AR.refreshCaptureDevices();
    check("a refresh outside a popup runs at once", J(calls) === J([["refresh_capture_devices"]]), J(calls));
    check("isSelectOpen is false", W.isSelectOpen() === false);
}

section("a failing held-back item does not drop the rest");
load();
{
    const mon = els["monitor-select"];
    press(mon);
    signal("captureDevicesChanged", "{not json");            // the handler ignores junk
    signal("monitorsChanged", "{not json");                  // this handler throws on junk
    signal("statusChanged", "Radar is active", true);
    const err = console.error; console.error = () => {};
    try { clickElsewhere(); } finally { console.error = err; }
    check("later items still landed", els["status-text"].textContent === "Radar is active");
    check("not open any more", W.isSelectOpen() === false);
}

// ── 6. A popup closed without a change ─────────────────────────────────
// The player clicks a control: pointerdown, mousedown, then the click. A
// switch's click toggles it and fires its onchange unless the click is cancelled.
function el(id, tag) { return els[id] || (els[id] = new El(id, tag)); }
function clickOn(target, action) {
    doc.fire("pointerdown", { target, button: 0, type: "pointerdown" }, true);
    doc.fire("mousedown", { target, button: 0, type: "mousedown" }, true);
    const ev = { target, prevented: false, preventDefault() { this.prevented = true; } };
    doc.fire("click", ev, true);
    if (!ev.prevented && action) action();
    doc.fire("click", ev, false);
    return ev;
}
function clickSwitch(id, onchange) {
    const cb = el(id, "INPUT");
    return clickOn(cb, () => { asPlayer(() => { cb.checked = !cb.checked; }); onchange(cb.checked); });
}
const toggle = () => clickOn(el("btn-toggle", "BUTTON"), () => W.AR.toggleRadar());
// Monitor opened and closed with Esc: the page saw the press and nothing after.
function escOnMonitor() { const m = els["monitor-select"]; press(m); churn(m); }

section("reviewer A: Esc on Monitor, the hotkey starts the radar, the player clicks Start");
load();
escOnMonitor();
signal("statusChanged", "Radar is active", true);         // global hotkey, from the game
windowBlur(); windowFocus();
doc.fire("pointermove", { target: el("status-text") }, true);
check("still held back before the click", els["status-text"].textContent === "Radar stopped");
calls.length = 0;
toggle();
check("the click sends neither stop_radar nor start_radar",
    !calls.some(c => c[0] === "stop_radar" || c[0] === "start_radar"), J(calls));
check("the player now sees the real state", els["status-text"].textContent === "Radar is active"
    && els["btn-toggle"].textContent === "End", els["btn-toggle"].textContent);
toggle();
check("their next click stops it", J(calls.filter(c => /_radar$/.test(c[0]))) === J([["stop_radar"]]), J(calls));

section("a click whose state did not change still works");
load();
escOnMonitor();
signal("captureInfoChanged", J({ active: false }));
calls.length = 0;
toggle();
check("Start sends start_radar", J(calls.filter(c => /_radar$/.test(c[0]))) === J([["start_radar"]]), J(calls));

section("the dropped click never sticks");
load();
escOnMonitor();
signal("statusChanged", "Radar is active", true);
// Press on Start, release elsewhere: no click.
doc.fire("pointerdown", { target: el("btn-toggle", "BUTTON"), button: 0, type: "pointerdown" }, true);
calls.length = 0;
toggle();                                                  // a later, ordinary click on End
check("the next click acts normally", J(calls.filter(c => /_radar$/.test(c[0]))) === J([["stop_radar"]]), J(calls));
load();
escOnMonitor();
signal("statusChanged", "Radar is active", true);
clickOn(el("status-text"));                               // the flush happens on a click elsewhere
calls.length = 0;
toggle();
check("a flush on a click elsewhere does not drop the next Start/End click",
    J(calls.filter(c => /_radar$/.test(c[0]))) === J([["stop_radar"]]), J(calls));

section("Move/Done: the drag state changed under the click");
load();
signal("overlayPositionChanged", J({ x: 0, y: 0, drag_enabled: false }));
escOnMonitor();
signal("overlayPositionChanged", J({ x: 5, y: 5, drag_enabled: true }));
calls.length = 0;
clickOn(el("btn-move", "BUTTON"), () => W.AR.toggleMoveMode());
check("no set_overlay_drag_enabled from that click", !calls.some(c => c[0] === "set_overlay_drag_enabled"), J(calls));
check("the button shows Done", els["btn-move"].textContent === "Done", els["btn-move"].textContent);
clickOn(el("btn-move", "BUTTON"), () => W.AR.toggleMoveMode());
check("the next click ends Move", J(calls.filter(c => c[0] === "set_overlay_drag_enabled")) === J([["set_overlay_drag_enabled", false]]), J(calls));

section("switches: Mono Output and Outline changed under the click");
load();
escOnMonitor();
signal("monoStateChanged", mono(["Headphones"], true, ""));   // turned on elsewhere (a profile)
calls.length = 0;
let ev = clickSwitch("mono-enabled", on => W.AR.setMonoEnabled(on));
check("the click is cancelled", ev.prevented);
check("the switch shows the real state (on)", els["mono-enabled"].checked === true);
check("no set_mono_enabled from that click", !calls.some(c => c[0] === "set_mono_enabled"), J(calls));
ev = clickSwitch("mono-enabled", on => W.AR.setMonoEnabled(on));
check("the next click turns it off", !ev.prevented && J(calls.filter(c => c[0] === "set_mono_enabled")) === J([["set_mono_enabled", false]]), J(calls));

load();
asPlayer(() => { el("outline-enabled", "INPUT").checked = true; });
escOnMonitor();
signal("appearanceChanged", J({ outline_enabled: false }));
calls.length = 0;
// Clicking the switch's track: a click on the span inside the label, then on the input.
const track = Object.assign(new El("outline-track", "SPAN"), { closest: q => (q === "label" ? {} : null) });
clickOn(track);
// The label then clicks its input: a click with no press of its own.
{
    const cb = el("outline-enabled", "INPUT");
    ev = { target: cb, prevented: false, preventDefault() { this.prevented = true; } };
    doc.fire("click", ev, true);
    if (!ev.prevented) { asPlayer(() => { cb.checked = !cb.checked; }); W.AR.setOutlineEnabled(cb.checked); }
    doc.fire("click", ev, false);
}
check("label click: the input's click is still cancelled", ev.prevented);
check("outline shows the real state (off)", els["outline-enabled"].checked === false);
check("no set_outline_enabled from that click", !calls.some(c => c[0] === "set_outline_enabled"), J(calls));

section("Delete: the preset showing changed under the click");
load();
asPlayer(() => { els["preset-select"].value = "My CS2"; });
escOnMonitor();
signal("profilesChanged", J({}));                         // My CS2 deleted elsewhere
calls.length = 0;
clickOn(el("delete-btn", "BUTTON"), () => W.AR.deletePreset());
check("no delete_profile from that click", !calls.some(c => c[0] === "delete_profile"), J(calls));
check("the dropdown shows what is really selected", els["preset-select"].value === "All Sounds",
    els["preset-select"].value);

section("reviewer B: 1000 overlay positions while held back, then a click");
load();
escOnMonitor();
for (let i = 0; i < 1000; i++) signal("overlayPositionChanged", J({ x: i, y: i, drag_enabled: true }));
writes = [];
clickOn(el("status-text"));
check("applied as one item", writes.filter(w => w.startsWith("position-val")).length === 1, short(writes));
check("a handful of writes, not thousands", writes.length < 10, String(writes.length));
check("shows the last position", els["position-val"].textContent === "999, 999", els["position-val"].textContent);

// ── 7. Coalescing keeps order and meaning ──────────────────────────────
section("coalescing");
load();
escOnMonitor();
signal("overlayPositionChanged", J({ x: 1, y: 1, drag_enabled: false }));
signal("statusChanged", "Radar is active", true);
signal("overlayPositionChanged", J({ x: 2, y: 2, drag_enabled: false }));
signal("statusChanged", "Radar stopped", false);
signal("statusChanged", "Radar is active", true);
writes = [];
clickOn(el("status-text"));
check("cross-signal order kept: both positions applied",
    writes.filter(w => w.startsWith("position-val")).length === 2, short(writes));
check("statusChanged is never collapsed (3 applied)",
    writes.filter(w => w.startsWith("status-text")).length === 3, short(writes));
check("final status", els["status-text"].textContent === "Radar is active");

load();
escOnMonitor();
signal("appearanceChanged", J({ thickness: 5 }));
signal("appearanceChanged", J({ color: "#00FF00" }));
signal("audioSettingsChanged", J({ freq_low: 100, freq_high: 200, gain: 1 }));
signal("audioSettingsChanged", J({ gain: 2 }));
signal("audioSettingsChanged", J({ freq_low: 300 }));        // half a band: not applied, as before
signal("captureInfoChanged", J({ active: true, source: "app", device: "cs2.exe", channels: 2, layout: "stereo",
    mode: "stereo" }));
signal("captureInfoChanged", "{junk");                      // ignored by the handler, kept apart
clickOn(el("status-text"));
check("partial appearance payloads merge", els["thickness-val"].textContent === "5 px"
    && els["color-hex"].textContent === "#00FF00", els["color-hex"].textContent);
check("audio settings merge, the band stays a whole pair", els["gain-val"].textContent === "2.0x"
    && els["freq-val"].textContent === "100-200 Hz", els["freq-val"].textContent);
check("junk after a capture payload does not erase it", /cs2\.exe/.test(els["capture-status"].innerHTML),
    els["capture-status"].innerHTML);

load();
escOnMonitor();
signal("audioSettingsChanged", J({ sensitivity: 0.02, gain: 3, freq_low: 120, freq_high: 900, max_amp: 0.4 }));
signal("audioSettingsChanged", J({ sensitivity: null, gain: 4, freq_low: null, freq_high: 950, max_amp: undefined }));
clickOn(el("status-text"));
check("an explicit null never overwrites a real value", els["sensitivity-val"].textContent === "0.0200"
    && els["max-amp-val"].textContent === "0.40", els["sensitivity-val"].textContent + " " + els["max-amp-val"].textContent);
check("real newer values still win", els["gain-val"].textContent === "4.0x", els["gain-val"].textContent);
check("a band with a null half keeps the last whole pair", els["freq-val"].textContent === "120-900 Hz",
    els["freq-val"].textContent);

// ── 8. Ending without a page click ─────────────────────────────────────
section("Enter does not open a select, Space does");
load();
doc.activeElement = els["monitor-select"];
doc.fire("keydown", { target: els["monitor-select"], key: "Enter", keyCode: 13 }, true);
check("Enter: not open", W.isSelectOpen() === false);
doc.fire("keydown", { target: els["monitor-select"], key: " ", keyCode: 32 }, true);
check("Space: open", W.isSelectOpen(els["monitor-select"]) === true);

section("the page becoming hidden ends it");
load();
escOnMonitor();
signal("statusChanged", "Radar is active", true);
doc.ref.hidden = false;
doc.fire("visibilitychange", {});
check("visible again: still held back", els["status-text"].textContent === "Radar stopped");
doc.ref.hidden = true;
doc.fire("visibilitychange", {});
doc.ref.hidden = false;
check("hidden: applied", els["status-text"].textContent === "Radar is active");
check("hidden: not open any more", W.isSelectOpen() === false);
check("hidden: the Program refresh the churn asked for ran", calls.some(c => c[0] === "refresh_programs"), J(calls));

section("the application losing activation ends it (appDeactivated)");
load();
escOnMonitor();
signal("statusChanged", "Radar is active", true);
calls.length = 0;
signal("appDeactivated");
check("applied at once, not queued behind itself", els["status-text"].textContent === "Radar is active");
check("not open any more", W.isSelectOpen() === false);
check("no enumeration while the game takes focus", !calls.some(c => /^refresh_/.test(c[0])), J(calls));
windowFocus();                                            // the player comes back
doc.fire("pointermove", { target: el("status-text") }, true);
check("the Program refresh runs on return", J(calls.filter(c => /^refresh_/.test(c[0]))) === J([["refresh_programs"]]),
    J(calls));
W.AR.toggleRadar();
check("no click is dropped after it", J(calls.filter(c => /_radar$/.test(c[0]))) === J([["stop_radar"]]), J(calls));
load();
signal("appDeactivated");
check("with no dropdown open it changes nothing", writes.length === 0 && calls.length === 0, short(writes));
load({ noAppDeactivated: true });
check("an older backend without appDeactivated still loads", handlers.statusChanged && !handlers.appDeactivated);

section("a window focus during the lifetime queues the Program refresh");
load();
{
    const m = els["monitor-select"];
    press(m);                                             // window already active
    windowBlur(); windowFocus();                          // churn only
    check("no call while open", calls.length === 0, J(calls));
    pick(m, "0");
    check("refresh_programs ran after close", calls.some(c => c[0] === "refresh_programs"), J(calls));
}

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
