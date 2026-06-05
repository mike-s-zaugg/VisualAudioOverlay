# Security Policy

## What this app does (and does not do)

Visual Audio Overlay is an accessibility tool. It reads only the standard Windows
audio output (WASAPI loopback), the same signal already going to your headphones.
It does **not**:

- read or write game memory,
- inject code into any other process,
- modify game files,
- send your audio or any data over the network.

The source is available so anyone can verify these claims. If you find behavior
that contradicts the above, please report it (see below).

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Instead, use GitHub's private vulnerability reporting:

1. Go to the **Security** tab of the repository.
2. Click **Report a vulnerability**.
3. Describe the issue and, if possible, steps to reproduce.

If private reporting is unavailable, contact the maintainer directly through the
profile at https://github.com/mike-s-zaugg.

Please give a reasonable window to respond and ship a fix before any public
disclosure.

## Supported versions

This project is in active development. Security fixes are applied to the latest
release on `main`. Older builds are not maintained.

## A note on anti-cheat

This tool does not interact with games, but anti-cheat policies differ between
titles and can change. Whether you use an overlay during competitive play is your
decision and your responsibility. This project does not aim to evade anti-cheat
systems.
