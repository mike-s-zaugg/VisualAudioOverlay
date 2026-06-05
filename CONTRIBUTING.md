# Contributing to Visual Audio Overlay

Thanks for your interest in improving Visual Audio Overlay. This project exists to
make games playable for people with single-sided deafness and hearing loss, and
community help is what moves it forward.

## Ground rules

- Be respectful. See the [Code of Conduct](CODE_OF_CONDUCT.md).
- This is a **source-available** project under [FSL-1.1-MIT](LICENSE). You may use,
  modify, and contribute to the code for any purpose except building a competing
  product. By contributing, you agree your contribution is provided under the same
  license.
- Keep the accessibility mission front and center. Features that help mono-hearing
  and hard-of-hearing players take priority.

## Developer Certificate of Origin (sign-off required)

To keep the project's ownership clean, every commit must be signed off under the
[Developer Certificate of Origin](https://developercertificate.org/). This is a
simple statement that you wrote the code, or have the right to submit it, under
the project's license.

Add the sign-off automatically with the `-s` flag:

```bash
git commit -s -m "Add mono audio output option"
```

This appends a line like `Signed-off-by: Your Name <you@example.com>` to your
commit message. Pull requests with unsigned commits will be asked to amend.

## Development setup

```bash
git clone https://github.com/mike-s-zaugg/VisualAudioOverlay.git
cd VisualAudioOverlay
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requires **Windows 10 (build 19041+)** and **Python 3.10+**. The audio capture path
uses WASAPI loopback, so a real audio device is needed to test end to end.

## Project layout

| File | Role |
|------|------|
| `main.py` | App entry. Hosts the control panel and overlay, exposes the JS/Python bridge. |
| `audio_capture.py` | Captures loopback audio, band-pass filters it, computes direction + intensity. |
| `overlay.py` | The transparent, click-through radar window. |
| `dashboard_v2/` | The control-panel UI (HTML/CSS/JS, icons, bundled font). |

## Submitting a pull request

1. Open an issue first for anything non-trivial so we can agree on the approach.
2. Branch off `main`. Keep each PR focused on one change.
3. Match the style of the surrounding code, comments, and UI copy.
4. Test your change against at least one real game or audio source and describe
   what you tested in the PR.
5. Make sure every commit is signed off (see above).
6. Open the PR against `main` with a clear description of what and why.

## Reporting bugs and requesting features

Use GitHub Issues. For bugs, include your Windows version, headset type
(stereo vs. 7.1 / surround), the game or audio source, and steps to reproduce.

## Security

Do not file security issues in public. See [SECURITY.md](SECURITY.md).
