// Toggle hotkey tests for dashboard_v2/script.js: reading key presses into a
// canonical combination, the capture flow (listeners, bridge calls), rendering
// of every hotkeyChanged state, the radar status following statusChanged from
// any source (button or hotkey), and the new version notice in the footer.
//
// Run:  node tests/test_hotkey_ui.js   (exit 0 = pass)
//
// Same harness as test_look_settings.js: loads the real script.js against a
// minimal DOM stub and records every bridge call. This stub also records event
// listeners on window and document, so the tests can press keys and check that
// nothing is left listening once capture ends.
//
// The contract (slot names, payload keys, combination format) is shared with
// main.py; see the "Toggle hotkey" block at the top of script.js.
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
        this.options = []; this.attrs = {}; this.focused = 0;
        this.style = { setProperty() {} };
        this.classList = new ClassList();
        this.dataset = { min: "20", max: "20000" };
    }
    addEventListener() {}
    appendChild(o) { this.options.push(o); }
    querySelector() { return { style: {} }; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    focus() { this.focused++; }
}

// Listener registry with real add/remove semantics (same fn + same capture flag).
function listenerTarget() {
    const list = [];
    const cap = o => (typeof o === "boolean" ? o : !!(o && o.capture));
    return {
        list,
        addEventListener(type, fn, opts) {
            if (!list.some(l => l.type === type && l.fn === fn && l.capture === cap(opts))) {
                list.push({ type, fn, capture: cap(opts) });
            }
        },
        removeEventListener(type, fn, opts) {
            const i = list.findIndex(l => l.type === type && l.fn === fn && l.capture === cap(opts));
            if (i >= 0) list.splice(i, 1);
        },
        fire(type, ev) { list.filter(l => l.type === type).forEach(l => l.fn(ev)); },
        count(type) { return list.filter(l => l.type === type).length; },
    };
}

const els = {};
function resetDom() {
    for (const k of Object.keys(els)) delete els[k];
    // Mirror index.html: the modal starts hidden, the read-only readout too.
    els["hotkey-modal"] = new Generic("hotkey-modal");
    els["hotkey-modal"].classList.add("is-hidden");
    els["hotkey-readout"] = new Generic("hotkey-readout");
    els["hotkey-readout"].classList.add("is-hidden");
    els["status-text"] = Object.assign(new Generic("status-text"), { textContent: "Radar stopped" });
    els["status-dot"] = new Generic("status-dot");
    els["status-dot"].classList.add("status-dot--off");
}
resetDom();

const calls = [];
const bridge = new Proxy({}, {
    get(_t, name) {
        if (name === "then") return undefined;
        return (...args) => calls.push([String(name), ...args]);
    },
    has() { return true; },
});

const win = listenerTarget();
const doc = listenerTarget();
const sandbox = {
    console,
    document: {
        activeElement: null,
        hidden: false,
        getElementById(id) {
            if (id === "preview-canvas") return null;          // skip canvas painting
            if (!(id in els)) els[id] = new Generic(id);
            return els[id];
        },
        createElement() { return new Option(); },
        // DOMContentLoaded is swallowed (as in the other tests); the rest is recorded.
        addEventListener(type, fn, opts) { if (type !== "DOMContentLoaded") doc.addEventListener(type, fn, opts); },
        removeEventListener: doc.removeEventListener,
    },
    window: null,
    bridge,
    addEventListener: win.addEventListener,
    removeEventListener: win.removeEventListener,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const src = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "script.js"), "utf8");
vm.runInContext(src, sandbox);

// ── Assertions ─────────────────────────────────────────────────────────
let failed = 0;
function check(name, cond, extra) {
    if (cond) { console.log("  PASS  " + name); }
    else { failed++; console.log("  FAIL  " + name + (extra ? "  -> " + extra : "")); }
}
function section(t) { console.log("\n" + t); }
function callsTo(slot) { return calls.filter(c => c[0] === slot); }
function clearCalls() { calls.length = 0; }
const J = JSON.stringify;

const W = sandbox.window;
const text = id => els[id] ? els[id].textContent : undefined;
const hidden = id => !!(els[id] && els[id].classList.contains("is-hidden"));

