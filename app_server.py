#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片压缩器 - 一体化本地服务（无外部依赖，仅用标准库）。

同时提供：
  1) 静态页面托管：把 index.html 等以 http://127.0.0.1:PORT 提供，
     使浏览器运行在安全上下文（localhost），File System Access API 可用；
  2) /fetch_url?url=... 图片代理：在服务器侧下载图片再回传，
     彻底绕过浏览器 CORS / 防盗链（同源请求，前端无需任何额外配置）；
  3) /ping 健康检查。

双击「启动压缩器.bat」即用 pythonw 无窗口运行本脚本：
  - 若端口已被占用（说明服务已在运行），直接打开浏览器后退出；
  - 否则启动服务并在 1 秒后自动打开浏览器；
  - 设置环境变量 NO_BROWSER=1 可只启动服务、不弹浏览器（用于开机自启）。
"""
import os
import sys
import time
import webbrowser
import threading
import http.server
import socketserver
import urllib.request
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
NO_BROWSER = os.environ.get("NO_BROWSER") == "1"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        if self.path.startswith("/fetch_url") or self.path == "/ping":
            self.send_response(204)
            self._cors()
            self.end_headers()
        else:
            super().do_OPTIONS()

    def do_HEAD(self):
        if self.path == "/ping":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            return
        super().do_HEAD()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/ping":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"pong")
            return
        if parsed.path == "/fetch_url":
            self._fetch(parsed)
            return
        # 其余按静态文件处理（index.html 等在根目录）
        super().do_GET()

    def _fetch(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        url = qs.get("url", [""])[0]
        if not url:
            self.send_error(400, "Missing url parameter")
            return
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_error(502, "Remote returned %s: %s" % (e.code, e.reason))
        except Exception as e:  # 网络错误 / 超时 / 防盗链返回非图等
            self.send_error(502, "Fetch failed: %s" % e)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def open_browser():
    try:
        webbrowser.open("http://127.0.0.1:%d/" % PORT)
    except Exception:
        pass


def main():
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # 端口已被占用：说明服务已在运行，用户主动启动则直接打开浏览器
        if "Address already in use" in str(e) or getattr(e, "winerror", 0) == 10048:
            open_browser()
            sys.exit(0)
        raise

    print("图片压缩器本地服务已启动： http://127.0.0.1:%d/" % PORT)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(1.0)  # 等服务真正开始监听后再开浏览器
    if not NO_BROWSER:
        open_browser()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
