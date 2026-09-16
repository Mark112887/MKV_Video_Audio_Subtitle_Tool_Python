# -*- mode: python ; coding: utf-8 -*-

import os

a = Analysis(
    ['MKV_Video_Audio_Subtitle_Tool.py'],
    pathex=[],
    binaries=[
        ('mkvinfo.exe', '.'),
        ('mkvmerge.exe', '.'),
        ('mkvpropedit.exe', '.'),
        ('ffmpeg.exe', '.'),
    ],
    datas=[],
    hiddenimports=['win32gui', 'win32con'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.dnd'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MKV Video Audio & Subtitle Tool',
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
