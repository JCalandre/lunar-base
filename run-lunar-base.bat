@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo Virtual environment not found. Run setup.bat first.
    exit /b 1
)

call .venv\Scripts\activate.bat

rem Bind address / port are resolved in Python (web/config.py): by default it
rem auto-detects this PC's LAN IP so the app is reachable from other devices and
rem is NOT served on 127.0.0.1. Override with LUNAR_BASE_HOST / LUNAR_BASE_PORT
rem (e.g. set LUNAR_BASE_HOST=127.0.0.1 for this-PC-only). See README.
python -m web
endlocal
