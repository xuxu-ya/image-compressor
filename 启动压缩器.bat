@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

:: 一键启动器：检测→静默启动→就绪后打开浏览器（无黑窗口、无确认）
:: 优先使用项目本地 venv 的 pythonw，依次回退
set PY="%~dp0venv\Scripts\pythonw.exe"
if not exist %PY% set PY=pythonw.exe
if not exist %PY% set PY=python.exe

start "" %PY% "%~dp0launch.py"
