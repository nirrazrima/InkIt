# InkIt

Local video annotation player for MP4 and MOV.

## Standalone Executable

The standalone single executable is located in:
```
dist\InkIt.exe
```
This is a **100% self-contained single file** that includes Python, Qt6, OpenCV, and embedded FFmpeg. You can copy `InkIt.exe` to any Windows machine and run it directly without installing Python, FFmpeg, or any other dependencies.

## Run from Source

Double-click `Launch InkIt.vbs` (no command window) or `Launch InkIt.bat`.

## Rebuild Executable

Run `build.bat` or run:
```bat
pyinstaller --clean --noconfirm InkIt.spec
```

## Notes & File Associations

Notes save as `clipname.inkit`. Double-click an `.inkit` file to open it in InkIt.
Replace `icon.png` or `icon.ico` in this folder to change the app icon.
