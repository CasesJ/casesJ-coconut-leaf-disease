@echo off
REM OpenVINO Quick Setup Script for Windows
REM This script sets up and tests the OpenVINO real-time inference

echo.
echo ========================================
echo  OpenVINO Real-Time Setup for Windows
echo ========================================
echo.

REM Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://www.python.org
    pause
    exit /b 1
)

echo [1] Checking Python version...
python --version
echo.

REM Check if requirements.txt exists
if not exist "models\requirements.txt" (
    echo [ERROR] models\requirements.txt not found
    echo Please ensure you're in the project root directory
    pause
    exit /b 1
)

echo [2] Installing dependencies...
echo (This may take a few minutes)
echo.
python -m pip install -q -r models\requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo.
echo [OK] Dependencies installed successfully
echo.

REM Check model files
echo [3] Checking model files...
if not exist "best_openvino_model\best.xml" (
    echo [ERROR] best_openvino_model\best.xml not found
    pause
    exit /b 1
)
if not exist "best_openvino_model\best.bin" (
    echo [ERROR] best_openvino_model\best.bin not found
    pause
    exit /b 1
)
echo [OK] Model files found
echo.

REM Run setup verification script
echo [4] Running verification tests...
python setup_openvino.py
if errorlevel 1 (
    echo [ERROR] Verification failed
    pause
    exit /b 1
)

echo.
echo ========================================
echo.  SETUP COMPLETE!
echo.
echo Next steps:
echo 1. Start the API server:
echo    python main.py
echo.
echo 2. Or test real-time detection:
echo    python realtime_stream.py
echo.
echo 3. For more examples:
echo    See OPENVINO_EXAMPLES.md
echo.
echo ========================================
echo.
pause
