#!/usr/bin/env python3
"""本地 URL 下载代理（仅用于绕过浏览器 CORS/防盗链限制）。
启动后监听 127.0.0.1:8001，前端自动在直接下载失败时回退到此代理。
"""
import http.server
import socketserver
import urllib.request
import urllib.parse
import sys

PORT = 8001

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)


class Handler(http.server.BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/ping":
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/ping":
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"pong")
            return
        if parsed.path != "/fetch_url":
            self.send_error(404, "Only /fetch_url is supported")
            return
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
                self._cors_headers()
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_error(502, f"Remote returned {e.code}: {e.reason}")
        except Exception as e:
            self.send_error(502, f"Fetch failed: {e}")

    def log_message(self, format, *args):
        # 打印到控制台，方便用户排查
        sys.stderr.write("%s - - [%s] %s\n" % (
            self.address_string(),
            self.log_date_time_string(),
            format % args,
        ))


if __name__ == "__main__":
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"URL proxy running at http://127.0.0.1:{PORT}")
        print("按 Ctrl+C 停止（请勿关闭此窗口，否则图片链接模式会失效）")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)


def make_server(host="127.0.0.1", port=PORT):
    """供 run_proxy.py 复用：返回一个已绑定但未启动的 TCPServer 实例。"""
    return socketserver.TCPServer((host, port), Handler)