// A keydown event as QtWebEngine delivers it. `mods` is e.g. "ctrl alt shift meta altgr".
function key(keyCode, mods = "") {
    const m = new Set(mods.split(/\s+/).filter(Boolean));
    return {
        keyCode,
        ctrlKey: m.has("ctrl") || m.has("altgr"),     // Windows reports AltGr as Ctrl+Alt
        altKey: m.has("alt") || m.has("altgr"),
        shiftKey: m.has("shift"),
        metaKey: m.has("meta"),
        getModifierState(name) { return name === "AltGraph" && m.has("altgr"); },
        prevented: false, stopped: false,
        preventDefault() { this.prevented = true; },
        stopPropagation() { this.stopped = true; },
    };
}
function press(keyCode, mods) { const e = key(keyCode, mods); win.fire("keydown", e); return e; }
// The capture listeners only. The document also keeps the permanent dropdown
// guard (keydown, visibilitychange; see installPopupGuard), which is not counted.
function hkCount(t, type, fn) { return t.list.filter(l => l.type === type && l.fn === fn).length; }
function docVisibility() { return hkCount(doc, "visibilitychange", sandbox.onHotkeyCaptureVisibility); }
function captureListeners() { return win.count("keydown") + win.count("blur") + docVisibility(); }
// The modal's own Esc listener. The document also has the permanent dropdown
// guard listening for keydown (see installPopupGuard), which is not counted.
function modalKeyListeners() { return doc.list.filter(l => l.type === "keydown" && l.fn === sandbox.onHotkeyModalKey).length; }

const BOUND = { combo: "Ctrl+Shift+H", state: "ok", effective: "Ctrl+Shift+H", requested: "",
    error: "", capturing: false, can_choose: true };
function hk(over) { W.onHotkeyChanged(J(Object.assign({}, BOUND, over || {}))); }

// Fresh DOM + bound hotkey + not capturing.
function reset(over) {
    if (W.isHotkeyCapturing()) W.AR.cancelHotkeyCapture();
    resetDom();
    hk(over);
    clearCalls();
}

// ── 1. keyCode mapping ─────────────────────────────────────────────────
section("keyCode -> key name");
const MAP = [
    [65, "A"], [72, "H"], [90, "Z"], [48, "0"], [53, "5"], [57, "9"],
    [112, "F1"], [120, "F9"], [123, "F12"], [124, "F13"], [135, "F24"],
    [96, "Num0"], [101, "Num5"], [105, "Num9"],
    [45, "Insert"], [46, "Delete"], [36, "Home"], [35, "End"], [33, "PageUp"], [34, "PageDown"],
    [37, "Left"], [38, "Up"], [39, "Right"], [40, "Down"],
];
for (const [code, name] of MAP) {
    check(`${code} -> ${name}`, W.hotkeyKeyName(code) === name, J(W.hotkeyKeyName(code)));
}
// Outside the vocabulary: Backspace, Tab, Enter, Space, Esc, ; (OEM), Num*, F25, 0, junk.
for (const code of [8, 9, 13, 32, 27, 186, 106, 107, 110, 111, 136, 64, 91, 0, -1, 999, NaN, undefined, "x"]) {
    check(`${String(code)} is not a hotkey key`, W.hotkeyKeyName(code) === "", J(W.hotkeyKeyName(code)));
}

// ── 2. Canonical order ─────────────────────────────────────────────────
section("canonical combination");
check("Ctrl+Alt+Shift order", W.hotkeyCombo(true, true, true, "K") === "Ctrl+Alt+Shift+K", W.hotkeyCombo(true, true, true, "K"));
check("Ctrl+Shift", W.hotkeyCombo(true, false, true, "H") === "Ctrl+Shift+H", W.hotkeyCombo(true, false, true, "H"));
check("Alt+Shift", W.hotkeyCombo(false, true, true, "F9") === "Alt+Shift+F9", W.hotkeyCombo(false, true, true, "F9"));
check("key alone", W.hotkeyCombo(false, false, false, "F9") === "F9", W.hotkeyCombo(false, false, false, "F9"));
let r = W.readHotkeyEvent(key(75, "shift alt ctrl"));
check("event: modifiers come out in canonical order", r.action === "bind" && r.combo === "Ctrl+Alt+Shift+K", J(r));
r = W.readHotkeyEvent(key(34, "alt"));
check("event: Alt+PageDown", r.action === "bind" && r.combo === "Alt+PageDown", J(r));
r = W.readHotkeyEvent(key(103, "ctrl"));
check("event: Ctrl+Num7", r.action === "bind" && r.combo === "Ctrl+Num7", J(r));

