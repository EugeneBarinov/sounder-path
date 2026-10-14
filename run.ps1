# SeaPath ECDIS PowerShell Launcher
Set-Location -Path $PSScriptRoot
$python = if (Test-Path ".\venv\Scripts\python.exe") { ".\venv\Scripts\python.exe" } else { "python" }
Write-Host "[INFO] Запуск SeaPath ECDIS на http://127.0.0.1:8000..." -ForegroundColor Cyan
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
