@echo off
chcp 65001 >nul
title HubGPT - setup
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=E:\ComfyUI_windows_portable\python_embeded\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -s -u app\setup_hub.py
echo.
pause