// ── 3. Validity rules ──────────────────────────────────────────────────
section("validity rules");
const VALID = [
    [false, false, false, "F9"], [false, false, false, "F1"], [false, false, false, "F24"],
    [false, false, true, "F9"], [true, false, false, "K"], [false, true, false, "K"],
    [true, false, true, "H"], [false, true, true, "1"], [true, true, false, "Up"], [true, false, false, "Num0"],
];
const INVALID = [
    [false, false, false, "K"], [false, false, true, "K"], [false, false, true, "Num5"],
    [false, false, false, "Up"], [false, false, true, "Delete"], [false, false, false, "7"],
    [true, true, true, ""], [false, false, false, "F25"], [false, false, false, "F0"],
];
const label = ([c, a, s, k]) => J([c ? "Ctrl" : "", a ? "Alt" : "", s ? "Shift" : "", k].filter(Boolean).join("+"));
for (const v of VALID) check(`${label(v)} is valid`, W.isValidHotkey(...v) === true);
for (const v of INVALID) check(`${label(v)} is invalid`, W.isValidHotkey(...v) === false);
r = W.readHotkeyEvent(key(75, "shift"));
check("event: Shift+K rejected with the modifier hint",
    r.action === "reject" && r.hint === "Add Ctrl or Alt, or use an F key.", J(r));
