@echo off
chcp 65001 >nul
title SukoonVoice - setup
rem ------------------------------------------------------------------------------------------------
rem  Makes this project ready on any PC. Copy the whole folder, edit config.json if the drives differ,
rem  run this. Picks a Python:
rem    1  a SHARED Python that already has PyTorch + CUDA, e.g. ComfyUI's - listed in config.json
rem    2  its OWN Python + PyTorch in runtime\ - about 4.5 GB, only inside this folder
rem  The choice is saved in python.txt. Then installs this project's libraries into libs\cb, checks the
rem  graphics card + ffmpeg, offers the model downloads. Asks before every download. Nothing on C:.
rem ------------------------------------------------------------------------------------------------
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "RT=%~dp0runtime"
set "OWN=%RT%\venv\Scripts\python.exe"
set "PY="
if exist python.txt set /p PY=<python.txt
if defined PY if not exist "%PY%" set "PY="
if defined PY goto run

set "SHARED="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$c = Get-Content '%~dp0config.json' -Raw | ConvertFrom-Json; @($c.python) + @($c.shared_pythons) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1"`) do set "SHARED=%%p"
if exist "%OWN%" set "SHARED=%OWN%"
if not defined SHARED goto noshared

echo.
echo   Python found: %SHARED%
echo     1  use it - recommended, no PyTorch download
echo     2  build this project's OWN Python + PyTorch in runtime\  - about 4.5 GB
set /p CH="  choose 1 or 2 [1]: "
if "%CH%"=="2" goto own
set "PY=%SHARED%"
goto save

:noshared
echo.
echo   No Python with PyTorch found - add yours to "shared_pythons" in config.json, or:
set /p CH="  build this project's OWN Python + PyTorch in runtime\  - about 4.5 GB? [Y/n]: "
if /i "%CH%"=="n" exit /b 1

:own
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%RT%\python"
if not exist "%RT%" mkdir "%RT%"
if not exist "%RT%\uv.exe" (
  echo   downloading uv - the Python installer, 20 MB ...
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%RT%\uv.zip'; Expand-Archive '%RT%\uv.zip' '%RT%' -Force"
)
if not exist "%RT%\uv.exe" (echo   !! could not get uv & pause & exit /b 1)
"%RT%\uv.exe" venv "%RT%\venv" --python 3.12
"%RT%\uv.exe" pip install --python "%OWN%" pip numpy transformers huggingface-hub safetensors
echo   installing PyTorch with CUDA 12.8 - about 3 GB ...
"%RT%\uv.exe" pip install --python "%OWN%" torch torchaudio --index-url https://download.pytorch.org/whl/cu128
set "PY=%OWN%"

:save
> python.txt echo %PY%

:run
echo   using %PY%
"%PY%" -s -u run.py setup %*
echo.
pause
