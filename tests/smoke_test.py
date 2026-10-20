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
from _media import frame_id as block_id, make_frame, make_h264, make_sequence  # noqa: E402


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

    from PySide6.QtCore import QPoint, Qt as _Qt
    from PySide6.QtTest import QTest
    check("add_range_note" not in w._actions, "no separate range-note action")
    seen: dict = {}

    def fill():
        dlg = qapp.activeModalWidget()
        seen["defaults"] = (dlg.sp_start.value(), dlg.sp_end.value())
        dlg.edit.setPlainText("hold pose two more frames")
        box = dlg.sp_end  # click-to-type the end frame
        QTest.mouseClick(box, _Qt.MouseButton.LeftButton, pos=QPoint(40, 15))
        box._edit.selectAll()
        QTest.keyClicks(box._edit, "1012")
        QTest.keyClick(box._edit, _Qt.Key.Key_Return)
        dlg.accept()
    QTimer.singleShot(50, fill)
    w.add_text_note()
    check(seen.get("defaults") == (1005, 1005), f"note dialog defaults From/To to current frame ({seen.get('defaults')})")
    n = w.project.text_notes
    check(len(n) == 1 and n[0]["start"] == 4 and n[0]["end"] == 11, f"range note stored 0-based (1005-1012 -> {n})")
    w.project.text_notes.append({"start": 30, "end": 30, "text": "arc is floaty"})
    w._notes_changed()
    check(w.notes_list.count() == 2 and w.notes_list.item(0).text().startswith("1005–1012"), "sidebar lists notes with display frames")
    def move():
        dlg = qapp.activeModalWidget()
        box = dlg.sp_start  # drag-scrub From +8 frames (4 px/step); To follows
        QTest.mousePress(box, _Qt.MouseButton.LeftButton, pos=QPoint(30, 15))
        QTest.mouseMove(box, QPoint(46, 15))
        QTest.mouseMove(box, QPoint(62, 15))
        QTest.mouseRelease(box, _Qt.MouseButton.LeftButton, pos=QPoint(62, 15))
        seen["edit"] = (dlg.sp_start.value(), dlg.sp_end.value())
        dlg.accept()
    QTimer.singleShot(50, move)
    w._edit_text_note(1)
    check(seen.get("edit") == (1039, 1039) and w.project.text_notes[1]["start"] == 38, f"editing a note changes its frames ({seen.get('edit')})")
    w.project.text_notes[1].update({"start": 30, "end": 30})
    w._notes_changed()
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