r = W.readHotkeyEvent(key(75));
check("event: K alone rejected", r.action === "reject", J(r));
r = W.readHotkeyEvent(key(120));
check("event: F9 alone accepted", r.action === "bind" && r.combo === "F9", J(r));
r = W.readHotkeyEvent(key(13, "ctrl"));
check("event: Ctrl+Enter rejected (not in the vocabulary)", r.action === "reject" && /can't be used/.test(r.hint), J(r));
r = W.readHotkeyEvent(key(75, "ctrl meta"));
check("event: Win key rejected", r.action === "reject" && r.hint === "The Windows key can't be used.", J(r));
r = W.readHotkeyEvent(key(81, "altgr"));
check("event: AltGr rejected", r.action === "reject" && /AltGr/.test(r.hint), J(r));
for (const code of [16, 17, 18, 91, 92, 225]) {
    r = W.readHotkeyEvent(key(code, "ctrl alt shift"));
    check(`event: modifier ${code} alone is ignored`, r.action === "ignore", J(r));
}
r = W.readHotkeyEvent(key(27));
check("event: Esc cancels", r.action === "cancel", J(r));
r = W.readHotkeyEvent(key(27, "shift"));
check("event: Shift+Esc is not a cancel", r.action === "reject", J(r));
r = W.readHotkeyEvent(key(8));
check("event: Backspace clears", r.action === "clear", J(r));
r = W.readHotkeyEvent(key(46));
check("event: Delete alone clears", r.action === "clear", J(r));
r = W.readHotkeyEvent(key(46, "ctrl alt"));
check("event: Ctrl+Alt+Delete is a combination", r.action === "bind" && r.combo === "Ctrl+Alt+Delete", J(r));
r = W.readHotkeyEvent({});
check("event: empty object does not throw", r.action === "reject", J(r));

// ── 4. Capture flow ────────────────────────────────────────────────────
section("start capture");
reset();
W.AR.startHotkeyCapture();
check("capturing", W.isHotkeyCapturing() === true);
check("set_hotkey_capture(true) once", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",true]]', J(calls));
check("keydown listener attached (capture phase)", win.list.some(l => l.type === "keydown" && l.capture), J(win.list.map(l => l.type)));
check("blur and visibilitychange listeners attached", win.count("blur") === 1 && docVisibility() === 1);
check("modal opened", !hidden("hotkey-modal"));
check("card button says Press keys...", text("hotkey-btn") === "Press keys...", text("hotkey-btn"));
check("modal button says Press keys...", text("hotkey-capture-btn") === "Press keys...", text("hotkey-capture-btn"));
check("hint says how to cancel", /Esc cancels/.test(text("hotkey-hint")), text("hotkey-hint"));
check("modal button is focused", els["hotkey-capture-btn"].focused === 1);
check("modal button shows the active state", els["hotkey-capture-btn"].classList.contains("is-active"));

section("modifier-only presses are ignored");
clearCalls();
for (const [code, mods] of [[17, "ctrl"], [16, "ctrl shift"], [18, "ctrl alt shift"], [91, "meta"]]) {
    const e = press(code, mods);
    check(`keyCode ${code}: default prevented`, e.prevented && e.stopped);
}
check("still capturing", W.isHotkeyCapturing() === true);
check("no bridge call", calls.length === 0, J(calls));
check("hint unchanged", /Esc cancels/.test(text("hotkey-hint")), text("hotkey-hint"));

section("rejected presses keep waiting and say why");
let e = press(75, "shift");
check("Shift+K: default prevented", e.prevented && e.stopped);
check("Shift+K: still capturing", W.isHotkeyCapturing());
check("Shift+K: hint", text("hotkey-hint") === "Add Ctrl or Alt, or use an F key.", text("hotkey-hint"));
e = press(75, "ctrl meta");
check("Win+Ctrl+K: still capturing, hint", W.isHotkeyCapturing() && text("hotkey-hint") === "The Windows key can't be used.", text("hotkey-hint"));
e = press(81, "altgr");
check("AltGr+Q: still capturing, hint", W.isHotkeyCapturing() && /AltGr/.test(text("hotkey-hint")), text("hotkey-hint"));
e = press(13);
check("Enter: still capturing, default prevented", W.isHotkeyCapturing() && e.prevented, text("hotkey-hint"));
check("no bridge call for rejected keys", calls.length === 0, J(calls));

section("a valid combination binds");
clearCalls();
e = press(82, "ctrl");                         // Ctrl+R must not reload the page
check("default prevented and stopped", e.prevented && e.stopped);
check("set_toggle_hotkey called once with the canonical string",
    J(callsTo("set_toggle_hotkey")) === '[["set_toggle_hotkey","Ctrl+R"]]', J(calls));
check("set_hotkey_capture(false) NOT called", callsTo("set_hotkey_capture").length === 0, J(calls));
check("capture ended", W.isHotkeyCapturing() === false);
check("all capture listeners removed", captureListeners() === 0, J(win.list.map(l => l.type)));
e = press(75, "ctrl alt");
check("later key presses are not captured", !e.prevented && callsTo("set_toggle_hotkey").length === 1, J(calls));
W.onHotkeyChanged(J(Object.assign({}, BOUND, { combo: "Ctrl+R", effective: "Ctrl+R" })));
check("button shows the new binding", text("hotkey-btn") === "Ctrl+R" && text("hotkey-capture-btn") === "Ctrl+R", text("hotkey-btn"));
check("hint confirms it saved", /^Saved\./.test(text("hotkey-hint")), text("hotkey-hint"));

section("F9 alone and a full combination");
reset();
W.AR.startHotkeyCapture(); clearCalls();
press(120);
check("F9 -> set_toggle_hotkey(\"F9\")", J(callsTo("set_toggle_hotkey")) === '[["set_toggle_hotkey","F9"]]', J(calls));
reset();
W.AR.startHotkeyCapture(); clearCalls();
press(17, "ctrl"); press(18, "ctrl alt"); press(16, "ctrl alt shift");
press(36, "ctrl alt shift");
check("Ctrl+Alt+Shift+Home after holding the modifiers",
    J(callsTo("set_toggle_hotkey")) === '[["set_toggle_hotkey","Ctrl+Alt+Shift+Home"]]', J(calls));

section("Esc cancels");
reset();
W.AR.startHotkeyCapture(); clearCalls();
e = press(27);
check("default prevented", e.prevented && e.stopped);
check("set_hotkey_capture(false) once", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));
check("no set_toggle_hotkey", callsTo("set_toggle_hotkey").length === 0, J(calls));
check("capture ended, listeners removed", !W.isHotkeyCapturing() && captureListeners() === 0);
check("button shows the binding again", text("hotkey-btn") === "Ctrl+Shift+H", text("hotkey-btn"));
check("hint back to idle", text("hotkey-hint") === "Starts or stops the radar while you play.", text("hotkey-hint"));
check("modal stays open after Esc in capture", !hidden("hotkey-modal"));
// A second Esc (not capturing) closes the modal through its own listener.
check("modal key listener attached while open", modalKeyListeners() === 1, String(modalKeyListeners()));
doc.fire("keydown", key(27));
check("second Esc closes the modal", hidden("hotkey-modal"));
check("modal key listener removed on close", modalKeyListeners() === 0);
check("focus back on the card button", els["hotkey-btn"].focused === 1);

section("blur and page hide cancel");
reset();
W.AR.startHotkeyCapture(); clearCalls();
win.fire("blur", {});
check("blur: set_hotkey_capture(false)", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));
check("blur: listeners removed", !W.isHotkeyCapturing() && captureListeners() === 0);
reset();
W.AR.startHotkeyCapture(); clearCalls();
sandbox.document.hidden = false;
doc.fire("visibilitychange", {});
check("visible again: keeps waiting", W.isHotkeyCapturing() && calls.length === 0, J(calls));
sandbox.document.hidden = true;
doc.fire("visibilitychange", {});
sandbox.document.hidden = false;
check("hidden page: set_hotkey_capture(false)", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));
check("hidden page: listeners removed", !W.isHotkeyCapturing() && captureListeners() === 0);

