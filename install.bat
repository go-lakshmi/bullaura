@echo off
setlocal
cd /d "%~dp0"
title Stock Shocker Installer
echo.
echo ============================================
echo       STOCK SHOCKER - ONE CLICK INSTALL
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found.
  echo Install Python 3.11 or newer and enable "Add Python to PATH".
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js/npm was not found.
  echo.
  where winget >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Node.js/npm is missing and Windows Package Manager (winget) is not available.
    echo Please install Node.js LTS manually, then run this installer again.
    pause
    exit /b 1
  )
  echo Installing Node.js LTS automatically...
  winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo ERROR: Automatic Node.js installation failed.
    echo Please install Node.js LTS manually, then run this installer again.
    pause
    exit /b 1
  )
  rem Refresh PATH for this installer process.
  if exist "C:\Program Files\nodejs\" set "PATH=C:\Program Files\nodejs;%PATH%"
  where npm >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Node.js was installed but npm is not visible yet.
    echo Close this window, open a new Command Prompt, and run install.bat again.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating Python environment...
  python -m venv .venv
) else (
  echo [1/4] Python environment already exists.
)

call ".venv\Scripts\activate.bat"
echo [2/4] Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: Python dependency installation failed.
  pause
  exit /b 1
)

echo [3/4] Installing frontend dependencies...
cd frontend
call npm install
if errorlevel 1 (
  echo ERROR: Frontend dependency installation failed.
  cd ..
  pause
  exit /b 1
)
call npm run build
cd ..

echo [4/4] Preparing local configuration...
if not exist ".env" copy ".env.example" ".env" >nul
if not exist "data" mkdir data

echo.
echo ============================================
echo INSTALLATION COMPLETE
echo ============================================
echo.
echo Double-click START.BAT to start the dashboard.
echo.
pause
