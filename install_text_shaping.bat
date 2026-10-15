@echo off
title HubGPT - Hindi text drawer
cd /d "%~dp0"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
if not exist logs mkdir logs
echo Installing uharfbuzz + freetype-py into the video engine (engines\video\.venv) ...
"engines\video\.venv\Scripts\python.exe" -m pip install uharfbuzz freetype-py > logs\text_shaping.log 2>&1
"engines\video\.venv\Scripts\python.exe" engines\video\engine\text_selftest.py >> logs\text_shaping.log 2>&1
type logs\text_shaping.log
echo.
pause