section("second click, Done and backdrop stop waiting");
reset();
W.AR.startHotkeyCapture(); clearCalls();
W.AR.startHotkeyCapture();
check("second click cancels", !W.isHotkeyCapturing() && J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));
check("modal stays open", !hidden("hotkey-modal"));
W.AR.startHotkeyCapture(); clearCalls();
W.AR.closeHotkey();
check("closing while waiting cancels", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));
check("closing hides the modal and removes listeners", hidden("hotkey-modal") && captureListeners() === 0 && modalKeyListeners() === 0);
clearCalls();
W.AR.closeHotkey();
check("closing when idle calls nothing", calls.length === 0, J(calls));

// ── 5. Clear ───────────────────────────────────────────────────────────
section("clear");
reset();
check("clear button visible when bound", !hidden("hotkey-clear"));
W.AR.clearHotkey();
check("clear button: set_toggle_hotkey(\"\")", J(callsTo("set_toggle_hotkey")) === '[["set_toggle_hotkey",""]]', J(calls));
check("clear button: no set_hotkey_capture", callsTo("set_hotkey_capture").length === 0, J(calls));
reset();
W.AR.startHotkeyCapture(); clearCalls();
W.AR.clearHotkey();
check("clear while waiting: set_toggle_hotkey(\"\") only", J(calls) === '[["set_toggle_hotkey",""]]', J(calls));
check("clear while waiting: listeners removed", !W.isHotkeyCapturing() && captureListeners() === 0);
reset();
W.AR.startHotkeyCapture(); clearCalls();
press(8);
check("Backspace while waiting clears", J(calls) === '[["set_toggle_hotkey",""]]', J(calls));
reset();
W.AR.startHotkeyCapture(); clearCalls();
press(46);
check("Delete while waiting clears", J(calls) === '[["set_toggle_hotkey",""]]', J(calls));

// ── 6. Rendering hotkeyChanged ─────────────────────────────────────────
section("render: ok");
reset();
check("card button", text("hotkey-btn") === "Ctrl+Shift+H" && !hidden("hotkey-btn"), text("hotkey-btn"));
check("readout hidden", hidden("hotkey-readout"));
check("idle hint", text("hotkey-hint") === "Starts or stops the radar while you play.", text("hotkey-hint"));
check("button title explains it", /Starts or stops the radar/.test(els["hotkey-btn"].attrs.title || ""), J(els["hotkey-btn"].attrs));
check("button aria-label names the binding", /Ctrl\+Shift\+H/.test(els["hotkey-btn"].attrs["aria-label"] || ""));
check("rendering calls nothing", calls.length === 0, J(calls));

section("render: unbound");
reset({ combo: "", effective: "", state: "unbound" });
check("Not set", text("hotkey-btn") === "Not set" && text("hotkey-capture-btn") === "Not set", text("hotkey-btn"));
check("clear hidden", hidden("hotkey-clear"));
check("unbound hint", /No hotkey set/.test(text("hotkey-hint")), text("hotkey-hint"));
check("button still usable", !hidden("hotkey-btn"));

