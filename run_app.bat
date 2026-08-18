@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 图片压缩小工具

set "VENV_PY=%~dp0venv\Scripts\python.exe"

rem ---- 1) 优先使用本目录下的 venv ----
if exist "%VENV_PY%" goto havepy

rem ---- 2) 依次尝试 py / python / python3 ----
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD where python3 >nul 2>nul && set "PYCMD=python3"
if not defined PYCMD (
  echo [错误] 未检测到 Python。
  echo 请先安装 Python 3.10 或更高版本，安装时务必勾选 "Add python.exe to PATH"：
  echo   https://www.python.org/downloads/
  pause
  exit /b 1
)

echo 首次运行：创建虚拟环境 ...
%PYCMD% -m venv venv
if errorlevel 1 (
  echo [错误] 创建虚拟环境失败，请检查 Python 安装后重试。
  pause
  exit /b 1
)

:havepy
"%VENV_PY%" -c "import PIL,numpy" >nul 2>nul
if errorlevel 1 (
  echo 首次运行：安装依赖 Pillow + numpy（约 1 分钟，请耐心等待）...
  "%VENV_PY%" -m pip install -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重新双击本文件。
    pause
    exit /b 1
  )
)

echo 正在启动图片压缩小工具 ...
echo 浏览器将自动打开 http://127.0.0.1:8000 （关闭本窗口即停止服务）
start "" http://127.0.0.1:8000
"%VENV_PY%" app.py
pause