def test_sequences_fps_frames(w, tmp: Path, qapp) -> None:
    """Image sequences (PNG, gaps, EXR), fps override, annotated-frame + contact sheet export."""
    seq_dir = tmp / "seq"
    first = make_sequence(seq_dir, "sh040_anim", start=1001, n=12)
    (seq_dir / "sh040_anim.1005.png").unlink()  # a gap: frame 1005 holds 1004
    pat = app.sequence_pattern_for(first)
    check(pat is not None and pat.endswith("sh040_anim.####.png"), f"sequence pattern detected ({pat})")
    check(app.sequence_in_folder(seq_dir) == pat, "dropping the folder finds the sequence")
    w._settings.setdefault("start_frames", {}).pop(w._notes_key(pat), None)
    w._settings.setdefault("fps_overrides", {}).pop(w._notes_key(pat), None)
    w._set_queue([pat])
    w._queue_open(w.queue_paths.index(pat))
    check(w.reader.is_sequence and w.project.frame_count == 12, f"sequence opened as 12-frame clip ({w.project.frame_count})")
    check(w.project.start_frame == 1001 and w.lbl_time.text() == "Frame 1001 / 1012", f"start frame from sequence numbering ({w.lbl_time.text()})")
    ids = [block_id(w.reader.frame(i)) for i in range(12)]
    check(ids == [0, 1, 2, 3, 3, 5, 6, 7, 8, 9, 10, 11], f"sequence frames in order, gap holds previous ({ids})")

    w._apply_fps(12.0)
    check(w.project.fps == 12.0 and w.project.fps_override == 12.0 and "12* fps" in w.lbl_meta.text(), f"fps override applied ({w.lbl_meta.text()})")
    w.project.strokes_at(2).append(app.Stroke(color="#ff00ff", base_width=10.0, points=[app.Point(0.1, 0.5, 1.0), app.Point(0.9, 0.5, 1.0)]))
    w.project.text_notes.append({"start": 7, "end": 7, "text": "ease out"})
    scene = tmp / "sh040.inkit"
    scene.write_text(json.dumps(w.project.to_json()), encoding="utf-8")
    w._apply_fps(None)
    w._open_scene_path(str(scene))
    check(w.reader.is_sequence and w.project.fps == 12.0 and w.project.annotated_frames() == [2], "sequence scene round-trip keeps fps override + drawings")

    out = w.export_annotated_frames(str(tmp / "frames"))
    names = sorted(Path(x).name for x in out)
    check(names == ["sh040_anim.1003.png", "sh040_anim.1008.png", "sh040_anim_contact_sheet.png"], f"annotated frames exported with display numbers ({names})")
    png = cv2.imread(str(tmp / "frames" / "sh040_anim.1003.png"))
    check(png is not None and int(png[90, 160, 2]) > 200 and int(png[90, 160, 1]) < 80, "exported PNG has the drawing burned in")
    sheet = cv2.imread(str(tmp / "frames" / "sh040_anim_contact_sheet.png"))
    check(sheet is not None and sheet.shape[1] > 900, f"contact sheet written ({None if sheet is None else sheet.shape})")

    # Video export of a sequence (ffmpeg, no audio) at the override rate
    dest = tmp / "sh040_notes.mp4"
    wk = app.ExportWorker(w.project.path, w.project, str(dest), export_audio=True,
                          strokes_snapshot={k: list(v) for k, v in w.project.strokes.items()}, overlay_snapshot=[])
    res: list[str] = []
    wk.failed.connect(lambda m: res.append("fail:" + m))
    wk.finished_ok.connect(lambda d: res.append("ok"))
    wk.run()
    cap = cv2.VideoCapture(str(dest))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    check(res == ["ok"] and n == 12 and abs(fps - 12.0) < 0.01, f"sequence exported to MP4 at 12 fps ({res}, {n} frames, {fps})")

    # Regression: plain video export still works through the new reader
    vclip = tmp / "sh041.mp4"
    make_h264(vclip, n=30, gop=250)
    w._open_video_path(str(vclip))
    vdest = tmp / "sh041_notes.mp4"
    wk = app.ExportWorker(w.project.path, w.project, str(vdest), export_audio=True, strokes_snapshot={}, overlay_snapshot=[])
    res2: list[str] = []
    wk.finished_ok.connect(lambda d: res2.append("ok"))
    wk.failed.connect(lambda m: res2.append("fail:" + m))
    wk.run()
    cap = cv2.VideoCapture(str(vdest))
    frames = [block_id(cap.read()[1]) for _ in range(30)]
    cap.release()
    check(res2 == ["ok"] and frames == list(range(30)), f"video export keeps every frame in order ({res2})")

    # EXR (float, linear) if this OpenCV build has OpenEXR
    exr_dir = tmp / "exr"
    exr_dir.mkdir()
    okexr = True
    for k in range(3):
        lin = (make_frame(k).astype(np.float32) / 255.0) ** 2.2
        okexr = okexr and bool(cv2.imwrite(str(exr_dir / f"plate.{k + 1:04d}.exr"), lin))
    if okexr:
        r = app.VideoReader()
        r.open(str(exr_dir / "plate.####.exr"))
        check(r.seq_start == 1 and [block_id(r.frame(i)) for i in range(3)] == [0, 1, 2], "EXR sequence decodes (linear -> sRGB)")
        r.close()
    else:
        print("skip  EXR (this OpenCV build has no OpenEXR writer)")


