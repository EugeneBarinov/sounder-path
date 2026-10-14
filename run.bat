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
echo [INFO] Starting SeaPath on http://127.0.0.1:8000 ...

"%PYTHON_EXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Server exited with code %ERRORLEVEL%
    pause
)
