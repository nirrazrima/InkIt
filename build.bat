@echo off
rem build.bat — builds dist\InkIt.exe (single self-contained exe) with PyInstaller.
rem Creates/uses .venv next to this file so "Launch InkIt.bat" keeps working too.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail
python -m pip install --upgrade pip || goto :fail
python -m pip install -r requirements.txt || goto :fail
python -m py_compile app.py maya_inkit_sync.py inkit_bootstrap.py || goto :fail
pyinstaller --clean --noconfirm InkIt.spec || goto :fail
echo.
echo Built: %~dp0dist\InkIt.exe
exit /b 0
:fail
echo.
echo BUILD FAILED
exit /b 1
