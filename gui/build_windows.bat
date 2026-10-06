@echo off
REM Build a single-file Windows executable of the dimmer GUI.
REM Requires: Python 3.10+ from python.org (with tkinter), internet for pip.
REM Result: dist\PT4112Dimmer.exe
setlocal
cd /d "%~dp0"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

python -m PyInstaller --noconfirm --clean PT4112Dimmer.spec
if errorlevel 1 goto :fail

echo.
echo ==== Build OK: %~dp0dist\PT4112Dimmer.exe
pause
exit /b 0

:fail
echo.
echo ==== Build FAILED
pause
exit /b 1