def test_compare(w, tmp: Path, qapp) -> None:
    """A/B compare: frame lock by number, wipe / side modes, drawings stay on A."""
    a = tmp / "sh050_v001.1001-1060.mp4"
    make_h264(a, n=60, gop=250)
    bdir = tmp / "b"
    make_sequence(bdir, "sh050_v002", start=1011, n=40)
    w._settings.get("start_frames", {}).pop(w._notes_key(str(a)), None)
    w._open_video_path(str(a))
    check(w.project.start_frame == 1001, "A start 1001")
    w._settings["compare_mode"] = "wipe"
    check(w.load_compare_clip(str(bdir / "sh050_v002.1020.png")), "load B from any sequence frame")
    check(w.compare_start == 1011 and w.canvas.compare_mode == "wipe", "B start 1011 detected, wipe mode on")
    w._show_frame(25)  # A frame 1026 -> B frame 1026 -> B index 15
    check(block_id(w.compare_reader.frame(w.compare_index_for(25))) == 15, "B locked to A by frame number (1026)")
    w._show_frame(0)   # A 1001 is before B starts: B holds its first frame
    check(w.compare_index_for(0) == 0, "B clamps before its first frame")
    w.project.strokes_at(30).append(app.Stroke(color="#ffcc00", base_width=12.0, points=[app.Point(0.05, 0.5, 1.0), app.Point(0.95, 0.5, 1.0)]))
    w._show_frame(30)
    qapp.processEvents()
    shots = tmp / "shots"
    shots.mkdir(exist_ok=True)
    w.sl_wipe.setValue(35)
    w.grab().save(str(shots / "compare_wipe.png"))
    full = w.canvas._dst
    w._set_compare_mode("side")
    qapp.processEvents()
    w.canvas.repaint()
    check(w.canvas._dst.width() < full.width() * 0.75, "side-by-side puts A in the left half")
    w.grab().save(str(shots / "compare_side.png"))
    # B only / Overlay: sample a pixel of the drawing-free area (A frame 30 vs B frame 20)
    def px():
        w.canvas.repaint()
        img = w.canvas.grab().toImage()
        d = w.canvas._dst
        return img.pixelColor(int(d.x() + d.width() * 0.075), int(d.y() + d.height() * 0.04)).lightness()
    a_bits = (30 >> 1) & 1   # second block = bit 1 of the frame index (A=1)
    b_bits = (20 >> 1) & 1   # (B=0)
    w._set_compare_mode("bonly")
    qapp.processEvents()
    v_b = px()
    check(abs(w.canvas._dst.width() - full.width()) < 2 and (v_b > 200) == bool(b_bits), f"B only shows B in A's frame ({v_b})")
    w._set_compare_mode("overlay")
    w.sl_b_op.setValue(0)
    v0 = px()
    w.sl_b_op.setValue(100)
    v100 = px()
    w.sl_b_op.setValue(50)
    v50 = px()
    check(w.sl_b_op.isVisible() and not w.sl_wipe.isVisible(), "overlay shows the B opacity control")
    check((v0 > 200) == bool(a_bits) and (v100 > 200) == bool(b_bits) and min(v0, v100) < v50 < max(v0, v100) + 1,
          f"overlay blends B over A by opacity (0%={v0}, 50%={v50}, 100%={v100})")
    w.grab().save(str(shots / "compare_overlay.png"))
    w._set_compare_mode("off")
    qapp.processEvents()
    w.canvas.repaint()
    check(abs(w.canvas._dst.width() - full.width()) < 2, "A only restores the full view")
    w.close_compare()
    check(w.canvas.compare_image is None and not w._compare_bar.isVisible(), "close compare")


