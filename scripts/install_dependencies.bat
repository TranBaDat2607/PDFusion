@echo off
echo ========================================
echo PDFusion Dependencies Installer (Windows)
echo ========================================

echo.
echo Creating new Python environment...
conda create -n pdfusion-env python=3.11 -y
if %errorlevel% neq 0 (
    echo Failed to create environment
    pause
    exit /b 1
)

echo.
echo Activating environment...
call conda activate pdfusion-env

echo.
echo Upgrading pip...
python -m pip install --upgrade pip

echo.
echo Installing dependencies in stages...
python install_dependencies.py

echo.
echo ========================================
echo Installation completed!
echo ========================================
echo.
echo To use PDFusion:
echo 1. conda activate pdfusion-env
echo 2. python main.py
echo.
pause
