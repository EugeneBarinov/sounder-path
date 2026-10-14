@echo off
chcp 65001 > nul
echo ========================================================
echo SeaPath ECDIS - Запуск навигационного сервера
echo ========================================================
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo [INFO] Запуск Uvicorn на http://127.0.0.1:8000 ...
%PYTHON_EXE% -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
pause
