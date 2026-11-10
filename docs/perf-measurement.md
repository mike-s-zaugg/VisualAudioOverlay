# Measuring the overlay's effect on game frame pacing

A player reported that CS2's FPS fluctuates, especially the 1% lows, while the
Visual Audio Overlay is enabled. This page explains what we changed, how to
check the effect in CS2, and what to tell players. The effect on game FPS has
not been measured yet; that is what the protocol below is for.

## What we changed and what may remain

All numbers in this section were measured offscreen on the developer's
machine, with a test harness driving the overlay. They are overlay activity,
not game FPS.

- **No repaints in silence.** Before, the overlay redrew itself about 32 times
  per second even when nothing played. Now it redraws only while an arc is
  fading and stops once the last arc is gone (31.4 paints/s before, 0 after).
- **One repaint per tick while sound plays.** Before, every audio event caused
  an extra redraw on top of the fade timer (42 paints/s with 20 events/s, now
  31). The first sound after silence is still drawn immediately.
- **Hidden overlay gets no work.** Audio events that arrive while the overlay
  is hidden are dropped instead of piling up.
- **Paint objects are built once.** Pens, colours and shapes are created when a
  setting changes instead of on every frame.
- **Opt-in perf log** (`VAO_PERF=1`, see below).

The radar looks the same: same colours, arc width, merge angle and fade (the
same 26 fade steps). 16 fixed states rendered before and after are
pixel-identical.

**What may remain:**

- **Compositing cost while the overlay is on screen.** The overlay is a
  transparent always-on-top window. Windows (DWM) has to combine it with the
  game's frames, which can stop the game from using its fastest presentation
  path ("independent flip"). See "Checks that need the real game".
