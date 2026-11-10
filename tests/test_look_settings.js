// Overlay look settings tests for dashboard_v2/script.js: size, alert opacity
// and outline (the three controls in the CUSTOMIZATION card's Overlay column).
//
// Run:  node tests/test_look_settings.js   (exit 0 = pass)
//
// Same harness as test_dropdowns.js: loads the real script.js against a minimal
// DOM stub and records every bridge call. Run by CI after pytest (see ci.yml).
//
// Guards the bridge contract (slot names, int/bool types), that values coming
// from Python never echo back into the bridge, and that profiles saved before
// these settings existed leave them alone.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class Option {
    constructor() { this.value = ""; this.textContent = ""; }
}

class Generic {
    constructor(id) { this.id = id; this.value = ""; this.textContent = ""; this.checked = false;
        this.options = []; this.fill = null;
        this.style = { setProperty: (k, v) => { if (k === "--fill") this.fill = v; } };
        this.classList = { toggle() {}, add() {}, remove() {} };
        this.dataset = { min: "20", max: "20000" }; }
    addEventListener() {}
    appendChild(o) { this.options.push(o); }
    querySelector() { return { style: {} }; }
}

const els = {};
// Mirror the index.html defaults for the controls under test.
function resetLookControls() {
    els["overlay-size"] = Object.assign(new Generic("overlay-size"), { value: "100" });
    els["alert-opacity"] = Object.assign(new Generic("alert-opacity"), { value: "100" });
    els["outline-enabled"] = Object.assign(new Generic("outline-enabled"), { checked: true });
    els["overlay-size-val"] = Object.assign(new Generic("overlay-size-val"), { textContent: "100 % · 300 px" });
    els["alert-opacity-val"] = Object.assign(new Generic("alert-opacity-val"), { textContent: "100 %" });
}
resetLookControls();

const calls = [];
const bridge = new Proxy({}, {
    get(_t, name) {
        if (name === "then") return undefined;
        return (...args) => calls.push([String(name), ...args]);
    },
    has() { return true; },
});

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
        addEventListener() {},                                  // swallow DOMContentLoaded
    },
    window: null,
    bridge,
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
const LOOK_SLOTS = ["set_overlay_size", "set_alert_opacity", "set_outline_enabled"];
function lookCalls() { return calls.filter(c => LOOK_SLOTS.includes(c[0])); }
function clearCalls() { calls.length = 0; }

const W = sandbox.window;
const size = () => els["overlay-size"];
const opacity = () => els["alert-opacity"];
const outline = () => els["outline-enabled"];
const text = id => els[id].textContent;

// ── 1. Size readout ────────────────────────────────────────────────────
section("size readout");
check("100 % is the 300 px window", W.overlaySizeText(100) === "100 % · 300 px", W.overlaySizeText(100));
check("50 % is 150 px", W.overlaySizeText(50) === "50 % · 150 px", W.overlaySizeText(50));
check("200 % is 600 px", W.overlaySizeText(200) === "200 % · 600 px", W.overlaySizeText(200));
check("accepts the slider's string value", W.overlaySizeText("130") === "130 % · 390 px", W.overlaySizeText("130"));

// ── 2. Setters call the right slot with the right type ─────────────────
section("setters (JS -> Python)");
clearCalls();
W.AR.setOverlaySize("150");                     // oninput passes a string
let c = callsTo("set_overlay_size");
check("set_overlay_size called once", c.length === 1, JSON.stringify(calls));
check("size sent as an int", c[0] && c[0][1] === 150 && Number.isInteger(c[0][1]), JSON.stringify(c));
check("size readout updated", text("overlay-size-val") === "150 % · 450 px", text("overlay-size-val"));
check("size fill updated", size().fill === ((150 - 50) / 150 * 100) + "%", size().fill);

clearCalls();
W.AR.setAlertOpacity("35");
c = callsTo("set_alert_opacity");
check("set_alert_opacity called once", c.length === 1, JSON.stringify(calls));
check("opacity sent as an int", c[0] && c[0][1] === 35 && Number.isInteger(c[0][1]), JSON.stringify(c));
check("opacity readout updated", text("alert-opacity-val") === "35 %", text("alert-opacity-val"));
check("opacity fill updated", opacity().fill === ((35 - 20) / 80 * 100) + "%", opacity().fill);

clearCalls();
W.AR.setOutlineEnabled(false);
W.AR.setOutlineEnabled(1);                      // truthy non-bool is coerced
c = callsTo("set_outline_enabled");
check("set_outline_enabled called per change", c.length === 2, JSON.stringify(calls));
check("outline sent as bool false", c[0] && c[0][1] === false, JSON.stringify(c));
check("outline sent as bool true", c[1] && c[1][1] === true, JSON.stringify(c));
check("setters call nothing else", calls.length === 2, JSON.stringify(calls));

