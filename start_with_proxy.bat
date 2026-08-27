@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

:: 优先使用项目本地 venv，找不到则回退到系统 python
set PY="%~dp0venv\Scripts\python.exe"
if not exist %PY% set PY=python.exe

echo 正在启动 URL 代理并打开压缩工具（请保持此窗口开启）...
%PY% "%~dp0run_proxy.py"
if errorlevel 1 (
    echo.
    echo 启动失败：未找到 Python 或 run_proxy.py 运行出错。
    echo 请确认 venv\Scripts\python.exe 存在，或已安装 Python 并加入 PATH。
    pause
)
