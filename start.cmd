@echo off
setlocal
cd /d "%~dp0"

rem 1. 启动前初始化环境（仅耗时 1~2 秒，完成后 PowerShell 立即退出）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -SetupOnly %*
if errorlevel 1 (
    echo.
    echo [FrameLab] 启动前环境检查或构建失败。
    pause
    exit /b 1
)

rem 2. 原生 Python 常驻运行控制台，关机时平滑退出，无 powershell.exe 崩溃弹框
"%~dp0.venv\Scripts\python.exe" "%~dp0server.py" --reload --open-browser --with-worker %*

