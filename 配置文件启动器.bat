@echo off
setlocal

:: 1. 解决中文和路径问题
chcp 65001 >nul
cd /d "%~dp0"

:: 2. 设置虚拟环境路径
set "VENV_NAME=venv"
set "ACTIVATE_PATH=%~dp0%VENV_NAME%\Scripts\activate.bat"

:: 3. 检查环境
if not exist "%ACTIVATE_PATH%" (
    echo [ERROR] 找不到虚拟环境: %ACTIVATE_PATH%
    echo 请确保已创建虚拟环境且文件夹名为 "venv"。
    pause
    exit /b
)

:: 4. 激活并启动编辑器
echo [INFO] 正在启动配置编辑器...
call "%ACTIVATE_PATH%"
python config_editor.py

:: 5. 退出
if %errorlevel% neq 0 (
    echo [ERROR] 编辑器异常退出。
    pause
)
