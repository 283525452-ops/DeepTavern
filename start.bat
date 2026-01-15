@echo off
:: 1. 切换到脚本所在目录 (使用引号包裹以处理空格和括号)
cd /d "%~dp0"

:: 2. 调试信息：显示当前路径
echo Current Dir: "%cd%"

:: 3. 简单粗暴地检测并激活
if exist "venv\Scripts\activate.bat" (
    echo Found venv, activating...
    call "venv\Scripts\activate.bat"
    echo [SUCCESS] Environment Activated.
) else (
    echo [ERROR] venv\Scripts\activate.bat NOT FOUND.
    echo Please check folder name.
    pause
)

:: 4. 无论成功失败，都强制停留在命令行，防止闪退
cmd /k
