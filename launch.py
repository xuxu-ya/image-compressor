#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片压缩器 - 一键启动器（无窗口）

单次点击即可完成：检测服务 → 未运行则静默启动 → 轮询就绪 → 打开浏览器。
全程不弹任何控制台 / 黑窗口，不做任何确认，点完即用。

由桌面「图片压缩器.vbs」以 pythonw 方式调用（无窗口）。
"""
import os
import sys
import time
import shutil
import webbrowser
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
PING_URL = f"http://127.0.0.1:{PORT}/ping"
APP_URL = f"http://127.0.0.1:{PORT}/"
PYTHONW = os.path.join(HERE, "venv", "Scripts", "pythonw.exe")
APP_SERVER = os.path.join(HERE, "app_server.py")
CREATE_NO_WINDOW = 0x08000000


def ping():
    """服务是否就绪。"""
    try:
        urllib.request.urlopen(PING_URL, timeout=1.0)
        return True
    except Exception:
        return False


def resolve_pythonw():
    """优先用项目 venv 的 pythonw，缺失则回退系统 pythonw/python。"""
    if os.path.exists(PYTHONW):
        return PYTHONW
    for cand in ("pythonw.exe", "python.exe"):
        p = shutil.which(cand)
        if p:
            return p
    return PYTHONW  # 最后兜底，交给 subprocess 报错


def ensure_server():
    """确保服务运行：已在运行则直接返回；否则静默后台启动并轮询就绪。"""
    if ping():
        return True
    py = resolve_pythonw()
    env = os.environ.copy()
    env["NO_BROWSER"] = "1"  # 由本启动器统一打开浏览器，避免重复弹窗
    try:
        subprocess.Popen(
            [py, APP_SERVER],
            cwd=HERE,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        # 极端回退：直接拖当前（无窗口的 pythonw）进程拉起
        subprocess.Popen(
            ["pythonw", APP_SERVER],
            cwd=HERE,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    # 轮询就绪，最多等 12 秒（每 0.2 秒一次，服务一好就开）
    deadline = time.time() + 12.0
    while time.time() < deadline:
        time.sleep(0.2)
        if ping():
            return True
    return ping()


def main():
    ensure_server()  # 无论成功与否都尝试打开，让用户看到明确状态
    try:
        webbrowser.open(APP_URL)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
