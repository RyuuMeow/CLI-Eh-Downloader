@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [WARNING] Virtual environment ^(venv^) not found!
    choice /C YN /M "Do you want to create it and install dependencies now"
    if errorlevel 2 (
        echo Setup cancelled. Exiting...
        pause
        exit /b 1
    )
    
    echo.
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Ensure python is installed.
        pause
        exit /b 1
    )
    
    echo Installing dependencies...
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -e .
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    
    echo.
    echo Setup completed successfully!
    echo ----------------------------------------
)

"%~dp0venv\Scripts\python.exe" -m cli_eh_downloader %*

if %errorlevel% neq 0 (
    pause
    exit /b %errorlevel%
)
if "%~1"=="" (
    pause
)
