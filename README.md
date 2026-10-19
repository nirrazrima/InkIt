# InkIt

**InkIt** is a local, video and image annotation and animation-review player for Windows. It lets you draw directly on top of video frames — frame by frame — the way animators annotate dailies, boards, and animatics. It is fully self-contained: one executable, no Python, FFmpeg, or Qt install required.

InkIt was designed as a lightweight, frame-accurate companion for reviewing and marking up sequence work, and it pairs tightly with Autodesk Maya for live, two-way frame syncing during animation review.
it also autosave based on each stroke so you never lose the notes, 
you can share the .json file, with others and if location of the clips are on same path, it just opens the flowlessly.

I have design this based on my own way of working with wacom, and I believe it's very easy to adjust to it.

Since I work on Maya most of the time, I can use this tool as a video reference player, that syncs with your notes.

I haven't test it on other machine and just saving it here for a back up, so please test it and leave me some feedback. 

I would love to continue improving it.

If you are an UI/UX artists and interested in the tool please get in touch to make it look better.

---

## Screenshots

| Annotating a sequence | Reviewing in Maya Sync | Frame-accurate review |
| :---: | :---: | :---: |
| ![Annotating a sequence](screenshots/annotation.png) | ![Reviewing with Maya Sync](screenshots/maya-sync.png) | ![Frame-accurate review](screenshots/review.png) |

---

## What you can do with InkIt

### Draw on your footage
- **Pen & Eraser** with adjustable **brush size**, **hardness** (soft/hard edge), and **opacity** — toggle with a click or the `B` key.
- **Lazy Mouse** stroke stabilization (like Photoshop / Krita stabilizers): a percentage slider that smooths your strokes, with more smoothing at higher values.
- **Custom colors**: pick any color with a screen eyedropper, or use the four quick-access color slots (reassign them with a double-click). Switch slots with keys `1`–`4`.
- **Stylus / tablet friendly**: pressure-sensitive width, eraser tip, barrel buttons, and touch are supported.
- **Undo / Redo** (`Ctrl+Z` / `Ctrl+Shift+Z`) and **Clear Frame** (`Delete`), with per-frame redo history.

### Animate and review frame by frame
- Fine **frame-accurate scrubbing** along the timeline — middle-drag to scrub, and keep dragging forever because the cursor wraps seamlessly across multiple monitors.
- **Playback** with play/pause, frame stepping, **loop**, audio with volume control, and a clickable frames/time readout.
- Quick **jump to the previous/next drawing** frame, plus **onion skinning** to ghost the frames before and after your current drawing.
- A **timeline scrubber** shows small dots above every frame you've drawn on, so you can see progress at a glance.
- **Picture-in-Picture (PiP)**: pop out a small always-on-top mini player that mirrors the main window's drawings, with its own scrubbing, playback, and even a pen to draw directly on the mini view.

### Add notes on top of the picture
- Draw any kind of annotation — notes, camera directions, timing marks, pose breakdowns — on the video, on a still, or on a board.
- Control how it looks: **Notes opacity** and **Clip opacity** (fade the underlying picture so your marks pop), and a **show/hide notes** toggle.
- Persist your work in a lightweight scene file that opens right back up with all frames and strokes intact — with **autosave** in the background so you never lose work.

### Review tools
- **Start frame number**: the **Start** field next to the frame counter sets the first frame number of the clip (0, 1, 1001, 6586…). Click it to type a value, or click-drag left/right to scrub it (Shift ×10, Ctrl fine, wheel ±1). It is auto-detected from image-sequence numbering and from names like `shot.1001-1100.mov` or `shot_fr6586.mov`, saved in the scene, remembered per clip, and used by the counter, the frame HUD, **Go to frame** (`Ctrl+G`), notes, exports and Maya sync (with a numbered clip, Maya frame 1001 = InkIt frame 1001).
- **Typed review notes**: `N` adds a note; its From / To frame fields default to the current frame (click to type or drag to make it a range, also when editing). Notes appear as a caption on the canvas, as cyan ticks on the timeline and in the **Review Notes** sidebar (`Ctrl+Shift+N`; click = jump, double-click = edit, Del = remove). They are saved in the `.inkit` scene (older scenes still open).
- **Review sheet** (`Ctrl+Shift+R`): exports a CSV plus an HTML page with an annotated thumbnail for every note and every drawn frame.
- **Annotated frames + contact sheet** (`Ctrl+Shift+E`): writes every drawn / noted frame as `shot.1001.png` (real frame numbers, drawings burned in) plus `shot_contact_sheet.png`.
- **A/B compare** (`Ctrl+Shift+B` to load B, `Ctrl+Shift+C` to cycle): wipe or side-by-side against a second clip or sequence, locked by frame number (B has its own **B start**); your drawings stay on A.
- **Image sequences** (`Ctrl+Alt+O`, or drop a folder): numbered PNG / JPG / TIFF / EXR frames open as a clip; missing numbers hold the previous frame. EXR is shown linear → sRGB.
- **Frame rate override** (`Ctrl+Alt+F`): set playback / export fps (sequences default to 24). Saved per scene; a retimed video plays without audio.
- **Frame-accurate seeking**: random access is verified by frame timestamps and backed by a small frame cache, so long-GOP H.264 playblasts land on the exact frame when scrubbing, stepping or syncing with Maya.

