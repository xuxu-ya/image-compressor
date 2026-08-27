@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 URL 下载代理（用于绕过防盗链/CORS）...
"%~dp0venv\Scripts\python.exe" url_proxy.py
pause
