@echo off
setlocal

:: 1. 解决中文乱码问题
chcp 65001 >nul

:: 2. 自动切换到脚本所在目录
cd /d "%~dp0"

:: 3. 设置虚拟环境路径 (假设文件夹名为 venv)
set "VENV_DIR=venv"
set "ACTIVATE_SCRIPT=%VENV_DIR%\Scripts\activate.bat"

:: 4. 检查虚拟环境是否存在
if not exist "%ACTIVATE_SCRIPT%" (
    echo [ERROR] 找不到虚拟环境启动脚本: %ACTIVATE_SCRIPT%
    echo 请确保已运行 "python -m venv %VENV_DIR%" 创建环境。
    pause
    exit /b 1
)

:: 5. 激活环境
call "%ACTIVATE_SCRIPT%"

:: 6. 启动后端
echo [INFO] 正在启动 DeepTavern 后端...
python main.py

:: 7. 如果程序异常退出，暂停显示错误信息
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 后端服务异常退出 (Exit Code: %errorlevel%)
    pause
)