section("render: conflict after a failed change");
const CONFLICT = "Another app already uses Ctrl+Alt+K. Pick a different one.";
reset({ state: "conflict", requested: "Ctrl+Alt+K", error: CONFLICT });
check("still shows the active binding", text("hotkey-btn") === "Ctrl+Shift+H", text("hotkey-btn"));
check("error shown as given", text("hotkey-hint") === CONFLICT, text("hotkey-hint"));
check("error also in the card button title", (els["hotkey-btn"].attrs.title || "").startsWith(CONFLICT));
reset({ state: "conflict", requested: "Ctrl+Alt+K", error: "" });
check("no error text: names the requested combination", text("hotkey-hint") === "Ctrl+Alt+K is already in use. Pick another.", text("hotkey-hint"));

section("render: startup conflict");
const STARTUP = "Another app already uses Ctrl+Shift+H. Pick a different one.";
reset({ state: "conflict", effective: "", error: STARTUP });
check("Not active", text("hotkey-btn") === "Not active", text("hotkey-btn"));
check("error shown", text("hotkey-hint") === STARTUP, text("hotkey-hint"));
check("clear available", !hidden("hotkey-clear"));
check("can still pick another", !hidden("hotkey-btn"));

section("render: invalid");
reset({ state: "invalid", requested: "Shift+K", error: "That combination can't be used." });
check("keeps the active binding", text("hotkey-btn") === "Ctrl+Shift+H", text("hotkey-btn"));
check("error shown", text("hotkey-hint") === "That combination can't be used.", text("hotkey-hint"));
reset({ state: "invalid", error: "" });
check("fallback text without error", text("hotkey-hint") === "That hotkey can't be used. Pick another.", text("hotkey-hint"));

section("render: pending");
reset({ state: "pending", effective: "", requested: "Ctrl+Alt+K" });
check("Checking...", text("hotkey-btn") === "Checking...", text("hotkey-btn"));
check("pending hint", text("hotkey-hint") === "Setting up the hotkey...", text("hotkey-hint"));

section("render: unsupported");
const UNSUP = "Hotkeys are not available on this system.";
reset({ state: "unsupported", combo: "", effective: "", error: UNSUP, can_choose: false });
check("card button hidden, readout shown", hidden("hotkey-btn") && !hidden("hotkey-readout"));
check("readout says Not available", text("hotkey-readout") === "Not available", text("hotkey-readout"));
check("error in readout title and aria-label", els["hotkey-readout"].attrs.title === UNSUP
    && (els["hotkey-readout"].attrs["aria-label"] || "").includes(UNSUP), J(els["hotkey-readout"].attrs));
check("modal controls hidden", hidden("hotkey-capture-btn") && hidden("hotkey-clear"));
check("error in the hint", text("hotkey-hint") === UNSUP, text("hotkey-hint"));
W.AR.startHotkeyCapture();
check("cannot start capture", !W.isHotkeyCapturing() && calls.length === 0 && hidden("hotkey-modal"), J(calls));
reset({ state: "unsupported", combo: "", effective: "", error: "", can_choose: true });
check("unsupported even with can_choose true", hidden("hotkey-btn") && text("hotkey-hint") === UNSUP);

section("render: can_choose false (system managed)");
reset({ can_choose: false });
check("readout shows the effective binding", !hidden("hotkey-readout") && text("hotkey-readout") === "Ctrl+Shift+H", text("hotkey-readout"));
check("no change or clear controls", hidden("hotkey-btn") && hidden("hotkey-capture-btn") && hidden("hotkey-clear"));
check("read-only hint", text("hotkey-hint") === "Your system sets this hotkey.", text("hotkey-hint"));
W.AR.startHotkeyCapture();
check("cannot start capture", !W.isHotkeyCapturing() && calls.length === 0, J(calls));

section("render: effective differs from combo");
reset({ combo: "Ctrl+Shift+H", effective: "Ctrl+Shift+H (system)" });
check("effective is what is shown", text("hotkey-btn") === "Ctrl+Shift+H (system)", text("hotkey-btn"));

section("render: unknown state");
reset({ state: "something-new" });
check("treated as bound", text("hotkey-btn") === "Ctrl+Shift+H" && text("hotkey-hint") === "Starts or stops the radar while you play.",
    text("hotkey-btn") + " / " + text("hotkey-hint"));