### Sync to Maya for animation review
- InkIt and Autodesk Maya talk over a local two-way connection, letting you review animation directly against a frame-accurate playblast while keeping both apps in sync.
- Use the **InkIt Sync** panel/shelf button in Maya to match InkIt to Maya's current frame — live, including during playback or scrubbing — with a configurable **frame offset** so Maya frames map exactly to your InkIt frames (offsets are remembered per scene).
- Go straight to the **next/previous drawing** from Maya, and let InkIt drive or follow playback so review stays in lockstep.
- Sync starts **off** on every launch and is enabled only when you turn it on.

### Export & share
- Export the annotated video as **MP4 / MOV** (H.264 + AAC) with your strokes burned in — option to include or exclude the audio track.
- Export stills/boards as **PNG / JPG / BMP**.
- use autosave files to share/or get back to, incase you forgot to save, it's stroke based saves.
- The last export folder is remembered, so round-trips are quick.

### Organize your shots
- A resizable **shot list / queue** panel for staging multiple clips and scenes, with add, open, save, delete, and thumbnail controls for fast switching.
- Jump straight to the **next / previous shot** with the on-screen buttons or the `Alt+Down` / `Alt+Up` shortcuts — at the list's ends the current clip jumps to its last / first frame instead.
- Load media by drag-and-drop, and double-click an `.inkit` scene or a video file to open it (choose in **Settings ▸ Files** what a double-click does while InkIt is already running: replace the current clip without touching the shot list, add to the shot list and switch, or open a new window).

### Work your way
- Fully **customizable keyboard shortcuts** in the Settings editor — redefine any action to suit your flow.
- **Save your preferences**: colors, brush sizes, opacities, pressure curve, loop behavior, time mode, window layout, recent files — all remembered between sessions.
- A floating **shot/board viewer** and a customizable brush cursor (circle, crosshair, or a custom image) to match how you like to work.
- Reskinnable icons and (for the brave) a clean, single-file codebase you can run directly from source.

---

## Quick start

**Fully compiled, no dependencies:** download the latest `InkIt.exe` from **Releases** (published automatically for every `v*` tag), or grab the `InkIt-exe` artifact from the latest **Actions ▸ Build InkIt.exe** run. Just double-click — that's it.

**Run from source (developers):**
```
python app.py
```
or double-click `Launch InkIt.vbs` / `Launch InkIt.bat` if you're running from a clone.

**Open media:** hit `Ctrl+O` and pick an `.mp4`, `.mov`, or a still image (`.png`/`.jpg`/`.jpeg`/`.bmp`/`.webp`), or drop a file onto the window. Image sequences: `Ctrl+Alt+O` (pick any frame) or drop the folder. Open saved scenes with `Ctrl+Shift+O`.

**Start drawing:** pick the pen, choose a color and size, and draw. Use `Space` to play, `Left`/`Right` to step frames, and `Down`/`Up` to jump between drawings.

---

## Common workflows

- **Animation dailies review** — load a playblast, scrub frame by frame, and sketch poses/breakdown notes with onion-skin context, then export for the team.
- **Maya-assisted review** — keep InkIt synced to Maya's viewport and mark up exactly the frame Maya is showing, jumping drawing-to-drawing as you go.
- **Storyboard / board notes** — open still boards, layer pose and camera notes on top, and save reusable scenes.
- **Shot breakdown and handoff** — queue all the shots for an asset, mark each up, and export annotated passes to share with the pipeline.

---

## Requirements

- **Windows** (64-bit). 
- **Compiled executable:** none — everything is bundled (Python, Qt, OpenCV, and FFmpeg are embedded).
- **From source:** Python 3 with PySide6, OpenCV 4.x (EXR support), numpy, imageio-ffmpeg and QtMultimedia (see `requirements.txt`).
- **Tests:** `QT_QPA_PLATFORM=offscreen python tests/smoke_test.py` (headless; generates its own test clips and sequences).

---

## Building from source

The single self-contained `InkIt.exe` is built with PyInstaller from `InkIt.spec` (onefile; bundles Python, Qt, OpenCV, `ffmpeg.exe` from imageio-ffmpeg, the `icons/` folder, the app icons and the Maya sync scripts).

On Windows, just run:

```
build.bat
```

It creates/uses `.venv`, installs `requirements.txt`, runs a compile check and then `pyinstaller --clean --noconfirm InkIt.spec`. The result is `dist\InkIt.exe`.

CI: `.github/workflows/build.yml` (currently staged as `ci/build.yml` — move it into `.github/workflows/` to enable it) builds the exe on `windows-latest` for every push / pull request (download it from the run's **Artifacts**), runs a headless smoke test, and publishes a GitHub Release with the exe attached when you push a tag such as `v1.2.0`:

```
git tag v1.2.0
git push origin v1.2.0
```

## Notes

- Scene files use the `.inkit` extension and are auto-associated so double-clicking opens InkIt.
- Autosaves are written to `Documents/InkIt/Autosave` and are listed in **File ▸ Autosaves**.
- Settings live in `%APPDATA%\InkIt\settings.json`; icon PNGs can be replaced in the app's icon folder/AppData `icons` directory.
- Light theme is currently disabled — InkIt runs in a clean dark theme (more themes to come).
