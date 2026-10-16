@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Define the name of the output file
set "OUTPUT_FILE=consolidated_code.py"

:: Preserve the previous merged source before rebuilding it.
if exist "%OUTPUT_FILE%" (
    for /f %%i in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%i"
    copy /y "%OUTPUT_FILE%" "%OUTPUT_FILE%.bak_!STAMP!" >nul
)
type nul > "%OUTPUT_FILE%"

echo Consolidating Python files into %OUTPUT_FILE%...
echo.

:: Loop through all .py files in the current folder and all subfolders (/r)
for /r %%i in (*.py) do (
    :: Prevent the script from copying the output file into itself
    if not "%%~nxi"=="%OUTPUT_FILE%" (
        echo Merging: %%~nxi
        
        :: Add a header block with the filename to the output file
        echo # ========================================== >> "%OUTPUT_FILE%"
        echo # File: %%~nxi >> "%OUTPUT_FILE%"
        echo # Path: %%i >> "%OUTPUT_FILE%"
        echo # ========================================== >> "%OUTPUT_FILE%"
        
        :: Append the contents of the Python file
        type "%%i" >> "%OUTPUT_FILE%"
        
        :: Add spacing at the end of each file
        echo. >> "%OUTPUT_FILE%"
        echo. >> "%OUTPUT_FILE%"
    )
)

echo.
echo Done! All Python files have been combined.
if /i not "%~1"=="--no-pause" pause