section("hotkeyChanged while waiting");
reset();
W.AR.startHotkeyCapture(); clearCalls();
hk({ state: "ok" });
check("an ok update keeps waiting", W.isHotkeyCapturing() && text("hotkey-btn") === "Press keys...", text("hotkey-btn"));
hk({ state: "unsupported", error: UNSUP, can_choose: false });
check("becoming unsupported stops waiting", !W.isHotkeyCapturing() && captureListeners() === 0);
check("and tells Python", J(callsTo("set_hotkey_capture")) === '[["set_hotkey_capture",false]]', J(calls));

// ── 7. Partial and broken payloads ─────────────────────────────────────
section("partial payloads never throw");
reset();
for (const bad of ["", "null", "{", "[]", "42", "\"text\"", "{}", undefined, null, '{"state":5}',
    '{"combo":null,"effective":7}', '{"can_choose":"no"}', '{"capturing":1}']) {
    let threw = null;
    try { W.onHotkeyChanged(bad); } catch (err) { threw = err; }
    check(`${J(bad)} does not throw`, threw === null, String(threw));
}
check("junk keeps the previous binding", text("hotkey-btn") === "Ctrl+Shift+H" && !hidden("hotkey-btn"), text("hotkey-btn"));
W.onHotkeyChanged('{"state":"conflict","error":"Taken."}');
check("partial payload merges with the rest", text("hotkey-btn") === "Ctrl+Shift+H" && text("hotkey-hint") === "Taken.",
    text("hotkey-btn") + " / " + text("hotkey-hint"));
check("rendering made no bridge call", calls.length === 0, J(calls));

// ── 8. The hotkey starts and stops the radar ───────────────────────────
// Python handles the key press and reports the result through the normal
// statusChanged, exactly like a click on Start/End. Nothing here may depend
// on the change having come from a click.
section("radar started and stopped by the hotkey (no click)");
reset();
W.onStatusChanged("Radar active - capturing cs2.exe", true);
check("status line shows the message as given", text("status-text") === "Radar active - capturing cs2.exe", text("status-text"));
check("button says End", text("btn-toggle") === "End" && els["btn-toggle"].classList.contains("is-active"), text("btn-toggle"));
check("dot on", els["status-dot"].classList.contains("status-dot--on") && !els["status-dot"].classList.contains("status-dot--off"));
clearCalls();
W.AR.toggleRadar();
check("a click after a hotkey start stops the radar", J(calls) === '[["stop_radar"]]', J(calls));
W.onStatusChanged("Radar stopped", false);
check("stopped by the hotkey: status line", text("status-text") === "Radar stopped", text("status-text"));
check("button says Start", text("btn-toggle") === "Start" && !els["btn-toggle"].classList.contains("is-active"), text("btn-toggle"));
check("dot off", els["status-dot"].classList.contains("status-dot--off") && !els["status-dot"].classList.contains("status-dot--on"));
clearCalls();
W.AR.toggleRadar();
check("a click after a hotkey stop starts the radar", J(calls) === '[["start_radar"]]', J(calls));
W.onStatusChanged("Radar active - capturing cs2.exe", true);
hk({ combo: "F9", effective: "F9" });
check("a hotkeyChanged leaves the status line alone", text("status-text") === "Radar active - capturing cs2.exe", text("status-text"));
check("the hidden state is gone", typeof W.onOverlayVisibilityChanged === "undefined" && typeof W.hiddenStatusText === "undefined");

section("Python refuses Alt+F4");
const ALT_F4 = "Alt+F4 closes windows, so it cannot be used as the hotkey.";
reset();
W.AR.startHotkeyCapture(); clearCalls();
press(115, "alt");
check("Alt+F4 is sent like any valid combination", J(callsTo("set_toggle_hotkey")) === '[["set_toggle_hotkey","Alt+F4"]]', J(calls));
hk({ state: "invalid", requested: "Alt+F4", error: ALT_F4 });
check("Python's sentence is shown as given", text("hotkey-hint") === ALT_F4, text("hotkey-hint"));
check("the previous binding stays on the button", text("hotkey-btn") === "Ctrl+Shift+H" && text("hotkey-capture-btn") === "Ctrl+Shift+H");

// ── 9. New version notice in the footer ────────────────────────────────
section("new version notice");
const html = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "index.html"), "utf8");
const footer = html.slice(html.indexOf('<footer class="footer">'), html.indexOf("</footer>"));
check("the notice is in the footer", footer.includes('id="update-notice"'));
check("it sits right before the version number",
    /id="update-notice"[\s\S]*<\/span>\s*<span class="footer-version" id="footer-version">/.test(footer)
    && footer.indexOf('id="update-notice"') < footer.indexOf('id="footer-version"'));
