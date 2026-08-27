@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY="%~dp0venv\Scripts\python.exe"
if not exist %PY% set PY=python.exe
echo 正在启动 URL 下载代理（用于绕过防盗链/CORS），请保持此窗口开启...
%PY% "%~dp0url_proxy.py"
if errorlevel 1 pause
