"""Test-media helpers: clips and image sequences whose frames encode their own
index as 12 black/white blocks (robust to lossy codecs)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

BITS = 12
BLOCK = 16


def make_frame(i: int, w: int = 320, h: int = 180) -> np.ndarray:
    img = np.full((h, w, 3), 60, np.uint8)
    for b in range(BITS):
        v = 255 if (i >> b) & 1 else 0
        img[0:BLOCK, b * BLOCK:(b + 1) * BLOCK] = v
    cv2.putText(img, str(i), (20, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (255, 255, 255), 5)
    return img


def frame_id(bgr: np.ndarray) -> int:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    out = 0
    for b in range(BITS):
        c = g[4:BLOCK - 4, b * BLOCK + 4:(b + 1) * BLOCK - 4]
        if float(c.mean()) > 127:
            out |= 1 << b
    return out


def make_h264(path: Path, n: int = 300, fps: float = 24.0, gop: int = 250, bframes: int = 3) -> None:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    w, h = 320, 180
    p = subprocess.Popen(
        [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-g", str(gop), "-bf", str(bframes),
         "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE,
    )
    assert p.stdin is not None
    for i in range(n):
        p.stdin.write(make_frame(i, w, h).tobytes())
    p.stdin.close()
    p.wait()
    assert p.returncode == 0, "ffmpeg failed to write test clip"


def make_sequence(folder: Path, stem: str = "shot", start: int = 1001, n: int = 12, ext: str = ".png") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    first = None
    for k in range(n):
        f = folder / f"{stem}.{start + k:04d}{ext}"
        cv2.imwrite(str(f), make_frame(k))
        first = first or f
    return first