- **Audio capture CPU.** With "All system audio" capture, the audio library
  checks for new audio about 580 times per second (about 5 to 6% of one core,
  measured on the developer's machine). Per-app capture uses about 1 to 2%. It
  runs on its own thread; fixing it needs a different capture backend.

## What you need

- CS2 and a short demo you can replay (one of your own match demos).
- **CapFrameX** (free, https://www.capframex.com). It records frame times with
  PresentMon and calculates average FPS, 1% low and 0.1% low. Plain
  **PresentMon** (free, Intel, GitHub) works too if you prefer CSV files.
- VAO built from this branch (or run from source with `python main.py`).

## Keep every run the same

1. Same demo, same start tick (`demo_gototick <tick>`), same 60 seconds.
2. Same CS2 video settings and `fps_max` in every run. Write them down.
3. Close other overlays and recorders (Discord overlay, Steam FPS counter,
   GPU vendor overlays, OBS).
4. Laptop: plugged in, same power plan.
5. Do each run 3 times and use the middle result, not the best one.
6. Note the Windows settings listed in the results table. Results only compare
   within the same settings.

## Turn on the VAO perf log

The perf log shows what the overlay did during a run, so you can prove a
"silent" run really was silent. In PowerShell:

- Packaged app, from the folder with the exe: `$env:VAO_PERF = "1"; .\VisualAudioOverlay.exe`
- From source: `$env:VAO_PERF = "1"; python main.py`

`perf.log` appears next to `settings.json` (next to the exe, or in the repo
folder when run from source). The app must run from a writable folder,
otherwise no perf.log appears. Each start adds a `#` header, then one line per
second:

```
2026-11-10 14:03:07 secs=1.00 paints=0 paint_ms_mean=0.000 paint_ms_max=0.000 ticks=0 events=0 dropped_hidden=0 chunks=20 chunk_ms_mean=0.310 chunk_ms_max=0.912
```

| Field | Meaning |
|---|---|
| `paints` | overlay redraws in that second (0 in silence) |
| `paint_ms_mean` / `paint_ms_max` | time spent drawing one frame |
| `ticks` | fade timer ticks (0 in silence, about 32 while arcs fade) |
| `events` | audio events received |
| `dropped_hidden` | events thrown away because the overlay was hidden |
| `chunks`, `chunk_ms_*` | audio chunks analysed (about 20/s) and time per chunk |

Without `VAO_PERF=1` nothing is logged and the app pays no measurable cost.

## The runs

Run the matrix once in **Fullscreen** and once in **Fullscreen Windowed** (CS2:
Settings > Video > Display Mode). First check the radar is visible over the
game in that mode; if not, note it and skip the "VAO on" runs there.

| Run | Setup |
|---|---|
| A. VAO off | VAO fully closed (not just stopped). |
| B. VAO on, silence | Radar started, CS2 master volume 0. Close or mute every other sound source (Discord, browser, music): in "All system audio" mode they still produce events. `events=0 paints=0` in perf.log proves the run was silent. |
| C. VAO on, sound | Radar started, normal game volume, same demo segment. |

For a "before" baseline, repeat B and C with the current public release (it has
no perf log, which is fine). For each run: start the demo at the chosen tick,
wait a few seconds, capture 60 seconds in CapFrameX, save it with a name like
`FS-B-silence-1`.

## What to compare

In CapFrameX (Analysis / Comparison): **average FPS**, **1% low**, **0.1% low**,
and the **frame-time graph** (regular spikes or a "fuzzy" band that only
appears with VAO on).

- **A vs B**: cost of the overlay just being on screen. It no longer redraws in
  silence, so a remaining gap points at compositing.
- **B vs C**: cost of the arcs animating.
- **Current release vs this branch** in B and C: what this change gained.

A difference smaller than the spread between your 3 repeats is noise.

## Checks that need the real game

These need the real overlay on screen over a running game, so they were not
measured during development.

1. **Present mode.** In the PresentMon data (CapFrameX stores it too), check the
   `PresentMode` column for A, B and C. If A shows `Hardware: Independent Flip`
   and B shows `Composed: Flip`, the visible overlay forces Windows to composite
   the game. That is the most likely remaining cause of worse 1% lows.
2. **DWM load.** With VAO on in silence, add the "GPU" column in Task Manager >
   Details and watch `dwm.exe` for 30 seconds, VAO on vs off.
3. **Optional, for the maintainer: is the overlay a layered window?** With the
   radar running, run this in a Python prompt. The dashboard and the overlay
   both use the title "Visual Audio Overlay", so it prints one line for each:
   the overlay is the line with `layered= True transparent= True`, the
   dashboard shows `layered= False`.
   ```python
   import ctypes
   from ctypes import wintypes
   u = ctypes.windll.user32
   @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
   def cb(h, _):
       buf = ctypes.create_unicode_buffer(256)
       u.GetWindowTextW(h, buf, 256)
       if buf.value == "Visual Audio Overlay":
           ex = u.GetWindowLongW(h, -20) & 0xFFFFFFFF
           print(hex(ex), "layered=", bool(ex & 0x80000), "transparent=", bool(ex & 0x20))
       return True
   u.EnumWindows(cb, 0)
   ```

## Results table

| Mode | Run | Avg FPS | 1% low | 0.1% low | PresentMode | Notes |
|---|---|---|---|---|---|---|
| Fullscreen | A off | | | | | |
| Fullscreen | B silence | | | | | |
| Fullscreen | C sound | | | | | |
| FS Windowed | A off | | | | | |
| FS Windowed | B silence | | | | | |
| FS Windowed | C sound | | | | | |

Settings (change only one at a time between sets of runs): HAGS __ (Settings >
System > Display > Graphics), Optimizations for windowed games __, MPO __ (on by
default; if disabled via registry, Windows composites more), VRR / G-Sync /
FreeSync __, refresh __ Hz, Game Mode __, fps_max __, GPU __, CPU __.

## For players

### What players can try themselves

- Compare Fullscreen and Fullscreen Windowed in CS2 and keep whichever gives
  steadier frame times with the radar on.
- Cap the frame rate (`fps_max`) a little below what the PC reaches in busy
  scenes. Steady frame times usually help more than a higher average.
- In the Program list, pick the game instead of "All system audio". It uses
  less CPU (it only reads stereo, so surround users may prefer system capture).
- Close overlays you do not need while playing.
- If you disabled MPO in the registry, consider turning it back on.

### Draft reply to the player

> Thank you so much for reporting the FPS drops! We found and removed
> unnecessary redrawing: the overlay used to redraw itself about 30 times per
> second even in silence, and now it does nothing at all while there is no
> sound. We are still checking how much this helps in CS2 itself. Until then,
> it is worth trying both Fullscreen and Fullscreen Windowed, a frame rate cap
> a little below your usual FPS, and picking CS2 in the Program list instead of
> all system audio. If the 1% lows still drop with the overlay on, a short
> CapFrameX capture with the overlay on and off would help us a lot.
