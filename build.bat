@echo off
REM Builds dist\PingRate.exe (the app) and dist\PingRateEchoServer.exe (optional echo server).
REM Needs Python 3.10+ on PATH.
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name PingRate ^
  --exclude-module pyqtgraph.examples ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia --exclude-module tkinter ^
  run.py || exit /b 1
python -m PyInstaller --noconfirm --onefile --console --name PingRateEchoServer tools\echo_server.py || exit /b 1
echo.
echo Done: dist\PingRate.exe  and  dist\PingRateEchoServer.exe
