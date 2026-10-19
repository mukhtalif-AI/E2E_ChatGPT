@echo off
chcp 65001 >nul
title HubGPT Experimental UI (port 8782)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "HUB_PORT=8782"
set "PY=E:\ComfyUI_windows_portable\python_embeded\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "engines\voice\libs\cb" (echo. & echo   First time? Run setup_hub.bat once - the voice engine is not set up yet. & echo.)
echo Experimental copy: http://127.0.0.1:8782/experimental
echo This copy uses the configured HubGPT media/output folders.
"%PY%" -s -u app\server.py %*
echo.
pause
