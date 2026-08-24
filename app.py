#!/usr/bin/env python3
"""
=============================================================================
InkIt — Frame-Accurate Video Player with Pressure-Sensitive Drawing
=============================================================================

ARCHITECTURE & COMPONENT MAP:
-----------------------------------------------------------------------------
1. SYSTEM & DEPENDENCIES CONFIGURATION
   - Core libraries: PySide6 (Qt6), OpenCV (cv2), NumPy
   - get_ffmpeg_path(): Resolves FFmpeg binary across PyInstaller frozen bundles,
     imageio_ffmpeg, local directory, PATH, and system installations.

2. DATA MODELS & SERIALIZATION
   - Point: Sub-pixel coordinate (x, y) with stylus pressure (0.0 to 1.0)
   - Stroke: Collection of points with color, base width, opacity, eraser flag
   - Project: Video metadata, frame count, FPS, per-frame stroke map, JSON I/O

3. VIDEO DECODING & HELPERS
   - VideoReader: OpenCV VideoCapture wrapper with single-frame caching
   - bgr_to_qimage(): Fast color conversion from OpenCV BGR to Qt QImage
   - config_dir(): Roaming application data directory (%APPDATA%/InkIt)
   - DEFAULT_SHORTCUTS & ACTION_LABELS: Global keyboard shortcut maps

4. CUSTOM UI WIDGETS & DIALOGS
   - TimelineSlider: Custom scrub bar with yellow dots for annotated frames
   - ShortcutDialog: Interactive table for customizing keyboard shortcuts
   - SettingsDialog: Dialog to configure autosave folder & clean up backups
   - ClickableLabel: Hand-cursor label for toggling frame/time display

5. VECTOR ICONS & GRAPHICS HELPERS
   - make_nav_icon(): Procedural vector navigation arrows (prev/next/drawings)
   - make_tool_icon(): Procedural vector tool icons (eye, trash, loop, audio)
   - app_icon_path(): Resolves icon.ico/icon.png across frozen and source modes
   - write_default_icon(): Procedural icon generator fallback

6. TIME & AUTOSAVE UTILITIES
   - default_settings(): App defaults (colors, opacities, paths, history)
   - default_autosave_dir(): Default autosave location (%USERPROFILE%/Documents/InkIt/Autosave)
   - is_autosave_name(): Identifies autosaved backup filenames
   - format_time(): Converts frame number to timecode string

7. DRAWING CANVAS (Canvas Widget)
   - Coordinate transformation: Normalizes screen points (0.0 to 1.0)
   - Pressure-sensitive brush engine with antialiased geometric interpolation
   - Isolated layer opacity compositing (prevents stamp overlapping artifacts)
   - Mouse & Wacom Stylus tablet event handlers (pressure, eraser tip, barrel buttons)
   - Middle-click mouse scrubbing & scroll wheel stepping

8. VIDEO EXPORT ENGINE
   - render_annotations_on_bgr(): High-quality alpha compositing onto video frames
   - ExportWorker: Background QThread encoding MP4/MOV with FFmpeg subprocess

9. MAIN APPLICATION WINDOW (MainWindow)
   - UI Construction (_build_ui):
       * Toolbar Controls (Pen, Eraser, Color Picker, 4 Fixed Color Circles)
       * Size Slider (1 to 80 px)
       * Pen Opacity Slider (0% to 100%)
       * Clip Opacity Slider (0% to 100%)
       * Notes Opacity Slider (0% to 100%)
       * Hide Notes Button & Clear Frame Button
       * Playback Controls (Play/Pause, Prev/Next Frame, Prev/Next Drawing)
       * Loop Toggle & Audio Toggle (with right-click export audio context menu)
       * Timeline Slider Scrubber & Timecode / Frame Display
   - Menus, Shortcuts, Video / Scene loading, Settings persistence, Audio sync

10. SYSTEM INTEGRATION & ENTRY POINT
   - register_inkit_association(): Windows Registry .inkit file association
   - main(): QApplication startup, arguments parser, window presentation
=============================================================================
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Packaged (Nuitka) bootstrap: point Qt at plugins bundled next to the exe.
# Dev runs never hit this — "__compiled__" only exists under Nuitka.
if "__compiled__" in globals():
    _base = os.path.dirname(os.path.abspath(__file__))
    _qplug = os.path.join(_base, "QtPlugins")
    if os.path.isdir(_qplug):
        os.environ["QT_PLUGIN_PATH"] = _qplug
        try:
            os.add_dll_directory(_qplug)
        except OSError:
            pass

import cv2
import numpy as np
from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QPointingDevice,
    QRadialGradient,
    QTabletEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QAbstractItemView,
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ===========================================================================
# 1. SYSTEM & DEPENDENCIES CONFIGURATION (FFmpeg Detection)
# ===========================================================================

def get_ffmpeg_path() -> str:
    """
    Locates a working FFmpeg executable across various deployment environments:
    1. PyInstaller bundled temp directory (sys._MEIPASS when frozen as onefile)
    2. Local folder next to the .exe / script
    3. Bundled imageio_ffmpeg binary package
    4. System PATH (shutil.which)
    5. Standard Windows installation fallbacks (WinGet / Program Files)
    """
    # 1. PyInstaller extracted bundle (standalone onefile executable)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass_ffmpeg = Path(sys._MEIPASS) / "ffmpeg.exe"
        if meipass_ffmpeg.is_file():
            return str(meipass_ffmpeg)

    # 2. Alongside the executable or python script
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    local_ffmpeg = exe_dir / "ffmpeg.exe"
    if local_ffmpeg.is_file():
        return str(local_ffmpeg)

    # 3. imageio_ffmpeg packaged binary
    try:
        import imageio_ffmpeg
        img_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if img_ffmpeg and Path(img_ffmpeg).is_file():
            return str(img_ffmpeg)
    except Exception:
        pass

    # 4. System PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    # 5. Known Windows fallback paths
    fallbacks = [
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-8.1.2-full_build" / "bin" / "ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for fb in fallbacks:
        if Path(fb).is_file():
            return str(fb)

    return "ffmpeg"


# Resolved global FFmpeg binary path
FFMPEG = get_ffmpeg_path()


# ===========================================================================
# 2. DATA MODELS & SERIALIZATION
# ===========================================================================

@dataclass
class Point:
    x: float
    y: float
    p: float  # 0..1 pressure


@dataclass
class Stroke:
    color: str
    base_width: float
    points: list[Point] = field(default_factory=list)
    eraser: bool = False
    opacity: float = 1.0  # 0..1
    hardness: float = 1.0  # 0=soft .. 1=hard


class Project:
    def __init__(self) -> None:
        self.path: str | None = None
        self.fps: float = 24.0
        self.frame_count: int = 0
        self.width: int = 0
        self.height: int = 0
        self.strokes: dict[int, list[Stroke]] = {}
        self.scene_path: str | None = None
        self.user_saved: bool = False

    def strokes_at(self, f: int) -> list[Stroke]:
        return self.strokes.setdefault(f, [])

    def annotated_frames(self) -> list[int]:
        return sorted(
            f for f, ss in self.strokes.items() if any(s.points for s in ss)
        )

    def to_json(self) -> dict:
        def stroke_d(s: Stroke) -> dict:
            return {
                "color": s.color,
                "base_width": s.base_width,
                "eraser": s.eraser,
                "opacity": s.opacity,
                "hardness": float(getattr(s, "hardness", 1.0)),
                "points": [{"x": p.x, "y": p.y, "p": p.p} for p in s.points],
            }

        return {
            "video": self.path,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "strokes": {str(k): [stroke_d(s) for s in v] for k, v in self.strokes.items() if v},
        }

    def load_json(self, data: dict, scene_dir: Path | None = None) -> None:
        self.strokes.clear()
        video = data.get("video") or ""
        if video:
            p = Path(video)
            if not p.is_file() and scene_dir is not None:
                alt = scene_dir / p.name
                if alt.is_file():
                    p = alt
            self.path = str(p) if p.is_file() else video
        # Restore timeline geometry so media-less board scenes reopen correctly
        if data.get("fps"):
            self.fps = float(data["fps"])
        if data.get("frame_count"):
            self.frame_count = int(data["frame_count"])
        if data.get("width"):
            self.width = int(data["width"])
        if data.get("height"):
            self.height = int(data["height"])
        for k, arr in data.get("strokes", {}).items():
            self.strokes[int(k)] = [
                Stroke(
                    color=s["color"],
                    base_width=s["base_width"],
                    eraser=s.get("eraser", False),
                    opacity=float(s.get("opacity", 1.0)),
                    hardness=float(s.get("hardness", 1.0)),
                    points=[Point(pt["x"], pt["y"], pt["p"]) for pt in s["points"]],
                )
                for s in arr
            ]


# ===========================================================================
# 3. VIDEO DECODING & HELPERS
# ===========================================================================

class VideoReader:
    """
    Fast, frame-accurate video frame reader using OpenCV VideoCapture.
    Maintains a single-frame cache to prevent redundant seeks and decodes.
    """
    def __init__(self) -> None:
        self.cap: cv2.VideoCapture | None = None
        self.path: str | None = None
        self._last_idx = -1
        self._last_bgr: np.ndarray | None = None

    def open(self, path: str) -> tuple[int, float, int, int]:
        """Opens video file and returns (frame_count, fps, width, height)."""
        self.close()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video:\n{path}")
        self.cap = cap
        self.path = path
        self._last_idx = -1
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        if fps <= 1e-3:
            fps = 24.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        return count, fps, w, h

    def frame(self, index: int) -> np.ndarray | None:
        """Retrieves raw BGR frame by index (0-based) with seek optimization."""
        if self.cap is None:
            return None
        index = max(0, index)
        # Return cached frame if requested again
        if index == self._last_idx and self._last_bgr is not None:
            return self._last_bgr
        # Only seek if not strictly sequential next frame
        if index != self._last_idx + 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, bgr = self.cap.read()
        if not ok:
            # Fallback seek attempt
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, bgr = self.cap.read()
            if not ok:
                return self._last_bgr
        self._last_idx = index
        self._last_bgr = bgr
        return bgr

    def close(self) -> None:
        """Releases the video capture resource."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self._last_idx = -1
        self._last_bgr = None


def bgr_to_qimage(bgr: np.ndarray) -> QImage:
    """Converts an OpenCV BGR numpy array to a Qt QImage (RGB888)."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    return QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def _is_image_path(p: str | Path) -> bool:
    """True if `p` looks like a still image InkIt can open as a drawable canvas."""
    return str(p).lower().endswith(IMAGE_EXTS)


DEFAULT_PRESSURE_CURVE = [[0.0, 0.02], [1.0, 1.0]]  # hairline at zero press -> full pen size


def normalize_pressure_curve(curve) -> list[list[float]]:
    """Clamps/sorts curve points to [x,y] in [0,1] with endpoints pinned at x=0 and x=1."""
    pts: list[list[float]] = []
    for pt in (curve or []):
        try:
            x, y = float(pt[0]), float(pt[1])
        except Exception:
            continue
        pts.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
    if len(pts) < 2:
        return [list(p) for p in DEFAULT_PRESSURE_CURVE]
    pts.sort(key=lambda q: q[0])
    pts[0][0] = 0.0
    out = [pts[0]]
    for q in pts[1:-1]:
        if q[0] - out[-1][0] >= 0.02 and out[-1][0] <= 0.98:
            out.append(q)
    last = pts[-1]
    if out[-1][0] > 0.98:
        out.pop()
    out.append([1.0, last[1]])
    return out


def eval_pressure_curve(curve, p: float) -> float:
    """Maps raw stylus pressure p (0..1) to a width fraction of the selected pen size."""
    pts = normalize_pressure_curve(curve)
    p = min(max(float(p), 0.0), 1.0)
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        if p <= x1 or i == len(pts) - 1:
            t = 0.0 if x1 <= x0 else (p - x0) / (x1 - x0)
            return min(max(y0 + (y1 - y0) * t, 0.0), 1.0)
    return pts[-1][1]


def config_dir() -> Path:
    """Returns application config folder (%APPDATA%/InkIt), migrating old settings if found."""
    p = Path.home() / "AppData" / "Roaming" / "InkIt"
    old = Path.home() / "AppData" / "Roaming" / "FrameNotes"
    if not p.exists() and old.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
            for name in ("settings.json", "shortcuts.json"):
                src = old / name
                if src.is_file() and not (p / name).exists():
                    (p / name).write_bytes(src.read_bytes())
        except Exception:
            p.mkdir(parents=True, exist_ok=True)
    else:
        p.mkdir(parents=True, exist_ok=True)
    return p


# ===========================================================================
# ██ SHORTCUTS / LABELS / DESCRIPTIONS / ICONS — SINGLE SOURCE OF TRUTH ██
#
# HOW TO ADD OR CHANGE A SHORTCUT
#   1. Edit DEFAULT_SHORTCUTS below ("key": "Ctrl+Shift+X").
#   2. The Settings ▸ Shortcuts editor lists every entry automatically and
#      persists user overrides to config_dir()/shortcuts.json.
#
# HOW TO ADD A WHOLE NEW COMMAND
#   1. Add one line to each: DEFAULT_SHORTCUTS, ACTION_LABELS,
#      ACTION_DESCRIPTIONS (status-bar/tooltip text).
#   2. In _build_menus():  self._act("<key>", handler)   — that's all.
#
# BUTTON ↔ SHORTCUT LINKING
#   BUTTON_ACTION_KEYS maps a widget attribute (e.g. "btn_onion") to an action
#   key. _apply_shortcuts() then appends "[shortcut]" to that button's tooltip
#   and sets its statusTip so hovering shows what it does in the status bar.
#
# ICON FILES
#   Every icon kind is exported to <app folder>/icons/<kind>.png by
#   ensure_icon_assets() (runs once at startup). Replace a PNG there to
#   reskin that button — the loader prefers your file over the built-in
#   vector art. icons/names.txt documents every file.
# ===========================================================================

DEFAULT_SHORTCUTS: dict[str, str] = {
    # -- File ---------------------------------------------------------
    "open_video": "Ctrl+O",
    "open_scene": "Ctrl+Shift+O",
    "save_scene": "Ctrl+S",
    "export": "Ctrl+E",
    "quit": "Ctrl+Q",
    # -- Edit ---------------------------------------------------------
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Shift+Z",
    "clear_frame": "Delete",
    # -- Tools --------------------------------------------------------
    "pen": "B",
    "eraser": "E",
    "brush_smaller": "[",
    "brush_larger": "]",
    "pick_color": "C",
    # -- View ---------------------------------------------------------
    "toggle_notes": "H",
    "zoom_mode": "Z",
    "pan_mode": "X",
    "reset_view": "Ctrl+0",
    "onion": "O",
    "frame_overlay": "Ctrl+Shift+H",
    "queue_panel": "Ctrl+L",
    "time_mode": "Ctrl+T",
    "show_thumbs": "Ctrl+Shift+T",
    "settings": "Ctrl+,",
    # -- Playback -----------------------------------------------------
    "play_pause": "Space",
    "prev_frame": "Left",
    "next_frame": "Right",
    "prev_drawing": "Up",
    "next_drawing": "Down",
    "toggle_loop": "L",
}

ACTION_LABELS: dict[str, str] = {
    "open_video": "Open…",
    "open_scene": "Open scene",
    "save_scene": "Save scene",
    "export": "Export with notes",
    "quit": "Quit",
    "undo": "Undo stroke",
    "redo": "Redo stroke",
    "clear_frame": "Clear frame",
    "pen": "Pen ↔ Eraser (toggle)",
    "eraser": "Eraser tool",
    "brush_smaller": "Smaller brush",
    "brush_larger": "Larger brush",
    "pick_color": "Pick color from screen",
    "toggle_notes": "Toggle notes visibility",
    "zoom_mode": "View nav on/off",
    "pan_mode": "View nav on/off",
    "reset_view": "Reset zoom & pan",
    "onion": "Onion skin",
    "frame_overlay": "Frame counter overlay",
    "queue_panel": "Shot list",
    "time_mode": "Frames / time display",
    "show_thumbs": "Show thumbnails",
    "settings": "Settings…",
    "play_pause": "Play / pause",
    "prev_frame": "Previous frame",
    "next_frame": "Next frame",
    "prev_drawing": "Previous drawing",
    "next_drawing": "Next drawing",
    "toggle_loop": "Loop playback",
}

# Status-bar sentence shown while hovering the control (and used in tooltips).
ACTION_DESCRIPTIONS: dict[str, str] = {
    "open_video": "Open a video clip or still image",
    "open_scene": "Open a saved InkIt scene",
    "save_scene": "Save annotations as an InkIt scene",
    "export": "Export the video/image with notes burned in",
    "quit": "Close InkIt (autosaves first)",
    "undo": "Undo the last stroke on this frame",
    "redo": "Re-apply the last undone stroke",
    "clear_frame": "Erase all drawings on this frame",
    "pen": "Toggle between pen and eraser — [ and ] resize the brush",
    "eraser": "Select the eraser tool",
    "brush_smaller": "Decrease brush size",
    "brush_larger": "Increase brush size",
    "pick_color": "Eyedropper — hover anywhere on screen, click to set the pen color",
    "toggle_notes": "Show or hide all drawings",
    "zoom_mode": "Toggle View nav — middle-drag pans, right-drag zooms, double-click resets",
    "pan_mode": "Toggle View nav — middle-drag pans, right-drag zooms, double-click resets",
    "reset_view": "Reset zoom & pan to fit",
    "onion": "Ghost nearby drawings for reference",
    "frame_overlay": "Frame counter badge in the canvas corner",
    "queue_panel": "Show or hide the shot list panel",
    "time_mode": "Switch timeline between frames and timecode",
    "show_thumbs": "Show or hide shot list thumbnails",
    "settings": "Open the settings dialog",
    "play_pause": "Play or pause playback",
    "prev_frame": "Step one frame back",
    "next_frame": "Step one frame forward",
    "prev_drawing": "Jump to the previous drawing",
    "next_drawing": "Jump to the next drawing",
    "toggle_loop": "Loop playback at the end",
}

# Toolbar/playback buttons linked to their action keys (tooltip + status bar).
BUTTON_ACTION_KEYS: dict[str, str] = {
    "btn_tool": "pen",
    "btn_view": "zoom_mode",
    "btn_onion": "onion",
    "btn_picker": "pick_color",
    "btn_clear": "clear_frame",
    "btn_hide": "toggle_notes",
    "btn_play": "play_pause",
    "btn_prev": "prev_frame",
    "btn_next": "next_frame",
    "btn_prev_draw": "prev_drawing",
    "btn_next_draw": "next_drawing",
    "btn_loop": "toggle_loop",
    "btn_q_thumbs": "show_thumbs",
}


# Dark / light UI palettes. Keys are referenced by the window stylesheet and
# dynamic restylers; canvas painting reads the same values via Canvas.theme.
THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#1a1a1c", "panel": "#111114", "wrap": "#232329",
        "card": "#2a2a30", "card_hover": "#35353c",
        "border": "#3a3a42", "sep": "#4a4a55",
        "list_bg": "#16161a", "text": "#eeeeee", "subtext": "#bbbbbb",
        "muted": "#888888", "accent": "#3d5afe",
    },
    "light": {
        "bg": "#f2f2f5", "panel": "#fafafc", "wrap": "#e9e9ee",
        "card": "#ffffff", "card_hover": "#dfe0e8",
        "border": "#c6c7cf", "sep": "#b4b5bf",
        "list_bg": "#ffffff", "text": "#222226", "subtext": "#55555c",
        "muted": "#8a8a92", "accent": "#3d5afe",
    },
}


# ===========================================================================
# 4. CUSTOM UI WIDGETS & DIALOGS
# ===========================================================================

# ---------------------------------------------------------------------------
# [WIDGET] TimelineSlider: Timeline scrubber with drawing indicators
# ---------------------------------------------------------------------------
class TimelineSlider(QSlider):
    """
    Custom horizontal QSlider representing the video timeline.
    Draws yellow dots above frames that contain annotations/drawings.
    Supports instant click-to-position scrubbing.
    """
    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.marks: set[int] = set()
        self.setPageStep(1)
        self.setSingleStep(1)

    def _value_at(self, x: float) -> int:
        """Calculates frame value corresponding to horizontal pixel x."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
        )
        span = max(1, groove.width() - handle.width())
        pos = x - groove.x() - handle.width() / 2
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(pos), int(span), False)

    def mousePressEvent(self, ev) -> None:
        """Immediate jump on left click."""
        if ev.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_at(ev.position().x()))
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        """Continuous scrubbing while dragging."""
        if self.isSliderDown() and ev.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(ev.position().x()))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def set_marks(self, frames: list[int] | set[int]) -> None:
        """Updates frame numbers that have drawings and repaints markers."""
        nxt = set(frames)
        if nxt != self.marks:
            self.marks = nxt
            self.update()

    def paintEvent(self, ev) -> None:
        """Paints standard slider groove/handle and overlays yellow drawing marker dots."""
        super().paintEvent(ev)
        if not self.marks or self.maximum() <= self.minimum():
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
        )
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        span = max(1, groove.width() - handle.width())
        x0 = groove.x() + handle.width() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffcc00"))  # Yellow marker dot color
        for f in self.marks:
            x = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), int(f), int(span), False)
            p.drawEllipse(QPointF(x0 + x, groove.center().y()), 3.2, 3.2)
        p.end()


# ---------------------------------------------------------------------------
# [DIALOG] ShortcutDialog: Keyboard Shortcut Configuration Modal
# ---------------------------------------------------------------------------
class ShortcutDialog(QDialog):
    """Interactive modal dialog displaying a table of actions and key sequence editors."""
    def __init__(self, current: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shortcuts")
        self.resize(520, 520)
        self._edits: dict[str, QKeySequenceEdit] = {}
        layout = QVBoxLayout(self)
        hint = QLabel("Click a shortcut and press the keys you want. Existing shortcuts are shown.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        table = QTableWidget(len(ACTION_LABELS), 2)
        table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, key in enumerate(ACTION_LABELS):
            name = QTableWidgetItem(ACTION_LABELS[key])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 0, name)
            edit = QKeySequenceEdit()
            seq = current.get(key, DEFAULT_SHORTCUTS.get(key, ""))
            if seq:
                edit.setKeySequence(QKeySequence(seq))
            table.setCellWidget(row, 1, edit)
            self._edits[key] = edit
        layout.addWidget(table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._defaults)
        layout.addWidget(buttons)

    def _defaults(self) -> None:
        """Resets all shortcut input fields to standard default bindings."""
        for key, edit in self._edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS.get(key, "")))

    def result_map(self) -> dict[str, str]:
        """Collects current key sequences from all table fields."""
        out: dict[str, str] = {}
        for key, edit in self._edits.items():
            out[key] = edit.keySequence().toString(QKeySequence.SequenceFormat.NativeText)
        return out


# ---------------------------------------------------------------------------
# [WIDGET] PressureCurveEdit: Krita/Photoshop-style pressure response editor
# ---------------------------------------------------------------------------

class PressureCurveEdit(QWidget):
    """Editable pressure→width curve. Drag handles, double-click adds, right-click removes."""

    curveChanged = Signal()
    MAX_POINTS = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points = [list(p) for p in DEFAULT_PRESSURE_CURVE]
        self._drag: int = -1
        self.setStyleSheet("background:#1a1a1c; border:1px solid #3a3a42; border-radius:4px;")

    # -- mapping helpers ---------------------------------------------------

    def _plot_rect(self) -> QRectF:
        m = 16.0
        return QRectF(m, m, max(10.0, self.width() - 2 * m), max(10.0, self.height() - 2 * m))

    def _to_px(self, pt) -> QPointF:
        r = self._plot_rect()
        return QPointF(r.x() + pt[0] * r.width(), r.y() + (1.0 - pt[1]) * r.height())

    def _from_px(self, pos: QPointF):
        r = self._plot_rect()
        x = min(max((pos.x() - r.x()) / max(1.0, r.width()), 0.0), 1.0)
        y = min(max(1.0 - (pos.y() - r.y()) / max(1.0, r.height()), 0.0), 1.0)
        return [x, y]

    def _hit_index(self, pos: QPointF) -> int | None:
        for i, pt in enumerate(self.points):
            if (self._to_px(pt) - pos).manhattanLength() < 24:
                return i
        return None

    # -- public API ---------------------------------------------------------

    def set_curve(self, pts) -> None:
        self.points = normalize_pressure_curve(pts)
        self.update()

    def curve(self) -> list[list[float]]:
        return normalize_pressure_curve(self.points)

    # -- mouse editing ------------------------------------------------------

    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        if ev.button() == Qt.MouseButton.RightButton:
            hit = self._hit_index(pos)
            if hit is not None and 0 < hit < len(self.points) - 1 and len(self.points) > 2:
                del self.points[hit]
                self.update()
                self.curveChanged.emit()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit_index(pos)
        if hit is None and len(self.points) < self.MAX_POINTS:
            cand = self._from_px(pos)
            insert_at = len(self.points)
            for i, pt in enumerate(self.points):
                if cand[0] < pt[0]:
                    insert_at = i
                    break
            too_close = any(abs(cand[0] - q[0]) < 0.04 for q in self.points)
            if not too_close:
                self.points.insert(insert_at, cand)
                hit = insert_at
                self.update()
                self.curveChanged.emit()
        if hit is None:
            hit = self._nearest(pos)
        self._drag = hit
        if hit is not None:
            self._drag_to(pos)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None:
            self._drag_to(ev.position())

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = -1

    def _nearest(self, pos: QPointF) -> int | None:
        best, best_d = None, 1e9
        for i, pt in enumerate(self.points):
            d = (self._to_px(pt) - pos).manhattanLength()
            if d < best_d:
                best, best_d = i, d
        return best

    def _drag_to(self, pos: QPointF) -> None:
        i = self._drag
        if not (0 <= i < len(self.points)):
            return
        x, y = self._from_px(pos)
        first, last = (i == 0), (i == len(self.points) - 1)
        lo = 0.02 if not first else 0.0
        hi = 0.98 if not last else 1.0
        prev_x = self.points[i - 1][0] if i > 0 else 0.0
        next_x = self.points[i + 1][0] if i < len(self.points) - 1 else 1.0
        x = min(max(x, prev_x + 0.02, lo), max(next_x - 0.02, hi))
        y = min(max(y, 0.0), 1.0)
        self.points[i] = [x, y]
        self.points = normalize_pressure_curve(self.points)
        idx = min(range(len(self.points)), key=lambda k: abs(self.points[k][0] - x))
        self._drag = idx
        self.update()
        self.curveChanged.emit()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#55555f"), 1))
        for gx in range(1, 4):
            x = self._to_px([gx / 4.0, 0.0]).x()
            p.drawLine(QPointF(x, self._plot_rect().top()), QPointF(x, self._plot_rect().bottom()))
        for gy in range(1, 4):
            y = self._to_px([0.0, gy / 4.0]).y()
            p.drawLine(QPointF(self._plot_rect().left(), y), QPointF(self._plot_rect().right(), y))
        p.setPen(QPen(QColor("#8a8a95"), 1))
        p.drawText(QRectF(0, self.height() - 15, self.width(), 14), Qt.AlignmentFlag.AlignCenter, "pen pressure →")
        p.save()
        p.translate(11, self.height() / 2.0)
        p.rotate(-90)
        p.drawText(QRectF(-60, -40, 120, 14), Qt.AlignmentFlag.AlignCenter, "stroke width")
        p.restore()
        p.setPen(QPen(QColor("#e6e6ee"), 2))
        poly = [self._to_px(q) for q in self.points]
        for a, b in zip(poly, poly[1:]):
            p.drawLine(a, b)
        for pt in self.points:
            c = self._to_px(pt)
            p.setBrush(QBrush(QColor("#ffffff" if self._drag != self.points.index(pt) else "#ffd166")))
            p.setPen(QPen(QColor("#202028"), 1))
            p.drawEllipse(c, 4.5, 4.5)


