@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo  图片压缩器 - 诊断与修复工具
echo  如遇 ERR_EMPTY_RESPONSE / 无反应 请双击运行本工具
echo ==========================================
echo.

:: 优先使用项目本地 venv 的 python
set PY="%~dp0venv\Scripts\python.exe"
if not exist %PY% set PY="%~dp0venv\Scripts\pythonw.exe"
if not exist %PY% set PY=python.exe

%PY% "%~dp0diagnose_compressor.py"

pause
