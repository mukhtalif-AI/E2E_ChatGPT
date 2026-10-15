@echo off
title HubGPT - get elements
cd /d "%~dp0"
if not exist logs mkdir logs
"E:\Program_Files\python312\python.exe" -u app\elements_get.py > logs\elements_get.log 2>&1
echo   preparing new clips (seamless grey loops) ... >> logs\elements_get.log
"engines\video\.venv\Scripts\python.exe" -u engines\video\engine\elements_prep.py >> logs\elements_get.log 2>&1
type logs\elements_get.log
echo.
pause
