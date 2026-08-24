@echo off
setlocal
cd /d "%~dp0"
echo Building standalone InkIt executable...
call ..\.venv\Scripts\activate.bat
pyinstaller --clean --noconfirm --distpath . --workpath build --specpath . InkIt.spec
if %ERRORLEVEL% equ 0 (
    echo.
    echo ========================================================
    echo Build successful! Executable is at: InkIt.exe
    echo ========================================================
) else (
    echo Build failed with error %ERRORLEVEL%
)
pause
