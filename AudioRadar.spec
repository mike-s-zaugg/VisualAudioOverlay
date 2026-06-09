# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # Bundle the web UI (HTML/CSS/JS), its icons, and the self-hosted font so the
    # packaged .exe renders correctly offline for end users.
    datas=[
        ('dashboard_v2', 'dashboard_v2'),
    ],
    # process_loopback is imported lazily (inside functions), and the COM/audio
    # stack uses dynamic imports PyInstaller can miss - list them explicitly.
    hiddenimports=[
        'process_loopback',
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
    name='AudioRadar',
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
)
