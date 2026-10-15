@echo off
title English narrator - setup
cd /d "%~dp0"
set /p PY=<python.txt
"%PY%" -s run.py setup
pause
