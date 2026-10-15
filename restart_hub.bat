@echo off
title Hub restart
cd /d "%~dp0"
rem never cut a running job short: ask first
set RUN=0
for /f %%c in ('curl -s http://127.0.0.1:8781/api/jobs ^| find /c """running"""') do set RUN=%%c
if not "%RUN%"=="0" (
  echo.
  echo   A hub job is running right now - restarting would stop it.
  choice /m "  Restart anyway"
  if errorlevel 2 exit
)
taskkill /FI "WINDOWTITLE eq HubGPT  (keep open*" /T /F >nul 2>&1
rem also stop whatever still holds the hub port (an older hub window)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8781 " ^| findstr LISTENING') do taskkill /PID %%p /T /F >nul 2>&1
timeout /t 2 /nobreak >nul
start "" "%~dp0hub.bat"
exit
