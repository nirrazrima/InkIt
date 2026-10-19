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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _media import frame_id as block_id, make_h264  # noqa: E402


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


def test_frame_accurate_seek(w, tmp: Path, qapp) -> None:
    """Random access, backward stepping and a backend whose seeks land late."""
    import random
    clip = tmp / "gop250.mp4"
    make_h264(clip, n=300, gop=250, bframes=3)
    r = app.VideoReader()
    n, _fps, _w, _h = r.open(str(clip))
    rnd = random.Random(7)
    order = rnd.sample(range(n), 80) + list(range(120, 90, -1)) + [299, 0, 298]
    bad = [i for i in order if block_id(r.frame(i)) != i]
    check(not bad, f"VideoReader frame-accurate on long-GOP H.264 ({len(order)} seeks)")

    class LateSeek:
        """Wraps a capture so CAP_PROP_POS_FRAMES seeks land 5 frames late."""
        def __init__(self, c):
            self.c = c
        def set(self, prop, v):
            if prop == cv2.CAP_PROP_POS_FRAMES:
                v = v + 5
            return self.c.set(prop, v)
        def __getattr__(self, k):
            return getattr(self.c, k)

    r.close()
    r.open(str(clip))
    r.cap = LateSeek(r.cap)
    bad = [i for i in rnd.sample(range(n), 60) + [150, 149, 148] if block_id(r.frame(i)) != i]
    check(not bad, "VideoReader stays exact when the backend seek lands late")
    r.close()
    # Through the UI path (what Maya sync drives): _show_frame -> displayed frame
    w._open_video_path(str(clip))
    for idx in (200, 17, 199, 250):
        w._show_frame(idx)
        got = block_id(w.reader.frame(w.canvas.current_frame))
        check(got == idx, f"_show_frame({idx}) displays decoded frame {idx}")


def test_start_frame(w, tmp: Path, qapp) -> None:
    """Start frame: detection, click-to-type, drag-scrub, labels, scene + Maya mapping."""
    import socket
    import threading
    from PySide6.QtCore import QPoint, Qt as _Qt
    from PySide6.QtTest import QTest

    check(app.detect_start_frame("sh010_anim.1001-1100.mov") == 1001, "detect 1001 from 'x.1001-1100.mov'")
    check(app.detect_start_frame("sh010_fr6586.mp4") == 6586, "detect 6586 from 'x_fr6586.mp4'")
    check(app.detect_start_frame("sh010_v003.mp4") is None, "no false start frame from a version number")

    clip = tmp / "sh020_1001-1047.mp4"
    make_clip(clip)
    w._open_video_path(str(clip))
    check(w.project.start_frame == 1001, "start frame auto-detected on open (1001)")
    check(w.box_start.value() == 1001, "start box shows 1001")
    w._show_frame(5)
    check(w.lbl_time.text() == "Frame 1006 / 1048", f"counter honours start ({w.lbl_time.text()})")

    # click without drag -> inline editor; type 6586 + Enter
    box = w.box_start
    QTest.mouseClick(box, _Qt.MouseButton.LeftButton, pos=QPoint(40, 15))
    qapp.processEvents()
    check(box._edit.isVisible(), "click opens inline start-frame editor")
    box._edit.selectAll()
    QTest.keyClicks(box._edit, "6586")
    QTest.keyClick(box._edit, _Qt.Key.Key_Return)
    qapp.processEvents()
    check(w.project.start_frame == 6586, f"typed start frame applied ({w.project.start_frame})")
    check(w.lbl_time.text().startswith("Frame 6591"), "counter relabelled after typing")

    # click-drag scrub: +40 px -> +10 frames at 4 px/step
    QTest.mousePress(box, _Qt.MouseButton.LeftButton, pos=QPoint(40, 15))
    QTest.mouseMove(box, QPoint(60, 15))
    QTest.mouseMove(box, QPoint(80, 15))
    QTest.mouseRelease(box, _Qt.MouseButton.LeftButton, pos=QPoint(80, 15))
    qapp.processEvents()
    check(w.project.start_frame == 6596 and not box._edit.isVisible(), f"drag scrubs start frame (+10 -> {w.project.start_frame})")

    # scene round-trip keeps it
    scene = tmp / "sh020.inkit"
    scene.write_text(json.dumps(w.project.to_json()), encoding="utf-8")
    w._set_start_frame(1)
    w._open_scene_path(str(scene))
    check(w.project.start_frame == 6596, "start frame restored from scene")
    old = json.loads(scene.read_text())
    old.pop("start_frame")
    w._settings["start_frames"] = {}
    legacy = tmp / "legacy.inkit"
    legacy.write_text(json.dumps(old), encoding="utf-8")
    check(app.Project().start_frame == 0, "legacy default start frame is 0")

    # Maya mapping: Maya frame N == InkIt frame N when offset 0
    w._set_start_frame(1001)
    w._maya_offsets.pop(str(clip), None)
    w._load_maya_offset_for_path(str(clip))
    check(w._maya_offset == 0, "Maya offset defaults to 0 for numbered clips")
    w._settings["maya_sync"] = True
    w._apply_maya_frame(1010)
    w._process_maya_pending()
    check(w.canvas.current_frame == 9 and w.lbl_time.text().startswith("Frame 1010"), "Maya 1010 -> InkIt frame 1010 (index 9)")
    got: list[str] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    w._maya_send_port = srv.getsockname()[1]
    w._maya_send_fail_until = 0

    def serve():
        srv.settimeout(2)
        for _ in range(2):
            try:
                c, _a = srv.accept()
                got.append(c.recv(100).decode().strip())
                c.close()
            except Exception:
                break
    t = threading.Thread(target=serve)
    t.start()
    w._show_frame(20)
    t.join()
    srv.close()
    w._settings["maya_sync"] = False
    check("FRAME 1021" in got and "APPFRAME 1021" in got, f"InkIt 1021 sends Maya FRAME/APPFRAME 1021 ({got})")


