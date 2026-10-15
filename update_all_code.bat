@echo off
setlocal
title HubGPT - Update all code snapshots
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_all_code.ps1"
if errorlevel 1 (
  echo.
  echo Code snapshot update failed. The previous snapshots were kept.
  pause
  exit /b 1
)
echo.
echo Code snapshots updated successfully.
pause
