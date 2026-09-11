@echo off
setlocal
cd /d "%~dp0"
title Stock Shocker

if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  echo Frontend dependencies are missing. Please run install.bat first.
  pause
  exit /b 1
)

echo Starting Stock Shocker backend...
start "Stock Shocker Backend" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

timeout /t 3 /nobreak >nul

echo Starting Stock Shocker frontend...
start "Stock Shocker Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev -- --host 127.0.0.1 --port 5173"

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5173"

echo.
echo Stock Shocker is starting.
echo Frontend: http://127.0.0.1:5173
echo Backend:  http://127.0.0.1:8000
echo.
