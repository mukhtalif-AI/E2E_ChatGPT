@echo off
setlocal enabledelayedexpansion

:: Define the name of the output file
set "OUTPUT_FILE=consolidated_code.py"

:: Delete the output file if it already exists so we start fresh
if exist "%OUTPUT_FILE%" del "%OUTPUT_FILE%"

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
pause