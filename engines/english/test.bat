@echo off
title English narrator - test
cd /d "%~dp0"
set /p PY=<python.txt
if not exist logs mkdir logs
"%PY%" -s -u run.py test %* > logs\test.txt 2>&1
type logs\test.txt