// Local preview mode: no QWebChannel, so no bridge at all.
section("setters without a bridge");
const savedBridge = sandbox.bridge;
sandbox.bridge = undefined;
let threw = null;
try {
    W.AR.setOverlaySize("80");
    W.AR.setAlertOpacity("60");
    W.AR.setOutlineEnabled(false);
} catch (e) { threw = e; }
sandbox.bridge = savedBridge;
check("no exception without a bridge", threw === null, String(threw));
check("size readout still updates", text("overlay-size-val") === "80 % · 240 px", text("overlay-size-val"));
check("opacity readout still updates", text("alert-opacity-val") === "60 %", text("alert-opacity-val"));

// ── 3. appearanceChanged (Python -> JS) never calls back ───────────────
section("onAppearanceChanged with the new keys");
resetLookControls();
clearCalls();
W.onAppearanceChanged(JSON.stringify({ color: "#112233", thickness: 8,
    overlay_size: 120, alert_opacity: 45, outline_enabled: false }));
check("no bridge call at all", calls.length === 0, JSON.stringify(calls));
check("size slider set", Number(size().value) === 120, String(size().value));
check("size readout set", text("overlay-size-val") === "120 % · 360 px", text("overlay-size-val"));
check("size fill set", size().fill === ((120 - 50) / 150 * 100) + "%", size().fill);
check("opacity slider set", Number(opacity().value) === 45, String(opacity().value));
check("opacity readout set", text("alert-opacity-val") === "45 %", text("alert-opacity-val"));
check("opacity fill set", opacity().fill === ((45 - 20) / 80 * 100) + "%", opacity().fill);
check("outline switch off", outline().checked === false, String(outline().checked));

W.onAppearanceChanged(JSON.stringify({ outline_enabled: true }));
check("partial payload: outline back on", outline().checked === true, String(outline().checked));
check("partial payload: size untouched", Number(size().value) === 120, String(size().value));
check("partial payload: opacity untouched", Number(opacity().value) === 45, String(opacity().value));
check("still no bridge call", calls.length === 0, JSON.stringify(calls));

section("onAppearanceChanged without the new keys (older backend)");
resetLookControls();
size().value = "70"; opacity().value = "30"; outline().checked = false;
els["overlay-size-val"].textContent = "70 % · 210 px";
els["alert-opacity-val"].textContent = "30 %";
clearCalls();
W.onAppearanceChanged(JSON.stringify({ color: "#9751F2", thickness: 6 }));
check("no bridge call", calls.length === 0, JSON.stringify(calls));
check("size untouched", size().value === "70" && text("overlay-size-val") === "70 % · 210 px",
    size().value + " / " + text("overlay-size-val"));
check("opacity untouched", opacity().value === "30" && text("alert-opacity-val") === "30 %",
    opacity().value + " / " + text("alert-opacity-val"));
check("outline untouched", outline().checked === false, String(outline().checked));

// ── 4. Applying profiles ───────────────────────────────────────────────
const AUDIO = { sensitivity: 50, gain: 10, freq_low: 150, freq_high: 4000, max_amp: 100 };

section("applyProfileValues with an old profile (no look keys)");
resetLookControls();
size().value = "70"; opacity().value = "30"; outline().checked = false;
clearCalls();
W.applyProfileValues(Object.assign({ accent_color: "#9751F2", thickness: 6 }, AUDIO));
check("no look slot called", lookCalls().length === 0, JSON.stringify(lookCalls()));
check("size untouched", size().value === "70", size().value);
check("opacity untouched", opacity().value === "30", opacity().value);
check("outline untouched", outline().checked === false, String(outline().checked));
check("the audio part still applied", callsTo("set_sensitivity").length === 1, JSON.stringify(calls));

section("applyProfileValues with outline_enabled: false");
resetLookControls();
clearCalls();
W.applyProfileValues(Object.assign({ overlay_size: 80, alert_opacity: 40, outline_enabled: false }, AUDIO));
check("outline sent as false", JSON.stringify(callsTo("set_outline_enabled")) === '[["set_outline_enabled",false]]',
    JSON.stringify(callsTo("set_outline_enabled")));
check("outline switch off", outline().checked === false, String(outline().checked));
check("size sent as int 80", JSON.stringify(callsTo("set_overlay_size")) === '[["set_overlay_size",80]]',
    JSON.stringify(callsTo("set_overlay_size")));
check("size control + readout", Number(size().value) === 80 && text("overlay-size-val") === "80 % · 240 px",
    size().value + " / " + text("overlay-size-val"));
