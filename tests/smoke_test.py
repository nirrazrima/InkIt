"""Headless smoke test for InkIt (run with QT_QPA_PLATFORM=offscreen).

Generates a small test clip, opens it in a MainWindow, steps frames, draws a
stroke and saves/re-opens a scene. Exits non-zero on the first failure.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import app  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def make_clip(path: Path, n: int = 48, fps: float = 24.0, size=(320, 180), gop: int = 0) -> None:
    """Writes a clip whose frame i carries its index in the red channel of pixel (0,0)
    and as big digits, so seeking accuracy can be verified exactly."""
    w, h = size
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened(), "VideoWriter failed"
    for i in range(n):
        img = np.full((h, w, 3), 40, np.uint8)
        img[:, :, 2] = (i * 5) % 256
        cv2.putText(img, str(i), (20, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)
        vw.write(img)
    vw.release()


def frame_id(bgr) -> int:
    """Recovers the frame index encoded in make_clip (red level / 5)."""
    return int(round(float(np.median(bgr[: 20, -40:, 2])) / 5.0))


CHECKS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    CHECKS.append(msg)
    print("ok  ", msg)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="inkit_smoke_"))
    clip = tmp / "shot_test.mp4"
    make_clip(clip)

    qapp = QApplication.instance() or QApplication(sys.argv)
    w = app.MainWindow()
    w.resize(1280, 720)
    w.show()
    qapp.processEvents()

    check(w._open_video_path(str(clip)), "open test clip")
    check(w.project.frame_count == 48, f"frame count 48 (got {w.project.frame_count})")
    for idx in (10, 3, 40, 0, 47, 20):
        w._show_frame(idx)
        check(w.canvas.current_frame == idx, f"show frame {idx}")

    # Draw a stroke programmatically and save/re-open the scene
    st = app.Stroke(color="#ff0000", base_width=6.0, points=[app.Point(0.1, 0.1, 1.0), app.Point(0.5, 0.5, 1.0)])
    w.project.strokes_at(20).append(st)
    scene = tmp / "shot_test.inkit"
    scene.write_text(json.dumps(w.project.to_json(), indent=2), encoding="utf-8")
    w._open_scene_path(str(scene))
    check(w.project.annotated_frames() == [20], "scene round-trip keeps drawing on frame 20")

    if hasattr(sys.modules[__name__], "extra_checks"):
        extra_checks(w, tmp, qapp)  # noqa: F821  (defined by later feature tests)

    w._close_clip()
    w.close()
    print(f"\nALL {len(CHECKS)} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
