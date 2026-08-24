# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
import imageio_ffmpeg

block_cipher = None

# Get ffmpeg binary path from imageio_ffmpeg
ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
binaries = []
if os.path.exists(ffmpeg_bin):
    binaries.append((ffmpeg_bin, "."))

datas = [
    ("icon.ico", "."),
    ("icon.png", "."),
    ("icons", "icons"),
]

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "imageio_ffmpeg",
]

excludes = [
    "tkinter",
    "unittest",
    "test",
    "pydoc",
    "sqlite3",
    "scipy",
    "matplotlib",
    "PIL",
    # Exclude unused large Qt6 modules
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngine",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtSensors",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSpatialAudio",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtCharts",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtDataVisualization",
    "PySide6.QtScxml",
    "PySide6.QtRemoteObjects",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtHttpServer",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtTextToSpeech",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtAxContainer",
    "PySide6.QtDBus",
    "PySide6.QtStateMachine",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="InkIt",
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
    icon="icon.ico",
)
