@echo off
chcp 65001 >nul
echo 正在停止图片压缩器本地服务（端口 8765）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo 已停止（若此前未运行则无需操作）。
pause