check("opacity sent as int 40", JSON.stringify(callsTo("set_alert_opacity")) === '[["set_alert_opacity",40]]',
    JSON.stringify(callsTo("set_alert_opacity")));
check("opacity control + readout", Number(opacity().value) === 40 && text("alert-opacity-val") === "40 %",
    opacity().value + " / " + text("alert-opacity-val"));

section("applyProfileValues with outline_enabled: true");
outline().checked = false;
clearCalls();
W.applyProfileValues(Object.assign({ outline_enabled: true }, AUDIO));
check("outline sent as true", JSON.stringify(callsTo("set_outline_enabled")) === '[["set_outline_enabled",true]]',
    JSON.stringify(callsTo("set_outline_enabled")));
check("outline switch on", outline().checked === true, String(outline().checked));
check("absent size/opacity not sent",
    callsTo("set_overlay_size").length === 0 && callsTo("set_alert_opacity").length === 0, JSON.stringify(calls));

// ── 5. Junk values from a hand-edited profile are skipped ──────────────
// A range input turns junk into its midpoint (130 % size, 60 % opacity), so
// anything that is not a finite number must leave the control and overlay alone.
section("applyProfileValues skips junk size/opacity");
for (const junk of ["abc", true, "", NaN, "  ", false, {}, [], Infinity]) {
    resetLookControls();
    size().value = "70"; opacity().value = "30";
    clearCalls();
    W.applyProfileValues(Object.assign({ overlay_size: junk, alert_opacity: junk }, AUDIO));
    const label = typeof junk === "string" ? JSON.stringify(junk) : String(junk);
    check(`${label}: no size/opacity bridge call`,
        callsTo("set_overlay_size").length === 0 && callsTo("set_alert_opacity").length === 0,
        JSON.stringify(lookCalls()));
    check(`${label}: controls untouched`, size().value === "70" && opacity().value === "30",
        size().value + " / " + opacity().value);
}

section("applyProfileValues passes numbers and numeric strings through the slider");
// The stub input does not snap like a real range input; what matters is that the
// value reaches the slider and the bridge gets whatever the slider then holds.
for (const [given, want] of [[75, 75], [999, 999], ["120", 120], [" 40 ", 40]]) {
    resetLookControls();
    clearCalls();
    W.applyProfileValues(Object.assign({ overlay_size: given, alert_opacity: given }, AUDIO));
    const label = JSON.stringify(given);
    check(`${label}: size applied`, JSON.stringify(callsTo("set_overlay_size")) === `[["set_overlay_size",${want}]]`,
        JSON.stringify(callsTo("set_overlay_size")));
    check(`${label}: opacity applied`, JSON.stringify(callsTo("set_alert_opacity")) === `[["set_alert_opacity",${want}]]`,
        JSON.stringify(callsTo("set_alert_opacity")));
}

section("applyProfileValues skips a non-boolean outline");
for (const junk of ["yes", 1, 0, null, "false", "true"]) {
    for (const start of [true, false]) {
        resetLookControls();
        outline().checked = start;
        clearCalls();
        W.applyProfileValues(Object.assign({ outline_enabled: junk }, AUDIO));
        const label = `${JSON.stringify(junk)} (switch ${start ? "on" : "off"})`;
        check(`${label}: no outline bridge call`, callsTo("set_outline_enabled").length === 0,
            JSON.stringify(lookCalls()));
        check(`${label}: switch untouched`, outline().checked === start, String(outline().checked));
    }
}

// ── 6. Saving a profile stores the three keys ──────────────────────────
section("addPreset stores the look keys");
function savedProfile() {
    const s = callsTo("save_profile");
    return s.length ? JSON.parse(s[s.length - 1][1]) : null;
}
sandbox.prompt = () => "Look test";
resetLookControls();
size().value = "130"; opacity().value = "45"; outline().checked = false;
clearCalls();
W.AR.addPreset();
let p = savedProfile();
check("profile saved", p !== null, JSON.stringify(calls));
check("overlay_size stored as int", p && p.overlay_size === 130, p && JSON.stringify(p.overlay_size));
check("alert_opacity stored as int", p && p.alert_opacity === 45, p && JSON.stringify(p.alert_opacity));
check("outline_enabled stored as false", p && p.outline_enabled === false, p && JSON.stringify(p.outline_enabled));
check("existing look keys still stored", p && "accent_color" in p && "thickness" in p, p && Object.keys(p).join(","));

outline().checked = true;
clearCalls();
W.AR.addPreset();
p = savedProfile();
check("outline_enabled stored as true", p && p.outline_enabled === true, p && JSON.stringify(p.outline_enabled));

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