# ---------------------------------------------------------------------------
# [DIALOG] SettingsDialog: Sidebar layout with Autosave / Cursor / Pen / Shortcuts pages
# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    """Settings window with a left navigation sidebar and stacked pages."""

    def __init__(self, current: Path, default: Path, cursor: dict | None = None, shortcuts: dict | None = None, parent=None, autodelete: bool = True, max_days: int = 90, onion_opacity: int = 35) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(660, 540)
        self._default = default
        self.deleted = False
        cursor = cursor or {}

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        # --- Left navigation sidebar -------------------------------------
        self._nav = QListWidget()
        self._nav.setFixedWidth(128)
        self._nav.setObjectName("settingsNav")
        self._nav.setStyleSheet(
            "QListWidget#settingsNav { background: #16161a; border: 1px solid #3a3a42;"
            " border-radius: 4px; outline: 0; font-weight: 600; }"
            "QListWidget#settingsNav::item { padding: 8px 10px; color: #bbbbbb; }"
            "QListWidget#settingsNav::item:selected { background: #2a3ec4; color: #ffffff;"
            " border-radius: 3px; }"
        )
        for title in ("Autosave", "Cursor", "Pen", "Onion Skinning", "Shortcuts"):
            self._nav.addItem(title)
        body.addWidget(self._nav)

        # --- Stacked pages ------------------------------------------------
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_autosave_page(current, autodelete, max_days))
        self._stack.addWidget(self._build_cursor_page(cursor))
        self._stack.addWidget(self._build_pen_page(cursor))
        self._stack.addWidget(self._build_onion_page(cursor, onion_opacity))
        self._stack.addWidget(self._build_shortcuts_page(shortcuts or {}))

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.custom_edit.textChanged.connect(self._update_preview)
        self._on_mode_changed()

    # -- Page builders ------------------------------------------------------

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        return w, v

    def _build_autosave_page(self, current: Path, autodelete: bool = True, max_days: int = 90) -> QWidget:
        page, av = self._page()
        av.addWidget(QLabel("Autosave location"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(current))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        av.addLayout(row)
        btns = QHBoxLayout()
        use_def = QPushButton("Default")
        use_def.clicked.connect(self._use_default)
        delete = QPushButton("Delete autosave notes…")
        delete.clicked.connect(self._delete_notes)
        btns.addWidget(use_def)
        btns.addWidget(delete)
        btns.addStretch(1)
        av.addLayout(btns)

        # --- Auto-delete old autosave files (age in days, 1..365) ----------
        arow = QHBoxLayout()
        self.autodelete_cb = QCheckBox("Auto-delete autosaves older than")
        self.autodelete_cb.setChecked(bool(autodelete))
        self.autodelete_cb.setToolTip("Purge autosave files older than the age below")
        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 365)
        self.days_spin.setValue(int(max_days))
        self.days_spin.setSuffix(" days")
        self.days_spin.setEnabled(bool(autodelete))
        self.autodelete_cb.toggled.connect(self.days_spin.setEnabled)
        arow.addWidget(self.autodelete_cb)
        arow.addWidget(self.days_spin)
        arow.addStretch(1)
        av.addLayout(arow)
        av.addStretch(1)
        return page

    def autodelete_settings(self) -> tuple[bool, int]:
        """Returns (enabled, max_age_days) for autosave auto-deletion."""
        return bool(self.autodelete_cb.isChecked()), int(self.days_spin.value())

    def _build_cursor_page(self, cursor: dict) -> QWidget:
        page, cv = self._page()
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Mouse cursor"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Circle (shows brush size)", "circle")
        self.mode_combo.addItem("Crosshair", "crosshair")
        self.mode_combo.addItem("System default", "default")
        self.mode_combo.addItem("Custom image…", "custom")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(cursor.get("mode", "circle"))))
        crow.addWidget(self.mode_combo, 1)
        cv.addLayout(crow)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Cursor icon"))
        self.custom_edit = QLineEdit(str(cursor.get("custom", "")))
        self.custom_edit.setPlaceholderText("Pick your own cursor image (.png .ico .cur .jpg …)")
        fbrowse = QPushButton("Select icon…")
        fbrowse.clicked.connect(self._browse_cursor)
        frow.addWidget(self.custom_edit, 1)
        frow.addWidget(fbrowse)
        cv.addLayout(frow)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("Circle color"))
        self.circle_color = QColor(cursor.get("color", "#ffffff"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(36, 22)
        self._sync_color_btn()
        self.color_btn.clicked.connect(self._pick_circle_color)
        orow.addWidget(self.color_btn)
        orow.addWidget(QLabel("Outline"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 6)
        self.width_spin.setValue(int(cursor.get("width", 2)))
        self.width_spin.setToolTip("Circle outline thickness (px)")
        self.width_spin.valueChanged.connect(self._update_preview)
        orow.addWidget(self.width_spin)
        self.dot_check = QCheckBox("Center dot")
        self.dot_check.setChecked(bool(cursor.get("dot", True)))
        self.dot_check.toggled.connect(self._update_preview)
        orow.addWidget(self.dot_check)
        orow.addStretch(1)
        cv.addLayout(orow)

        prow = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(240, 60)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background:#1a1a1c; border:1px solid #3a3a42; border-radius:4px;")
        prow.addWidget(self.preview)
        prow.addStretch(1)
        cv.addLayout(prow)
        cv.addStretch(1)
        return page

    def _build_pen_page(self, cursor: dict) -> QWidget:
        page, pv = self._page()
        self.aa_check = QCheckBox("Antialiasing (smooth stroke edges)")
        self.aa_check.setChecked(bool(cursor.get("antialias", True)))
        pv.addWidget(self.aa_check)
        pv.addWidget(QLabel("Soft ↔ hard brush: use the Hardness slider in the toolbar."))

        pv.addSpacing(10)
        title = QLabel("Pressure curve — pen pressure → stroke width")
        title.setStyleSheet("font-weight: 600;")
        pv.addWidget(title)
        self.curve_edit = PressureCurveEdit(self)
        self.curve_edit.set_curve(cursor.get("pressure_curve") or [list(p) for p in DEFAULT_PRESSURE_CURVE])
        self.curve_edit.setFixedSize(300, 220)
        pv.addWidget(self.curve_edit)
        hint = QLabel(
            "Left = how hard you press · bottom = hairline · top = full pen size.\n"
            "Drag points to shape the response, double-click to add, right-click to remove."
        )
        hint.setWordWrap(True)
        pv.addWidget(hint)
        reset_row = QHBoxLayout()
        btn_reset_curve = QPushButton("Reset to default")
        btn_reset_curve.clicked.connect(
            lambda: self.curve_edit.set_curve([list(p) for p in DEFAULT_PRESSURE_CURVE])
        )
        reset_row.addWidget(btn_reset_curve)
        reset_row.addStretch(1)
        pv.addLayout(reset_row)

        pv.addStretch(1)
        return page

    def _build_onion_page(self, cursor: dict, onion_opacity: int = 35) -> QWidget:
        """Dedicated Onion Skinning settings section."""
        page, ov = self._page()
        title = QLabel("Ghost visibility")
        title.setStyleSheet("font-weight: 600;")
        ov.addWidget(title)
        srow = QHBoxLayout()
        self.onion_vis_slider = QSlider(Qt.Orientation.Horizontal)
        self.onion_vis_slider.setRange(5, 100)
        self.onion_vis_slider.setValue(int(min(max(int(onion_opacity), 5), 100)))
        self.onion_vis_label = QLabel(f"{self.onion_vis_slider.value()}%")
        self.onion_vis_label.setFixedWidth(38)
        self.onion_vis_slider.valueChanged.connect(
            lambda v: self.onion_vis_label.setText(f"{v}%")
        )
        srow.addWidget(self.onion_vis_slider, 1)
        srow.addWidget(self.onion_vis_label)
        ov.addLayout(srow)
        hint = QLabel(
            "Overall brightness of ghosted drawings. The drawing nearest the "
            "current one is the brightest; older ones fade with distance."
        )
        hint.setWordWrap(True)
        ov.addWidget(hint)

        ov.addSpacing(10)
        onion_lbl = QLabel("Ghost colors")
        onion_lbl.setStyleSheet("font-weight: 600;")
        ov.addWidget(onion_lbl)
        orow = QHBoxLayout()
        self.btn_onion_prev = QPushButton()
        self.btn_onion_prev.setFixedSize(34, 24)
        self.btn_onion_prev.setToolTip("Color for the previous frame's ghost")
        self.btn_onion_prev.clicked.connect(self._pick_onion_prev)
        self.btn_onion_next = QPushButton()
        self.btn_onion_next.setFixedSize(34, 24)
        self.btn_onion_next.setToolTip("Color for the next frame's ghost")
        self.btn_onion_next.clicked.connect(self._pick_onion_next)
        orow.addWidget(QLabel("Previous"))
        orow.addWidget(self.btn_onion_prev)
        orow.addSpacing(14)
        orow.addWidget(QLabel("Next"))
        orow.addWidget(self.btn_onion_next)
        orow.addStretch(1)
        ov.addLayout(orow)
        self._sync_onion_swatches(cursor.get("onion_prev", "#c248ff"), cursor.get("onion_next", "#33ccff"))

        tip = QLabel("Toggle ghosts with the onion button on the toolbar.")
        tip.setWordWrap(True)
        ov.addWidget(tip)
        ov.addStretch(1)
        return page

    def onion_visibility(self) -> int:
        return int(self.onion_vis_slider.value())

    def _sync_onion_swatches(self, prev_hex: str, next_hex: str) -> None:
        self._onion_prev_hex = prev_hex
        self._onion_next_hex = next_hex
        self.btn_onion_prev.setStyleSheet(
            f"background:{prev_hex}; border:1px solid #55555f; border-radius:3px;"
        )
        self.btn_onion_next.setStyleSheet(
            f"background:{next_hex}; border:1px solid #55555f; border-radius:3px;"
        )

    def _pick_onion_prev(self) -> None:
        col = QColorDialog.getColor(QColor(self._onion_prev_hex), self, "Previous frame ghost color")
        if col.isValid():
            self._sync_onion_swatches(col.name(QColor.NameFormat.HexRgb), self._onion_next_hex)

    def _pick_onion_next(self) -> None:
        col = QColorDialog.getColor(QColor(self._onion_next_hex), self, "Next frame ghost color")
        if col.isValid():
            self._sync_onion_swatches(self._onion_prev_hex, col.name(QColor.NameFormat.HexRgb))

    def onion_colors(self) -> tuple[str, str]:
        return (self._onion_prev_hex, self._onion_next_hex)

    def pressure_curve(self) -> list[list[float]]:
        return normalize_pressure_curve(self.curve_edit.points)

    def _build_shortcuts_page(self, current: dict[str, str]) -> QWidget:
        page, sv = self._page()
        hint = QLabel("Click a shortcut and press the keys you want.")
        hint.setWordWrap(True)
        sv.addWidget(hint)
        table = QTableWidget(len(ACTION_LABELS), 2)
        table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._edits: dict[str, QKeySequenceEdit] = {}
        for row, key in enumerate(ACTION_LABELS):
            name = QTableWidgetItem(ACTION_LABELS[key])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 0, name)
            edit = QKeySequenceEdit()
            seq = current.get(key, DEFAULT_SHORTCUTS.get(key, ""))
            if seq:
                edit.setKeySequence(QKeySequence(seq))
            table.setCellWidget(row, 1, edit)
            self._edits[key] = edit
        sv.addWidget(table, 1)
        reset_row = QHBoxLayout()
        restore = QPushButton("Restore defaults")
        restore.clicked.connect(self._shortcut_defaults)
        reset_row.addWidget(restore)
        reset_row.addStretch(1)
        sv.addLayout(reset_row)
        return page

    # -- Behavior -----------------------------------------------------------

    def _sync_color_btn(self) -> None:
        self.color_btn.setStyleSheet(
            f"background: {self.circle_color.name()}; border: 1px solid #888; border-radius: 3px;"
        )

    def _pick_circle_color(self) -> None:
        c = QColorDialog.getColor(self.circle_color, self, "Circle color")
        if c.isValid():
            self.circle_color = c
            self._sync_color_btn()
            self._update_preview()

    def _browse_cursor(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select cursor image",
            self.custom_edit.text(),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.ico *.cur *.ani);;All files (*.*)",
        )
        if path:
            self.custom_edit.setText(path)

    def _on_mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        self.custom_edit.setEnabled(mode == "custom")
        for w in (self.color_btn, self.width_spin, self.dot_check):
            w.setEnabled(mode == "circle")
        self._update_preview()

    def _update_preview(self) -> None:
        mode = self.mode_combo.currentData()
        pm = QPixmap(self.preview.size())
        pm.fill(QColor("#1a1a1c"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = pm.width() // 2, pm.height() // 2
        if mode == "circle":
            cur = make_circle_cursor_pixmap(
                30, self.circle_color, int(self.width_spin.value()), bool(self.dot_check.isChecked())
            )
            p.drawPixmap(cx - cur.width() // 2, cy - cur.height() // 2, cur)
        elif mode == "crosshair":
            p.setPen(QPen(self.circle_color, 1))
            p.drawLine(cx - 12, cy, cx + 12, cy)
            p.drawLine(cx, cy - 12, cx, cy + 12)
        elif mode == "custom":
            img = QPixmap(self.custom_edit.text()) if self.custom_edit.text().strip() else QPixmap()
            if not img.isNull():
                if img.width() > 40 or img.height() > 40:
                    img = img.scaled(
                        40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                p.drawPixmap(cx - img.width() // 2, cy - img.height() // 2, img)
            else:
                p.setPen(QColor("#888888"))
                p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "No image selected")
        else:
            p.setPen(QColor("#888888"))
            p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "System arrow")
        p.end()
        self.preview.setPixmap(pm)

    def _browse(self) -> None:
        """Opens folder picker dialog to select custom autosave folder."""
        d = QFileDialog.getExistingDirectory(self, "Autosave folder", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def _use_default(self) -> None:
        """Resets the path input to default Documents/InkIt/Autosave folder."""
        self.path_edit.setText(str(self._default))

    def _delete_notes(self) -> None:
        """Deletes all autosave files (*_autosave.inkit / .json) in the selected directory."""
        folder = Path(self.path_edit.text().strip() or self._default)
        if QMessageBox.question(
            self,
            "Delete autosave notes",
            f"Delete all autosave files in:\n{folder}",
        ) != QMessageBox.StandardButton.Yes:
            return
        n = 0
        if folder.is_dir():
            for f in folder.glob("*"):
                if f.is_file() and is_autosave_name(f):
                    f.unlink()
                    n += 1
        self.deleted = True
        QMessageBox.information(self, "Autosave", f"Deleted {n} autosave file(s).")

    def _shortcut_defaults(self) -> None:
        for key, edit in self._edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS.get(key, "")))

    def chosen_dir(self) -> str:
        return self.path_edit.text().strip()

    def cursor_settings(self) -> dict:
        return {
            "mode": str(self.mode_combo.currentData()),
            "custom": self.custom_edit.text().strip(),
            "color": self.circle_color.name(QColor.NameFormat.HexRgb),
            "width": int(self.width_spin.value()),
            "dot": bool(self.dot_check.isChecked()),
        }

    def antialias_enabled(self) -> bool:
        return bool(self.aa_check.isChecked())

    def shortcuts_result(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, edit in self._edits.items():
            out[key] = edit.keySequence().toString(QKeySequence.SequenceFormat.NativeText)
        return out


# ---------------------------------------------------------------------------
# [WIDGET] ClickableLabel: Clickable Time / Frame Mode Toggle Label
# ---------------------------------------------------------------------------
class ClickableLabel(QLabel):
    """A QLabel that emits a clicked signal on left click and displays a pointing hand cursor."""
    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


# ---------------------------------------------------------------------------
# [WIDGET] BoxSlider: minimalist label+track+value slider; click/drag to change
# ---------------------------------------------------------------------------

def _paint_hardness_glyph(p: QPainter, cx: float, cy: float, r: float) -> None:
    """Two half circles: crisp right edge, feathered/blurred left edge."""
    solid = QColor("#e8eaf0")
    # right half — hard edge
    p.save()
    p.setClipRect(QRectF(cx, cy - r - 1.0, r + 1.0, 2.0 * r + 2.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(solid)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()
    # left half — soft fade outwards (blurred edge)
    grad = QRadialGradient(cx, cy, r)
    grad.setColorAt(0.0, solid)
    grad.setColorAt(0.55, solid)
    grad.setColorAt(1.0, QColor(solid.red(), solid.green(), solid.blue(), 0))
    p.save()
    p.setClipRect(QRectF(cx - r - 1.0, cy - r - 1.0, r + 1.0, 2.0 * r + 2.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()


def _paint_opacity_glyph(p: QPainter, cx: float, cy: float) -> None:
    """Two circles stacked diagonally: full-color one over a 30%-alpha twin."""
    base = QColor("#e8eaf0")
    under = QColor(base.red(), base.green(), base.blue(), int(255 * 0.3))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(under)
    p.drawEllipse(QPointF(cx - 3.0, cy + 3.0), 6.0, 6.0)
    p.setBrush(base)
    p.drawEllipse(QPointF(cx + 3.0, cy - 3.0), 6.0, 6.0)


def _paint_clip_glyph(p: QPainter, cx: float, cy: float) -> None:
    """Single film negatives stacked diagonally: top fully visible, bottom 30%."""
    base = QColor("#e8eaf0")
    hole = QColor(17, 17, 20)
    for off, alpha in ((3.0, int(255 * 0.3)), (-3.0, 255)):
        col = QColor(base.red(), base.green(), base.blue(), alpha)
        p.save()
        p.translate(cx - off, cy + off)
        p.rotate(-14.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(-7.0, -4.5, 14.0, 9.0), 1.5, 1.5)
        p.setBrush(hole)
        for hx in (-4.4, 0.0, 4.4):
            p.drawEllipse(QPointF(hx, -3.0), 1.05, 1.05)
            p.drawEllipse(QPointF(hx, 3.0), 1.05, 1.05)
        p.restore()


def _paint_notes_glyph(p: QPainter, cx: float, cy: float) -> None:
    """Two wavy note lines: top fully visible, bottom 30%."""
    base = QColor("#e8eaf0")
    for off, alpha in ((-3.5, 255), (3.5, int(255 * 0.3))):
        col = QColor(base.red(), base.green(), base.blue(), alpha)
        pen = QPen(col, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        pts = QPolygonF()
        for i in range(25):
            t = i / 24.0
            pts.append(
                QPointF(cx - 7.0 + 14.0 * t, cy + off + 2.2 * math.sin(4.0 * math.pi * t))
            )
        p.drawPolyline(pts)


class BoxSlider(QWidget):
    """Minimalist slider row: [glyph or title] [thin track] [value]."""
    valueChanged = Signal(int)

    def __init__(self, title: str, minimum: int, maximum: int, value: int, suffix: str = "%", parent=None, icon: str | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.suffix = suffix
        self.icon_kind = icon
        self._icon_pm = QPixmap()
        if icon:
            f = ICONS_DIR / f"slider_{icon}.png"
            try:
                if f.is_file():
                    self._icon_pm.load(str(f))
            except Exception:
                self._icon_pm = QPixmap()
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self._value = min(max(int(value), self.minimum), self.maximum)
        self._drag = False
        fm = self.fontMetrics()
        val_w = max(
            fm.horizontalAdvance(f"{self.minimum}{suffix}"),
            fm.horizontalAdvance(f"{self.maximum}{suffix}"),
        )
        self._label_w = 36 if icon else fm.horizontalAdvance(title)
        self._val_w = val_w
        track_w = 59
        self.setFixedSize(
            int(8 + self._label_w + 6 + track_w + 6 + val_w + 8),
            36 if icon else 24,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press_x = 0.0
        self._press_frac = 0.0
        self._press_value = 0
        self._rel_drag = False

    def _label(self) -> str:
        return f"{self.title} {self._value}{self.suffix}"

    def _frac(self) -> float:
        span = max(1, self.maximum - self.minimum)
        return (self._value - self.minimum) / span

    def _label_value(self) -> str:
        return f"{self._value}{self.suffix}"

    def _draw_hardness_icon(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        _paint_hardness_glyph(p, cx, cy, r)

    def _draw_opacity_icon(self, p: QPainter, cx: float, cy: float) -> None:
        _paint_opacity_glyph(p, cx, cy)

    def _draw_clip_icon(self, p: QPainter, cx: float, cy: float) -> None:
        _paint_clip_glyph(p, cx, cy)

    def _draw_notes_icon(self, p: QPainter, cx: float, cy: float) -> None:
        _paint_notes_glyph(p, cx, cy)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = float(self.height())
        cy = h / 2.0
        x = 8.0
        # leading label: user png override, glyph, or plain title
        if self.icon_kind and not self._icon_pm.isNull():
            p.drawPixmap(
                QRectF(x, cy - 18.0, 36.0, 36.0),
                self._icon_pm,
                QRectF(self._icon_pm.rect()),
            )
        elif self.icon_kind == "hardness":
            self._draw_hardness_icon(p, x + 18.0, cy, 16.0)
        elif self.icon_kind in ("opacity", "clip", "notes"):
            p.save()
            p.translate(x + 18.0, cy)
            p.scale(2.0, 2.0)
            {
                "opacity": self._draw_opacity_icon,
                "clip": self._draw_clip_icon,
                "notes": self._draw_notes_icon,
            }[self.icon_kind](p, 0.0, 0.0)
            p.restore()
        else:
            p.setPen(QColor("#d6d9e0"))
            p.drawText(
                QRectF(x, 0.0, float(self._label_w), h),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self.title,
            )
        tx0, tw = self._track_geom()
        # trailing value in a fixed-width column so track geometry is stable
        val = self._label_value()
        vx = float(self.width()) - 8.0 - float(self._val_w)
        frac = self._frac()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#33343c"))
        p.drawRoundedRect(QRectF(tx0, cy - 2.0, tw, 4.0), 2.0, 2.0)
        fw = frac * tw
        if fw > 0.5:
            p.setBrush(QColor("#3d5afe"))
            p.drawRoundedRect(QRectF(tx0, cy - 2.0, fw, 4.0), 2.0, 2.0)
        p.setPen(QPen(QColor("#202028"), 1))
        p.setBrush(QColor("#f2f3f7"))
        p.drawEllipse(QPointF(tx0 + fw, cy), 5.0, 5.0)
        p.setPen(QColor("#eef0f5"))
        p.drawText(
            QRectF(vx, 0.0, float(self._val_w), h),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            val,
        )

    def _track_geom(self) -> tuple[float, float]:
        """Track x-start and width — the exact geometry used by painting."""
        tx0 = 8.0 + float(self._label_w) + 6.0
        tw = float(self.width()) - 8.0 - float(self._val_w) - 6.0 - tx0
        return tx0, tw

    def _raw_frac(self, x: float) -> float:
        """Unclamped 0..1 position across the widget (relative-drag math)."""
        m = 8.0
        span = max(1.0, self.width() - 2 * m)
        return (x - m) / span

    def _frac_at(self, x: float) -> float:
        return min(max(self._raw_frac(x), 0.0), 1.0)

    def _value_at_frac(self, f: float) -> int:
        return int(round(self.minimum + f * (self.maximum - self.minimum)))

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._press_x = ev.position().x()
            self._press_frac = self._frac_at(self._press_x)
            self._press_value = self._value
            tx0, tw = self._track_geom()
            if tw > 1 and tx0 - 4 <= self._press_x <= tx0 + tw + 4:
                # Press on the track: knob jumps exactly under the pointer.
                self._rel_drag = False
                f = min(max((self._press_x - tx0) / tw, 0.0), 1.0)
                self.setValue(self._value_at_frac(f))
            else:
                # Press on icon/label: no jump — drag adjusts from current %.
                self._rel_drag = True
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag:
            if self._rel_drag:
                # Relative from where it was; Ctrl = 1/8 fine-tune speed.
                speed = 8.0 if (ev.modifiers() & Qt.KeyboardModifier.ControlModifier) else 1.0
                df = (self._raw_frac(ev.position().x()) - self._press_frac) / speed
                self.setValue(int(round(self._press_value + df * (self.maximum - self.minimum))))
            else:
                tx0, tw = self._track_geom()
                f = min(max((ev.position().x() - tx0) / max(tw, 1.0), 0.0), 1.0)
                self.setValue(self._value_at_frac(f))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag:
            self._drag = False
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y()
        if d:
            self.setValue(self._value + (1 if d > 0 else -1))
            ev.accept()
            return
        super().wheelEvent(ev)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = min(max(int(v), self.minimum), self.maximum)
        if v == self._value:
            return
        self._value = v
        self.update()
        self.valueChanged.emit(v)


class ToolButton(QWidget):
    """
    Combined pen/eraser control, slightly larger than the other boxes.
    - Click: toggles pen <-> eraser
    - Click-drag horizontally: adjusts the active tool's size (fill follows)
    - Ctrl-drag: fine adjustment (1/8 sensitivity)
    - Wheel over it: size +/- 1
    """

    toolToggled = Signal()
    sizeDragged = Signal(int)

    def __init__(self, minimum: int = 1, maximum: int = 80, value: int = 6, parent=None) -> None:
        super().__init__(parent)
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self._value = min(max(int(value), self.minimum), self.maximum)
        self.tool = "pen"
        self._press_pos = None
        self._last_x = 0.0
        self._accum = 0.0
        self._moved = False
        fm = self.fontMetrics()
        num_w = max(fm.horizontalAdvance(str(self.maximum)), fm.horizontalAdvance(str(self.minimum)))
        self.setFixedSize(int(num_w) + 48, 34)  # a bit bigger than the other boxes
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # User-supplied pen.png (icons folder) replaces the built-in pencil glyph
        self._user_pen_pm: QPixmap | None = None
        upen = ICONS_DIR / "pen.png"
        if upen.is_file():
            self._user_pen_pm = QPixmap(str(upen))

    # -- public API ---------------------------------------------------------
    def setTool(self, tool: str) -> None:
        if tool in ("pen", "eraser") and tool != self.tool:
            self.tool = tool
            self.update()

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = min(max(int(v), self.minimum), self.maximum)
        if v == self._value:
            return
        self._value = v
        self.update()
        self.sizeDragged.emit(v)

    # -- painting -----------------------------------------------------------
    def _label(self) -> str:
        return f"{'Pen' if self.tool == 'pen' else 'Eraser'} {self._value}"

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        body = QPainterPath()
        body.addRoundedRect(r, 5.0, 5.0)  # same rounding as the eyedropper button
        # Solid tool color; size fill is two shades darker so the level reads
        if self.tool == "pen":
            base, fill = QColor("#3d5afe"), QColor("#2a3ec4")
        else:
            base, fill = QColor("#ff9f43"), QColor("#c97a1e")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawPath(body)
        span = max(1, self.maximum - self.minimum)
        fw = ((self._value - self.minimum) / span) * (self.width() - 2)
        if fw > 0.5:
            p.save()
            p.setClipRect(QRectF(1, 1, fw, self.height() - 2))
            p.setBrush(fill)
            p.drawPath(body)
            p.restore()
        f = self.font()
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        # Tool glyph on the left (user png wins), plain size number on the right
        if self.tool == "pen" and self._user_pen_pm is not None and not self._user_pen_pm.isNull():
            pm = self._user_pen_pm
            side = 22.0
            p.drawPixmap(QRectF(17.0 - side / 2.0, self.height() / 2.0 - side / 2.0, side, side).toRect(), pm)
        else:
            self._draw_tool_icon(p, 17.0, self.height() / 2.0)
        num = str(self._value)
        nw = p.fontMetrics().horizontalAdvance(num)
        p.drawText(
            QRectF(float(self.width()) - 12.0 - nw, 0.0, float(nw), float(self.height())),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            num,
        )

    def _draw_tool_icon(self, p: QPainter, cx: float, cy: float) -> None:
        """Small white vector glyph for the active tool (pencil / eraser)."""
        pen = QPen(QColor("#ffffff"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.save()
        p.translate(cx, cy)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.tool == "pen":
            p.rotate(45.0)
            p.drawRoundedRect(QRectF(-2.8, -10.0, 5.6, 14.0), 1.5, 1.5)
            p.setBrush(QColor("#ffffff"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(-2.8, 4.0), QPointF(2.8, 4.0), QPointF(0.0, 9.5)]))
        else:
            p.rotate(-40.0)
            p.drawRoundedRect(QRectF(-7.0, -4.5, 14.0, 9.0), 2.0, 2.0)
            p.drawLine(QPointF(-7.0, -1.0), QPointF(7.0, -1.0))
            thin = QPen(QColor("#ffffff"), 1.1)
            thin.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(thin)
            p.drawLine(QPointF(-8.5, 8.0), QPointF(8.5, 8.0))
        p.restore()

    # -- mouse ---------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position()
            self._last_x = ev.position().x()
            self._accum = 0.0
            self._moved = False
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._press_pos is not None:
            d = ev.position() - self._press_pos
            if not self._moved and (abs(d.x()) > 4 or abs(d.y()) > 4):
                self._moved = True
            if self._moved:
                # Relative drag: value keeps moving from where it started,
                # so clicking anywhere never jumps the size. Ctrl = 1/8 speed.
                px2unit = (self.maximum - self.minimum) / max(1.0, self.width() - 16.0)
                rate = px2unit / 8.0 if (ev.modifiers() & Qt.KeyboardModifier.ControlModifier) else px2unit
                self._accum += (ev.position().x() - self._last_x) * rate
                self._last_x = ev.position().x()
                new_v = min(max(int(round(self._value + self._accum)), self.minimum), self.maximum)
                if new_v != self._value:
                    self._accum -= new_v - self._value  # consume applied delta, no dead zone
                    self.setValue(new_v)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            was_drag = self._moved
            self._press_pos = None
            self._moved = False
            if not was_drag:
                self.toolToggled.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y()
        if d:
            self.setValue(self._value + (1 if d > 0 else -1))
            ev.accept()
            return
        super().wheelEvent(ev)


class OnionButton(QWidget):
    """
    Onion skin control acting like the Pen slider:
    - Click: toggles onion skin on/off
    - Click-drag horizontally: adjusts ghost visibility % (fill follows)
    - Ctrl-drag: fine adjustment (1/8 sensitivity)
    - Wheel over it: ±1%
    """

    toggled = Signal(bool)          # onion on/off after a click
    opacityChanged = Signal(int)    # ghost visibility percent

    def __init__(self, value: int = 35, radius: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.minimum = 5
        self.maximum = 100
        self._value = min(max(int(value), self.minimum), self.maximum)
        self.radius = max(0, int(radius))
        self.on = False
        self._press_pos = None
        self._last_x = 0.0
        self._accum = 0.0
        self._moved = False
        self.setFixedSize(48, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- public API ---------------------------------------------------------
    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = min(max(int(v), self.minimum), self.maximum)
        if v == self._value:
            return
        self._value = v
        self.update()
        self.opacityChanged.emit(v)

    def setOn(self, on: bool) -> None:
        if bool(on) != self.on:
            self.on = bool(on)
            self.update()
            self.toggled.emit(self.on)

    # -- painting -----------------------------------------------------------
    def _label(self) -> str:
        return f"Onion {self._value}%" if self.on else "Onion"

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self.on:
            base, fill = QColor("#8e44ad"), QColor("#6c2fa0")
        else:
            base, fill = QColor("#4a4a55"), QColor("#3a3a44")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)

        def paint_body() -> None:
            if self.radius > 0:
                path = QPainterPath()
                path.addRoundedRect(r, float(self.radius), float(self.radius))
                p.drawPath(path)
            else:
                p.drawRect(r)

        paint_body()
        span = max(1, self.maximum - self.minimum)
        fw = ((self._value - self.minimum) / span) * (self.width() - 2)
        if fw > 0.5:
            p.save()
            p.setClipRect(QRectF(1, 1, fw, self.height() - 2))
            p.setBrush(fill)
            paint_body()
            p.restore()
        # Onion skinning icon (centered) instead of text; fill bar still shows %
        col = QColor("#eeeeee")
        side_a = QColor("#c248ff") if self.on else QColor("#8a8a95")
        side_b = QColor("#33ccff") if self.on else QColor("#8a8a95")
        p.save()
        s = min(self.width() / 44.0, self.height() / 33.0)
        p.translate((self.width() - 32 * s) / 2.0, (self.height() - 32 * s) / 2.0)
        p.scale(s, s)
        p.setPen(QPen(side_a, 2))
        p.drawRect(QRectF(2, 11, 9, 12))
        p.setPen(QPen(side_b, 2))
        p.drawRect(QRectF(21, 11, 9, 12))
        p.setPen(QPen(col, 2.4))
        p.drawRect(QRectF(9, 6, 14, 20))
        p.restore()
        p.end()

    # -- mouse ---------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position()
            self._last_x = ev.position().x()
            self._accum = 0.0
            self._moved = False
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._press_pos is not None:
            if not self.on:
                # onion off: click still toggles on release, but sliding does nothing
                ev.accept()
                return
            d = ev.position() - self._press_pos
            if not self._moved and (abs(d.x()) > 4 or abs(d.y()) > 4):
                self._moved = True
            if self._moved:
                px2unit = (self.maximum - self.minimum) / max(1.0, self.width() - 16.0)
                rate = px2unit / 8.0 if (ev.modifiers() & Qt.KeyboardModifier.ControlModifier) else px2unit
                self._accum += (ev.position().x() - self._last_x) * rate
                self._last_x = ev.position().x()
                new_v = min(max(int(round(self._value + self._accum)), self.minimum), self.maximum)
                if new_v != self._value:
                    self._accum -= new_v - self._value
                    self.setValue(new_v)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            was_drag = self._moved
            self._press_pos = None
            self._moved = False
            if not was_drag:
                self.setOn(not self.on)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        if not self.on:
            ev.accept()
            return
        d = ev.angleDelta().y()
        if d:
            self.setValue(self._value + (1 if d > 0 else -1))
            ev.accept()
            return
        super().wheelEvent(ev)


# ===========================================================================
# 5. VECTOR ICONS & GRAPHICS HELPERS
# ===========================================================================

# ---------------------------------------------------------------------------
# [WIDGET] VolumeButton: audio toggle whose blue fill tracks the volume level
# ---------------------------------------------------------------------------
class ColorSwatch(QPushButton):
    """Fixed toolbar color slot: click applies the color, double-click assigns it."""

    assigned = Signal(int)

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = int(index)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.assigned.emit(self.index)
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)


class ValueBox(QWidget):
    """Compact inline −/+ value box, 0-10 range (user-chosen toolbar design)."""

    valueChanged = Signal(int)

    def __init__(self, initial: int = 0, minimum: int = 0, maximum: int = 10, parent=None) -> None:
        super().__init__(parent)
        self._min = int(minimum)
        self._max = int(maximum)
        self._value = min(max(int(initial), self._min), self._max)
        # QWidget subclasses skip QSS backgrounds/borders without this flag
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setStyleSheet(
            "ValueBox { background-color:#2b2b2b; border:1px solid #4a4a4a;"
            " border-radius:6px; }"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.minus_btn = QPushButton("−")
        self.plus_btn = QPushButton("+")
        self.display = QLabel(str(self._value))
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setFixedWidth(18)
        self.display.setStyleSheet(
            "color:white; font-size:12px; font-weight:bold; background:transparent;"
        )

        for b in (self.minus_btn, self.plus_btn):
            b.setFixedSize(17, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background-color:transparent; color:#ccc; border:none;"
                " padding:0; font-size:11px; }"
                "QPushButton:hover { background-color:#3c3c3c; }"
                "QPushButton:pressed { background-color:#5285a6; color:white; }"
            )

        lay.addWidget(self.minus_btn)
        lay.addWidget(self.display)
        lay.addWidget(self.plus_btn)
        self.setFixedSize(52, 30)

        self.minus_btn.clicked.connect(self.decrement)
        self.plus_btn.clicked.connect(self.increment)
        self._refresh_button_states()

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = max(self._min, min(self._max, int(v)))
        if v != self._value:
            self._value = v
            self.display.setText(str(v))
            self.valueChanged.emit(v)
        self._refresh_button_states()

    def increment(self) -> None:
        self.setValue(self._value + 1)

    def decrement(self) -> None:
        self.setValue(self._value - 1)

    def _refresh_button_states(self) -> None:
        self.plus_btn.setEnabled(self._value < self._max)
        self.minus_btn.setEnabled(self._value > self._min)


class VolumeButton(QPushButton):
    """Checked = audio on: the button fills blue from the left proportional to volume."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._volume = 100
        self._hover = False

    def setVolumeLevel(self, v: int) -> None:
        v = min(max(int(v), 0), 100)
        if v != self._volume:
            self._volume = v
            self.update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        on = self.isChecked()
        p.setPen(QPen(QColor("#3d5afe") if on else QColor("#3a3a42"), 1))
        p.setBrush(QColor("#35353c") if (self._hover and not on) else QColor("#2a2a30"))
        p.drawRoundedRect(r, 4, 4)
        if on and self._volume > 0:
            fw = (self.width() - 2) * (self._volume / 100.0)
            p.save()
            p.setClipRect(QRectF(1, 1, fw, self.height() - 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#3d5afe"))
            p.drawRoundedRect(r, 4, 4)
            p.restore()
        if self.icon().isNull():
            return
        ir = QRect(0, 0, self.width(), self.height())
        self.icon().paint(p, ir, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal, QIcon.State.On)


class ScreenColorPicker(QWidget):
    """
    Live screen eyedropper: a transparent topmost overlay keeps the screen
    completely live (video keeps playing, nothing freezes); a crosshair
    pointer with an eyedropper glyph and a sample circle follows the mouse,
    sampling the color under it in real time via GDI GetPixel. Left-click
    picks; right-click or Esc cancels.
    """

    picked = Signal(QColor)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._picked = False
        self._geo = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(self._geo)
        self._cur = QCursor.pos()
        c = self._sample(self._cur)
        self._color = c if c is not None else QColor("#000000")
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._follow)

    # -- live sampling ------------------------------------------------------
    @staticmethod
    def _gdi_pixel(x: float, y: float) -> QColor | None:
        """Reads the physical screen pixel under logical coords; None if unavailable."""
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
        except AttributeError:  # non-Windows platform
            return None
        scr = QGuiApplication.screenAt(QPoint(int(x), int(y)))
        dpr = (scr.devicePixelRatio() if scr else None) or 1.0
        hdc = user32.GetDC(0)
        res = gdi32.GetPixel(hdc, int(round(x * dpr)), int(round(y * dpr)))
        user32.ReleaseDC(0, hdc)
        if res == 0xFFFFFFFF:  # CLR_INVALID — outside the visible surface
            return None
        return QColor(res & 0xFF, (res >> 8) & 0xFF, (res >> 16) & 0xFF)

    def _sample(self, gp) -> QColor | None:
        return self._gdi_pixel(gp.x(), gp.y())

    # -- behavior -----------------------------------------------------------
    def pick_global(self, gp=None) -> None:
        """Sample the pixel at global pos (cursor by default) and finish."""
        self._picked = True
        self.picked.emit(self._sample(gp or QCursor.pos()) or self._color)
        self.close()

    def cancel(self) -> None:
        self.close()

    def _follow(self) -> None:
        pos = QCursor.pos()
        c = self._sample(pos)
        changed = pos != self._cur
        if c is not None and c != getattr(self, "_color", None):
            self._color = c
            changed = True
        if changed:
            self._cur = pos
            self.update()

    def showEvent(self, ev) -> None:
        self._timer.start()
        super().showEvent(ev)

    def closeEvent(self, ev) -> None:
        self._timer.stop()
        if not self._picked:
            self.cancelled.emit()
        super().closeEvent(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.pick_global()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.cancel()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        g = self.mapFromGlobal(self._cur)
        cur = QPointF(g)
        cx, cy = cur.x(), cur.y()

        def line(a: QPointF, b: QPointF) -> None:
            p.setPen(QPen(QColor(0, 0, 0, 170), 3))
            p.drawLine(a, b)
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.drawLine(a, b)

        # precise-location crosshair with a tiny gap over the exact pixel
        gap, arm = 3.0, 13.0
        line(QPointF(cx - arm, cy), QPointF(cx - gap, cy))
        line(QPointF(cx + gap, cy), QPointF(cx + arm, cy))
        line(QPointF(cx, cy - arm), QPointF(cx, cy - gap))
        line(QPointF(cx, cy + gap), QPointF(cx, cy + arm))

        # eyedropper glyph: tip exactly on the cursor point, body lower-right
        shadow = QPen(QColor(0, 0, 0, 170), 3.4)
        shadow.setCapStyle(Qt.PenCapStyle.RoundCap)
        shadow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        core = QPen(QColor("#ffffff"), 1.4)
        core.setCapStyle(Qt.PenCapStyle.RoundCap)
        core.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        def pipette(pen: QPen, fill_tip: bool) -> None:
            p.save()
            p.translate(cx, cy)
            p.rotate(-45.0)  # tip at cursor, body extends to lower-right
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(0.0, 6.0), QPointF(0.0, 14.0))    # tube
            p.drawEllipse(QRectF(-2.8, 13.5, 5.6, 5.4))          # bulb
            if fill_tip:
                p.setBrush(QColor("#ffffff"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(
                    QPolygonF([QPointF(0.0, 1.0), QPointF(-1.7, 6.5), QPointF(1.7, 6.5)])
                )
            else:
                p.drawLine(QPointF(-1.7, 6.5), QPointF(1.7, 6.5))
            p.restore()

        pipette(shadow, False)
        pipette(core, True)

        # live sample dot floating above the pointer
        col = self._color
        dot_y = cy - 34.0
        if dot_y < 12.0:
            dot_y = cy + 42.0
        dot = QPointF(cx, dot_y)
        p.setPen(QPen(QColor(0, 0, 0, 150), 1))
        p.setBrush(col)
        p.drawEllipse(dot, 9.5, 9.5)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawEllipse(dot, 8.0, 8.0)


def make_nav_icon(direction: str, with_dot: bool = False) -> QIcon:
    """
    Renders custom crisp vector navigation icons:
    - 'prev' + without dot: Step previous frame (<)
    - 'next' + without dot: Step next frame (>)
    - 'prev' + with dot: Jump to previous drawing (|<)
    - 'next' + with dot: Jump to next drawing (>|)
    """
    user = ICONS_DIR / (f"nav_{direction}_dot.png" if with_dot else f"nav_{direction}.png")
    if user.is_file():
        return QIcon(str(user))
    w = 46 if with_dot else 32
    pm = QPixmap(w, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#eeeeee"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if direction == "prev":
        ox = 12 if with_dot else 0
        cx = 18 + ox
        p.drawLine(QPointF(cx + 5, 9), QPointF(cx - 4, 16))
        p.drawLine(QPointF(cx - 4, 16), QPointF(cx + 5, 23))
        if with_dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffcc00"))
            p.drawEllipse(QPointF(8, 16), 4.0, 4.0)
    else:
        cx = 14
        p.drawLine(QPointF(cx - 5, 9), QPointF(cx + 4, 16))
        p.drawLine(QPointF(cx + 4, 16), QPointF(cx - 5, 23))
        if with_dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ffcc00"))
            p.drawEllipse(QPointF(w - 8, 16), 4.0, 4.0)
    p.end()
    return QIcon(pm)


def make_tool_icon(kind: str, on: bool = True) -> QIcon:
    """
    Renders procedural icons for toolbar actions:
    - 'eye': Notes visibility toggle (open eye / crossed eye)
    - 'trash': Clear annotations on current frame
    - 'loop': Loop playback toggle (circular arrow / crossed arrow)
    - 'audio': Audio playback toggle (speaker with sound waves / crossed speaker)
     - 'plus': Plus sign (add files to shot list)
     - 'open': Folder (open shot list)
     - 'save': Floppy disk (save shot list)
     - 'x': Crossed lines (delete selected shots)
     - 'zoom': Magnifier with plus (zoom tool toggle)
     - 'pan': Four-way move arrows (pan tool toggle)
     - 'reset': Frame with corner marks (reset zoom & pan)
      - 'picker': Eyedropper (pick pen color)
    """
    user = ICONS_DIR / f"{kind}.png"
    if user.is_file():
        return QIcon(str(user))
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor("#eeeeee")
    p.setPen(QPen(col, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "eye":
        p.drawEllipse(QRectF(5, 11, 22, 12))
        if on:
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(12, 13, 8, 8))
        else:
            p.drawLine(QPointF(7, 24), QPointF(25, 8))
    elif kind == "trash":
        p.drawLine(QPointF(10, 11), QPointF(22, 11))
        p.drawLine(QPointF(13, 8), QPointF(19, 8))
        p.drawRect(QRectF(11, 11, 10, 13))
        p.drawLine(QPointF(14, 14), QPointF(14, 21))
        p.drawLine(QPointF(18, 14), QPointF(18, 21))
    elif kind == "loop":
        p.drawArc(QRectF(7, 8, 18, 16), 40 * 16, 280 * 16)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon([QPointF(22, 7), QPointF(28, 11), QPointF(20, 14)])
        else:
            p.drawLine(QPointF(8, 24), QPointF(24, 8))
    elif kind == "audio":
        p.drawPolygon([QPointF(6, 12), QPointF(12, 12), QPointF(18, 7), QPointF(18, 25), QPointF(12, 20), QPointF(6, 20)])
        if on:
            p.drawArc(QRectF(16, 10, 10, 12), -60 * 16, 120 * 16)
            p.drawArc(QRectF(19, 7, 10, 18), -60 * 16, 120 * 16)
        else:
            p.drawLine(QPointF(7, 25), QPointF(25, 7))
    elif kind == "plus":
        p.drawLine(QPointF(16, 8), QPointF(16, 24))
        p.drawLine(QPointF(8, 16), QPointF(24, 16))
    elif kind == "pen":
        p.save()
        p.translate(16, 16)
        p.rotate(-45)
        p.drawRect(QRectF(-3.5, -12, 7, 17))
        p.setBrush(col)
        p.drawPolygon([QPointF(-3.5, 5), QPointF(3.5, 5), QPointF(0, 12.5)])
        p.restore()
    elif kind == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon([QPointF(12.0, 9.0), QPointF(12.0, 23.0), QPointF(25.0, 16.0)])
    elif kind == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRect(QRectF(10.5, 9, 4.2, 14))
        p.drawRect(QRectF(17.3, 9, 4.2, 14))
    elif kind == "open":
        p.drawLine(QPointF(6, 23), QPointF(6, 10))
        p.drawLine(QPointF(6, 10), QPointF(12, 10))
        p.drawLine(QPointF(12, 10), QPointF(14, 12))
        p.drawLine(QPointF(14, 12), QPointF(25, 12))
        p.drawLine(QPointF(25, 12), QPointF(25, 23))
        p.drawLine(QPointF(25, 23), QPointF(6, 23))
    elif kind == "save":
        p.drawRect(QRectF(8, 8, 16, 16))
        p.drawLine(QPointF(11, 8), QPointF(11, 13))
        p.drawLine(QPointF(11, 13), QPointF(21, 13))
        p.drawLine(QPointF(21, 13), QPointF(21, 8))
        p.drawRect(QRectF(12, 17, 8, 7))
    elif kind == "x":
        p.drawLine(QPointF(10, 10), QPointF(22, 22))
        p.drawLine(QPointF(22, 10), QPointF(10, 22))
    elif kind == "view_nav":
        # magnifier with a four-way move arrow: zoom / pan / reset combined
        p.drawEllipse(QRectF(7, 7, 15, 15))
        p.drawLine(QPointF(20, 20), QPointF(26, 26))
        mx, my = 14.5, 14.5
        p.drawLine(QPointF(mx - 5.0, my), QPointF(mx + 5.0, my))
        p.drawLine(QPointF(mx, my - 5.0), QPointF(mx, my + 5.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon([QPointF(mx, my - 7.2), QPointF(mx - 2.1, my - 4.6), QPointF(mx + 2.1, my - 4.6)])
        p.drawPolygon([QPointF(mx, my + 7.2), QPointF(mx - 2.1, my + 4.6), QPointF(mx + 2.1, my + 4.6)])
        p.drawPolygon([QPointF(mx - 7.2, my), QPointF(mx - 4.6, my - 2.1), QPointF(mx - 4.6, my + 2.1)])
        p.drawPolygon([QPointF(mx + 7.2, my), QPointF(mx + 4.6, my - 2.1), QPointF(mx + 4.6, my + 2.1)])
    elif kind == "picker":
        # eyedropper: angled tip, body tube, top bulb
        p.drawLine(QPointF(7, 25), QPointF(13, 19))
        p.drawLine(QPointF(5, 22), QPointF(10, 27))
        p.drawLine(QPointF(12, 20), QPointF(21, 11))
        p.drawLine(QPointF(14, 22), QPointF(23, 13))
        p.drawLine(QPointF(21, 11), QPointF(24, 8))
        p.drawLine(QPointF(23, 13), QPointF(26, 10))
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(25.5, 6.5), 4.0, 4.0)
    elif kind == "onion":
        side_a = QColor("#c248ff") if on else QColor("#777780")
        side_b = QColor("#33ccff") if on else QColor("#777780")
        p.setPen(QPen(side_a, 2))
        p.drawRect(QRectF(2, 11, 9, 12))
        p.setPen(QPen(side_b, 2))
        p.drawRect(QRectF(21, 11, 9, 12))
        p.setPen(QPen(col, 2.4))
        p.drawRect(QRectF(9, 6, 14, 20))
    elif kind == "grid":
        # Thumbnail grid toggle (shot list): 2x2 tiles
        p.drawRect(QRectF(6, 6, 8, 8))
        p.drawRect(QRectF(18, 6, 8, 8))
        if on:
            p.setBrush(col)
        p.drawRect(QRectF(6, 18, 8, 8))
        p.drawRect(QRectF(18, 18, 8, 8))
    p.end()
    return QIcon(pm)


def make_vertical_text_icon(text: str, color: str = "#cccccc", font: QFont | None = None) -> QIcon:
    """Renders text rotated 90° (reads bottom-to-top) for vertical dock tabs."""
    pm = QPixmap(90, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setPen(QColor(color))
    f = QFont(font) if font is not None else QFont()
    f.setBold(True)
    if font is None:
        f.setPointSize(10)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QIcon(pm.transformed(QTransform().rotate(-90)))


def make_circle_cursor_pixmap(diameter: float, color: QColor, thickness: int = 2, dot: bool = True) -> QPixmap:
    """Renders a brush-size circle cursor pixmap with a dark halo for visibility on any background."""
    d = max(3.0, float(diameter))
    pad = int(thickness) + 4
    size = int(round(d + pad * 2)) + 1
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    c = QPointF(size / 2.0, size / 2.0)
    rad = d / 2.0
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 170), thickness + 2))
    p.drawEllipse(c, rad + 0.5, rad + 0.5)
    p.setPen(QPen(color, max(1, int(thickness))))
    p.drawEllipse(c, rad + 0.5, rad + 0.5)
    if dot:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 1.4, 1.4)
    p.end()
    return pm


APP_DIR = Path(__file__).resolve().parent

# ===========================================================================
# ██ ICON FOLDER — replace PNGs here to reskin buttons ██
#
# ensure_icon_assets() exports every built-in icon to  APP_DIR/icons/<name>.png
# (once, only for missing files) and writes icons/names.txt describing each.
# make_tool_icon() / make_nav_icon() prefer a user-supplied PNG in that folder
# over the built-in vector art, so swapping a file instantly reskins the app.
# Widgets painted in code (no icon file): volume button, pen-size slider,
# onion-skin pill, tool size button — see PAINTED_WIDGETS note in names.txt.
# ===========================================================================
ICONS_DIR = APP_DIR / "icons"

# name -> human-readable description (also written to icons/names.txt)
ICON_NAMES: dict[str, str] = {
    "pen": "Pen / eraser tool",
    "eye": "Show / hide notes",
    "trash": "Clear drawings on this frame",
    "loop": "Loop playback",
    "audio": "Audio playback toggle",
    "play": "Play",
    "pause": "Pause",
    "plus": "Add files / frames",
    "open": "Open shot list folder",
    "save": "Save scene",
    "x": "Close / remove item",
    "view_nav": "Zoom / Pan / Reset view",
    "onion": "Onion skin toggle",
    "grid": "Thumbnails toggle",
    "picker": "Color picker (eyedropper)",
    "slider_hardness": "Brush hardness slider logo",
    "slider_opacity": "Pen opacity slider logo",
    "slider_clip": "Clip opacity slider logo",
    "slider_notes": "Notes opacity slider logo",
    "nav_prev": "Previous frame (<)",
    "nav_next": "Next frame (>)",
    "nav_prev_dot": "Jump to previous drawing (|<)",
    "nav_next_dot": "Jump to next drawing (>||)",
}


def _render_icon_png(name: str) -> QPixmap:
    """Built-in vector art for one icon-folder entry."""
    if name.startswith("slider_"):
        kind = name[len("slider_"):]
        pm = QPixmap(128, 128)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.scale(4.0, 4.0)
        if kind == "hardness":
            _paint_hardness_glyph(p, 17.0, 16.0, 8.0)
        else:
            {"opacity": _paint_opacity_glyph,
             "clip": _paint_clip_glyph,
             "notes": _paint_notes_glyph}[kind](p, 16.0, 16.0)
        p.end()
        return pm
    if name.startswith("nav_"):
        direction = "prev" if "prev" in name else "next"
        return make_nav_icon(direction, with_dot=name.endswith("_dot")).pixmap(64, 44)
    return make_tool_icon(name).pixmap(64, 64)


def ensure_icon_assets() -> None:
    """
    Exports every ICON_NAMES entry to ICONS_DIR as <name>.png (missing files
    only) and writes names.txt. Runs at startup; cheap after the first run.
    Delete a PNG to regenerate the default artwork for it.
    """
    try:
        ICONS_DIR.mkdir(exist_ok=True)
        for name, desc in ICON_NAMES.items():
            f = ICONS_DIR / f"{name}.png"
            if not f.is_file():
                _render_icon_png(name).save(str(f), "PNG")
        lines = ["InkIt icon folder — replace any .png to reskin that button.",
                 "Delete a png to restore the built-in vector art on next start.", ""]
        lines += [f"{n}.png  |  {d}" for n, d in ICON_NAMES.items()]
        lines += ["", "Painted in code (no icon file): audio volume button,",
                  "pen/eraser size button, onion spin pills, eyedropper pointer."]
        (ICONS_DIR / "names.txt").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def app_icon_path() -> Path:
    """Finds icon.ico / icon.png across development and frozen standalone modes."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            if (meipass / "icon.ico").is_file():
                return meipass / "icon.ico"
            if (meipass / "icon.png").is_file():
                return meipass / "icon.png"
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "icon.ico").is_file():
            return exe_dir / "icon.ico"
        if (exe_dir / "icon.png").is_file():
            return exe_dir / "icon.png"
    png = APP_DIR / "icon.png"
    ico = APP_DIR / "icon.ico"
    if ico.is_file():
        return ico
    return png


def write_default_icon(path: Path) -> None:
    """Generates procedural default InkIt icon and saves to disk if missing."""
    img = QImage(256, 256, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor("#1c1c22"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(8, 8, 240, 240, 48, 48)
    p.setPen(QPen(QColor("#3d5afe"), 10))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(40, 56, 176, 144, 16, 16)
    p.setPen(QPen(QColor("#ffcc00"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(QPointF(78, 168), QPointF(118, 108))
    p.drawLine(QPointF(118, 108), QPointF(148, 148))
    p.drawLine(QPointF(148, 148), QPointF(188, 88))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffcc00"))
    p.drawEllipse(QPointF(78, 168), 9, 9)
    p.drawEllipse(QPointF(188, 88), 9, 9)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


# ===========================================================================
# 6. TIME & AUTOSAVE UTILITIES
# ===========================================================================

SWATCH_COLORS_DEFAULT = ("#ff3b30", "#ffcc00", "#34c759", "#007aff")


def default_settings() -> dict:
    """Returns default settings dictionary."""
    return {
        "tool": "pen",
        "color": "#ff3b30",
        "swatch_colors": list(SWATCH_COLORS_DEFAULT),
        "brush": 6,
        "hardness": 100,
        "pen_antialias": True,
        "cursor_mode": "circle",
        "cursor_custom": "",
        "cursor_color": "#ffffff",
        "cursor_width": 2,
        "cursor_dot": True,
        "pen_opacity": 100,
        "clip_opacity": 100,
        "notes_opacity": 100,
        "fade_color": "#ffffff",
        "notes_visible": True,
        "time_mode": "frames",
        "loop": True,
        "audio": True,
        "volume": 100,
        "export_audio": True,
        "always_on_top": False,
        "last_open_dir": "",
        "last_save_dir": "",
        "last_export_dir": "",
        "autosave_dir": "",
        "autosave_autodelete": True,   # Settings ▸ Autosave: purge old autosave files
        "autosave_max_days": 90,       # age threshold in days (1..365), default 3 months
        "win_geometry": "",
        "recent_open": [],
        "recent_saved": [],
        "pen_recent": ["#ff3b30", "#ffcc00", "#34c759", "#007aff", "#ffffff", "#000000"],
        "fade_recent": ["#ffffff", "#000000", "#888888"],
    }


def default_autosave_dir() -> Path:
    """Returns default autosave path in user Documents folder."""
    p = Path.home() / "Documents" / "InkIt" / "Autosave"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_autosave_name(path: str | Path) -> bool:
    """Checks if a filepath belongs to an autosaved file."""
    n = Path(path).name.lower()
    return n.endswith("_autosave.inkit") or n.endswith("_autosave.framenotes.json") or n.endswith("_autosave.json")


def format_time(frame: int, fps: float) -> str:
    """Formats frame count into timecode string: HH:MM:SS:FF or MM:SS:FF."""
    fps = fps if fps > 1e-3 else 24.0
    total = frame / fps
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = int(total % 60)
    frac = total - int(total)
    ff = int(round(frac * fps))
    if ff >= int(round(fps)):
        ff = 0
        seconds += 1
        if seconds >= 60:
            seconds = 0
            minutes += 1
            if minutes >= 60:
                minutes = 0
                hours += 1
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}:{ff:02d}"
    return f"{minutes}:{seconds:02d}:{ff:02d}"


# ===========================================================================
# 7. DRAWING CANVAS (Canvas Widget)
# ===========================================================================

class Canvas(QWidget):
    """
    Central interactive viewport for video presentation and drawing.
    - Handles letterbox aspect-ratio scaling
    - Renders pressure-sensitive geometric strokes with sub-pixel interpolation
    - Handles mouse, stylus tablet, and mouse wheel input events
    """
    strokeFinished = Signal()
    toolFromTablet = Signal(str)
    frameDelta = Signal(int)
    scrubToRatio = Signal(float)

    antialias = True
    hardness = 1.0

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.setMouseTracking(True)
        self.setTabletTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

        # Drawing state & visual settings
        self.image: QImage | None = None
        self.tool = "pen"                   # 'pen' or 'eraser'
        self.color = QColor("#ff3b30")      # Current stroke color
        self.brush = 6.0                    # Base stroke width in pixels
        self.pen_opacity = 1.0              # Stroke opacity (0.0 to 1.0)
        self.clip_opacity = 1.0             # Video clip background opacity (0.0 to 1.0)
        self.notes_opacity = 1.0            # Annotations layer opacity (0.0 to 1.0)
        self.notes_visible = True           # Master visibility toggle for annotations
        self.frame_overlay = False          # HUD frame counter (never exported)
        self.frame_overlay_text = ""
        self.theme = dict(THEMES["dark"])   # Active palette for painted chrome
        self.current_frame = 0              # Currently displayed frame index
        self._dst = QRectF()                # Video drawing rect inside canvas viewport
        self._drawing = False
        self._stroke: Stroke | None = None
        self._using_tablet = False
        self._tablet_eraser = False
        self._pre_erase_tool: str | None = None
        # Viewport zoom / pan (Alt+Right-drag zoom, Alt+Middle-drag pan, Alt+DblClick reset)
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._zooming = False
        self._panning = False
        self._zoom_y0 = 0.0
        self._zoom_start = 1.0
        self._pan_pos = QPointF(0.0, 0.0)
        self._pan_start = QPointF(0.0, 0.0)
        # Manual double-click tracking (tablet events carry no DblClick type)
        self._alt_tap_last_t = 0.0
        self._alt_tap_last_pos = QPointF(0.0, 0.0)
        self._alt_tap_last_btn: Qt.MouseButton | None = None
        # Toolbar View nav mode: middle-drag pans, right-drag zooms, and
        # double-clicks reset (right=zoom, middle=pan, left=both).
        self._nav_mode = False
        self._gesture_btn: Qt.MouseButton | None = None
        self._gesture_moved = False  # true once an active drag exceeds tap tolerance
        self._middle_scrub = False
        self._middle_last_x = 0.0
        self.fade_color = QColor("#ffffff") # Background fade color behind transparent clip
        self.hardness = 1.0                 # Brush hardness (0.0 soft .. 1.0 hard)
        self.pressure_curve = [list(p) for p in DEFAULT_PRESSURE_CURVE]  # stylus pressure -> width fraction of pen size
        self.is_board = False              # Blank drawing-board mode (no media loaded)
        self.board_bg = QColor("#ffffff")  # Board page background color
        self.onion_enabled = False         # Onion skin ghosting toggle
        self.onion_prev = QColor("#c248ff")
        self.onion_next = QColor("#33ccff")
        self.onion_opacity = 0.35
        self.onion_depth_prev = 1   # how many drawings back to ghost
        self.onion_depth_next = 1   # how many drawings ahead to ghost
        self.antialias = True               # Pen antialiasing toggle
        self.cursor_mode = "circle"         # 'circle' | 'crosshair' | 'default' | 'custom'
        self.cursor_custom = ""             # Path to custom cursor image
        self.cursor_color = QColor("#ffffff")
        self.cursor_width = 2
        self.cursor_dot = True
        self._cursor_cache: dict[tuple, QPixmap] = {}
        self._cursor_override = False
        self._apply_brush_cursor()
        self._committed: QImage | None = None   # Cached render of finished strokes on this frame
        self._active: QImage | None = None      # Incremental layer for the in-progress stroke
        self._cache_key = None
        self._n_committed = 0                   # Strokes baked into _committed
        self._rendered = 0                      # Points of active stroke already painted
        self._ghost_cache: dict = {}            # LRU {frame: (QImage, sig)} for onion ghosts
        self._ghost_gkey = None                 # invalidation key for _ghost_cache

    def set_nav_mode(self, on: bool) -> None:
        """Activates the toolbar View nav gestures (False disables)."""
        self._nav_mode = bool(on)
        if not self._nav_mode and not self._zooming and not self._panning:
            self._apply_brush_cursor()

    def set_cursor_config(self, mode: str, custom: str, color: QColor, width: int, dot: bool) -> None:
        """Applies cursor preferences from Settings and refreshes the active cursor."""
        self.cursor_mode = mode if mode in ("circle", "crosshair", "default", "custom") else "circle"
        self.cursor_custom = custom
        self.cursor_color = QColor(color)
        self.cursor_width = max(1, min(6, int(width)))
        self.cursor_dot = bool(dot)
        self._cursor_cache.clear()
        self._apply_brush_cursor()

    def _draw_width(self) -> float:
        """Effective stroke width for the current brush — size 1 renders as an ultra-fine 0.1px hairline."""
        return 0.1 if self.brush <= 1.5 else float(self.brush)

    def _brush_cursor_pixmap(self) -> QPixmap:
        w = self._draw_width()
        key = (round(float(w), 2), self.cursor_color.name(), self.cursor_width, self.cursor_dot)
        pm = self._cursor_cache.get(key)
        if pm is None:
            pm = make_circle_cursor_pixmap(w, self.cursor_color, self.cursor_width, self.cursor_dot)
            self._cursor_cache[key] = pm
        return pm

    def _configured_cursor(self) -> QCursor:
        """Builds the QCursor matching current preferences (circle sized to brush by default)."""
        mode = self.cursor_mode
        if mode == "crosshair":
            return QCursor(Qt.CursorShape.CrossCursor)
        if mode == "default":
            return QCursor(Qt.CursorShape.ArrowCursor)
        if mode == "custom" and self.cursor_custom:
            pm = QPixmap(self.cursor_custom)
            if not pm.isNull():
                if pm.width() > 48 or pm.height() > 48:
                    pm = pm.scaled(
                        48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                return QCursor(pm, 0, 0)
        pm = self._brush_cursor_pixmap()
        return QCursor(pm, pm.width() // 2, pm.height() // 2)

    def _apply_brush_cursor(self) -> None:
        """Installs the configured mouse cursor for hovering the canvas."""
        self.setCursor(self._configured_cursor())

    def _push_draw_cursor(self) -> None:
        """Locks the configured cursor for the whole stroke via an application
        override, so spurious enter/leave events or tablet grabs can't flip it
        back to the system arrow mid-draw."""
        if not self._cursor_override:
            QApplication.setOverrideCursor(self._configured_cursor())
            self._cursor_override = True

    def _pop_draw_cursor(self) -> None:
        if self._cursor_override:
            QApplication.restoreOverrideCursor()
            self._cursor_override = False

    def enterEvent(self, ev) -> None:
        self._apply_brush_cursor()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.unsetCursor()
        super().leaveEvent(ev)

    def set_frame_image(self, img: QImage | None) -> None:
        """Updates currently displayed video frame image and repaints."""
        self.image = img
        self.update()

    def _ensure_cache(self) -> None:
        """Rebuilds the committed-stroke cache only when frame/size/project/undo state changes.

        While drawing, the in-progress stroke lives only in _active, so starting or
        finishing a stroke never triggers a full-frame rebuild.
        """
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            self._committed = None
            self._cache_key = None
            return
        strokes = self.project.strokes_at(self.current_frame)
        total = len(strokes)
        key = (
            id(self.project), self.current_frame, sz.width(), sz.height(),
            bool(getattr(self, "antialias", True)),
            round(self._zoom, 3), round(self._pan.x()), round(self._pan.y()),
        )
        expected = self._n_committed + (1 if self._drawing else 0)
        if key == self._cache_key and self._committed is not None and total == expected:
            return
        n = total
        if self._drawing and n and strokes[-1] is self._stroke:
            n -= 1
        img = QImage(sz, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        ip = QPainter(img)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
        for s in strokes[:n]:
            self._paint_stroke(ip, s, self._dst)
        ip.end()
        self._committed = img
        self._active = None
        self._rendered = 0
        self._n_committed = n
        self._cache_key = key

    def _ensure_active(self) -> None:
        """Paints only the newly added points of the in-progress stroke (incremental)."""
        if not self._drawing or self._stroke is None:
            return
        if self._active is None or self._active.size() != self.size():
            img = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            self._active = img
            self._rendered = 0
        pts = self._stroke.points
        if self._rendered >= len(pts):
            return
        p = QPainter(self._active)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
        if self._stroke.eraser:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            self._stroke_geometry(p, self._stroke, self._dst, QColor(0, 0, 0, 255), seg_from=self._rendered)
        elif float(getattr(self._stroke, "hardness", 1.0)) < 0.995:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Lighten)
            self._soft_stroke(p, self._stroke, self._dst, QColor(self._stroke.color), seg_from=self._rendered)
        else:
            col = QColor(self._stroke.color)
            col.setAlpha(255)
            self._stroke_geometry(p, self._stroke, self._dst, col, seg_from=self._rendered)
        p.end()
        self._rendered = len(pts)

    def _fit(self) -> QRectF:
        """Calculates letterboxed rectangle preserving media aspect ratio, with zoom/pan applied."""
        if self.is_board:
            # Boards have no decoded frame — size the page from project dimensions
            iw, ih = int(self.project.width or 1920), int(self.project.height or 1080)
        elif self.image is None or self.image.isNull():
            return QRectF()
        else:
            iw, ih = self.image.width(), self.image.height()
        wr = self.rect()
        scale = min(wr.width() / max(iw, 1), wr.height() / max(ih, 1))
        w, h = iw * scale, ih * scale
        x = wr.x() + (wr.width() - w) / 2
        y = wr.y() + (wr.height() - h) / 2
        z, pan = self._zoom, self._pan
        if z != 1.0 or not pan.isNull():
            c = QPointF(wr.center().x() + pan.x(), wr.center().y() + pan.y())
            w *= z
            h *= z
            x = c.x() - w / 2.0
            y = c.y() - h / 2.0
        return QRectF(x, y, w, h)

    def _to_norm(self, pos: QPointF) -> QPointF | None:
        """Converts screen pixel coordinates into normalized 0.0-1.0 video space."""
        r = self._dst
        if r.isEmpty() or r.width() <= 0 or r.height() <= 0:
            return None
        nx = min(max((pos.x() - r.x()) / r.width(), 0.0), 1.0)
        ny = min(max((pos.y() - r.y()) / r.height(), 0.0), 1.0)
        return QPointF(nx, ny)

    def _fit_base_width(self) -> float:
        """Fit-to-window page width (zoom=1, pan=0) for the current viewport."""
        z = getattr(self, "_zoom", 1.0)
        pan = getattr(self, "_pan", QPointF(0.0, 0.0))
        self._zoom, self._pan = 1.0, QPointF(0.0, 0.0)
        try:
            return self._fit().width()
        finally:
            self._zoom, self._pan = z, pan

    def _page_scale(self, r: QRectF) -> float:
        """Page-space scale of rect r: 1.0 == fit-to-window; == current zoom on canvas."""
        try:
            base = self._fit_base_width()
        except Exception:
            # Off-canvas renderers (video export) paint at native page size.
            return 1.0
        return r.width() / base if base > 1e-6 else 1.0

    def paintEvent(self, _ev) -> None:
        """Paints the background canvas, video frame, and frame annotations."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.theme.get("bg", "#1a1a1c")))  # Viewport background
        self._dst = self._fit()
        if self.image and not self.image.isNull() and not self._dst.isEmpty() and not self.is_board:
            p.fillRect(self._dst, self.fade_color)
            p.setOpacity(self.clip_opacity)
            p.drawImage(self._dst, self.image)
            p.setOpacity(1.0)
        elif self.is_board:
            # Blank drawing board: solid page in the board background color
            if not self._dst.isEmpty():
                p.fillRect(self._dst, self.board_bg)
        else:
            # Startup placeholder — invite drag & drop
            p.setPen(QColor(self.theme.get("muted", "#888888")))
            f = self.font()
            f.setPointSize(max(14, int(self.height() / 26)))
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drag your videos/images here")
            p.end()
            return

        if not self.notes_visible or self.notes_opacity <= 0:
            # Notes hidden — the frame counter HUD stays visible
            self._draw_frame_hud(p)
            p.end()
            return

        # Onion skin: ghosted previous/next drawings (boards & clips); always visible,
        # even mid-stroke, so you draw over the ghosts.
        if (
            getattr(self, "onion_enabled", False)
            and self.project is not None
            and self.project.frame_count > 1
            and not self._dst.isEmpty()
        ):
            self._draw_onion(p)

        # Composite cached layers: finished strokes + in-progress stroke (incremental)
        self._ensure_cache()
        self._ensure_active()
        p.setOpacity(self.notes_opacity)
        if (
            self._drawing and self._stroke is not None and self._stroke.eraser
            and self._committed is not None
        ):
            combined = QImage(self._committed)
            cp = QPainter(combined)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
            cp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            self._stroke_geometry(cp, self._stroke, self._dst, QColor(0, 0, 0, 255))
            cp.end()
            p.drawImage(0, 0, combined)
        else:
            if self._committed is not None:
                p.drawImage(0, 0, self._committed)
            if self._active is not None and self._drawing and self._stroke is not None:
                p.setOpacity(self.notes_opacity * min(max(self._stroke.opacity, 0.0), 1.0))
                p.drawImage(0, 0, self._active)

        # Frame counter HUD (screen-only; never part of exports)
        self._draw_frame_hud(p)
        p.end()

    def _draw_frame_hud(self, p: QPainter) -> None:
        """Draws the frame counter badge (top-right). Skipped when disabled/empty."""
        if not getattr(self, "frame_overlay", False) or not self.frame_overlay_text:
            return
        f = self.font()
        f.setBold(True)
        f.setPointSize(max(11, int(self.height() / 40)))
        p.setFont(f)
        text = self.frame_overlay_text
        metrics = p.fontMetrics()
        pad = 10
        tw = metrics.horizontalAdvance(text) + pad * 2
        th = metrics.height() + pad
        box = QRectF(self.width() - tw - pad, pad, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor("#eeeeee"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def _onion_picks(self) -> list[tuple[int, QColor, int]]:
        """Nearest annotated drawings before/after the current frame (per drawing, not per frame).

        Returns [(frame, color, rank)] where rank 1 is closest and opacity fades with rank.
        """
        proj = self.project
        f0 = int(self.current_frame)
        dp = min(max(int(getattr(self, "onion_depth_prev", 1)), 0), 5)
        dn = min(max(int(getattr(self, "onion_depth_next", 1)), 0), 5)
        picks: list[tuple[int, QColor, int]] = []
        if dp > 0:
            before = [f for f in proj.annotated_frames() if f < f0][-dp:]
            for i, f in enumerate(reversed(before)):
                # rank 1 = the ghost NEAREST the current drawing (brightest)
                picks.append((f, self.onion_prev, i + 1))
        if dn > 0:
            after = [f for f in proj.annotated_frames() if f > f0][:dn]
            for i, f in enumerate(after):
                picks.append((f, self.onion_next, i + 1))
        return picks

    def _draw_onion(self, p: QPainter) -> None:
        """Renders previous/next *drawings* as tinted ghosts, cached per frame.

        Every ghosted drawing is pre-rendered once in DOCUMENT pixels into an
        LRU cache keyed by its frame. During playback only frames whose
        neighbours actually changed cost a rebuild — the rest are reused — so
        onion skinning no longer drags the frame rate down.
        """
        picks = self._onion_picks()
        if not picks:
            return
        pw = max(2, int(self.project.width or 1920))
        ph = max(2, int(self.project.height or 1080))
        wr = self.rect()
        scale0 = min(wr.width() / max(pw, 1), wr.height() / max(ph, 1))
        base_w = max(1.0, float(pw) * scale0)
        wscale = float(pw) / base_w  # doc px per screen px at unzoomed fit
        gkey = (
            id(self.project), pw, ph,
            round(wscale, 4),
            int(getattr(self, "onion_depth_prev", 1)), int(getattr(self, "onion_depth_next", 1)),
            self.onion_prev.name(QColor.NameFormat.HexRgb), self.onion_next.name(QColor.NameFormat.HexRgb),
            bool(getattr(self, "antialias", True)),
        )
        cache = getattr(self, "_ghost_cache", None)
        if cache is None or getattr(self, "_ghost_gkey", None) != gkey:
            cache = {}
            self._ghost_cache = cache
            self._ghost_gkey = gkey
        out = []
        for f, col, rank in picks:
            strokes = self.project.strokes.get(f, [])
            sig = (
                col.name(QColor.NameFormat.HexRgb),
                len([s for s in strokes if s.points and not s.eraser]),
                sum(len(s.points) for s in strokes),
            )
            ent = cache.pop(f, None)
            if ent is not None and ent[1] == sig:
                cache[f] = ent  # LRU touch
                out.append((ent[0], rank))
                continue
            built = self._build_onion_ghosts([(f, col, rank)], pw, ph, wscale)
            if not built:
                continue
            cache[f] = (built[0][0], sig)
            if len(cache) > 16:
                cache.pop(next(iter(cache)))
            out.append((built[0][0], rank))
        base_op = min(max(getattr(self, "onion_opacity", 0.35), 0.05), 1.0)
        for (ghost, rank) in out:
            p.setOpacity(base_op * (0.65 ** (rank - 1)))
            p.drawImage(self._dst, ghost)
        p.setOpacity(1.0)

    def _build_onion_ghosts(self, picks, pw: int, ph: int, wscale: float):
        """Pre-renders each ghosted drawing into its own document-space image."""
        out = []
        dst = QRectF(0.0, 0.0, float(pw), float(ph))
        for f, col, rank in picks:
            strokes = [s for s in self.project.strokes.get(f, []) if s.points and not s.eraser]
            if not strokes:
                continue
            ghost = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
            ghost.fill(Qt.GlobalColor.transparent)
            gp = QPainter(ghost)
            gp.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
            for s in strokes:
                tinted = Stroke(
                    color=col.name(QColor.NameFormat.HexRgb),
                    base_width=max(0.1, s.base_width * wscale),
                    eraser=False,
                    opacity=s.opacity,
                    hardness=float(getattr(s, "hardness", 1.0)),
                    points=list(s.points),
                )
                self._paint_stroke(gp, tinted, dst)
            gp.end()
            out.append((ghost, rank))
        return out

    def _paint_stroke(self, p: QPainter, s: Stroke, r: QRectF) -> None:
        """Paints a single Stroke onto target painter."""
        if len(s.points) < 1:
            return
        if s.eraser:
            # Eraser clears pixels directly
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            col = QColor(0, 0, 0, 255)
            self._stroke_geometry(p, s, r, col)
            return

        # Draw stroke fully opaque on an isolated layer, then composite once at stroke opacity.
        # This prevents overlapping stamp points from accumulating dark dots.
        op = min(max(s.opacity, 0.0), 1.0)
        col = QColor(s.color)
        col.setAlpha(255)
        soft = float(getattr(s, "hardness", 1.0)) < 0.995
        if op >= 0.999 and not soft:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._stroke_geometry(p, s, r, col)
            return

        dev = p.device()
        layer = QImage(dev.width(), dev.height(), QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
        if soft:
            lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Lighten)
            self._soft_stroke(lp, s, r, col)
        else:
            self._stroke_geometry(lp, s, r, col)
        lp.end()
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(op)
        p.drawImage(0, 0, layer)
        p.restore()

    def _stroke_geometry(self, p: QPainter, s: Stroke, r: QRectF, col: QColor, seg_from: int = 1) -> None:
        """Geometric antialiased rendering engine with dynamic pressure response."""
        # Stroke widths live in PAGE space: they scale with zoom so ink looks
        # like real ink on paper (zoom in -> thicker lines, out -> thinner).
        k = self._page_scale(r)

        def to_screen(pt: Point) -> QPointF:
            return QPointF(r.x() + pt.x * r.width(), r.y() + pt.y * r.height())

        def width_for(pr: float) -> float:
            return k * max(min(0.8, s.base_width), s.base_width * pr)

        pts = s.points
        p.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
        if not pts:
            return

        # Leading round cap (also covers single-dot taps; keeps incremental == full render)
        if int(seg_from) <= 1:
            a0 = pts[0]
            p.setPen(QPen(col, width_for(a0.p), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPoint(to_screen(a0))

        # Continuous smooth line interpolation
        for i in range(max(1, int(seg_from)), len(pts)):
            a, b = pts[i - 1], pts[i]
            pa, pb = to_screen(a), to_screen(b)
            wa, wb = width_for(a.p), width_for(b.p)
            dist = ((pb.x() - pa.x()) ** 2 + (pb.y() - pa.y()) ** 2) ** 0.5
            steps = max(1, int(dist / max(1.0, min(wa, wb) * 0.35)))
            for t in range(steps + 1):
                u = t / steps
                x = pa.x() + (pb.x() - pa.x()) * u
                y = pa.y() + (pb.y() - pa.y()) * u
                w = wa + (wb - wa) * u
                pen = QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                if t == 0:
                    p.drawPoint(QPointF(x, y))
                else:
                    prev_u = (t - 1) / steps
                    x0 = pa.x() + (pb.x() - pa.x()) * prev_u
                    y0 = pa.y() + (pb.y() - pa.y()) * prev_u
                    p.drawLine(QPointF(x0, y0), QPointF(x, y))

    def _soft_stroke(self, p: QPainter, s: Stroke, r: QRectF, col: QColor, seg_from: int = 1) -> None:
        """Soft brush engine: feathered radial-gradient stamps with max-alpha compositing."""
        h = min(max(float(getattr(s, "hardness", 1.0)), 0.0), 1.0)
        inner = 0.08 + 0.82 * h
        # Ease the feather falloff so low hardness feels airier: gamma > 1
        # thins the mid-feather ink at hardness 0 while hardness 100% keeps
        # its crisp near-linear edge.
        gamma = 1.0 + 0.70 * (1.0 - h)
        # Overlapping stamps accumulate alpha (Qt blends Lighten like
        # source-over), which re-saturates the feather and makes soft brushes
        # read hard. Keep the core opaque but scale the feather stops down at
        # low hardness so the outer envelope stays translucent.
        peak = 1.0 - 0.60 * (1.0 - h)
        edge = QColor(col.red(), col.green(), col.blue(), 0)

        def stamp(x: float, y: float, w: float) -> None:
            ri = w / 2.0
            # Qt rasterizes sub-pixel gradient ellipses as empty (worst on
            # exact pixel-corner centers), so keep a viable drawn radius AND
            # a viable visible core (~1.1px) for hairline brushes.
            rad = max(2.0 * k, ri)
            sc = min(1.0, max(0.55, ri / rad))
            g = QRadialGradient(x, y, rad)
            g.setColorAt(0.0, col)
            g.setColorAt(min(inner * sc, 0.995), col)
            span = max(1e-3, 1.0 - inner)
            base_a = col.alpha() * peak
            for tt in (0.25, 0.5, 0.75):
                a = int(round(base_a * ((1.0 - tt) ** gamma)))
                pos = min((inner + span * tt) * sc, 0.999)
                g.setColorAt(pos, QColor(col.red(), col.green(), col.blue(), a))
            g.setColorAt(min(sc, 1.0), edge)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(x, y), rad, rad)

        def to_screen(pt: Point) -> QPointF:
            return QPointF(r.x() + pt.x * r.width(), r.y() + pt.y * r.height())

        k = self._page_scale(r)  # widths live in page space, like _stroke_geometry

        def width_for(pr: float) -> float:
            return k * max(min(0.8, s.base_width), s.base_width * pr)

        def step_for(w: float) -> float:
            # Space stamps as a fraction of the feather width so consecutive
            # outlines overlap smoothly at every hardness (no visible circles,
            # even on fast strokes with long segments).
            rad = w / 2.0
            feather = max(0.0, rad * (1.0 - inner))
            return max(0.6, rad * 0.12, min(feather * 0.18, rad * 0.45))

        pts = s.points
        if not pts:
            return
        if int(seg_from) <= 1:
            q0 = to_screen(pts[0])
            stamp(q0.x(), q0.y(), width_for(pts[0].p))
        for i in range(max(1, int(seg_from)), len(pts)):
            a, b = pts[i - 1], pts[i]
            pa, pb = to_screen(a), to_screen(b)
            wa, wb = width_for(a.p), width_for(b.p)
            dist = ((pb.x() - pa.x()) ** 2 + (pb.y() - pa.y()) ** 2) ** 0.5
            step = step_for(min(wa, wb))
            n = max(1, int(dist / step))
            for t in range(n + 1):
                u = t / n
                stamp(
                    pa.x() + (pb.x() - pa.x()) * u,
                    pa.y() + (pb.y() - pa.y()) * u,
                    wa + (wb - wa) * u,
                )

    def _begin(self, pos: QPointF, pressure: float, eraser: bool | None = None) -> None:
        """Starts a new stroke at position with given pressure."""
        n = self._to_norm(pos)
        if n is None:
            return
        use_eraser = self.tool == "eraser" if eraser is None else eraser
        self._drawing = True
        self._stroke = Stroke(
            color=self.color.name(QColor.NameFormat.HexRgb),
            base_width=self._draw_width(),
            eraser=use_eraser,
            opacity=self.pen_opacity,
            hardness=self.hardness,
            points=[Point(n.x(), n.y(), pressure)],
        )
        self.project.strokes_at(self.current_frame).append(self._stroke)
        self._dst = self._fit()
        self._active = None
        self._rendered = 0
        self._ensure_active()
        self._push_draw_cursor()
        self.update()

    def _move(self, pos: QPointF, pressure: float) -> None:
        """Appends new sample point to the active stroke."""
        if not self._drawing or self._stroke is None:
            return
        n = self._to_norm(pos)
        if n is None:
            return
        last = self._stroke.points[-1]
        dx, dy = n.x() - last.x, n.y() - last.y
        if (dx * dx + dy * dy) < 1e-8:
            return
        self._stroke.points.append(Point(n.x(), n.y(), pressure))
        self._ensure_active()
        self.update()

    def _end(self) -> None:
        """Finalizes current stroke by merging it into the committed cache (no rebuild)."""
        if not self._drawing:
            return
        self._pop_draw_cursor()
        s = self._stroke
        self._drawing = False
        self._stroke = None
        if s is not None and self._committed is not None:
            cp = QPainter(self._committed)
            if s.eraser:
                # Clear exactly the eraser geometry; compositing the transparent
                # active layer with CompositionMode_Clear would wipe the whole
                # committed cache (Clear ignores source alpha).
                cp.setRenderHint(QPainter.RenderHint.Antialiasing, bool(getattr(self, "antialias", True)))
                cp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                self._stroke_geometry(cp, s, self._dst, QColor(0, 0, 0, 255))
            elif self._active is not None:
                cp.setOpacity(min(max(float(s.opacity), 0.0), 1.0))
                cp.drawImage(0, 0, self._active)
            cp.end()
            self._n_committed += 1
        self._active = None
        self._rendered = 0
        self.strokeFinished.emit()

    # -----------------------------------------------------------------------
    # Middle-Click Timeline Scrubbing Handlers
    # -----------------------------------------------------------------------
    def _start_scrub(self, x: float) -> None:
        self._middle_scrub = True
        self._middle_last_x = x
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _stop_scrub(self) -> None:
        if self._middle_scrub:
            self._middle_scrub = False
            self._apply_brush_cursor()

    def _scrub_move(self, x: float) -> None:
        dx = x - self._middle_last_x
        step = 8.0  # Pixels per frame step
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            step *= 5.0  # Ctrl = fine scrubbing (5x more pixels per frame)
        if abs(dx) >= step:
            frames = int(dx / step)
            self._middle_last_x = x
            if frames:
                self.frameDelta.emit(frames)

    # -----------------------------------------------------------------------
    # Mouse & Tablet Input Events
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Viewport Zoom & Pan (shared by mouse and stylus input paths)
    # -----------------------------------------------------------------------
    def _reset_view(self) -> None:
        """Restores fit-to-window zoom/pan."""
        self._zooming = False
        self._panning = False
        self._gesture_btn = None
        self._gesture_moved = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.unsetCursor()
        self._apply_brush_cursor()
        self.update()

    def _reset_zoom(self) -> None:
        """Restores fit-to-window zoom, keeping the current pan offset."""
        self._zooming = False
        self._gesture_moved = False
        self._zoom = 1.0
        self.unsetCursor()
        self._apply_brush_cursor()
        self.update()

    def _reset_pan(self) -> None:
        """Recenters the pan offset, keeping the current zoom."""
        self._panning = False
        self._gesture_moved = False
        self._pan = QPointF(0.0, 0.0)
        self.unsetCursor()
        self._apply_brush_cursor()
        self.update()

    def _begin_zoom(self, y: float, btn: Qt.MouseButton) -> None:
        self._zooming = True
        self._gesture_btn = btn
        self._gesture_moved = False
        self._zoom_y0 = y
        self._zoom_start = self._zoom
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def _move_zoom(self, y: float) -> None:
        dy = y - self._zoom_y0
        if abs(dy) > 6.0:
            self._gesture_moved = True
        factor = 1.0 - dy * 0.005  # drag up = zoom in, down = zoom out
        self._zoom = min(8.0, max(0.25, self._zoom_start * factor))
        self.update()

    def _end_zoom(self) -> None:
        self._zooming = False
        self._gesture_btn = None
        self.unsetCursor()
        self._apply_brush_cursor()

    def _begin_pan(self, pos: QPointF, btn: Qt.MouseButton) -> None:
        self._panning = True
        self._gesture_btn = btn
        self._gesture_moved = False
        self._pan_pos = QPointF(pos)
        self._pan_start = QPointF(self._pan)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _move_pan(self, pos: QPointF) -> None:
        if (pos - self._pan_pos).manhattanLength() > 8.0:
            self._gesture_moved = True
        d = pos - self._pan_pos
        self._pan = QPointF(self._pan_start.x() + d.x(), self._pan_start.y() + d.y())
        self.update()

    def _end_pan(self) -> None:
        self._panning = False
        self._gesture_btn = None
        self.unsetCursor()
        self._apply_brush_cursor()

    def mousePressEvent(self, ev) -> None:
        # Fresh interaction: forget any drag distance from earlier gestures.
        # A true double-click skips this (Qt delivers DblClick instead of the
        # second press), so the guard below still sees the first tap.
        self._gesture_moved = False
        alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        if alt and ev.button() == Qt.MouseButton.RightButton:
            self._begin_zoom(ev.position().y(), Qt.MouseButton.RightButton)
            ev.accept()
            return
        if alt and ev.button() == Qt.MouseButton.MiddleButton:
            self._begin_pan(ev.position(), Qt.MouseButton.MiddleButton)
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.MiddleButton:
            if self._nav_mode:
                self._begin_pan(ev.position(), Qt.MouseButton.MiddleButton)
            else:
                self._start_scrub(ev.position().x())
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.RightButton:
            if self._nav_mode:
                self._begin_zoom(ev.position().y(), Qt.MouseButton.RightButton)
            ev.accept()
            return
        if self._using_tablet:
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            if alt or self._nav_mode:
                # Alt / View-nav reserve the left button for gestures and
                # reset double-clicks — never draws.
                ev.accept()
                return
            self._begin(ev.position(), 0.75)

    def mouseDoubleClickEvent(self, ev) -> None:
        """View nav resets: right = zoom, middle = pan, left = both.

        Guarded by _gesture_moved so a double-click right after an actual
        zoom/pan drag never wipes the view the user just adjusted.
        """
        btn = ev.button()
        alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        if (self._nav_mode or alt) and not self._gesture_moved:
            if btn == Qt.MouseButton.RightButton:
                self._reset_zoom()
                ev.accept()
                return
            if btn == Qt.MouseButton.MiddleButton:
                self._reset_pan()
                ev.accept()
                return
            if btn == Qt.MouseButton.LeftButton:
                self._reset_view()
                ev.accept()
                return
        ev.ignore()

    def mouseMoveEvent(self, ev) -> None:
        if self._zooming:
            self._move_zoom(ev.position().y())
            ev.accept()
            return
        if self._panning:
            self._move_pan(ev.position())
            ev.accept()
            return
        if self._middle_scrub:
            self._scrub_move(ev.position().x())
            ev.accept()
            return
        if self._using_tablet:
            return
        if self._drawing and (ev.buttons() & Qt.MouseButton.LeftButton):
            self._move(ev.position(), 0.75)

    def mouseReleaseEvent(self, ev) -> None:
        if self._zooming and ev.button() == self._gesture_btn:
            self._end_zoom()
            ev.accept()
            return
        if self._panning and ev.button() == self._gesture_btn:
            self._end_pan()
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.MiddleButton:
            self._stop_scrub()
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            return
        if self._using_tablet:
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            self._end()

    def wheelEvent(self, ev: QWheelEvent) -> None:
        """Mouse scroll wheel steps frames forward/backward."""
        dy = ev.angleDelta().y()
        if dy == 0:
            dx = ev.angleDelta().x()
            if dx:
                self.frameDelta.emit(1 if dx > 0 else -1)
            ev.accept()
            return
        self.frameDelta.emit(-1 if dy > 0 else 1)
        ev.accept()

    def tabletEvent(self, ev: QTabletEvent) -> None:
        """Wacom / Graphics Stylus tablet handler with high-precision pressure and eraser end detection.

        Tool policy: the user's selected tool always wins. Only physical contact
        with the stylus eraser end temporarily switches to eraser (restored on
        release). Hovering never switches tools or claims input, so the mouse
        keeps working after the pen has been near the tablet.
        """
        pressure = eval_pressure_curve(self.pressure_curve, float(ev.pressure()))
        eraser = ev.pointerType() == QPointingDevice.PointerType.Eraser
        t = ev.type()
        btn = ev.button()
        held = ev.buttons()
        alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)

        def is_middle() -> bool:
            return bool((btn | held) & Qt.MouseButton.MiddleButton)

        def is_right() -> bool:
            return bool((btn | held) & Qt.MouseButton.RightButton)

        if t == QTabletEvent.Type.TabletMove and not held and not self._drawing and not self._middle_scrub:
            # Proximity hover only — don't latch tablet mode (would block mouse).
            ev.accept()
            return

        self._using_tablet = True

        if t == QTabletEvent.Type.TabletPress:
            # Alt / View-nav + double left-tap (pen nib) resets the view —
            # timed manually since tablet events carry no DblClick type.
            # Right/middle are reserved for zoom/pan and never trigger one.
            now = time.monotonic()
            quick = (
                (alt or self._nav_mode)
                and btn == Qt.MouseButton.LeftButton
                and btn == self._alt_tap_last_btn
                and not self._drawing
                and (now - self._alt_tap_last_t) * 1000.0 <= QApplication.doubleClickInterval()
                and (ev.position() - self._alt_tap_last_pos).manhattanLength() < 8.0
            )
            self._alt_tap_last_btn = btn if alt else None
            self._alt_tap_last_t = now if (alt and btn != Qt.MouseButton.NoButton) else 0.0
            self._alt_tap_last_pos = QPointF(ev.position())
            if quick:
                self._reset_view()
                ev.accept()
                return
            if alt or self._nav_mode:
                if is_right():
                    self._begin_zoom(ev.position().y(), Qt.MouseButton.RightButton)
                elif is_middle():
                    self._begin_pan(ev.position(), Qt.MouseButton.MiddleButton)
                ev.accept()
                return
            if is_middle():
                self._start_scrub(ev.position().x())
                ev.accept()
                return
            if is_right():
                ev.accept()
                return
            if eraser:
                if not self._tablet_eraser:
                    self._tablet_eraser = True
                    self._pre_erase_tool = self.tool
                    self.toolFromTablet.emit("eraser")
            else:
                self._tablet_eraser = False
            # eraser=None lets _begin derive from the selected tool, so a manual
            # eraser choice is respected when drawing with the pen's front end.
            self._begin(ev.position(), pressure, eraser=eraser or None)
            ev.accept()
        elif t == QTabletEvent.Type.TabletMove:
            if self._zooming:
                self._move_zoom(ev.position().y())
                ev.accept()
                return
            if self._panning:
                self._move_pan(ev.position())
                ev.accept()
                return
            if self._middle_scrub or is_middle():
                self._scrub_move(ev.position().x())
                ev.accept()
                return
            if self._drawing and not is_right() and not is_middle():
                self._move(ev.position(), pressure)
            ev.accept()
        elif t == QTabletEvent.Type.TabletRelease:
            if self._zooming and btn == self._gesture_btn:
                self._end_zoom()
                QTimer.singleShot(80, lambda: setattr(self, "_using_tablet", False))
                ev.accept()
                return
            if self._panning and btn == self._gesture_btn:
                self._end_pan()
                QTimer.singleShot(80, lambda: setattr(self, "_using_tablet", False))
                ev.accept()
                return
            if is_middle() or self._middle_scrub:
                self._stop_scrub()
                ev.accept()
                QTimer.singleShot(80, lambda: setattr(self, "_using_tablet", False))
                return
            if self._drawing:
                self._end()
            if eraser and self._tablet_eraser:
                self.toolFromTablet.emit(self._pre_erase_tool or "pen")
                self._pre_erase_tool = None
                self._tablet_eraser = False
            QTimer.singleShot(80, lambda: setattr(self, "_using_tablet", False))
            ev.accept()
        else:
            ev.ignore()


# ===========================================================================
# 8. VIDEO EXPORT ENGINE
# ===========================================================================

def render_annotations_on_bgr(bgr: np.ndarray, strokes: list[Stroke], antialias: bool = True, notes_opacity: float = 1.0) -> np.ndarray:
    """Renders vector stroke annotations directly on an OpenCV BGR frame array with alpha blending."""
    h, w = bgr.shape[:2]
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, bool(antialias))
    r = QRectF(0, 0, w, h)

    # Create a minimal canvas-like object with the correct page scale for export.
    # The page space is normalized (0-1) and should map 1:1 to the frame pixels.
    class _ExportCanvas:
        _zoom = 1.0
        _pan = QPointF(0.0, 0.0)
        project = type('obj', (object,), {'width': w, 'height': h})()
        
        def _fit(self) -> QRectF:
            return QRectF(0, 0, w, h)
        
        def _fit_base_width(self) -> float:
            return float(w)
        
        def _page_scale(self, rect: QRectF) -> float:
            return rect.width() / w if w > 0 else 1.0

        # Delegate to the real Canvas implementations
        def _stroke_geometry(self, p, s, r, col, seg_from=1):
            return Canvas._stroke_geometry(self, p, s, r, col, seg_from)
        
        def _soft_stroke(self, p, s, r, col, seg_from=1):
            return Canvas._soft_stroke(self, p, s, r, col, seg_from)

    dummy = _ExportCanvas()
    dummy.antialias = bool(antialias)

    for s in strokes:
        Canvas._paint_stroke(dummy, p, s, r)
    p.end()

    ptr = img.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
    overlay_bgra = arr
    alpha = overlay_bgra[:, :, 3:4].astype(np.float32) / 255.0
    # Apply global notes opacity (canvas slider) as extra alpha multiplier
    notes_opacity = float(max(0.0, min(1.0, notes_opacity)))
    if notes_opacity < 0.995:
        alpha = alpha * notes_opacity
        # bgr_o is premultiplied — scale RGB by same factor to keep premultiplication correct
        overlay_bgra[:, :, 0:3] = (overlay_bgra[:, :, 0:3].astype(np.float32) * notes_opacity).astype(np.uint8)
        bgr_o = overlay_bgra[:, :, 0:3].astype(np.float32)
    else:
        bgr_o = overlay_bgra[:, :, 0:3].astype(np.float32)
    # overlay is premultiplied: out = bgr*(1-alpha) + bgr_o
    out = bgr.astype(np.float32) * (1.0 - alpha) + bgr_o
    return np.clip(out, 0, 255).astype(np.uint8)


def _fade_bgr(frame_shape, fade_hex: str) -> np.ndarray:
    """Solid BGR image of fade_color for clip-opacity blending."""
    c = QColor(fade_hex)
    b, g, r = c.blue(), c.green(), c.red()
    h, w = frame_shape[:2]
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:, :] = (b, g, r)
    return arr


class ExportWorker(QThread):
    """
    Background worker thread that composites annotations frame-by-frame
    and pipes raw video frames directly into an FFmpeg subprocess for H.264/AAC encoding.
    """
    progress = Signal(int)
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, video_path: str, project: Project, dest: str, export_audio: bool = True, antialias: bool = True, strokes_snapshot: dict | None = None, clip_opacity: float = 1.0, fade_color: str = "#ffffff", notes_opacity: float = 1.0) -> None:
        super().__init__()
        self.video_path = video_path
        self.project = project
        self.dest = dest
        self.export_audio = export_audio
        self.antialias = antialias
        self._strokes_snapshot = strokes_snapshot
        self.clip_opacity = float(max(0.0, min(1.0, clip_opacity)))
        self.fade_color = str(fade_color or "#ffffff")
        self.notes_opacity = float(max(0.0, min(1.0, notes_opacity)))

    def _encode(self, w: int, h: int, fps: float, with_audio: bool) -> tuple[int, str]:
        vcodec = [
            "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "1",
        ]
        if Path(self.dest).suffix.lower() == ".mov":
            vcodec += ["-movflags", "+faststart"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        ]
        if with_audio:
            cmd += ["-i", self.video_path, *vcodec, "-map", "0:v:0", "-map", "1:a?",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", self.dest]
        else:
            cmd += [*vcodec, self.dest]
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return 1, "Could not open source video."
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
        i = 0
        total = int(self.project.frame_count or cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
        self.progress.emit(1)
        try:
            assert proc.stdin is not None
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if frame.shape[1] != w or frame.shape[0] != h:
                    frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
                # Apply clip opacity vs fade_color (matches Canvas.paintEvent fillRect+setOpacity)
                if self.clip_opacity < 0.995:
                    # blend frame towards fade_color
                    fc = QColor(self.fade_color)
                    fb, fg, fr = fc.blue(), fc.green(), fc.red()
                    # fast per-frame blend
                    frame = (frame.astype(np.float32) * self.clip_opacity + np.array([fb, fg, fr], dtype=np.float32) * (1.0 - self.clip_opacity)).astype(np.uint8)
                strokes = self._strokes_snapshot.get(i, []) if self._strokes_snapshot is not None else self.project.strokes.get(i, [])
                if strokes:
                    frame = render_annotations_on_bgr(frame, strokes, antialias=self.antialias, notes_opacity=self.notes_opacity)
                proc.stdin.write(np.ascontiguousarray(frame).tobytes())
                i += 1
                self.progress.emit(min(99, 1 + int(i / max(total, 1) * 98)))
            proc.stdin.close()
            err = proc.stderr.read() if proc.stderr else b""
            proc.wait()
            return proc.returncode, (err or b"").decode(errors="ignore")
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            return 1, str(e)
        finally:
            cap.release()

    def run(self) -> None:
        probe = cv2.VideoCapture(self.video_path)
        if not probe.isOpened():
            self.failed.emit("Could not open source video.")
            return
        fps = probe.get(cv2.CAP_PROP_FPS) or self.project.fps
        w = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
        probe.release()
        if w % 2:
            w -= 1
        if h % 2:
            h -= 1
        code, err = self._encode(w, h, fps, with_audio=self.export_audio)
        if code != 0 and self.export_audio:
            code, err = self._encode(w, h, fps, with_audio=False)
        if code != 0:
            self.failed.emit(err[-800:] or "ffmpeg failed")
            return
        self.progress.emit(100)
        self.finished_ok.emit(self.dest)


# ===========================================================================
# 9. MAIN APPLICATION WINDOW (MainWindow)
# ===========================================================================

class QueueList(QListWidget):
    """Shot list: Delete removes entries, internal drag & drop reorders them."""

    deleteRequested = Signal()
    rowsMoved = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ShortcutOverride and ev.key() == Qt.Key.Key_Delete:
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)

    def dropEvent(self, ev) -> None:
        super().dropEvent(ev)
        if ev.isAccepted():
            self.rowsMoved.emit()


class MainWindow(QMainWindow):
    """
    Main application window containing:
    - Viewport canvas
    - Drawing toolbar (buttons, swatches, sliders for size and opacity)
    - Playback bar (frame navigation, playback buttons, timeline scrubber, timecode)
    - Menu bar, keyboard shortcuts, autosave timers, settings persistence, audio engine
    """
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("InkIt")
        self.resize(1280, 820)
        self.project = Project()
        self.reader = VideoReader()
        self._settings = default_settings()
        self._load_app_settings()
        self.time_mode = "frames"           # 'frames' or 'time'
        self._theme_name = "dark"           # Light mode disabled for now
        self._session_notes: dict = {}      # In-memory per-clip drawings for shot switching
        self._dyn_styles: list = []         # Zero-arg restylers re-run on theme switch
        self.playing = False                # Video playback status
        self.loop = True                    # Loop playback flag
        self.audio_on = True                # Audio enabled flag
        self.audio_volume = 100             # Audio volume (0..100)
        self.export_audio = True            # Include audio in export flag
        self._settings = default_settings()
        self._load_app_settings()
        self._loading_settings = False
        self._player = None
        self._audio_out = None
        # App-wide hover watcher: any widget with a statusTip shows it in the
        # status bar while hovered (see eventFilter). Installed after the UI
        # is built — see end of __init__.
        self._hover_status = False
        self._init_audio_engine()
        self._vol_drag_x = None             # Volume drag state (audio button)
        self._vol_drag_start = 0
        self._vol_dragged = False

        # Canvas Setup
        self.canvas = Canvas(self.project)
        # Per-tool brush sizes (pen / eraser), applied on tool switch
        self._tool_sizes = {
            "pen": min(max(int(self._settings.get("brush", 6)), 1), 80),
            "eraser": min(max(int(self._settings.get("brush_eraser", 30)), 1), 80),
        }
        # Shot queue: files staged on the left panel for quick switching
        self.queue_paths: list[str] = []
        self._thumbs: dict = {}     # Cached shot-list thumbnails, keyed by path+mtime
        self._is_still = False      # True while a still image (not a video clip) is loaded
        self._still_bgr = None      # Original BGR pixels of the loaded image
        self.queue_index: int = -1
        self.queue_visible = bool(self._settings.get("queue_visible", True))
        self.queue_minimized = bool(self._settings.get("queue_minimized", False))
        # Undo history for redo (per frame: Stroke items or cleared-frame lists)
        self._redo_stack: dict[int, list] = {}
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.strokeFinished.connect(self._on_stroke_finished)
        self.canvas.strokeFinished.connect(self._invalidate_redo)
        self.canvas.strokeFinished.connect(self._on_stroke_finished_autosave)
        self.canvas.toolFromTablet.connect(self._set_tool)
        self.canvas.frameDelta.connect(self._step)
        self.setAcceptDrops(True)
        self._shortcuts = dict(DEFAULT_SHORTCUTS)
        self._load_shortcuts()
        self._actions: dict[str, QAction] = {}

        # Timers
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._tick)
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._write_app_settings)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_notes)

        # Build UI & Menus
        self._build_ui()
        self._apply_settings_to_ui()
        self._apply_theme()
        self._build_menus()
        self.statusBar().showMessage(
            "Middle-drag or scroll wheel to change frames."
        )
        geo = self._settings.get("win_geometry") or ""
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
        if bool(self._settings.get("always_on_top", False)):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # App-wide hover watcher (status bar tips). Installed last so it never
        # sees events from widgets still under construction.
        QApplication.instance().installEventFilter(self)
        self._cleanup_autosaves()  # purge autosaves older than the configured age

    # -----------------------------------------------------------------------
    # Helper Widget Builders
    # -----------------------------------------------------------------------

    def _vline(self) -> QFrame:
        """Creates a sleek 1px vertical separator line for the toolbar."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedWidth(1)
        line.setFixedHeight(46)
        line.setStyleSheet("QFrame { color: #5a5a64; background: #5a5a64; border: none; }")
        return line

    def _icon_button(self, kind: str, tooltip: str, checkable: bool = False, highlight: bool = False) -> QPushButton:
        """Creates a standard styled 34x34 icon button."""
        btn = QPushButton()
        btn.setFixedSize(34, 34)
        btn.setCheckable(checkable)
        btn.setToolTip(tooltip)
        btn.setIcon(make_tool_icon(kind, True))
        btn.setIconSize(QSize(18, 18))

        def restyle() -> None:
            c = self._colors()
            checked = (
                f"QPushButton:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}"
                "QPushButton:checked:hover { background: #3d5afe; border-color: #3d5afe; }"
                if highlight
                else (
                    f"QPushButton:checked {{ background: {c['card']}; border-color: {c['muted']}; }}"
                    f"QPushButton:checked:hover {{ background: {c['card_hover']}; border-color: {c['muted']}; }}"
                )
            )
            btn.setStyleSheet(
                f"QPushButton {{ background: {c['card']}; border: 1px solid {c['border']};"
                f" border-radius: 4px; padding: 0; }}"
                + checked
                + f"QPushButton:hover {{ background: {c['card_hover']}; }}"
            )

        restyle()
        self._dyn_styles.append(restyle)
        return btn

    def _tool_group(self, *widgets) -> QFrame:
        """Flat modern group: buttons share one strip; thin dividers separate them."""
        box = QFrame()
        box.setObjectName("toolGroup")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        seps: list[QWidget] = []
        for i, wdg in enumerate(widgets):
            if i:
                sep = QWidget(box)
                sep.setFixedSize(1, 22)
                seps.append(sep)
                lay.addWidget(sep)
            lay.addWidget(wdg)

        def restyle() -> None:
            c = self._colors()
            box.setStyleSheet(
                "#toolGroup { background: transparent; border: none; }"
                "#toolGroup QPushButton { background: transparent; border: none;"
                " border-radius: 5px; padding: 3px; }"
                f"#toolGroup QPushButton:hover {{ background: {c['card_hover']}; }}"
                f"#toolGroup QPushButton:checked {{ background: {c['accent']}; border: none; }}"
                "#toolGroup QPushButton:checked:hover { background: #3d5afe; }"
            )
            for sep in seps:
                sep.setStyleSheet(f"background:{c['sep']};")

        restyle()
        self._dyn_styles.append(restyle)
        return box

    # -----------------------------------------------------------------------
    # Main UI Construction (_build_ui)
    # -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        """
        Constructs the entire UI hierarchy:
        1. Central Canvas Viewport
        2. Top Toolbar Bar (Tools, Swatches, Sliders)
        3. Bottom Playback Bar (Navigation, Play/Pause, Loop, Audio, Scrubber, Timecode)
        """
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # [QUEUE] Left-side shot queue panel (user-resizable) + canvas viewport
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.queue_panel = self._build_queue_panel()
        self.queue_panel.setMinimumWidth(140)
        self.splitter.addWidget(self.queue_panel)
        self.splitter.addWidget(self.canvas)
        self.splitter.setStretchFactor(1, 1)
        saved_w = int(self._settings.get("queue_width", 220) or 220)
        self.splitter.setSizes([max(140, saved_w), 10000])
        self.splitter.splitterMoved.connect(self._queue_split_moved)
        layout.addWidget(self.splitter, 1)
        self._apply_queue_layout()

        # -------------------------------------------------------------------
        # [TOOLBAR] Top Toolbar Bar
        # -------------------------------------------------------------------
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 4, 10, 6)
        bar.setSpacing(8)
        bar.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        wrap = QWidget()
        wrap.setLayout(bar)
        wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        def restyle_wrap() -> None:
            c = self._colors()
            wrap.setStyleSheet(
                f"QWidget {{ background: {c['panel']}; color: {c['text']}; }}"
                f"#toolGroup, #toolGroup QWidget {{ background: transparent; }}"
                f"QPushButton {{ background: {c['card']}; border: 1px solid {c['border']};"
                f" padding: 6px 12px; border-radius: 4px; }}"
                f"QPushButton:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}"
                f"QPushButton:hover {{ background: {c['card_hover']}; }}"
                "QPushButton:checked:hover { background: #3d5afe; border-color: #3d5afe; }"
                f"QComboBox, QSpinBox {{ background: {c['card']}; border: 1px solid {c['border']};"
                f" padding: 4px 8px; color: {c['text']}; }}"
                f"QSlider::groove:horizontal {{ height: 6px; background: {c['border']}; border-radius: 3px; }}"
                f"QSlider::handle:horizontal {{ width: 14px; background: {c['text']}; margin: -5px 0; border-radius: 7px; }}"
            )

        restyle_wrap()
        self._dyn_styles.append(restyle_wrap)

        # --- [BUTTON] Pen/Eraser combo (click = toggle, drag = size) ---
        self.btn_tool = ToolButton(value=int(self._settings.get("brush", 6)))
        self.btn_tool.setTool(self._settings.get("tool", "pen") if self._settings.get("tool") in ("pen", "eraser") else "pen")
        self.btn_tool.toolToggled.connect(self._toggle_tool)
        self.btn_tool.sizeDragged.connect(self._on_tool_size)

        # --- [BUTTON] Color Picker Swatch ---
        self.btn_color = QPushButton("  ")
        self.btn_color.setFixedWidth(40)
        self.btn_color.setStyleSheet("background: #ff3b30; border: 1px solid #888; border-radius: 4px;")
        self.btn_color.clicked.connect(self._pick_color)

        # --- [BUTTON] Eyedropper — opens the pen color dialog (shortcut C) ---
        self.btn_picker = self._icon_button("picker", "Pick color from screen — click to assign to pen")
        self.btn_picker.clicked.connect(self._pick_screen_color)

        # --- [BUTTONS] 4 fixed color circles — click applies, double-click assigns ---
        self._pen_swatches = []
        for i in range(4):
            sw = ColorSwatch(i)
            sw.setFixedSize(24, 24)
            sw.setStyleSheet("background:#444; border:2px solid #555; border-radius:12px;")
            sw.assigned.connect(self._assign_swatch_color)
            bar.addWidget(sw)
            self._pen_swatches.append(sw)

        bar.addWidget(self.btn_color)
        bar.addWidget(self.btn_picker)
        bar.addWidget(self.btn_tool)

        # --- [BOXES] Brush Hardness (top row) + Pen Opacity (bottom row) ---
        self.sl_hard = BoxSlider("Hardness", 0, 100, 100, "%", icon="hardness")
        self.sl_hard.setToolTip("Hardness — click or drag anywhere to adjust")
        self.sl_hard.valueChanged.connect(self._hardness_changed)
        self.sl_pen_op = BoxSlider("Opacity", 0, 100, 100, "%", icon="opacity")
        self.sl_pen_op.setToolTip("Opacity — click or drag anywhere to adjust")
        self.sl_pen_op.valueChanged.connect(self._pen_opacity_changed)
        pen_stack = QWidget()
        pen_stack_lay = QVBoxLayout(pen_stack)
        pen_stack_lay.setContentsMargins(0, 0, 0, 0)
        pen_stack_lay.setSpacing(1)
        pen_stack_lay.addWidget(self.sl_hard)
        pen_stack_lay.addWidget(self.sl_pen_op)
        bar.addWidget(pen_stack)

        bar.addWidget(self._vline())

        # --- [BOXES] Video Clip Opacity (top row) + Notes Opacity (bottom row) ---
        self.sl_clip_op = BoxSlider("Clip", 0, 100, 100, "%", icon="clip")
        self.sl_clip_op.setToolTip("Clip opacity — click or drag anywhere to adjust")
        self.sl_clip_op.valueChanged.connect(self._clip_opacity_changed)
        self.sl_notes_op = BoxSlider("Notes", 0, 100, 100, "%", icon="notes")
        self.sl_notes_op.setToolTip("Notes opacity — click or drag anywhere to adjust")
        self.sl_notes_op.valueChanged.connect(self._notes_opacity_changed)
        media_stack = QWidget()
        media_stack_lay = QVBoxLayout(media_stack)
        media_stack_lay.setContentsMargins(0, 0, 0, 0)
        media_stack_lay.setSpacing(1)
        media_stack_lay.addWidget(self.sl_clip_op)
        media_stack_lay.addWidget(self.sl_notes_op)
        bar.addWidget(media_stack)

        bar.addWidget(self._vline())

        # --- [BUTTON] Show / Hide Notes Toggle (Eye icon) ---
        self.btn_hide = self._icon_button("eye", "Show / hide notes", checkable=True, highlight=True)
        self.btn_hide.setChecked(True)
        self.btn_hide.clicked.connect(self._toggle_notes)
        bar.addWidget(self.btn_hide)

        # --- [BUTTON] Clear Frame Annotations (Trash icon) ---
        self.btn_clear = self._icon_button("trash", "Clear drawings on this frame")
        self.btn_clear.clicked.connect(self._clear_frame)
        bar.addWidget(self.btn_clear)

        bar.addStretch(1)

        # --- [BUTTON] Viewport navigation — zoom / pan / reset in one toggle ---
        # Active: middle-drag pans, right-drag zooms; double-click while active
        # resets (right = zoom, middle = pan, left = both). Alt does the same
        # on any canvas without activating the button.
        self.btn_view = self._icon_button(
            "view_nav",
            "View nav — middle-drag pan, right-drag zoom; double-click: right "
            "resets zoom, middle resets pan, left resets both (or hold Alt)",
            checkable=True, highlight=True,
        )
        self.btn_view.toggled.connect(self._nav_mode_changed)
        self.btn_onion = OnionButton(
            value=min(max(int(self._settings.get("onion_opacity", 35)), 5), 100),
            radius=5,  # matches the pen button's corner rounding
        )
        self.btn_onion.setToolTip("Onion skin — click to toggle, drag to set ghost visibility %")
        self.btn_onion.toggled.connect(self._toggle_onion)
        self.btn_onion.opacityChanged.connect(self._onion_opacity_changed)
        self.spin_onion_prev = ValueBox(initial=1, minimum=0, maximum=5)
        self.spin_onion_prev.setToolTip("Ghost this many drawings BEFORE the current one")
        self.spin_onion_prev.valueChanged.connect(self._onion_depth_changed)
        self.spin_onion_next = ValueBox(initial=1, minimum=0, maximum=5)
        self.spin_onion_next.setToolTip("Ghost this many drawings AFTER the current one")
        self.spin_onion_next.valueChanged.connect(self._onion_depth_changed)

        # [− n +]  onion button (existing painted icon)  [− n +]
        cluster = QWidget()
        clay = QHBoxLayout(cluster)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(6)
        clay.addWidget(self.spin_onion_prev)
        clay.addWidget(self.btn_onion)
        clay.addWidget(self.spin_onion_next)
        bar.addWidget(cluster)
        bar.addWidget(self._tool_group(self.btn_view))

        layout.addWidget(wrap)

        # -------------------------------------------------------------------
        # [PLAYBACK BAR] Bottom Playback & Scrubber Controls
        # -------------------------------------------------------------------
        play = QHBoxLayout()
        play.setContentsMargins(10, 0, 10, 10)
        play.setSpacing(8)
        play_wrap = QWidget()
        play_wrap.setLayout(play)
        play_wrap.setStyleSheet(wrap.styleSheet())
        play_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # --- [BUTTON] Previous Drawing (|<) ---
        self.btn_prev_draw = QPushButton()
        self.btn_prev_draw.setFixedSize(46, 34)
        self.btn_prev_draw.setIcon(make_nav_icon("prev", True))
        self.btn_prev_draw.setIconSize(QSize(42, 28))
        self.btn_prev_draw.setToolTip("Previous drawing")

        # --- [BUTTON] Previous Frame (<) ---
        self.btn_prev = QPushButton()
        self.btn_prev.setFixedSize(34, 34)
        self.btn_prev.setIcon(make_nav_icon("prev", False))
        self.btn_prev.setIconSize(QSize(28, 28))
        self.btn_prev.setToolTip("Previous frame")

        # --- [BUTTON] Play / Pause Toggle ---
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(34, 34)
        self.btn_play.setIconSize(QSize(20, 20))
        self.btn_play.setIcon(self._play_icon(False))

        # --- [BUTTON] Next Frame (>) ---
        self.btn_next = QPushButton()
        self.btn_next.setFixedSize(34, 34)
        self.btn_next.setIcon(make_nav_icon("next", False))
        self.btn_next.setIconSize(QSize(28, 28))
        self.btn_next.setToolTip("Next frame")

        # --- [BUTTON] Next Drawing (>|) ---
        self.btn_next_draw = QPushButton()
        self.btn_next_draw.setFixedSize(46, 34)
        self.btn_next_draw.setIcon(make_nav_icon("next", True))
        self.btn_next_draw.setIconSize(QSize(42, 28))
        self.btn_next_draw.setToolTip("Next drawing")

        # --- [BUTTON] Loop Playback Toggle ---
        self.btn_loop = self._icon_button("loop", "Loop playback", checkable=True, highlight=True)
        self.btn_loop.setChecked(True)
        self.btn_loop.clicked.connect(self._toggle_loop)

        # --- [BUTTON] Audio Playback Toggle (Right-click for audio export menu) ---
        self.btn_audio = VolumeButton()
        self.btn_audio.setFixedSize(34, 34)
        self.btn_audio.setCheckable(True)
        self.btn_audio.setIcon(make_tool_icon("audio", True))
        self.btn_audio.setIconSize(QSize(18, 18))
        self.btn_audio.setChecked(True)
        self.btn_audio.clicked.connect(self._toggle_audio)
        self.btn_audio.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_audio.customContextMenuRequested.connect(self._audio_context_menu)
        self.btn_audio.setToolTip(f"Audio on — drag to set volume ({self.audio_volume}%)")
        self.btn_audio.setVolumeLevel(self.audio_volume)
        self.btn_audio.installEventFilter(self)

        # Connect Navigation Actions
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_prev_draw.clicked.connect(self.goto_prev_drawing)
        self.btn_next_draw.clicked.connect(self.goto_next_drawing)
        self.btn_play.clicked.connect(self._toggle_play)

        play.addWidget(self.btn_prev_draw)
        play.addWidget(self.btn_prev)
        play.addWidget(self.btn_play)
        play.addWidget(self.btn_next)
        play.addWidget(self.btn_next_draw)
        play.addWidget(self.btn_loop)
        play.addWidget(self.btn_audio)

        # --- [SLIDER] Timeline Scrubber (TimelineSlider) ---
        self.slider = TimelineSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._scrub)
        play.addWidget(self.slider, 1)

        # --- [LABEL] Timecode / Frame Number Mode Display ---
        self.lbl_time = ClickableLabel("—")
        self.lbl_time.setMinimumWidth(150)
        self.lbl_time.setToolTip("Click to switch frames / time")
        self.lbl_time.clicked.connect(self._toggle_time_mode)
        play.addWidget(self.lbl_time)

        # --- [LABEL] Video Metadata Display (Dimensions & FPS) ---
        self.lbl_meta = QLabel("")
        self.lbl_meta.setMinimumWidth(140)
        self.lbl_meta.setStyleSheet("color: #bbb;")
        play.addWidget(self.lbl_meta)

        layout.addWidget(play_wrap)

    def _act(self, key: str, slot) -> QAction:
        a = QAction(ACTION_LABELS.get(key, key), self)
        a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        a.triggered.connect(lambda _checked=False, s=slot: s())
        self._actions[key] = a
        self.addAction(a)
        return a

    def _load_shortcuts(self) -> None:
        path = config_dir() / "shortcuts.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._shortcuts.update({k: str(v) for k, v in data.items() if k in DEFAULT_SHORTCUTS})
            except Exception:
                pass

    def _save_shortcuts(self) -> None:
        path = config_dir() / "shortcuts.json"
        path.write_text(json.dumps(self._shortcuts, indent=2), encoding="utf-8")

    def _apply_shortcuts(self) -> None:
        for key, action in self._actions.items():
            seq = self._shortcuts.get(key, DEFAULT_SHORTCUTS.get(key, ""))
            action.setShortcut(QKeySequence(seq) if seq else QKeySequence())
            action.setShortcutVisibleInContextMenu(True)
            if seq:
                action.setText(f"{ACTION_LABELS.get(key, key)}")
                action.setToolTip(f"{ACTION_LABELS.get(key, key)}  ({seq})")
            tip = ACTION_DESCRIPTIONS.get(key)
            if tip:
                action.setStatusTip(tip)
        # Link toolbar buttons to their actions: the tooltip gains a
        # "[shortcut]" line and hovering shows what it does in the status bar.
        # BUTTON_ACTION_KEYS (top of file) decides which buttons get this.
        for attr, key in BUTTON_ACTION_KEYS.items():
            wdg = getattr(self, attr, None)
            if wdg is None:
                continue
            sc = self._shortcuts.get(key, DEFAULT_SHORTCUTS.get(key, ""))
            base = wdg.toolTip().split("\n[")[0]
            wdg.setToolTip(f"{base}\n[{sc}]" if sc else base)
            desc = ACTION_DESCRIPTIONS.get(key) or ACTION_LABELS.get(key, key)
            wdg.setStatusTip(f"{desc} [{sc}]" if sc else desc)

    def _build_menus(self) -> None:
        m = self.menuBar()
        file_m = m.addMenu("File")
        file_m.addAction(self._act("open_video", self.open_video))
        file_m.addAction(self._act("open_scene", self.open_scene))
        file_m.addAction(self._act("save_scene", self.save_scene))
        file_m.addSeparator()
        self._recent_open_menu = file_m.addMenu("Recent opened")
        self._recent_saved_menu = file_m.addMenu("Recent saved")
        self._autosave_menu = file_m.addMenu("Autosaves")
        self._rebuild_recent_menus()
        self._rebuild_autosave_menu()
        file_m.addSeparator()
        file_m.addAction(self._act("export", self.export_video))
        file_m.addSeparator()
        file_m.addAction(self._act("quit", self.close))

        edit = m.addMenu("Edit")
        edit.addAction(self._act("undo", self.undo_stroke))
        edit.addAction(self._act("redo", self.redo_stroke))
        edit.addAction(self._act("clear_frame", self._clear_frame))
        edit.addSeparator()

        view = m.addMenu("View")
        view.addAction(self._act("toggle_notes", self._hotkey_hide))
        aot = view.addAction("Always on Top")
        aot.setCheckable(True)
        aot.setChecked(bool(self._settings.get("always_on_top", False)))
        aot.toggled.connect(self._toggle_always_on_top)
        self.act_queue = view.addAction("Shot List")
        self.act_queue.setCheckable(True)
        self.act_queue.setChecked(self.queue_visible)
        self.act_queue.toggled.connect(self._toggle_queue_panel)
        self.act_fov = view.addAction("Frame Counter Overlay")
        self.act_fov.setCheckable(True)
        self.act_fov.setChecked(bool(self._settings.get("frame_overlay", False)))
        self.act_fov.toggled.connect(self._toggle_frame_overlay)

        self._act("pen", self._toggle_tool)  # B toggles pen <-> eraser
        self._act("eraser", lambda: self._set_tool("eraser"))
        self._act("play_pause", self._toggle_play)
        self._act("prev_frame", lambda: self._step(-1))
        self._act("next_frame", lambda: self._step(1))
        self._act("prev_drawing", self.goto_prev_drawing)
        self._act("next_drawing", self.goto_next_drawing)
        self._act("brush_smaller", self._brush_smaller)
        self._act("brush_larger", self._brush_larger)
        self._act("pick_color", self._pick_screen_color)
        self._act("toggle_loop", self._toggle_loop)
        # -- commands that previously had no shortcut (see SHORTCUTS registry) --
        self._act("time_mode", self._toggle_time_mode)
        self._act("zoom_mode", self._toggle_view_btn)
        self._act("pan_mode", self._toggle_view_btn)
        self._act("reset_view", self.canvas._reset_view)
        self._act("onion", self._hotkey_onion)
        self._act("show_thumbs", self._hotkey_thumbs)
        # register manually-created menu actions so they receive shortcuts too
        self._actions["queue_panel"] = self.act_queue
        self._actions["frame_overlay"] = self.act_fov
        settings_a = self._actions.get("settings")
        if settings_a is not None:
            edit.addAction(settings_a)
        else:
            s_a = QAction("Settings…", self)
            s_a.triggered.connect(self._edit_settings)
            edit.addAction(s_a)
            self._actions["settings"] = s_a
        self._apply_shortcuts()

    def _hotkey_hide(self) -> None:
        self.btn_hide.setChecked(not self.btn_hide.isChecked())
        self._toggle_notes()

    def _toggle_always_on_top(self, on: bool) -> None:
        """Keeps the InkIt window above all other windows."""
        self._settings["always_on_top"] = bool(on)
        self._schedule_save()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()

    def _set_tool(self, name: str) -> None:
        self.canvas.tool = name
        if hasattr(self, "btn_tool"):
            self.btn_tool.setTool(name)
            self.btn_tool.setValue(int(self._tool_sizes.get(name, 6)))
        self.canvas.brush = float(self._tool_sizes.get(name, 6))
        self.canvas._apply_brush_cursor()
        if name == "pen":
            self.statusBar().showMessage("Pen — mouse or Wacom (pressure). Flip stylus to erase.")
        else:
            self.statusBar().showMessage("Eraser")
        if not self._loading_settings:
            self._settings["tool"] = name
            self._schedule_save()

    def _toggle_tool(self) -> None:
        self._set_tool("eraser" if self.canvas.tool == "pen" else "pen")

    def _toggle_view_btn(self) -> None:
        """Z / X shortcuts toggle the combined View nav button."""
        self.btn_view.toggle()

    def _nav_mode_changed(self, on: bool) -> None:
        """Syncs the View nav toolbar toggle with the canvas gesture mode."""
        self.canvas.set_nav_mode(bool(on))

    def _hotkey_onion(self) -> None:
        """Shortcut wrapper around the onion-skin pill button."""
        self.btn_onion.setOn(not self.btn_onion.on)

    def _hotkey_thumbs(self) -> None:
        """Shortcut wrapper around the thumbnails toggle."""
        self.btn_q_thumbs.setChecked(not self.btn_q_thumbs.isChecked())

    def _on_tool_size(self, v: int) -> None:
        """Size changed for the active tool (drag/wheel/keys on the tool button)."""
        self._tool_sizes[self.canvas.tool] = int(v)
        self.canvas.brush = float(v)
        self.canvas._apply_brush_cursor()
        if not self._loading_settings:
            self._settings["brush"] = int(self._tool_sizes["pen"])
            self._settings["brush_eraser"] = int(self._tool_sizes["eraser"])
            self._schedule_save()

    def _apply_color(self, c: QColor) -> None:
        hexcol = c.name(QColor.NameFormat.HexRgb)
        self.canvas.color = c
        self.btn_color.setStyleSheet(f"background: {hexcol}; border: 1px solid #888; border-radius: 4px;")
        self._settings["color"] = hexcol
        if not self._loading_settings:
            self._write_app_settings()
        else:
            self._schedule_save()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self.canvas.color, self, "Pen color")
        if c.isValid():
            self._apply_color(c)

    def _pick_screen_color(self) -> None:
        """Eyedropper: hover anywhere on screen for a live preview, click to
        assign the color under the cursor to the pen (Esc / right-click cancels)."""
        try:
            picker = ScreenColorPicker()
        except Exception:
            self._pick_color()  # fallback to the dialog if screen grab fails
            return
        self._screen_picker = picker  # keep a reference while it is open
        picker.picked.connect(self._apply_color)
        picker.destroyed.connect(self._end_screen_pick)
        picker.show()
        picker.activateWindow()
        picker.raise_()

    def _end_screen_pick(self, *_):
        """Picker closed: drop the reference and give focus back to InkIt."""
        self._screen_picker = None
        self.activateWindow()

    def _hardness_changed(self, v: int) -> None:
        self.canvas.hardness = min(max(int(v), 0), 100) / 100.0
        self._settings["hardness"] = int(v)
        self._schedule_save()

    def _brush_smaller(self) -> None:
        self.btn_tool.setValue(self.btn_tool.value() - 1)

    def _brush_larger(self) -> None:
        self.btn_tool.setValue(self.btn_tool.value() + 1)

    def _refresh_marks(self) -> None:
        self.slider.set_marks(self.project.annotated_frames())

    def goto_prev_drawing(self) -> None:
        frames = self.project.annotated_frames()
        if not frames:
            return
        cur = self.canvas.current_frame
        prevs = [f for f in frames if f < cur]
        self._show_frame(prevs[-1] if prevs else frames[-1])

    def goto_next_drawing(self) -> None:
        frames = self.project.annotated_frames()
        if not frames:
            return
        cur = self.canvas.current_frame
        nexts = [f for f in frames if f > cur]
        self._show_frame(nexts[0] if nexts else frames[0])

    def _refresh_color_swatches(self) -> None:
        """4 FIXED color slots — order never changes; double-click assigns a slot."""
        cols = [str(c) for c in (self._settings.get("swatch_colors") or [])][:4]
        while len(cols) < len(SWATCH_COLORS_DEFAULT):
            cols.append(SWATCH_COLORS_DEFAULT[len(cols)])
        self._settings["swatch_colors"] = cols
        for i, sw in enumerate(getattr(self, "_pen_swatches", [])):
            col = cols[i]
            sw.setStyleSheet(
                f"QPushButton {{ background:{col}; border:2px solid #555555;"
                " border-radius:12px; }"
                "QPushButton:hover { border-color:#dddddd; }"
            )
            try:
                sw.clicked.disconnect()
            except Exception:
                pass
            sw.clicked.connect(lambda _=False, cc=col: self._apply_color(QColor(cc)))

    def _assign_swatch_color(self, idx: int) -> None:
        """Double-click on a fixed slot: pick a color and store it in that slot."""
        cols = [str(c) for c in (self._settings.get("swatch_colors") or [])][:4]
        while len(cols) < len(SWATCH_COLORS_DEFAULT):
            cols.append(SWATCH_COLORS_DEFAULT[len(cols)])
        cur = QColor(cols[idx]) if QColor(cols[idx]).isValid() else QColor(SWATCH_COLORS_DEFAULT[idx])
        col = QColorDialog.getColor(cur, self, f"Assign color to slot {idx + 1}")
        if col.isValid():
            cols[idx] = col.name(QColor.NameFormat.HexRgb)
            self._settings["swatch_colors"] = cols
            self._schedule_save()
            self._refresh_color_swatches()

    def _pen_opacity_changed(self, v: int) -> None:
        self.canvas.pen_opacity = v / 100.0
        self._settings["pen_opacity"] = int(v)
        self._schedule_save()

    def _clip_opacity_changed(self, v: int) -> None:
        self.canvas.clip_opacity = v / 100.0
        self.canvas.update()
        self._settings["clip_opacity"] = int(v)
        self._schedule_save()

    def _notes_opacity_changed(self, v: int) -> None:
        self.canvas.notes_opacity = v / 100.0
        self.canvas.update()
        self._settings["notes_opacity"] = int(v)
        self._schedule_save()

    def _toggle_notes(self) -> None:
        vis = self.btn_hide.isChecked()
        self.canvas.notes_visible = vis
        self.btn_hide.setIcon(make_tool_icon("eye", vis))
        self.btn_hide.setToolTip("Hide notes" if vis else "Show notes")
        self.canvas.update()
        self._settings["notes_visible"] = vis
        self._schedule_save()

    def _clear_frame(self) -> None:
        f = self.canvas.current_frame
        strokes = self.project.strokes.get(f) or []
        if strokes:
            # One entry = whole-frame snapshot (list), distinguishable from undone Strokes
            self._redo_stack[f] = [list(strokes)]
        self.project.strokes[f] = []
        self.canvas.update()
        self._refresh_marks()
        self._autosave_timer.start(250)

    def undo_stroke(self) -> None:
        strokes = self.project.strokes_at(self.canvas.current_frame)
        if strokes:
            self._redo_stack.setdefault(self.canvas.current_frame, []).append(strokes.pop())
            self.canvas.update()
            self._refresh_marks()
            self._autosave_timer.start(250)

    def redo_stroke(self) -> None:
        """Restores the last undone stroke (or whole cleared frame) on this frame."""
        stack = self._redo_stack.get(self.canvas.current_frame)
        if not stack:
            return
        item = stack.pop()
        if isinstance(item, list):  # A cleared-frame snapshot
            self.project.strokes[self.canvas.current_frame] = list(item)
        else:  # A single undone Stroke
            self.project.strokes_at(self.canvas.current_frame).append(item)
        self.canvas.update()
        self._refresh_marks()
        self._autosave_timer.start(250)

    def _invalidate_redo(self) -> None:
        """New stroke invalidates redo history for that frame."""
        self._redo_stack.pop(self.canvas.current_frame, None)

    def _bind_new_project(self, path: str, count: int, fps: float, w: int, h: int, keep_strokes: dict | None = None) -> None:
        # Stash the outgoing media's drawings so switching shots never loses them
        old_path = getattr(self.project, "path", "")
        if old_path and not (bool(getattr(self.canvas, "is_board", False)) and not old_path):
            if any(s.points for lst in self.project.strokes.values() for s in lst):
                self._session_notes[self._notes_key(old_path)] = dict(self.project.strokes)
        if keep_strokes is None:
            self.project = Project()
        self._redo_stack = {}
        self.project.path = path
        self.project.fps = fps
        self.project.frame_count = max(count, 1)
        self.project.width = w
        self.project.height = h
        if keep_strokes is not None:
            self.project.strokes = keep_strokes
        self.canvas.project = self.project
        still = bool(self._is_still)
        board = bool(getattr(self.canvas, "is_board", False))
        self.slider.blockSignals(True)
        self.slider.setEnabled((not still) or board)
        self.slider.setRange(0, max(self.project.frame_count - 1, 0))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        board = board or bool(getattr(self.canvas, "is_board", False))
        self.btn_play.setEnabled(
            (not still and not board) or (board and self.project.frame_count > 1)
        )
        self.playing = False
        self.play_timer.stop()
        self.btn_play.setIcon(self._play_icon(False))
        if still or board:
            if getattr(self, "_player", None) is not None:
                try:
                    self._player.stop()
                except Exception:
                    pass
        else:
            self._load_clip_audio(path)
        self._show_frame(0)
        self._refresh_marks()
        if still:
            self.lbl_meta.setText(f"{w}×{h}   image")
        else:
            fps_txt = f"{fps:.3f}".rstrip("0").rstrip(".")
            self.lbl_meta.setText(f"{w}×{h}   {fps_txt} fps")
        self.setWindowTitle(f"InkIt — {Path(path).name}")

    def _notes_key(self, p: str) -> str:
        try:
            return str(Path(p).resolve()).lower()
        except Exception:
            return str(p).lower()

    def _open_start_dir(self) -> str:
        d = self._settings.get("last_open_dir") or ""
        return d if d and Path(d).is_dir() else ""

    def _save_start_dir(self) -> str:
        d = self._settings.get("last_save_dir") or ""
        if d and Path(d).is_dir() and not self._is_autosave_dir(d):
            return d
        return ""

    def get_autosave_dir(self) -> Path:
        custom = (self._settings.get("autosave_dir") or "").strip()
        if custom:
            p = Path(custom)
            p.mkdir(parents=True, exist_ok=True)
            return p
        return default_autosave_dir()

    def _is_autosave_dir(self, path: str | Path) -> bool:
        try:
            return Path(path).resolve() == self.get_autosave_dir().resolve()
        except Exception:
            return False

    def _edit_settings(self) -> None:
        cur = {
            "mode": self.canvas.cursor_mode,
            "custom": self.canvas.cursor_custom,
            "color": self.canvas.cursor_color.name(QColor.NameFormat.HexRgb),
            "width": self.canvas.cursor_width,
            "dot": self.canvas.cursor_dot,
            "antialias": bool(self.canvas.antialias),
            "pressure_curve": [list(q) for q in getattr(self.canvas, "pressure_curve", DEFAULT_PRESSURE_CURVE)],
            "onion_prev": self.canvas.onion_prev.name(QColor.NameFormat.HexRgb),
            "onion_next": self.canvas.onion_next.name(QColor.NameFormat.HexRgb),
        }
        dlg = SettingsDialog(
            self.get_autosave_dir(), default_autosave_dir(), cur, dict(self._shortcuts), self,
            autodelete=bool(self._settings.get("autosave_autodelete", True)),
            max_days=int(self._settings.get("autosave_max_days", 90)),
            onion_opacity=int(self._settings.get("onion_opacity", 35)),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            if dlg.deleted:
                self._rebuild_autosave_menu()
            return
        sm = dlg.shortcuts_result()
        if sm != self._shortcuts:
            self._shortcuts = sm
            self._save_shortcuts()
            self._apply_shortcuts()
        chosen = dlg.chosen_dir()
        default = str(default_autosave_dir())
        self._settings["autosave_dir"] = "" if chosen.replace("\\", "/").lower() == default.replace("\\", "/").lower() else chosen
        ad_on, ad_days = dlg.autodelete_settings()
        self._settings["autosave_autodelete"] = ad_on
        self._settings["autosave_max_days"] = ad_days
        cs = dlg.cursor_settings()
        aa = dlg.antialias_enabled()
        curve = dlg.pressure_curve()
        oprev, onext = dlg.onion_colors()
        self.canvas.set_cursor_config(cs["mode"], cs["custom"], QColor(cs["color"]), cs["width"], cs["dot"])
        self.canvas.antialias = aa
        self.canvas.pressure_curve = curve
        self.canvas.onion_prev = QColor(oprev)
        self.canvas.onion_next = QColor(onext)
        self.btn_onion.setValue(dlg.onion_visibility())  # syncs canvas + settings
        self._schedule_save()
        self.canvas.update()
        self._settings.update({
            "cursor_mode": cs["mode"],
            "cursor_custom": cs["custom"],
            "cursor_color": cs["color"],
            "cursor_width": cs["width"],
            "cursor_dot": cs["dot"],
            "pen_antialias": aa,
            "pen_pressure_curve": curve,
            "onion_prev": oprev,
            "onion_next": onext,
        })
        self._write_app_settings()
        self._rebuild_autosave_menu()
        self._cleanup_autosaves()
        if dlg.deleted:
            self.statusBar().showMessage("Autosave notes deleted", 3000)

    def _cleanup_autosaves(self) -> None:
        """Settings ▸ Autosave: delete autosave files older than the configured age."""
        if not bool(self._settings.get("autosave_autodelete", True)):
            return
        try:
            max_days = max(1, min(365, int(self._settings.get("autosave_max_days", 90))))
            cutoff = time.time() - max_days * 86400.0
            removed = 0
            for f in self.get_autosave_dir().glob("*.inkit"):
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
            if removed and hasattr(self, "_autosave_menu"):
                self._rebuild_autosave_menu()
        except (OSError, ValueError, TypeError):
            return

    def open_autosave(self) -> None:
        folder = str(self.get_autosave_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, "Open autosave", folder, "InkIt autosave (*_autosave.inkit);;InkIt (*.inkit);;JSON (*.json)"
        )
        if path:
            self._open_scene_path(path)

    def _rebuild_autosave_menu(self) -> None:
        if not hasattr(self, "_autosave_menu"):
            return
        self._autosave_menu.clear()
        open_folder = QAction("Open autosave folder…", self)
        open_folder.triggered.connect(self.open_autosave)
        self._autosave_menu.addAction(open_folder)
        self._autosave_menu.addSeparator()
        folder = self.get_autosave_dir()
        files = sorted(
            list(folder.glob("*_autosave.inkit")) + list(folder.glob("*_autosave*.json")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            empty = QAction("(empty)", self)
            empty.setEnabled(False)
            self._autosave_menu.addAction(empty)
            return
        for f in files[:20]:
            act = QAction(f.name, self)
            act.setToolTip(str(f))
            act.triggered.connect(lambda _=False, p=str(f): self._open_scene_path(p))
            self._autosave_menu.addAction(act)

    # -----------------------------------------------------------------------
    # Shot Queue (left panel)
    # -----------------------------------------------------------------------

    def _build_queue_panel(self) -> QWidget:
        """Left-side dock panel listing staged clips/scenes for quick switching."""
        panel = QWidget()
        panel.setMinimumWidth(140)

        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(6)
        self._q_vlayout = v

        head = QHBoxLayout()
        head.setSpacing(4)
        head.setContentsMargins(0, 0, 0, 0)
        self.btn_q_title = QPushButton("Shots")
        self.btn_q_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_q_title.setToolTip("Minimize shot list")
        self.btn_q_title.clicked.connect(
            lambda: self._set_queue_minimized(not self.queue_minimized)
        )
        head.addWidget(self.btn_q_title)
        self.lbl_queue_count = QLabel("")
        head.addWidget(self.lbl_queue_count)
        head.addStretch(1)

        self.btn_q_add = self._queue_button("plus", "Add shots…")
        self.btn_q_add.clicked.connect(self._queue_add_dialog)
        head.addWidget(self.btn_q_add)
        self.btn_q_open = self._queue_button("open", "Open shot list…")
        self.btn_q_open.clicked.connect(self._queue_open_list)
        head.addWidget(self.btn_q_open)
        self.btn_q_save = self._queue_button("save", "Save shot list…")
        self.btn_q_save.clicked.connect(self._queue_save_list)
        head.addWidget(self.btn_q_save)
        self.btn_q_del = self._queue_button("trash", "Delete selected")
        self.btn_q_del.clicked.connect(self._queue_delete_selected)
        head.addWidget(self.btn_q_del)
        self.btn_q_clear = self._queue_button("x", "Empty the shot list")
        self.btn_q_clear.clicked.connect(self._queue_clear)
        head.addWidget(self.btn_q_clear)
        self.btn_q_thumbs = self._queue_button("grid", "Show / hide thumbnails")
        self.btn_q_thumbs.setCheckable(True)
        self.btn_q_thumbs.setChecked(bool(self._settings.get("queue_thumbs", True)))
        self.btn_q_thumbs.toggled.connect(self._toggle_queue_thumbs)
        head.addWidget(self.btn_q_thumbs)

        self._q_head = QWidget()
        self._q_head.setLayout(head)
        v.addWidget(self._q_head, 0, Qt.AlignmentFlag.AlignTop)

        self.queue_list = QueueList()
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_list.deleteRequested.connect(self._queue_delete_selected)
        self.queue_list.rowsMoved.connect(self._queue_reordered)
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._show_queue_menu)
        self.queue_list.itemClicked.connect(self._on_queue_clicked)
        self.queue_list.itemDoubleClicked.connect(self._on_queue_clicked)

        def restyle_panel() -> None:
            c = self._colors()
            panel.setStyleSheet(f"QWidget {{ background: {c['panel']}; }}")
            self.btn_q_title.setStyleSheet(
                f"QPushButton {{ background: {c['card']}; border: 1px solid {c['border']};"
                f" border-radius: 4px; padding: 2px 10px; color: {c['subtext']}; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {c['card_hover']}; color: {c['text']}; }}"
            )
            self.lbl_queue_count.setStyleSheet(
                f"color:{c['muted']}; background:transparent;"
            )
            self.queue_list.setStyleSheet(
                f"QListWidget {{ background: {c['list_bg']}; border: 1px solid {c['border']};"
                f" border-radius: 4px; color: {c['text']}; outline: none; font-size: 12px; }}"
                f"QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {c['wrap']}; }}"
                f"QListWidget::item:selected {{ background: {c['accent']}; color: white; }}"
                f"QListWidget::item:hover:!selected {{ background: {c['card_hover']}; }}"
            )

        restyle_panel()
        self._dyn_styles.append(restyle_panel)
        v.addWidget(self.queue_list, 1)
        return panel

    def _queue_button(self, kind: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(26, 26)
        if kind in ("prev", "next"):
            btn.setIcon(make_nav_icon(kind, False))
            btn.setIconSize(QSize(22, 14))
        else:
            btn.setIcon(make_tool_icon(kind, True))
            btn.setIconSize(QSize(14, 14))
        btn.setToolTip(tooltip)

        def restyle() -> None:
            c = self._colors()
            btn.setStyleSheet(
                f"QPushButton {{ background: {c['card']}; border: 1px solid {c['border']};"
                f" border-radius: 4px; padding: 0; }}"
                f"QPushButton:hover {{ background: {c['card_hover']}; }}"
                f"QPushButton:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}"
                f"QPushButton:disabled {{ background: transparent; border-color: {c['border']}; }}"
            )

        restyle()
        self._dyn_styles.append(restyle)
        return btn

    def _set_queue(self, paths: list[str]) -> None:
        """Adds `paths` to the shot list (deduped); opens the first new one if nothing is open."""
        fresh = [p for p in paths if Path(p).suffix.lower() in (".mp4", ".mov", ".inkit", ".json") or _is_image_path(p)]
        if not fresh:
            return
        if self.queue_paths:
            known = {str(Path(p).resolve()).lower() for p in self.queue_paths}
            new = [p for p in fresh if str(Path(p).resolve()).lower() not in known]
            self.queue_paths.extend(new)
            self._queue_refresh()
            if self.queue_index < 0 and new:
                self._queue_open(self.queue_paths.index(new[0]))
            elif new:
                self.statusBar().showMessage(f"Added {len(new)} shot(s) to the list")
            else:
                self.statusBar().showMessage("Already in the list")
            return
        self.queue_paths = fresh
        self.queue_index = -1
        self._queue_refresh()
        self._queue_open(0)

    def _replace_queue(self, paths: list[str]) -> None:
        """Swaps the whole list (used by Open list) and opens the first entry."""
        fresh = [p for p in paths if Path(p).suffix.lower() in (".mp4", ".mov", ".inkit", ".json") or _is_image_path(p)]
        self.queue_paths = fresh
        self.queue_index = -1
        self._queue_refresh()
        if fresh:
            self._queue_open(0)

    def _queue_refresh(self) -> None:
        thumbs = bool(self._settings.get("queue_thumbs", True))
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        self.queue_list.setIconSize(QSize(72, 40) if thumbs else QSize(1, 1))
        for i, p in enumerate(self.queue_paths):
            item = QListWidgetItem(f"{i + 1}.  {Path(p).name}")
            if thumbs:
                item.setIcon(self._make_thumb(p))
                item.setSizeHint(QSize(0, 48))
            else:
                item.setSizeHint(QSize(0, 26))
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.queue_list.addItem(item)
        self.queue_list.blockSignals(False)
        if 0 <= self.queue_index < len(self.queue_paths):
            self.queue_list.setCurrentRow(self.queue_index)
        self.lbl_queue_count.setText(f"{len(self.queue_paths)}")

    def _queue_reordered(self) -> None:
        """Syncs queue_paths after a drag & drop move; the open shot stays open."""
        order = [
            str(self.queue_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.queue_list.count())
        ]
        if not order or len(order) != len(set(order)):
            self._queue_refresh()  # pathological drop state — rebuild canonical list
            return
        cur = (
            self.queue_paths[self.queue_index]
            if 0 <= self.queue_index < len(self.queue_paths)
            else None
        )
        self.queue_paths = order
        if cur is not None and cur in self.queue_paths:
            self.queue_index = self.queue_paths.index(cur)
        for i in range(self.queue_list.count()):
            it = self.queue_list.item(i)
            it.setText(f"{i + 1}.  {Path(str(it.data(Qt.ItemDataRole.UserRole))).name}")
        if 0 <= self.queue_index < len(self.queue_paths):
            self.queue_list.setCurrentRow(self.queue_index)
        self.statusBar().showMessage("Shot list reordered")

    def _toggle_queue_thumbs(self, on: bool) -> None:
        self._settings["queue_thumbs"] = bool(on)
        self._schedule_save()
        self._queue_refresh()

    def _queue_split_moved(self, pos: int, _index: int) -> None:
        self._settings["queue_width"] = int(pos)
        self._schedule_save()

    def _make_thumb(self, path: str) -> QIcon:
        """Small preview icon for a shot-list entry: video frame grab, image, or placeholder."""
        try:
            key = f"{path}|{int(Path(path).stat().st_mtime)}"
        except OSError:
            key = path
        if key in self._thumbs:
            return self._thumbs[key]
        pm = QPixmap()
        ext = Path(path).suffix.lower()
        try:
            if ext in (".mp4", ".mov") and cv2 is not None:
                cap = cv2.VideoCapture(path)
                n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * 0.1)))
                ok, fr = cap.read()
                cap.release()
                if ok and fr is not None:
                    h, w = fr.shape[:2]
                    rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
                    pm = QPixmap.fromImage(
                        QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
                    )
            elif _is_image_path(path):
                pm = QPixmap(path)
        except Exception:
            pm = QPixmap()
        if pm.isNull():
            # Placeholder tile for scenes (.inkit/.json) and unreadable files
            pm = QPixmap(96, 54)
            pm.fill(QColor("#232329"))
            pp = QPainter(pm)
            pp.setPen(QColor("#8a8a95"))
            fo = pp.font()
            fo.setBold(True)
            fo.setPointSize(16)
            pp.setFont(fo)
            pp.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "S")
            pp.end()
        pm = pm.scaled(
            96, 54,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        ic = QIcon(pm)
        self._thumbs[key] = ic
        return ic

    def _queue_open(self, index: int) -> None:
        """Opens queue entry `index`; selection only moves on success."""
        if not (0 <= index < len(self.queue_paths)):
            return
        path = self.queue_paths[index]
        ext = Path(path).suffix.lower()
        if ext in (".mp4", ".mov"):
            ok = self._open_video_path(path)
        elif _is_image_path(path):
            ok = self._open_image_path(path)
        else:
            self._open_scene_path(path)
            ok = self.project.path is not None
        if not ok:
            return
        self.queue_index = index
        self.queue_list.setCurrentRow(index)
        self.statusBar().showMessage(
            f"Shot {index + 1} / {len(self.queue_paths)}: {Path(path).name}"
        )

    def _close_clip(self) -> None:
        """Releases any open clip/board/still and restores the empty
        'Drag your videos/images here' drop state."""
        # Stash the outgoing clip's drawings so re-dropping it restores them,
        # exactly like a normal shot switch.
        old_path = getattr(self.project, "path", "")
        if old_path and any(s.points for lst in self.project.strokes.values() for s in lst):
            self._session_notes[self._notes_key(old_path)] = dict(self.project.strokes)
        self.playing = False
        self.play_timer.stop()
        if getattr(self, "_player", None) is not None:
            try:
                self._player.stop()
                self._player.setSource(QUrl())
            except Exception:
                pass
        self.reader.close()
        self.project = Project()
        self.canvas.project = self.project
        self.canvas.is_board = False
        self.canvas.set_frame_image(None)
        self.canvas.current_frame = 0
        self.canvas.update()
        self._is_still = False
        self._still_bgr = None
        self._redo_stack = {}
        self.slider.blockSignals(True)
        self.slider.setEnabled(False)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.btn_play.setEnabled(False)
        self.btn_play.setIcon(self._play_icon(False))
        self._update_time_label()

    def _queue_clear(self) -> None:
        self.queue_paths = []
        self.queue_index = -1
        self._queue_refresh()
        # An empty shot list must mean an empty viewport: stop playback and
        # release the open clip so the placeholder shows again.
        self._close_clip()

    def _queue_delete_selected(self) -> None:
        """Removes selected entries; if the open shot was removed, selects the next one."""
        rows = sorted({self.queue_list.row(i) for i in self.queue_list.selectedItems()})
        if not rows:
            return
        cur = self.queue_index
        for r in reversed(rows):
            del self.queue_paths[r]
        if cur in rows:
            nxt = min(rows[0], len(self.queue_paths) - 1)
            self._queue_refresh()
            if 0 <= nxt < len(self.queue_paths):
                self._queue_open(nxt)
            else:
                self._close_clip()
                self.statusBar().showMessage(f"Removed {len(rows)} shot(s) from the list")
            return
        self.queue_index = cur - sum(1 for r in rows if r < cur)
        self._queue_refresh()
        self.statusBar().showMessage(f"Removed {len(rows)} shot(s) from the list")

    def _build_queue_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Add…", self._queue_add_dialog)
        act_del = m.addAction("Delete selected", self._queue_delete_selected)
        act_del.setEnabled(bool(self.queue_list.selectedItems()))
        m.addAction("Clear list", self._queue_clear)
        m.addSeparator()
        m.addAction("Open list…", self._queue_open_list)
        m.addAction("Save list…", self._queue_save_list)
        return m

    def _show_queue_menu(self, pos) -> None:
        self._build_queue_menu().exec(self.queue_list.mapToGlobal(pos))

    def _queue_add_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add shot(s)", self._open_start_dir(),
            "Shots (*.mp4 *.mov *.MP4 *.MOV *.inkit *.json *.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)",
        )
        if paths:
            self._set_queue(paths)

    def _save_list_file(self, path: str) -> None:
        Path(path).write_text("\n".join(self.queue_paths), encoding="utf-8")
        self._settings["last_list_dir"] = str(Path(path).parent)
        self.statusBar().showMessage(f"Shot list saved: {path}")

    def _load_list_file(self, path: str) -> None:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except Exception as e:
            QMessageBox.critical(self, "Open shot list", str(e))
            return
        paths = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        valid = [p for p in paths if Path(p).is_file()]
        if not valid:
            QMessageBox.warning(self, "Open shot list", "No existing files found in this list.")
            return
        self._settings["last_list_dir"] = str(Path(path).parent)
        missing = len(paths) - len(valid)
        self._replace_queue(valid)
        if missing:
            self.statusBar().showMessage(f"Loaded {len(valid)} shots ({missing} missing skipped)")

    def _queue_save_list(self) -> None:
        if not self.queue_paths:
            QMessageBox.information(self, "Save shot list", "The list is empty.")
            return
        start = self._settings.get("last_list_dir") or self._open_start_dir() or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save shot list", str(Path(start) / "shots.txt"),
            "Shot list (*.txt);;All files (*.*)",
        )
        if path:
            self._save_list_file(path)

    def _queue_open_list(self) -> None:
        start = self._settings.get("last_list_dir") or self._open_start_dir() or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open shot list", start, "Shot list (*.txt);;All files (*.*)",
        )
        if path:
            self._load_list_file(path)

    def _apply_queue_layout(self) -> None:
        """Expanded panel, vertical 'Shots' tab when minimized, or fully removed."""
        if not self.queue_visible:
            self.queue_panel.setVisible(False)
            self.splitter.setHandleWidth(0)
            return
        expanded = not self.queue_minimized
        self.queue_panel.setVisible(True)
        self.splitter.setHandleWidth(5 if expanded else 0)
        if expanded:
            # Splitter owns the width now — only clamp it
            self.queue_panel.setMinimumWidth(140)
            self.queue_panel.setMaximumWidth(16777215)
            self._q_vlayout.setContentsMargins(6, 8, 6, 8)
            self.btn_q_title.setText("Shots")
            self.btn_q_title.setIcon(QIcon())
            self.btn_q_title.setFixedSize(QSize(64, 26))
            self.btn_q_title.setToolTip("Minimize shot list")
        else:
            self.queue_panel.setFixedWidth(32)
            self._q_vlayout.setContentsMargins(3, 6, 3, 6)
            self.btn_q_title.setText("")
            self.btn_q_title.setIcon(
                make_vertical_text_icon("Shots", "#cccccc", self.btn_q_title.font())
            )
            self.btn_q_title.setIconSize(QSize(18, 56))
            # Same box as expanded, just flipped 90 degrees
            self.btn_q_title.setFixedSize(QSize(26, 64))
            self.btn_q_title.setToolTip("Expand shot list")
        for wdg in (
            self.lbl_queue_count, self.btn_q_add, self.btn_q_open,
            self.btn_q_save, self.btn_q_del, self.btn_q_clear,
            self.btn_q_thumbs, self.queue_list,
        ):
            wdg.setVisible(expanded)

    def _set_queue_minimized(self, on: bool) -> None:
        self.queue_minimized = bool(on)
        self._apply_queue_layout()
        self._settings["queue_minimized"] = self.queue_minimized
        self._schedule_save()

    def _set_queue_visible(self, on: bool) -> None:
        self.queue_visible = bool(on)
        self._apply_queue_layout()
        act = getattr(self, "act_queue", None)
        if act is not None and act.isChecked() != self.queue_visible:
            act.blockSignals(True)
            act.setChecked(self.queue_visible)
            act.blockSignals(False)
        self._settings["queue_visible"] = self.queue_visible
        self._schedule_save()

    def _toggle_queue_panel(self, on: bool) -> None:
        self._set_queue_visible(on)

    def _on_queue_clicked(self, item: QListWidgetItem) -> None:
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            return  # Windows-style range/toggle selection; don't switch clips
        idx = self.queue_list.row(item)
        if idx != self.queue_index:
            self._queue_open(idx)

    # -----------------------------------------------------------------------
    # File open handlers
    # -----------------------------------------------------------------------

    def open_video(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open media", self._open_start_dir(),
            "Media (*.mp4 *.mov *.MP4 *.MOV *.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)"
        )
        if not paths:
            return
        self._set_queue(paths)

    def _open_video_path(self, path: str, strokes: dict | None = None) -> bool:
        try:
            count, fps, w, h = self.reader.open(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return False
        self._is_still = False
        self._still_bgr = None
        self.canvas.is_board = False
        if strokes is None:
            strokes = self._session_notes.pop(self._notes_key(path), None)
        else:
            self._session_notes.pop(self._notes_key(path), None)
        self._bind_new_project(path, count, fps, w, h, keep_strokes=strokes)
        self._settings["last_open_dir"] = str(Path(path).parent)
        self._push_recent("recent_open", path)
        return True

    def _on_stroke_finished_autosave(self) -> None:
        """First drawing on a fresh board immediately creates its autosave scene."""
        if not getattr(self.canvas, "is_board", False):
            return
        if getattr(self.project, "scene_path", None) or self.project.user_saved:
            return
        if self.project.annotated_frames():
            self._autosave_notes()

    def _colors(self) -> dict[str, str]:
        return THEMES.get(self._theme_name, THEMES["dark"])

    def _apply_theme(self) -> None:
        """Re-applies every registered stylesheet + painted chrome for the active theme."""
        c = self._colors()
        self.canvas.theme = dict(c)
        qss = (
            f"QMainWindow {{ background: {c['bg']}; }}"
            f"QWidget {{ color: {c['text']}; }}"
            f"QMenuBar {{ background: {c['wrap']}; color: {c['text'] }; }}"
            f"QMenuBar::item:selected {{ background: {c['card_hover']}; }}"
            f"QMenu {{ background: {c['wrap']}; color: {c['text']}; border: 1px solid {c['border']}; }}"
            f"QMenu::item:selected {{ background: {c['accent']}; color: white; }}"
            f"QStatusBar {{ background: {c['wrap']}; color: {c['subtext']}; }}"
            f"QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; }}"
        )
        self.setStyleSheet(qss)
        for fn in list(self._dyn_styles):
            try:
                fn()
            except Exception:
                pass
        for ob in self.findChildren(OnionButton):
            ob.update()
        self.canvas.update()

    def _bind_board_data(self, proj: Project, scene_path: str) -> bool:
        """Binds a saved media-less board scene (strokes over a blank page)."""
        if not proj.frame_count:
            return False
        self._is_still = False
        self._still_bgr = None
        self.canvas.is_board = True
        self._bind_new_project(
            "", max(proj.frame_count, 1), float(proj.fps or 24.0),
            int(proj.width or 1920), int(proj.height or 1080), keep_strokes=proj.strokes,
        )
        self.project.scene_path = scene_path
        self.project.user_saved = not is_autosave_name(scene_path)
        self._push_recent("recent_open", scene_path)
        self.setWindowTitle(f"InkIt — Drawing board  [{Path(scene_path).name}]")
        return True

    def _onion_depth_changed(self) -> None:
        self.canvas.onion_depth_prev = int(self.spin_onion_prev.value())
        self.canvas.onion_depth_next = int(self.spin_onion_next.value())
        self._settings["onion_depth_prev"] = self.canvas.onion_depth_prev
        self._settings["onion_depth_next"] = self.canvas.onion_depth_next
        if self.canvas.onion_enabled:
            self.canvas.update()

    def _onion_opacity_changed(self, v: int) -> None:
        self.canvas.onion_opacity = max(int(v), 5) / 100.0
        self._settings["onion_opacity"] = int(v)
        if self.canvas.onion_enabled:
            self.canvas.update()

    def _toggle_onion(self, on: bool) -> None:
        self.canvas.onion_enabled = bool(on)
        self._settings["onion"] = bool(on)
        if hasattr(self, "btn_onion"):
            self.btn_onion.setOn(bool(on))
        self.canvas.update()

    def _open_image_path(self, path: str, strokes: dict | None = None) -> bool:
        """Opens a still image (PNG/JPG/BMP/WEBP) as a single-frame drawable canvas."""
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            QMessageBox.critical(self, "Open failed", f"Could not read image:\n{path}")
            return False
        h, w = bgr.shape[:2]
        self._is_still = True
        self._still_bgr = bgr
        self.canvas.is_board = False
        if strokes is None:
            strokes = self._session_notes.pop(self._notes_key(path), None)
        else:
            self._session_notes.pop(self._notes_key(path), None)
        self._bind_new_project(path, 1, 24.0, w, h, keep_strokes=strokes)
        self._settings["last_open_dir"] = str(Path(path).parent)
        self._push_recent("recent_open", path)
        return True

    def save_scene(self) -> None:
        is_board = bool(getattr(self.canvas, "is_board", False)) and not self.project.path
        if not self.project.path and not is_board:
            QMessageBox.information(self, "Save scene", "Open a video first.")
            return
        folder = self._save_start_dir() or str(Path.home() / "Documents")
        name = (Path(self.project.path).stem if self.project.path else "board") + ".inkit"
        if (
            self.project.scene_path
            and Path(self.project.scene_path).is_file()
            and not is_autosave_name(self.project.scene_path)
            and not self._is_autosave_dir(Path(self.project.scene_path).parent)
        ):
            start = self.project.scene_path
        else:
            start = str(Path(folder) / name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save scene", start, "InkIt (*.inkit);;JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".inkit") and not path.lower().endswith(".json"):
            path += ".inkit"
        Path(path).write_text(json.dumps(self.project.to_json(), indent=2), encoding="utf-8")
        self.project.scene_path = path
        self.project.user_saved = True
        parent = str(Path(path).parent)
        if not self._is_autosave_dir(parent):
            self._settings["last_save_dir"] = parent
        self._push_recent("recent_saved", path)
        self.statusBar().showMessage(f"Scene saved: {path}")

    def open_scene(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scene", self._open_start_dir(), "InkIt (*.inkit);;JSON (*.json);;All files (*.*)"
        )
        if not path:
            return
        self._open_scene_path(path)

    def _supported_drop_paths(self, ev) -> list[str]:
        """All locally-dropped files InkIt understands, in drop order."""
        out: list[str] = []
        for url in ev.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = url.toLocalFile()
            if Path(p).suffix.lower() in (".mp4", ".mov", ".inkit", ".json") or _is_image_path(p):
                out.append(p)
        return out

    def dragEnterEvent(self, ev: QDragEnterEvent) -> None:
        if self._supported_drop_paths(ev):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev) -> None:
        if self._supported_drop_paths(ev):
            ev.acceptProposedAction()

    def dropEvent(self, ev: QDropEvent) -> None:
        paths = self._supported_drop_paths(ev)
        if not paths:
            return
        # Stage every dropped file in the left-side queue and open the first
        self._set_queue(paths)
        ev.acceptProposedAction()

    def _show_frame(self, index: int) -> None:
        if getattr(self.canvas, "is_board", False):
            index = int(min(max(index, 0), max(self.project.frame_count - 1, 0)))
            self.canvas.current_frame = index
            # Boards are pure drawing pages — never keep a media frame behind them
            if self.canvas.image is not None:
                self.canvas.set_frame_image(None)
            self.canvas.update()
            if self.slider.value() != index:
                self.slider.blockSignals(True)
                self.slider.setValue(index)
                self.slider.blockSignals(False)
            self._update_time_label()
            return
        if self._is_still and self._still_bgr is not None:
            self.canvas.current_frame = 0
            self.canvas.set_frame_image(bgr_to_qimage(self._still_bgr))
            if self.slider.value() != 0:
                self.slider.blockSignals(True)
                self.slider.setValue(0)
                self.slider.blockSignals(False)
            self._update_time_label()
            return
        if self.reader.cap is None:
            return
        index = int(min(max(index, 0), max(self.project.frame_count - 1, 0)))
        bgr = self.reader.frame(index)
        self.canvas.current_frame = index
        if bgr is not None:
            self.canvas.set_frame_image(bgr_to_qimage(bgr))
        else:
            self.canvas.update()
        if self.slider.value() != index:
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)
        self._update_time_label()
        if not self.playing:
            self._sync_audio()


    def _update_time_label(self) -> None:
        f = self.canvas.current_frame
        n = max(self.project.frame_count - 1, 0)
        fps = self.project.fps or 24.0
        if self.time_mode == "frames":
            self.lbl_time.setText(f"Frame {f} / {n}")
            self.canvas.frame_overlay_text = f"{f}"
        else:
            t = format_time(f, fps)
            self.lbl_time.setText(f"{t}  /  {format_time(n, fps)}")
            self.canvas.frame_overlay_text = t
        if getattr(self.canvas, "frame_overlay", False):
            self.canvas.update()

    def _toggle_time_mode(self) -> None:
        self.time_mode = "time" if self.time_mode == "frames" else "frames"
        self._settings["time_mode"] = self.time_mode
        self._update_time_label()
        self._schedule_save()

    def _toggle_frame_overlay(self, on: bool) -> None:
        self.canvas.frame_overlay = bool(on)
        self._settings["frame_overlay"] = bool(on)
        self._update_time_label()
        self.canvas.update()
        self._schedule_save()

    def _toggle_loop(self) -> None:
        # Button clicks already flip check state; shortcuts do not.
        if self.sender() is self.btn_loop:
            self.loop = self.btn_loop.isChecked()
        else:
            self.loop = not self.loop
            self.btn_loop.setChecked(self.loop)
        self.btn_loop.setIcon(make_tool_icon("loop", self.loop))
        self.btn_loop.setToolTip("Loop on" if self.loop else "Loop off")
        self._settings["loop"] = self.loop
        self._schedule_save()

    def _scrub(self, v: int) -> None:
        if self.playing:
            self.playing = False
            self.play_timer.stop()
            self.btn_play.setIcon(self._play_icon(False))
            if self._player is not None:
                self._player.pause()
        self._show_frame(v)

    def _step(self, d: int) -> None:
        self._show_frame(self.canvas.current_frame + d)

    def _audio_clock(self) -> bool:
        if not self.audio_on or self._player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer as QMP
            return self._player.playbackState() == QMP.PlaybackState.PlayingState
        except Exception:
            return False

    def _play_icon(self, playing: bool) -> QIcon:
        """Play/Pause glyph — reskinnable via icons/play.png or icons/pause.png."""
        f = ICONS_DIR / ("pause.png" if playing else "play.png")
        if f.is_file():
            return QIcon(str(f))
        return make_tool_icon("pause" if playing else "play")

    def _toggle_play(self) -> None:
        is_board = bool(getattr(self.canvas, "is_board", False))
        can_play = (not is_board and self.reader.cap is not None) or (
            is_board and self.project.frame_count > 1
        )
        if not can_play:
            return
        self.playing = not self.playing
        if self.playing:
            interval = max(8, int(1000 / max(self.project.fps, 1)))
            self.play_timer.start(interval)
            self.btn_play.setIcon(self._play_icon(True))
            if self._player is not None and self.audio_on and not is_board:
                self._sync_audio()
                self._player.play()
        else:
            self.play_timer.stop()
            self.btn_play.setIcon(self._play_icon(False))
            if self._player is not None:
                self._player.pause()

    def _tick(self) -> None:
        if self._audio_clock():
            fps = max(self.project.fps, 0.001)
            frame = int(self._player.position() * fps / 1000.0)
            last = max(self.project.frame_count - 1, 0)
            # Some backends park at EndOfMedia without position() ever passing
            # the last frame — treat that as end-of-video for looping too.
            # Others cap position() exactly at duration without ever raising
            # EndOfMedia, so also flag 'close enough to the end'.
            try:
                from PySide6.QtMultimedia import QMediaPlayer as _QMP
                pos = int(self._player.position())
                dur = int(self._player.duration())
                at_end = self._player.mediaStatus() == _QMP.MediaStatus.EndOfMedia or (
                    dur > 0 and pos >= dur - 40
                )
            except Exception:
                at_end = False
            if frame > last or at_end:
                if self.loop:
                    self._player.setPosition(0)
                    self._show_frame(0)
                else:
                    self.playing = False
                    self.play_timer.stop()
                    self.btn_play.setIcon(self._play_icon(False))
                    self._player.pause()
                return
            if frame != self.canvas.current_frame:
                self._show_frame(frame)
            return
        nxt = self.canvas.current_frame + 1
        if nxt >= self.project.frame_count:
            if self.loop and self.project.frame_count > 0:
                self._show_frame(0)
                if self.audio_on and self._player is not None:
                    self._player.setPosition(0)
                    self._player.play()
            else:
                self.playing = False
                self.play_timer.stop()
                self.btn_play.setIcon(self._play_icon(False))
                if self._player is not None:
                    self._player.pause()
            return
        self._show_frame(nxt)

    def export_video(self) -> None:
        if not self.project.path and not getattr(self.canvas, "is_board", False):
            QMessageBox.information(self, "Export", "Open a video first.")
            return
        if self._is_still:
            self._export_still_image()
            return
        ffmpeg_bin = get_ffmpeg_path()
        if not (Path(ffmpeg_bin).is_file() or shutil.which(ffmpeg_bin)):
            QMessageBox.critical(self, "Export", "FFmpeg was not found. Install FFmpeg and try again.")
            return
        export_dir = self._settings.get("last_export_dir") or self._save_start_dir()
        default = str(Path(export_dir or Path(self.project.path).parent) / (Path(self.project.path).stem + "_notes.mp4"))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export video with notes",
            default,
            "MP4 (*.mp4);;QuickTime MOV (*.mov)",
        )
        if not path:
            return
        # Finalize any in-progress stroke so it's in the project
        self.canvas._end()
        # Snapshot strokes for the worker to avoid cross-thread dict access
        strokes_snapshot = {f: list(strokes) for f, strokes in self.project.strokes.items()}
        self._progress = QProgressDialog("Exporting…", "Hide", 0, 100, self)
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._worker = ExportWorker(
            self.project.path, self.project, path,
            export_audio=self.export_audio, antialias=bool(self.canvas.antialias),
            strokes_snapshot=strokes_snapshot,
            clip_opacity=float(getattr(self.canvas, "clip_opacity", 1.0)),
            fade_color=str(getattr(self.canvas, "fade_color", QColor("#ffffff")).name(QColor.NameFormat.HexRgb)) if hasattr(getattr(self.canvas, "fade_color", None), "name") else str(getattr(self.canvas, "fade_color", "#ffffff")),
            notes_opacity=float(getattr(self.canvas, "notes_opacity", 1.0)),
        )
        self._worker.progress.connect(self._progress.setValue)
        self._worker.failed.connect(self._export_fail)
        self._worker.finished_ok.connect(self._export_ok)
        self._worker.start()

    def _export_still_image(self) -> None:
        """Exports the annotated image (strokes baked in) as PNG/JPG."""
        export_dir = self._settings.get("last_export_dir") or self._save_start_dir()
        default = str(Path(export_dir or Path(self.project.path).parent) / (Path(self.project.path).stem + "_notes.png"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export image with notes", default,
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)"
        )
        if not path:
            return
        if not Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
            path += ".png"
        # Apply clip opacity vs fade_color to still image background before notes (like Canvas paintEvent)
        base = self._still_bgr.copy() if self._still_bgr is not None else np.zeros((1080, 1920, 3), dtype=np.uint8)
        clip_op = float(getattr(self.canvas, "clip_opacity", 1.0))
        if clip_op < 0.995 and base is not None:
            fc = getattr(self.canvas, "fade_color", QColor("#ffffff"))
            fade_hex = fc.name(QColor.NameFormat.HexRgb) if hasattr(fc, "name") else str(fc)
            c = QColor(fade_hex)
            fade_bgr = np.array([c.blue(), c.green(), c.red()], dtype=np.float32)
            base = (base.astype(np.float32) * clip_op + fade_bgr * (1.0 - clip_op)).astype(np.uint8)
        # Ensure pending stroke finalized
        self.canvas._end()
        out = render_annotations_on_bgr(
            base, self.project.strokes.get(0, []),
            antialias=bool(self.canvas.antialias),
            notes_opacity=float(getattr(self.canvas, "notes_opacity", 1.0)),
        )
        ok = cv2.imwrite(path, out)
        if not ok:
            QMessageBox.critical(self, "Export failed", f"Could not write:\n{path}")
            return
        self._settings["last_export_dir"] = str(Path(path).parent)
        self._schedule_save()
        QMessageBox.information(self, "Export", f"Saved:\n{path}")

    def _export_fail(self, msg: str) -> None:
        self._progress.close()
        QMessageBox.critical(self, "Export failed", msg)

    def _export_ok(self, dest: str) -> None:
        self._progress.close()
        self._settings["last_export_dir"] = str(Path(dest).parent)
        self._schedule_save()
        QMessageBox.information(self, "Export", f"Saved:\n{dest}")

    def _settings_path(self) -> Path:
        return config_dir() / "settings.json"

    def _load_app_settings(self) -> None:
        path = self._settings_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict):
            self._settings.update(data)
        self.time_mode = self._settings.get("time_mode", "frames")
        self.loop = bool(self._settings.get("loop", True))
        self.audio_on = bool(self._settings.get("audio", True))
        self.audio_volume = min(max(int(self._settings.get("volume", 100)), 0), 100)
        self.export_audio = bool(self._settings.get("export_audio", True))

    def _schedule_save(self) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self._persist_timer.start(400)

    def _snapshot_tools(self) -> None:
        if not hasattr(self, "btn_tool"):
            return
        self._settings["color"] = self.canvas.color.name(QColor.NameFormat.HexRgb)
        self._settings["brush"] = int(self._tool_sizes["pen"])
        self._settings["brush_eraser"] = int(self._tool_sizes["eraser"])
        self._settings["pen_opacity"] = int(self.sl_pen_op.value())
        self._settings["clip_opacity"] = int(self.sl_clip_op.value())
        self._settings["notes_opacity"] = int(self.sl_notes_op.value())
        self._settings["notes_visible"] = bool(self.btn_hide.isChecked())
        self._settings["tool"] = self.canvas.tool
        self._settings["hardness"] = int(self.sl_hard.value())
        self._settings["pen_antialias"] = bool(self.canvas.antialias)
        self._settings["cursor_mode"] = self.canvas.cursor_mode
        self._settings["cursor_custom"] = self.canvas.cursor_custom
        self._settings["cursor_color"] = self.canvas.cursor_color.name(QColor.NameFormat.HexRgb)
        self._settings["cursor_width"] = int(self.canvas.cursor_width)
        self._settings["cursor_dot"] = bool(self.canvas.cursor_dot)
        self._settings["pen_pressure_curve"] = [
            list(q) for q in getattr(self.canvas, "pressure_curve", DEFAULT_PRESSURE_CURVE)
        ]
        self._settings["board_bg"] = self.canvas.board_bg.name(QColor.NameFormat.HexRgb)
        self._settings["onion"] = bool(getattr(self.canvas, "onion_enabled", False))
        self._settings["onion_depth_prev"] = int(getattr(self.canvas, "onion_depth_prev", 1))
        self._settings["onion_depth_next"] = int(getattr(self.canvas, "onion_depth_next", 1))
        self._settings["onion_opacity"] = int(round(float(getattr(self.canvas, "onion_opacity", 0.35)) * 100))
        self._settings["onion_prev"] = self.canvas.onion_prev.name(QColor.NameFormat.HexRgb)
        self._settings["onion_next"] = self.canvas.onion_next.name(QColor.NameFormat.HexRgb)
        self._settings["loop"] = bool(self.loop)
        self._settings["audio"] = bool(self.audio_on)
        self._settings["export_audio"] = bool(self.export_audio)
        self._settings["time_mode"] = self.time_mode
        self._settings["queue_visible"] = bool(getattr(self, "queue_visible", True))
        self._settings["queue_minimized"] = bool(getattr(self, "queue_minimized", False))

    def _write_app_settings(self) -> None:
        self._snapshot_tools()
        try:
            self._settings_path().write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _apply_settings_to_ui(self) -> None:
        self._loading_settings = True
        s = self._settings
        self._apply_color(QColor(s.get("color", "#ff3b30")))
        self._tool_sizes["pen"] = min(max(int(s.get("brush", 6)), 1), 80)
        self._tool_sizes["eraser"] = min(max(int(s.get("brush_eraser", 30)), 1), 80)
        self.btn_tool.setValue(self._tool_sizes[self.canvas.tool])
        self.sl_pen_op.setValue(int(s.get("pen_opacity", 100)))
        self.sl_clip_op.setValue(int(s.get("clip_opacity", 100)))
        self.sl_notes_op.setValue(int(s.get("notes_opacity", 100)))
        self.sl_hard.setValue(int(s.get("hardness", 100)))
        self.canvas.antialias = bool(s.get("pen_antialias", True))
        self.canvas.board_bg = QColor(s.get("board_bg", "#ffffff"))
        self.canvas.onion_prev = QColor(s.get("onion_prev", "#c248ff"))
        self.canvas.onion_next = QColor(s.get("onion_next", "#33ccff"))
        if hasattr(self, "btn_onion"):
            self.btn_onion.setOn(bool(s.get("onion", False)))
        if hasattr(self, "spin_onion_prev"):
            self.spin_onion_prev.setValue(min(max(int(s.get("onion_depth_prev", 1)), 0), 5))
            self.spin_onion_next.setValue(min(max(int(s.get("onion_depth_next", 1)), 0), 5))
            self.canvas.onion_depth_prev = self.spin_onion_prev.value()
            self.canvas.onion_depth_next = self.spin_onion_next.value()
        if hasattr(self, "btn_onion"):
            v = min(max(int(s.get("onion_opacity", 35)), 5), 100)
            self.btn_onion.setValue(v)
            self.canvas.onion_opacity = v / 100.0
        curve = s.get("pen_pressure_curve")
        if not curve:
            # migrate the old gamma setting into an equivalent 5-point curve
            g = min(max(float(s.get("pen_pressure_gamma", 1.0)), 0.5), 2.0)
            curve = [[i / 4.0, (i / 4.0) ** g] for i in range(5)]
        self.canvas.pressure_curve = normalize_pressure_curve(curve)
        self.canvas.set_cursor_config(
            s.get("cursor_mode", "circle"),
            s.get("cursor_custom", ""),
            QColor(s.get("cursor_color", "#ffffff")),
            int(s.get("cursor_width", 2)),
            bool(s.get("cursor_dot", True)),
        )
        self.canvas.fade_color = QColor("#ffffff")
        vis = bool(s.get("notes_visible", True))
        self.btn_hide.setChecked(vis)
        self.canvas.notes_visible = vis
        self.btn_hide.setIcon(make_tool_icon("eye", vis))
        self._set_tool(s.get("tool", "pen") if s.get("tool") in ("pen", "eraser") else "pen")
        self.loop = bool(s.get("loop", True))
        self.btn_loop.setChecked(self.loop)
        self.btn_loop.setIcon(make_tool_icon("loop", self.loop))
        self.audio_on = bool(s.get("audio", True))
        self.audio_volume = min(max(int(s.get("volume", 100)), 0), 100)
        self.export_audio = bool(s.get("export_audio", True))
        self.btn_audio.setChecked(self.audio_on)
        self.btn_audio.setVolumeLevel(self.audio_volume)
        self._update_audio_icon()
        self.btn_audio.setToolTip(
            f"Audio {'on' if self.audio_on else 'off'} — drag to set volume ({self.audio_volume}%)"
        )
        self.time_mode = s.get("time_mode", "frames")
        self._theme_name = "dark"  # Light mode disabled for now
        self.canvas.frame_overlay = bool(s.get("frame_overlay", False))
        self._update_time_label()
        self._apply_audio_mute()
        self._refresh_color_swatches()
        self._loading_settings = False

    def _push_recent(self, key: str, path: str) -> None:
        items = [p for p in self._settings.get(key, []) if isinstance(p, str) and p != path]
        items.insert(0, path)
        self._settings[key] = items[:10]
        self._rebuild_recent_menus()
        self._write_app_settings()

    def _rebuild_recent_menus(self) -> None:
        if not hasattr(self, "_recent_open_menu"):
            return
        for menu, key, opener in (
            (self._recent_open_menu, "recent_open", self._open_recent_open),
            (self._recent_saved_menu, "recent_saved", self._open_recent_saved),
        ):
            menu.clear()
            items = [p for p in self._settings.get(key, []) if isinstance(p, str)]
            if not items:
                empty = QAction("(empty)", self)
                empty.setEnabled(False)
                menu.addAction(empty)
                continue
            for p in items:
                act = QAction(p, self)
                act.triggered.connect(lambda _=False, path=p, fn=opener: fn(path))
                menu.addAction(act)

    def _open_recent_open(self, path: str) -> None:
        if not Path(path).is_file():
            QMessageBox.warning(self, "Recent", f"File not found:\n{path}")
            items = [p for p in self._settings.get("recent_open", []) if p != path]
            self._settings["recent_open"] = items
            self._rebuild_recent_menus()
            self._write_app_settings()
            return
        if path.lower().endswith((".json", ".inkit")):
            self._open_scene_path(path)
        elif _is_image_path(path):
            self._open_image_path(path)
        else:
            self._open_video_path(path)

    def _open_recent_saved(self, path: str) -> None:
        if not Path(path).is_file():
            QMessageBox.warning(self, "Recent", f"File not found:\n{path}")
            items = [p for p in self._settings.get("recent_saved", []) if p != path]
            self._settings["recent_saved"] = items
            self._rebuild_recent_menus()
            self._write_app_settings()
            return
        self._open_scene_path(path)

    def _open_scene_path(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(self, "Open scene", str(e))
            return
        proj = Project()
        proj.load_json(data, scene_dir=Path(path).parent)
        proj.scene_path = path
        video = proj.path
        if not video and proj.frame_count > 0:
            # Media-less drawing board scene
            if self._bind_board_data(proj, path):
                self._settings["last_open_dir"] = str(Path(path).parent)
            return
        if not video or not Path(video).is_file():
            QMessageBox.warning(self, "Open scene", "Scene loaded, but the clip was not found. Choose the media file.")
            video, _ = QFileDialog.getOpenFileName(
                self, "Locate clip for this scene", self._open_start_dir(),
                "Media (*.mp4 *.mov *.MP4 *.MOV *.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)"
            )
            if not video:
                return
            proj.path = video
        if _is_image_path(proj.path):
            ok = self._open_image_path(proj.path, strokes=proj.strokes)
        else:
            ok = self._open_video_path(proj.path, strokes=proj.strokes)
        if ok:
            self.project.scene_path = path
            self.project.user_saved = not is_autosave_name(path)
            self._settings["last_open_dir"] = str(Path(path).parent)
            self._push_recent("recent_open", path)
            self._refresh_marks()
            self.setWindowTitle(f"InkIt — {Path(proj.path).name}  [{Path(path).name}]")

    # -----------------------------------------------------------------------
    # Audio Engine & Playback Synchronization
    # -----------------------------------------------------------------------

    def _init_audio_engine(self) -> None:
        """Initializes QtMultimedia QMediaPlayer and QAudioOutput for audio playback."""
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception:
            return
        self._QMediaPlayer = QMediaPlayer
        self._audio_out = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_out)
        self._apply_audio_mute()
        self._player.mediaStatusChanged.connect(self._audio_status_changed)

    def _apply_audio_mute(self) -> None:
        if self._audio_out is not None:
            self._audio_out.setMuted(not self.audio_on)
            vol = min(max(int(self.audio_volume), 0), 100) / 100.0
            self._audio_out.setVolume(vol)

    def eventFilter(self, obj, ev) -> bool:
        """Horizontal drag on the audio button adjusts volume (click still toggles)."""
        # Hover anywhere: show the widget's statusTip — falling back to its
        # tooltip / nearest ancestor tooltip — in the status bar, then clear on
        # leave. Floating tooltip windows are suppressed entirely (QEvent.ToolTip
        # is consumed), so ALL hover info lives in the status bar only.
        et = ev.type()
        if et == QEvent.Type.ToolTip:
            return True  # no floating tooltips — hover info goes to the status bar
        # Eyedropper owns ALL mouse input while open: clicks sample the color
        # under the cursor and are never delivered to buttons underneath.
        pk = getattr(self, "_screen_picker", None)
        if pk is not None and et in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
            QEvent.Type.TabletPress,
            QEvent.Type.TabletRelease,
            QEvent.Type.KeyPress,
        ):
            if et == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
                pk.cancel()
            elif et == QEvent.Type.MouseButtonPress:
                if ev.button() == Qt.MouseButton.LeftButton:
                    pk.pick_global()
                elif ev.button() == Qt.MouseButton.RightButton:
                    pk.cancel()
            return True  # swallow — nothing else reacts while picking
        if et == QEvent.Type.Enter and obj is not self:
            vis = getattr(obj, "isVisible", None)
            if callable(vis) and not vis():
                return super().eventFilter(obj, ev)  # off-screen: no hover info
            st = getattr(obj, "statusTip", None)
            tip = st() if callable(st) else ""
            if not tip:
                tt = getattr(obj, "toolTip", None)
                tip = (tt() or "").strip() if callable(tt) else ""
                if not tip:
                    par = obj
                    while True:
                        nxt = getattr(par, "parentWidget", None)
                        if not callable(nxt):
                            break
                        par = nxt()
                        if par is None or par is self:
                            break
                        t2 = (par.toolTip() or "").strip() if hasattr(par, "toolTip") else ""
                        if t2:
                            tip = t2
                            break
            tip = " ".join((tip or "").split())
            if tip:
                self.statusBar().showMessage(tip)
                self._hover_status = True
        elif et == QEvent.Type.Leave and getattr(self, "_hover_status", False):
            self.statusBar().clearMessage()
            self._hover_status = False
        lm = getattr(self, "lbl_meta", None)
        if obj is self.btn_audio:
            t = ev.type()
            if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._vol_drag_x = ev.position().x()
                self._vol_drag_start = int(self.audio_volume)
                self._vol_dragged = False
            elif t == QEvent.Type.MouseMove and self._vol_drag_x is not None:
                dx = ev.position().x() - self._vol_drag_x
                if not self._vol_dragged and abs(dx) > 3:
                    self._vol_dragged = True
                    if not self.audio_on:
                        self.btn_audio.setChecked(True)
                        self._toggle_audio()
                if self._vol_dragged:
                    vol = int(round(self._vol_drag_start + dx * 0.7))
                    vol = min(max(vol, 0), 100)
                    if vol != int(self.audio_volume):
                        self.audio_volume = vol
                        self.btn_audio.setVolumeLevel(vol)
                        self._update_audio_icon()
                        self._apply_audio_mute()
                        self.statusBar().showMessage(f"Volume {vol}%")
                        self.btn_audio.setToolTip(f"Audio on — drag to set volume ({vol}%)")
                    return True
            elif t == QEvent.Type.MouseButtonRelease and self._vol_dragged:
                self._vol_drag_x = None
                self._settings["volume"] = int(self.audio_volume)
                self._schedule_save()
                return True  # swallow release so click-toggle doesn't fire after a drag
            elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonRelease):
                self._vol_drag_x = None
        return super().eventFilter(obj, ev)

    def _update_audio_icon(self) -> None:
        """Shows the muted icon when audio is off OR volume is dragged to 0%."""
        audible = bool(self.audio_on) and int(self.audio_volume) > 0
        self.btn_audio.setIcon(make_tool_icon("audio", audible))

    def _load_clip_audio(self, path: str) -> None:
        if self._player is None:
            return
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._apply_audio_mute()

    def _audio_status_changed(self, status) -> None:
        if self._player is None:
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer as QMP
            if status == QMP.MediaStatus.LoadedMedia or status == QMP.MediaStatus.BufferedMedia:
                if not self.playing:
                    self._sync_audio()
            if status == QMP.MediaStatus.EndOfMedia and self.playing:
                if self.loop:
                    self._player.setPosition(0)
                    self._player.play()
                    self._show_frame(0)
                else:
                    self.playing = False
                    self.play_timer.stop()
                    self.btn_play.setIcon(self._play_icon(False))
        except Exception:
            pass

    def _sync_audio(self) -> None:
        """Synchronizes audio track position with the active video frame index."""
        if self._player is None or not self.project.fps:
            return
        ms = int(round(self.canvas.current_frame / max(self.project.fps, 0.001) * 1000))
        self._player.setPosition(max(0, ms))

    def _toggle_audio(self) -> None:
        """Handler for Audio mute/unmute toggle button."""
        if self.sender() is self.btn_audio:
            self.audio_on = self.btn_audio.isChecked()
        else:
            self.audio_on = not self.audio_on
            self.btn_audio.setChecked(self.audio_on)
        self._update_audio_icon()
        self.btn_audio.setToolTip(
            f"Audio {'on' if self.audio_on else 'off'} — drag to set volume ({int(self.audio_volume)}%)"
        )
        self._apply_audio_mute()
        if self.playing and self.audio_on and self._player is not None:
            self._sync_audio()
            self._player.play()
        elif self._player is not None and not self.audio_on:
            self._player.pause()
        self._settings["audio"] = self.audio_on
        self._schedule_save()

    def _audio_context_menu(self, pos) -> None:
        """Context menu on right-click of the audio button to toggle audio export."""
        menu = QMenu(self)
        act = menu.addAction("Export audio")
        act.setCheckable(True)
        act.setChecked(self.export_audio)
        chosen = menu.exec(self.btn_audio.mapToGlobal(pos))
        if chosen is act:
            self.export_audio = act.isChecked()
            self._settings["export_audio"] = self.export_audio
            self._schedule_save()

    def _on_stroke_finished(self) -> None:
        self._refresh_marks()
        self._autosave_timer.start(250)

    def _autosave_notes(self) -> None:
        """Saves automated scene backups to Documents/InkIt/Autosave."""
        if self.canvas._drawing:
            self._autosave_timer.start(400)
            return
        is_board = bool(getattr(self.canvas, "is_board", False)) and not self.project.path
        if not self.project.path and not is_board:
            return
        if not self.project.annotated_frames() and not self.project.user_saved:
            return
        if (
            self.project.user_saved
            and self.project.scene_path
            and Path(self.project.scene_path).parent.is_dir()
            and not is_autosave_name(self.project.scene_path)
        ):
            dest = Path(self.project.scene_path)
        else:
            stem = Path(self.project.path).stem if self.project.path else "board"
            dest = self.get_autosave_dir() / f"{stem}_autosave.inkit"
            if not self.project.user_saved:
                self.project.scene_path = str(dest)
        try:
            dest.write_text(json.dumps(self.project.to_json(), indent=2), encoding="utf-8")
            self._rebuild_autosave_menu()
            if dest.parent == self.get_autosave_dir():
                self._cleanup_autosaves()
            if self.project.user_saved and not is_autosave_name(dest):
                self.statusBar().showMessage(f"Saved {dest.name}", 2000)
            else:
                self.statusBar().showMessage(f"Autosaved {dest.name}", 2000)
        except Exception as e:
            self.statusBar().showMessage(f"Autosave failed: {e}", 4000)

    def closeEvent(self, ev) -> None:
        """Clean shutdown handler saving autosave notes and settings."""
        self._settings["win_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self._autosave_notes()
        self._write_app_settings()
        self.play_timer.stop()
        if self._player is not None:
            self._player.stop()
        self.reader.close()
        super().closeEvent(ev)


# ===========================================================================
# 10. SYSTEM INTEGRATION & ENTRY POINT
# ===========================================================================

def register_inkit_association() -> None:
    """Registers .inkit file type association with Windows Registry."""
    if "__compiled__" in globals():
        # Packaged (Nuitka onefile): the exe lives in a temp extraction dir,
        # so a Start Menu-style association would point at a volatile path.
        return
    try:
        import winreg
    except Exception:
        return
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        cmd = f'"{exe_path}" "%1"'
    else:
        pythonw = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
        if not pythonw.is_file():
            pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            return
        cmd = f'"{pythonw}" "{APP_DIR / "app.py"}" "%1"'
    icon = app_icon_path()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.inkit") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "InkIt.Document")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\InkIt.Document") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "InkIt scene")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\InkIt.Document\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(icon))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\InkIt.Document\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    except Exception:
        pass


def main() -> None:
    """Application main entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("InkIt")
    app.setStyle("Fusion")
    icon = app_icon_path()
    if not icon.is_file():
        try:
            target = (Path(sys.executable).parent if getattr(sys, "frozen", False) else APP_DIR) / "icon.png"
            write_default_icon(target)
            icon = target
        except Exception:
            pass
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    ensure_icon_assets()  # export reskinnable button PNGs to APP_DIR/icons
    register_inkit_association()
    w = MainWindow()
    if icon.is_file():
        w.setWindowIcon(QIcon(str(icon)))
    w.show()
    # No startup board: the window opens on an empty "Drag your video or image
    # here" state until a file is opened or dropped.
    # Handle files passed via command-line arguments (e.g. double-clicking an .inkit or video)
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_file():
            low = p.suffix.lower()
            if low in (".inkit", ".json"):
                w._open_scene_path(str(p))
            elif low in (".mp4", ".mov"):
                w._open_video_path(str(p))
            elif _is_image_path(p):
                w._open_image_path(str(p))
            break
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
