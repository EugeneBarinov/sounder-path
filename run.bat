@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo SeaPath ECDIS - Maritime Navigation Server Launcher
echo ========================================================

set PYTHON_EXE=python
if exist "venv\Scripts\python.exe" set PYTHON_EXE=venv\Scripts\python.exe

echo [INFO] Python: %PYTHON_EXE%
echo [INFO] Starting SeaPath on http://localhost:8000 (LAN/Wi-Fi: http://0.0.0.0:8000) ...
echo [INFO] NMEA 0183 UDP receiver active on port 10110

"%PYTHON_EXE%" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Server exited with code %ERRORLEVEL%
    pause
)
