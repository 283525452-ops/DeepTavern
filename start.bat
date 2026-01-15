@echo off
setlocal
title DeepTavern Launcher

:: 1. 切换目录 (失败跳转错误)
cd /d "%~dp0"
if %errorlevel% neq 0 goto ERROR

echo ========================================================
echo        DeepTavern One-Click Launcher
echo ========================================================

:: 2. 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found or not in PATH.
    goto ERROR
)

:: 3. 创建环境 (如果不存在)
if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 goto ERROR
)

:: 4. 检查/安装依赖
".\venv\Scripts\python.exe" -c "import uvicorn" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    ".\venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 goto ERROR
)

:: 5. 启动主程序
echo.
echo [INFO] Launching DeepTavern...
echo.
".\venv\Scripts\python.exe" main.py

:: 6. 程序正常结束
echo.
echo [INFO] Server stopped.
cmd /k
exit /b

:: ========================================================
:: 错误处理区 (出错会跳到这里，绝不闪退)
:: ========================================================
:ERROR
echo.
echo ========================================================
echo [FATAL ERROR] Something went wrong!
echo ========================================================
echo.
echo Please check the error message above.
echo.
pause
cmd /k
