@echo off
REM ============================================================================
REM  EMC Test Workflow & Datasheet Generator - one-click launcher (Windows)
REM  Double-click this file. It sets up everything needed and starts the app.
REM  If something is missing it stops and tells you exactly what to fix.
REM ============================================================================
setlocal enableextensions
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

echo ============================================================
echo   EMC Test Workflow ^& Datasheet Generator
echo ============================================================
echo.

REM --- 1. Find a suitable Python (3.11+) ------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [BLOCKED] Python was not found on this computer.
  echo   Install Python 3.11 or newer from:
  echo       https://www.python.org/downloads/
  echo   During install, tick "Add python.exe to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" 2>nul
if errorlevel 1 (
  echo [BLOCKED] Python 3.11 or newer is required. Found:
  %PY% --version
  echo   Install a newer version from https://www.python.org/downloads/ and re-run.
  echo.
  pause
  exit /b 1
)

REM --- 2. Create the virtual environment if it does not exist ---------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating the virtual environment ^(.venv^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [BLOCKED] Could not create the virtual environment.
    echo   Your Python may be missing the 'venv' module. Reinstall Python and re-run.
    echo.
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"

REM --- 3. Install / update dependencies (downloads them on first run) --------
echo Installing dependencies ^(first run downloads packages; later runs are quick^)...
"%VPY%" -m pip install --upgrade pip >nul 2>nul
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [BLOCKED] Failed to install the Python dependencies.
  echo   - Check your internet connection.
  echo   - Read the pip error shown above for the exact package that failed.
  echo.
  pause
  exit /b 1
)

REM --- 4. Make sure a .env exists (database settings) -----------------------
if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo [ACTION NEEDED] A new .env file was just created from .env.example.
    echo   Open ".env" in a text editor and set your MySQL details:
    echo       MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
    echo   Save it, then run this file again.
    echo.
    pause
    exit /b 1
  ) else (
    echo [BLOCKED] No ".env" file found and no ".env.example" to copy from.
    echo   Create a ".env" with MYSQL_HOST/PORT/USER/PASSWORD/DATABASE and re-run.
    echo.
    pause
    exit /b 1
  )
)

REM --- 5. Pre-flight checks (MySQL reachable, database exists, port free) ----
echo.
"%VPY%" preflight.py
if errorlevel 1 (
  echo   Fix the issue shown above, then run this file again.
  echo.
  pause
  exit /b 1
)

REM --- 6. Launch the app -----------------------------------------------------
echo.
echo ------------------------------------------------------------
echo   Starting the server. Open your browser at:
echo       http://localhost:3000
echo   Keep this window open. Press Ctrl+C here to stop the server.
echo ------------------------------------------------------------
echo.
"%VPY%" app.py

echo.
echo The server has stopped.
pause