check("hidden by default", /<span id="update-notice" class="[^"]*\bis-hidden\b/.test(footer));
check("Download link uses the existing openUpdate path", /class="footer-update-link"[\s\S]*?AR\.openUpdate\(\)/.test(footer));
check("Download link has a descriptive aria-label",
    /class="footer-update-link"[^>]*aria-label="Download the new version"[^>]*>Download<\/a>/.test(footer));
check("the old bar is gone", !html.includes("update-banner") && !src.includes("update-banner")
    && !fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "style.css"), "utf8").includes("update-banner"));
check("no dismiss any more", typeof W.AR.dismissUpdate === "undefined");

els["update-notice"] = new Generic("update-notice");
els["update-notice"].classList.add("is-hidden");
els["update-notice-text"] = Object.assign(new Generic("update-notice-text"), { textContent: "A new version is available" });
clearCalls();
W.AR.openUpdate();
check("before any update: Download opens the latest release page",
    J(calls) === J([["open_url", "https://github.com/mike-s-zaugg/VisualAudioOverlay/releases/latest"]]), J(calls));
clearCalls();
W.onUpdateAvailable("0.2.2", "https://github.com/mike-s-zaugg/VisualAudioOverlay/releases/tag/v0.2.2");
check("shown", !hidden("update-notice"));
check("version text", text("update-notice-text") === "Version 0.2.2 is available", text("update-notice-text"));
check("showing it calls nothing", calls.length === 0, J(calls));
W.AR.openUpdate();
check("Download opens the stored release url through open_url",
    J(calls) === J([["open_url", "https://github.com/mike-s-zaugg/VisualAudioOverlay/releases/tag/v0.2.2"]]), J(calls));
W.onUpdateAvailable("0.2.2", "https://github.com/mike-s-zaugg/VisualAudioOverlay/releases/tag/v0.2.2");
W.onUpdateAvailable("0.2.3", "");
check("a repeat replaces the text, never appends", text("update-notice-text") === "Version 0.2.3 is available", text("update-notice-text"));
check("still shown, nothing duplicated", !hidden("update-notice") && els["update-notice"].options.length === 0
    && els["update-notice-text"].options.length === 0);
clearCalls();
W.AR.openUpdate();
check("an empty url keeps the last known release page",
    J(calls) === J([["open_url", "https://github.com/mike-s-zaugg/VisualAudioOverlay/releases/tag/v0.2.2"]]), J(calls));
W.onUpdateAvailable("", "");
check("no version: generic text", text("update-notice-text") === "A new version is available", text("update-notice-text"));

// ── 10. Without a bridge (local preview / older backend) ───────────────
section("without a bridge");
reset();
const savedBridge = sandbox.bridge;
sandbox.bridge = undefined;
let threw = null;
try {
    W.AR.startHotkeyCapture();
    press(75, "ctrl alt");
    W.AR.startHotkeyCapture();
    press(27);
    W.AR.clearHotkey();
    W.AR.closeHotkey();
} catch (err) { threw = err; }
sandbox.bridge = savedBridge;
check("no exception", threw === null, String(threw));
check("listeners cleaned up", captureListeners() === 0 && modalKeyListeners() === 0);

// ── 11. House rules (all of dashboard_v2) ──────────────────────────────
section("house rules");
const css = fs.readFileSync(path.join(__dirname, "..", "dashboard_v2", "style.css"), "utf8");
const code = src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
check("no timers or animation loops", !/\b(setTimeout|setInterval|requestAnimationFrame)\s*\(/.test(code));
check("no CSS animations", !/@keyframes|\banimation\s*:/.test(css));
const EM_DASH = String.fromCharCode(0x2014);
for (const [name, text] of [["script.js", src], ["index.html", html], ["style.css", css],
    ["this test file", fs.readFileSync(__filename, "utf8")]]) {
    check(`no em dashes in ${name}`, !text.includes(EM_DASH));
}
check("the old default is not shown anywhere", !src.includes("Ctrl+Shift+F9") && !html.includes("Ctrl+Shift+F9"));
check("no copy left about showing or hiding the radar",
    !/(show|hide)s? (or (show|hide)s? )?the radar|Radar hidden/i.test(src + html), "");

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
