# -*- mode: python ; coding: utf-8 -*-
# InkIt.spec — PyInstaller ONEFILE build of InkIt.exe
#
#   pyinstaller --clean --noconfirm InkIt.spec      (or just run build.bat)
#
# Bundled next to app.py inside the exe (sys._MEIPASS at runtime):
#   ffmpeg.exe              export encoder  (get_ffmpeg_path() looks here first)
#   icons/*.png             reskinnable button art (ICONS_DIR = APP_DIR / "icons")
#   icon.ico / icon.png     window + taskbar icon (app_icon_path())
#   shelf_icon.png          Maya shelf button art (_maya_script_paths())
#   maya_inkit_sync.py      Maya sync script deployed into Documents/maya
#   inkit_bootstrap.py      Maya userSetup bootstrap
import os
import shutil
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(SPECPATH).resolve()
BUILD_TMP = ROOT / "build" / "_bundle"
BUILD_TMP.mkdir(parents=True, exist_ok=True)

# imageio-ffmpeg ships a versioned binary name (ffmpeg-win-x86_64-v7.1.exe);
# the app expects plain "ffmpeg.exe" at the bundle root, so stage a copy.
_ff_src = Path(imageio_ffmpeg.get_ffmpeg_exe())
_ff_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
_ff_dst = BUILD_TMP / _ff_name
shutil.copy2(_ff_src, _ff_dst)

datas = [
    (str(ROOT / "icons"), "icons"),
    (str(ROOT / "icon.ico"), "."),
    (str(ROOT / "icon.png"), "."),
    (str(ROOT / "shelf_icon.png"), "."),
    (str(ROOT / "maya_inkit_sync.py"), "."),
    (str(ROOT / "inkit_bootstrap.py"), "."),
]
binaries = [(str(_ff_dst), ".")]

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PySide6.QtMultimedia"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick3D"],
    noarchive=False,
)
# Drop imageio-ffmpeg's own copy of the binary: ffmpeg.exe is already bundled
# at the root, which get_ffmpeg_path() checks first (saves ~30-80 MB).
a.datas = [d for d in a.datas if "imageio_ffmpeg/binaries" not in d[0].replace("\\", "/")]
a.binaries = [b for b in a.binaries if "imageio_ffmpeg/binaries" not in b[0].replace("\\", "/")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="InkIt",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    icon=str(ROOT / "icon.ico"),
)
