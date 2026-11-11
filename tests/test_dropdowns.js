// Dropdown-fill and preset-restore tests for dashboard_v2/script.js.
//
// Run:  node tests/test_dropdowns.js       (exit 0 = pass)
//
// Run by the unit-test job in .github/workflows/ci.yml after pytest (Node 22,
// no npm packages). Loads the real script.js against a minimal DOM
// stub; the bootstrap never fires because the stub swallows DOMContentLoaded.
//
// Covers the two things that broke in this area: QtWebEngine growing a <select>
// whose options are swapped while its native popup is open, and the saved preset
// name being restored across the two separate signals that build the dropdown.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SELECT_IDS = ["preset-select", "program-select", "monitor-select", "mono-output-select"];

class Option {
    constructor() { this.value = ""; this.textContent = ""; }
}

class Select {
    constructor(id) { this.id = id; this.tagName = "SELECT"; this.options = []; this._idx = -1; this.listeners = {}; }
    set innerHTML(v) { if (v === "") { this.options = []; this._idx = -1; } }
    get innerHTML() { return ""; }
    appendChild(o) { this.options.push(o); if (this._idx === -1) this._idx = 0; }
    get value() { return this._idx >= 0 && this.options[this._idx] ? this.options[this._idx].value : ""; }
    set value(v) {
        const i = this.options.findIndex(o => String(o.value) === String(v));
        this._idx = i;                      // -1 when absent: the blank-dropdown state
    }
    get selectedIndex() { return this._idx; }
    set selectedIndex(i) { this._idx = i; }
    addEventListener(ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); }
    fire(ev) { const l = this.listeners[ev] || []; this.listeners[ev] = []; l.forEach(cb => cb()); }
    labels() { return this.options.map(o => o.textContent); }
}

class Generic {
    constructor(id) { this.id = id; this.value = ""; this.textContent = ""; this.checked = false;
        this.style = { setProperty() {} }; this.classList = { toggle() {}, add() {}, remove() {} };
        this.dataset = { min: "20", max: "20000" }; }
    addEventListener() {}
    querySelector() { return { style: {} }; }
}

const els = {};
for (const id of SELECT_IDS) els[id] = new Select(id);

// Document listeners (the dropdown guard, see installPopupGuard), so a test can
// press, pick and blur like the page does. DOMContentLoaded is still swallowed.
const docListeners = [];
function fireDoc(type, ev, capture) {
    docListeners.filter(l => l.type === type && l.capture === capture).forEach(l => l.fn(ev));
}
// A press on a select (the popup starts opening), a pick (change), and focus
// moving on to another element of the page.
function press(sel) { fireDoc("pointerdown", { target: sel, button: 0 }, true); fireDoc("mousedown", { target: sel, button: 0 }, true); }
function pick(sel, value) { sel.value = value; fireDoc("change", { target: sel }, true); fireDoc("change", { target: sel }, false); }
function blurTo(sel, other) { fireDoc("blur", { target: sel, relatedTarget: other }, true); }

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
        addEventListener(type, fn, capture) {                   // swallow DOMContentLoaded
            if (type !== "DOMContentLoaded") docListeners.push({ type, fn, capture: !!capture });
        },
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

const preset = els["preset-select"];
const program = els["program-select"];
const mono = els["mono-output-select"];
const W = sandbox.window;

// ── 1. isSelectOpen ────────────────────────────────────────────────────
// Open means pressed and not closed yet: focus alone does not count (the popup
// steals and returns focus while it opens, see "Quiet dropdowns" in script.js).
section("isSelectOpen");
check("false when nothing is pressed", W.isSelectOpen() === false);
sandbox.document.activeElement = program;
check("focus alone is not open", W.isSelectOpen() === false);
press(program);
check("true for any select once one is pressed", W.isSelectOpen() === true);
check("true for the pressed select", W.isSelectOpen(program) === true);
check("false for a different select", W.isSelectOpen(preset) === false);
blurTo(program, null);
check("a blur with nowhere to go (popup focus churn) keeps it open", W.isSelectOpen(program) === true);
check("no bridge call while it is open", calls.length === 0, JSON.stringify(calls));
blurTo(program, preset);
check("focus moving to another element closes it", W.isSelectOpen() === false);
check("the Program refresh runs once it has closed", JSON.stringify(calls) === '[["refresh_programs"]]',
    JSON.stringify(calls));
calls.length = 0;
sandbox.document.activeElement = null;

