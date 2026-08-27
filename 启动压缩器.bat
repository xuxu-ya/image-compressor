@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

:: 优先使用项目本地 venv 的 pythonw（无控制台窗口），依次回退
set PY="%~dp0venv\Scripts\pythonw.exe"
if not exist %PY% set PY=pythonw.exe
if not exist %PY% set PY=python.exe

:: 启动本地服务（自动打开浏览器；若已在运行则直接打开浏览器）
start "" %PY% "%~dp0app_server.py"
