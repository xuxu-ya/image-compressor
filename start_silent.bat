@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 最小化后台启动图片压缩后端服务（不自动开浏览器，避免每次登录都弹网页）
start "图片压缩服务" /min "%~dp0venv\Scripts\python.exe" app.py
exit
