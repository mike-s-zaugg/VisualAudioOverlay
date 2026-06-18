# -*- mode: python ; coding: utf-8 -*-

import os

# Optionally bundle the VB-CABLE installer (Mono Output feature) if present.
# Drop VBCABLE_Setup_x64.exe into vendor/VBCABLE/ before building to embed it;
# without it, the app falls back to opening the VB-CABLE download page. Bundle
# the basic VB-CABLE only (not A+B / C+D) and keep it unmodified - see
# THIRD-PARTY-NOTICES.md for the donationware attribution terms.
_vendor_datas = [('vendor', 'vendor')] if os.path.isdir('vendor') else []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # Bundle the web UI (HTML/CSS/JS), its icons, and the self-hosted font so the
    # packaged .exe renders correctly offline for end users.
    datas=[
        ('dashboard_v2', 'dashboard_v2'),
        ('assets', 'assets'),
    ] + _vendor_datas,
    # process_loopback is imported lazily (inside functions), and the COM/audio
    # stack uses dynamic imports PyInstaller can miss - list them explicitly.
    hiddenimports=[
        'process_loopback',
        'mono_output',
        'comtypes',
        'pycaw',
        'pycaw.api.audioclient',
        'pycaw.utils',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VisualAudioOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