def test_text_notes(w, tmp: Path, qapp) -> None:
    """Typed notes: add via dialog, sidebar jump, scene round-trip, legacy scene, review sheet."""
    from PySide6.QtCore import QTimer
    clip = tmp / "sh030_1001-1047.mp4"
    make_clip(clip)
    w._open_video_path(str(clip))
    w._set_start_frame(1001)
    w._show_frame(4)

    def fill():
        dlg = qapp.activeModalWidget()
        dlg.edit.setPlainText("hold pose two more frames")
        dlg.sp_end.setValue(1012)
        dlg.accept()
    QTimer.singleShot(50, fill)
    w.add_text_note(ask_range=True)
    n = w.project.text_notes
    check(len(n) == 1 and n[0]["start"] == 4 and n[0]["end"] == 11, f"range note stored 0-based (1005-1012 -> {n})")
    w.project.text_notes.append({"start": 30, "end": 30, "text": "arc is floaty"})
    w._notes_changed()
    check(w.notes_list.count() == 2 and w.notes_list.item(0).text().startswith("1005–1012"), "sidebar lists notes with display frames")
    w._notes_jump(w.notes_list.item(1))
    check(w.canvas.current_frame == 30, "click note in sidebar jumps to its frame")
    check([x["text"] for x in w.project.notes_at(8)] == ["hold pose two more frames"], "notes_at covers the range")
    check(w.slider.note_marks == [(4, 11), (30, 30)], "timeline shows note ranges")
    w.project.strokes_at(20).append(app.Stroke(color="#00ff00", base_width=8.0, points=[app.Point(0.2, 0.2, 1.0), app.Point(0.8, 0.6, 1.0)]))

    scene = tmp / "sh030.inkit"
    scene.write_text(json.dumps(w.project.to_json()), encoding="utf-8")
    w.project.text_notes = []
    w._open_scene_path(str(scene))
    check(len(w.project.text_notes) == 2, "typed notes restored from scene")
    legacy = json.loads(scene.read_text())
    legacy.pop("text_notes")
    legacy.pop("start_frame")
    lp = tmp / "legacy_notes.inkit"
    lp.write_text(json.dumps(legacy), encoding="utf-8")
    pr = app.Project()
    pr.load_json(legacy)
    check(pr.text_notes == [] and pr.start_frame == 0 and pr.annotated_frames() == [20], "legacy scene (no notes / start) loads")

    out = w.export_review_sheet(str(tmp / "review.html"))
    html_txt = Path(out).read_text(encoding="utf-8")
    rows = list(__import__("csv").reader(open(tmp / "review.csv", encoding="utf-8-sig")))
    check(rows[0][:3] == ["shot", "frame_start", "frame_end"] and len(rows) == 4, f"review CSV has header + 3 rows ({len(rows)})")
    check(rows[1][1:3] == ["1005", "1012"] and rows[2][1] == "1021" and rows[2][5] == "yes", "CSV uses display frames, drawing-only row included")
    check(html_txt.count("data:image/jpeg;base64,") == 3 and "arc is floaty" in html_txt, "HTML sheet embeds 3 thumbnails")


FEATURE_TESTS = [test_frame_accurate_seek, test_start_frame, test_text_notes]


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

    for fn in FEATURE_TESTS:
        fn(w, tmp, qapp)

    w._close_clip()
    w.close()
    print(f"\nALL {len(CHECKS)} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
