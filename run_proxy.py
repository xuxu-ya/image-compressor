#!/usr/bin/env python3
"""一键启动器：启动 URL 代理 + 打开压缩工具，全部跑在【这一个窗口】里，常驻不闪退。
双击 start_with_proxy.bat 即调用本脚本。关闭此窗口即停止代理。
"""
import os
import sys
import time
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from url_proxy import make_server, PORT

BROWSER_DELAY = 1.2  # 等代理真正监听后再开浏览器


def main():
    srv = make_server()
    t = threading.Thread(target=srv.serve_forever, daemon=False)
    t.start()

    # 让代理先起来，再开浏览器，避免页面一加载就 ping 不到
    time.sleep(BROWSER_DELAY)

    target = os.path.join(HERE, "index.html")
    try:
        os.startfile(target)  # Windows：用默认程序（浏览器）打开
    except Exception:
        webbrowser.open(target)

    print("=" * 52)
    print(f"  URL 代理已在 http://127.0.0.1:{PORT} 运行")
    print("  压缩工具已在浏览器打开，现在可直接用「图片链接」模式")
    print("  【请勿关闭此窗口】—— 关闭后图片链接模式会失效")
    print("=" * 52)

    try:
        t.join()  # 代理线程常驻，主线程一并阻塞，窗口保持打开
    except KeyboardInterrupt:
        srv.shutdown()
        print("\n已停止代理。")


if __name__ == "__main__":
    main()
