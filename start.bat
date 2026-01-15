@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo        DeepTavern Launcher
echo ========================================================

REM 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Please install Python 3.10+
    pause
    exit /b
)

REM 2. Create Virtual Environment
if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating venv...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b
    )
)

REM 3. Install Dependencies
".\venv\Scripts\python.exe" -c "import uvicorn" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    ".\venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo [ERROR] Install failed. Check network.
        pause
        exit /b
    )
)

REM 4. Launch
echo [INFO] Starting...
".\venv\Scripts\python.exe" main.py

echo [INFO] Stopped.
pause
