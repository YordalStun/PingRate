@echo off
REM Builds dist\PingRate.exe (single file, no console). Needs Python 3.10+ on PATH.
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name PingRate ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia --exclude-module tkinter ^
  run.py || exit /b 1
copy /Y tools\echo_server.py dist\echo_server.py >nul
echo Done: dist\PingRate.exe