def test_value_controls_and_start_memory(w, tmp: Path, qapp) -> None:
    """Value-only BoxSlider (no track): click-to-type + drag; start frame remembered globally."""
    from PySide6.QtCore import QPoint, Qt as _Qt
    from PySide6.QtTest import QTest
    sl = w.sl_pen_op
    check(not hasattr(sl, "_track_geom"), "BoxSlider has no slider track any more")
    sl.setValue(50)
    QTest.mouseClick(sl, _Qt.MouseButton.LeftButton, pos=QPoint(sl.width() - 10, sl.height() // 2))
    qapp.processEvents()
    check(sl._edit.isVisible(), "click on a value control opens inline typing")
    sl._edit.selectAll()
    QTest.keyClicks(sl._edit, "73")
    QTest.keyClick(sl._edit, _Qt.Key.Key_Return)
    check(sl.value() == 73, f"typed value applied ({sl.value()})")
    y = sl.height() // 2
    QTest.mousePress(sl, _Qt.MouseButton.LeftButton, pos=QPoint(20, y))
    QTest.mouseMove(sl, QPoint(30, y))
    QTest.mouseMove(sl, QPoint(5, y))
    QTest.mouseRelease(sl, _Qt.MouseButton.LeftButton, pos=QPoint(5, y))
    check(sl.value() == 63 and not sl._edit.isVisible(), f"drag changes value relatively (-15px -> {sl.value()})")

    # Start frame: last user value is used for clips without detected/saved start
    w._open_video_path(str(tmp / "shot_test.mp4"))
    w._set_start_frame(6586)
    check(int(w._qsettings().value("start_frame/last", 0)) == 6586, "last start frame persisted in QSettings")
    plain = tmp / "plainclip.mp4"
    make_clip(plain)
    w._settings.get("start_frames", {}).pop(w._notes_key(str(plain)), None)
    w._open_video_path(str(plain))
    check(w.project.start_frame == 6586 and w.box_start.value() == 6586, "new clip without a start uses the last value (6586)")
    numbered = tmp / "sh060_1001-1047.mp4"
    make_clip(numbered)
    w._settings.get("start_frames", {}).pop(w._notes_key(str(numbered)), None)
    w._open_video_path(str(numbered))
    check(w.project.start_frame == 1001, "auto-detected start still wins over the last value")


def test_version(w, tmp: Path, qapp) -> None:
    """Single version source: semver beta, window title, About text, exe resource."""
    import re
    import version as V
    check(re.match(r"^\d+\.\d+\.\d+-beta\.\d+$", V.__version__) is not None, f"semantic beta version ({V.__version__})")
    check(app.__version__ == V.__version__, "app.py reads version.py")
    w._open_video_path(str(tmp / "shot_test.mp4"))
    t = w.windowTitle()
    check(t.startswith(f"InkIt Beta {V.__version__}"), f"window title shows Beta + version ({t})")
    a = w.about_text()
    check("Beta" in a and V.__version__ in a, "About shows Beta + version")
    try:
        import PyInstaller.utils.win32.versioninfo  # noqa: F401  (real classes on Windows)
    except ImportError:
        # Off Windows the module needs win32api: stand in with recording stubs
        import types
        stub = types.ModuleType("PyInstaller.utils.win32.versioninfo")

        def _mk(name):
            class _S:
                def __init__(self, *a, **k):
                    self.a, self.k = a, k

                def __repr__(self):
                    return f"{name}({', '.join(map(repr, self.a))}{', ' if self.a and self.k else ''}{', '.join(f'{k}={v!r}' for k, v in self.k.items())})"
            return _S
        for n in ("FixedFileInfo", "StringFileInfo", "StringTable", "VarFileInfo", "VarStruct", "VSVersionInfo"):
            setattr(stub, n, _mk(n))

        class StringStruct:
            def __init__(self, k, v):
                self.kv = (k, v)

            def __repr__(self):
                return repr(self.kv)
        stub.StringStruct = StringStruct
        sys.modules["PyInstaller.utils.win32.versioninfo"] = stub
    ns: dict = {"SPECPATH": str(ROOT), "__file__": str(ROOT / "InkIt.spec")}
    src = (ROOT / "InkIt.spec").read_text(encoding="utf-8")
    head = src.split("datas = [")[0]  # imports + version resource only (no Analysis)
    exec(compile(head, "InkIt.spec", "exec"), ns)
    res = str(ns["VERSION_RESOURCE"])
    check(f"'ProductVersion', '{V.__version__}'" in res and "'ProductName', 'InkIt Beta'" in res
          and "'FileVersion', '%d.%d.%d.%d'" % V.VERSION_TUPLE in res, "spec builds exe version resource from version.py")


FEATURE_TESTS = [test_version, test_value_controls_and_start_memory, test_frame_accurate_seek, test_start_frame, test_text_notes, test_sequences_fps_frames, test_compare]


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
