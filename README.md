# Visual Audio Overlay

A Windows desktop app that captures system audio, detects the **direction** a
sound is coming from (stereo L/R balance or 5.1 surround), and draws a
transparent, always-on-top **radar overlay** of directional "blips" — aimed at
FPS footstep awareness and accessibility.

If this project helps you, you can support development here:
☕ **[Buy Me a Coffee](https://buymeacoffee.com/mikezaugg)**

## Architecture

- **`main.py`** — app entry. Hosts the control panel (an HTML/JS dashboard in a
  `QWebEngineView`) and the overlay window. A `Bridge` object is exposed to JS as
  `window.bridge` via QWebChannel — the full JS↔Python API.
- **`audio_capture.py`** — `AudioCaptureThread`: records loopback audio (48 kHz),
  applies an FFT band-pass, and computes angle + intensity.
- **`overlay.py`** — `OverlayRadar`: frameless, transparent, click-through window
  that draws decaying accent-coloured arcs.
- **`dashboard_v2/`** — the UI (HTML/CSS/JS, icons, self-hosted font).

## Run (development)

```bash
pip install -r requirements.txt
python main.py
```

Requires **Python 3.10+** and **Windows 10 build 19041+** (for audio loopback).

## Build a standalone .exe

```bash
pip install pyinstaller
pyinstaller AudioRadar.spec
```

The `.spec` bundles `dashboard_v2/` (UI, icons, font) so the executable renders
correctly offline. Output lands in `dist/`.

> Note: QtWebEngine in a one-file build can be finicky; if the window is blank,
> prefer a one-dir build.

## Roadmap

- **Per-application capture** — pick a specific program (e.g. the game) so other
  apps like Discord are ignored. Planned via the Windows WASAPI Process Loopback
  API (INCLUDE mode).
- Editable presets persisted to disk.
