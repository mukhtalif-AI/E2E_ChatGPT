@echo off
setlocal
title HubGPT - Update all code snapshots
cd /d "%~dp0"
pushd "%~dp0engines\voice\voice"
call Consolidate_code.bat --no-pause
set "UPDATE_RC=%ERRORLEVEL%"
popd
if not "%UPDATE_RC%"=="0" (
  echo Voice source consolidation failed. The existing code snapshots were kept.
  pause
  exit /b 1
)
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
