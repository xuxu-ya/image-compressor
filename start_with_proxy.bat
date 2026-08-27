@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: 检查端口 8001 是否已被占用
powershell -NoProfile -Command "try { $c=New-Object System.Net.Sockets.TcpClient('127.0.0.1',8001); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    echo 代理已在运行，直接打开工具...
    goto OPEN
)

echo 正在启动 URL 下载代理（解决图片链接 CORS/防盗链问题）...

:: 优先使用项目本地 venv，找不到则回退到系统 python
set PYTHON=%~dp0venv\Scripts\python.exe
if not exist "%PYTHON%" (
    set PYTHON=python.exe
)

:: 窗口保持可见（/min 会让用户误以为没启动或随手关闭）
start "" "%PYTHON%" "%~dp0url_proxy.py"

:: 等待代理就绪（最多 10 秒）
echo 等待代理启动...
for /l %%i in (1,1,20) do (
    powershell -NoProfile -Command "try { $c=New-Object System.Net.Sockets.TcpClient('127.0.0.1',8001); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel!==0 goto OPEN
    ping -n 1 -w 500 127.0.0.1 >nul
)
echo 代理启动超时，请检查 Python 环境后重试。
pause
exit /b 1

:OPEN
start "" "%~dp0index.html"
