# Open test: does per-app capture survive Windows mono audio?

**Status:** NOT YET RUN (blocked on hardware access, 2026-06-18)
**Why it matters:** the result decides whether we keep or delete the entire
VB-CABLE + `MonoMixThread` mono-output system.

## Background

Two audio scopes, deliberately separate:

- **Hearing = global mono.** The user wants every sound on the machine summed to
  one ear, exactly like the Windows "mono audio" accessibility setting.
- **Radar = per-app.** Only the target app (e.g. the game) should feed the
  direction analysis, so a Discord call panning voices around does not pollute
  the radar.

The whole VB-CABLE machinery exists for ONE reason: the radar originally used
**system loopback**, which Windows mono audio corrupts by summing L+R *before*
the loopback tap (see the 2026-06-09 spike and audio_capture.py:252, where
direction is derived purely from the L/R difference -> equal channels = angle 0
= everything pinned to the top of the radar).

But the radar can instead use **per-app capture** (`process_loopback.py`, WASAPI
process loopback by PID). That taps the target app's own render stream, which is
plausibly captured *before* Windows applies its endpoint-level mono downmix. If
so, the radar stays stereo even with Windows mono audio ON -> no cable needed.

The earlier failed test (radar went flat with Windows mono ON) was the
**system-loopback** path. It says nothing about the per-app path. Different code.

## The test

No code changes needed. The app already supports per-app capture via the
program picker (`set_target(pid, name)`, main.py:481).

1. Turn Windows mono audio **ON** (Settings -> Accessibility -> Audio -> Mono audio).
2. Leave AudioRadar's own mono mode **OFF** (we are not testing the cable path).
3. In AudioRadar, select the **game from the per-app program list** so the radar
   captures it by PID (process loopback), not system loopback.
4. Play the game and watch the radar.

## How to read the result

- **Radar still shows left/right/direction** -> process loopback taps BEFORE the
  Windows downmix. WINNING outcome:
  - Hearing = Windows mono audio (global, native, zero latency, no doubling).
  - Radar = per-app capture.
  - **Delete / make optional:** VB-CABLE bundling, `mono_output.py` /
    `MonoMixThread`, the cable steps in the setup modal, the vendor/VBCABLE path.
- **Radar goes flat / everything at the top** -> per-app capture is ALSO mono'd by
  Windows. Fallback:
  - Keep Windows mono audio for global hearing.
  - Keep a slimmed VB-CABLE path ONLY as the radar's stereo source (route the
    game -> cable, capture the cable for the radar). Drop the custom mono mixer,
    since Windows now does the hearing.

Either way, hearing and radar stay separate scopes. The only unknown is whether
Windows mono corrupts the per-app capture, and this 5-minute test settles it.
