@echo off
chcp 65001 >nul
title HubGPT  (keep open - close to stop the hub)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=E:\ComfyUI_windows_portable\python_embeded\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "engines\voice\libs\cb" (echo. & echo   First time? Run setup_hub.bat once - the voice engine is not set up yet. & echo.)
"%PY%" -s -u app\server.py %*
echo.
pause
