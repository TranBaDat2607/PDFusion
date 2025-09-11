@echo off
echo Setting up Desktop PDF Translator development environment...
echo.

REM Check Python version
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo Setup complete!
echo.
echo Next steps:
echo 1. Option A: Copy .env.example to .env and add your API keys
echo 2. Option B: Run setup_api_keys.bat to set environment variables
echo 3. Run: python main.py
echo.
pause