// ── 2. Preset restore across two-signal arrival ────────────────────────
section("preset restore (saved name arrives before the lists)");
W.onSelectedPresetChanged("Footsteps - CS2");
check("nothing applied while both lists are empty", calls.length === 0, JSON.stringify(calls));
W.onProfilesChanged(JSON.stringify({}));
check("still nothing after profiles-only", calls.length === 0, JSON.stringify(calls));
W.onPresetsChanged(JSON.stringify(
    ["All Sounds", "Footsteps - CS2", "Footsteps - Valorant", "Custom"]));
check("dropdown shows the saved preset", preset.value === "Footsteps - CS2", preset.value);
// The restore must be SILENT. Re-applying the entry is what let a saved profile
// overwrite the accent colour, thickness and sliders the user changed after
// choosing it - so zero bridge traffic is the assertion that guards that fix.
check("restore selects without applying anything", calls.length === 0, JSON.stringify(calls));

// A real user pick, by contrast, both applies and persists.
W.AR.applyPreset("Footsteps - Valorant");
check("user pick applies", calls.some(c => c[0] === "apply_preset" && c[1] === "Footsteps - Valorant"),
    JSON.stringify(calls));
check("user pick persists", calls.some(c => c[0] === "set_selected_preset" && c[1] === "Footsteps - Valorant"),
    JSON.stringify(calls));

const before = calls.length;
W.onProfilesChanged(JSON.stringify({ "My CS2": { freq_low: 200 } }));
check("later rebuild does not re-apply", calls.length === before, JSON.stringify(calls.slice(before)));
check("selection survives the rebuild", preset.value === "Footsteps - CS2", preset.value);
check("profile got the star label", preset.labels().includes("My CS2  ★"), preset.labels().join("|"));

// ── 3. Stale saved name must not eat the selection ─────────────────────
section("stale saved name (profile deleted since)");
W._presetRestored = false;
W._savedPreset = "Deleted Profile";
preset.value = "Custom";
W.onPresetsChanged(JSON.stringify(["All Sounds", "Footsteps - CS2", "Custom"]));
check("keeps the current pick", preset.value === "Custom", preset.value);
check("never lands on a blank selection", preset.selectedIndex !== -1, String(preset.selectedIndex));
W.onProfilesChanged(JSON.stringify({}));
check("still keeps it after a second rebuild", preset.value === "Custom", preset.value);

// ── 4. Deferral while a popup is open ──────────────────────────────────
section("deferred fill (popup open)");
W._presetRestored = true;
program.value = "";
W._programsSig = null;
W.onProgramsChanged(JSON.stringify(["cs2.exe", "Discord.exe"]));
check("baseline list built", program.labels().join(",") === "All (system audio),cs2.exe,Discord.exe",
    program.labels().join(","));
program.value = "cs2.exe";

press(program);                                           // user opens the popup
W.onProgramsChanged(JSON.stringify(["cs2.exe", "Discord.exe", "chrome.exe"]));
check("options untouched while open", program.options.length === 3, String(program.options.length));
check("selection untouched while open", program.value === "cs2.exe", program.value);

blurTo(program, preset);                                  // focus moves on
check("queued rebuild applied on blur", program.options.length === 4, String(program.options.length));
check("selection preserved through the flush", program.value === "cs2.exe", program.value);

// ── 5. User picks during a deferred rebuild ────────────────────────────
section("user picks while a rebuild is queued");
press(program);
W._programsSig = null;
W.onProgramsChanged(JSON.stringify(["cs2.exe", "Discord.exe"]));   // chrome.exe disappears
pick(program, "Discord.exe");                                      // user picks it in the popup
check("user's pick wins over the queued rebuild", program.value === "Discord.exe", program.value);
check("queued options did land", program.options.length === 3, String(program.options.length));

// ── 6. Selected program stops playing ──────────────────────────────────
section("selected program drops off the list");
W._programsSig = null;
W.onProgramsChanged(JSON.stringify(["cs2.exe"]));
check("falls back to All (system audio)", program.value === "all", program.value);
check("not a blank selection", program.selectedIndex === 0, String(program.selectedIndex));

// ── 7. Mono select keeps "" as a real value ────────────────────────────
section("mono output select");
W.onMonoStateChanged(JSON.stringify({ devices: ["Headphones", "CABLE Input"], default: "Headphones",
    cable: "CABLE Input", enabled: false, selected: "" }));
check("System default selectable via empty value", mono.selectedIndex === 0, String(mono.selectedIndex));
W.onMonoStateChanged(JSON.stringify({ devices: ["Headphones", "CABLE Input"], default: "Headphones",
    cable: "CABLE Input", enabled: true, selected: "CABLE Input" }));
check("named device selected", mono.value === "CABLE Input", mono.value);

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
