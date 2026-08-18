"""
图片压缩小应用 —— 本地 Web 服务
启动: python app.py  (默认 http://127.0.0.1:8000)
仅依赖 Pillow + numpy（标准库 + 这两个第三方包）。
支持：
  1) 单张上传：前端把文件作为原始 body POST 到 /compress
  2) 图片链接：POST /compress?url=<图片URL>，服务端自动下载后压缩
  3) 批量：POST /batch（multipart/form-data），含文件夹文件 + 多行URL + CSV表格
参数（target/scale/width/height/max_colors/quality/allow_frame_skip/dither）放在 query string 或 params(JSON)。
"""
import os
import sys
import io
import json
import zipfile
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from compress import compress_image  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000

EXT = {"GIF": ".gif", "PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp",
       "BMP": ".bmp", "TIFF": ".tiff", "UNKNOWN": ".img"}


def _ext(fmt):
    return EXT.get((fmt or "UNKNOWN").upper(), ".img")


def _looks_like_image(data, ct):
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return True
    return (ct or "").lower().startswith("image/")


def _parse_opts(qs, prefix=""):
    def num(key, default):
        try:
            return float(qs.get(prefix + key, [default])[0])
        except Exception:
            return default
    return {
        "target_mb": num("target", 5.0),
        "scale": num("scale", 0.0),
        "width": int(num("width", 0)),
        "height": int(num("height", 0)),
        "max_colors": int(num("max_colors", 256)),
        "quality": int(num("quality", 85)),
        "allow_frame_skip": qs.get(prefix + "allow_frame_skip", ["1"])[0] != "0",
        "dither": qs.get(prefix + "dither", ["0"])[0] == "1",
    }


def _meta_string(res):
    return (
        f"ok={int(res['ok'])};"
        f"size_in_mb={res.get('size_in_mb')};"
        f"size_mb={res['size_mb']};"
        f"format_in={res.get('format_in')};format_out={res.get('format_out')};"
        f"width_in={res.get('width_in')};height_in={res.get('height_in')};"
        f"width_out={res.get('width_out')};height_out={res.get('height_out')};"
        f"frames_in={res.get('frames_in')};frames_out={res.get('frames_out')};"
        f"colors_in={res.get('colors_in')};colors_out={res.get('colors_out')};"
        f"duration_ms={res.get('duration_ms')};step={res.get('step')};"
        f"note={urllib.parse.quote(res.get('note') or '')}"
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "X-Compress-Meta, X-Filename, X-Saved-Path, X-Batch-Manifest, X-Batch-Count")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    # ------------------------------------------------------------------
    def _fetch_url(self, url):
        if not url.startswith(("http://", "https://")):
            raise ValueError("仅支持 http/https 链接")
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (image-compressor)"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = resp.read()
        ct = resp.headers.get("Content-Type", "").lower()
        if not _looks_like_image(data, ct):
            raise ValueError(f"链接返回的不是图片（Content-Type: {ct or '未知'}）")
        return data

    # ------------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/compress":
                self._post_compress()
            elif path == "/batch":
                self._post_batch()
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            import traceback as _tb
            sys.stderr.write(_tb.format_exc() + "\n"); sys.stderr.flush()
            self._send(500, f"error: {e}".encode("utf-8"), "text/plain; charset=utf-8")

    def _post_compress(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url = (qs.get("url", [""])[0] or "").strip()
        if url:
            raw = self._fetch_url(url)
            base = url.rsplit("/", 1)[-1].split("?")[0] or "image"
        else:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            base = (qs.get("name", [""])[0] or "").rsplit("/")[-1].rsplit("\\")[-1] or "image"
        if not raw:
            raise ValueError("空文件")

        opts = _parse_opts(qs)
        res = compress_image(raw, **opts)
        fname = os.path.splitext(base)[0] + _ext(res.get("format_out"))

        saved_path = self._try_save_dir(qs, fname, res["bytes"])

        extra = {"X-Compress-Meta": _meta_string(res), "X-Filename": fname}
        if saved_path:
            extra["X-Saved-Path"] = saved_path
        ctype = "image/gif" if res.get("format_out") == "GIF" else "image/" + res.get("format_out", "png").lower()
        self._send(200, res["bytes"], ctype, extra=extra)

    # ------------------------------------------------------------------
    def _try_save_dir(self, qs, fname, data):
        # 默认自动保存到程序目录下的 output 文件夹；显式传入 save_dir 则存到指定位置
        save_dir = (qs.get("save_dir", [""])[0] or "").strip()
        sd = (save_dir if os.path.isabs(save_dir) else os.path.join(HERE, save_dir)) \
            if save_dir else os.path.join(HERE, "output")
        try:
            os.makedirs(sd, exist_ok=True)
            out_name = ("compressed_" + fname) if not fname.startswith("compressed_") else fname
            out_path = os.path.join(sd, out_name)
            with open(out_path, "wb") as f:
                f.write(data)
            return out_path
        except Exception as se:
            return f"__ERR__{se}"

    # ------------------------------------------------------------------
    def _post_batch(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        ct = self.headers.get("Content-Type", "")
        boundary = None
        if "boundary=" in ct:
            boundary = ct.split("boundary=")[1].strip().strip('"')
        parts = _parse_multipart(body, boundary.encode() if boundary else b"")
        files = [(p["filename"], p["data"]) for p in parts if p.get("filename")]
        form = {p["name"]: p["data"] for p in parts if not p.get("filename") and p.get("name")}

        urls_text = form.get("urls", b"").decode("utf-8", "replace")
        params_json = form.get("params", b"{}").decode("utf-8", "replace") or "{}"
        pobj = json.loads(params_json) if params_json.strip() else {}
        qs = {k: [str(v)] for k, v in pobj.items()}
        opts = _parse_opts(qs)

        # 收集待处理项：(显示名, bytes)
        items = []
        for fn, data in files:
            if _looks_like_image(data, "") or fn.lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                items.append((fn or "image", data))
        for line in urls_text.splitlines():
            u = line.strip()
            if u and u.lower().startswith(("http://", "https://")):
                items.append((u, None))  # None 标记需下载

        manifest = []
        zip_buf = io.BytesIO()
        used = {}
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            report_rows = ["name,format_in,format_out,width_in,height_in,width_out,height_out,"
                           "frames_in,frames_out,colors_in,colors_out,size_in_mb,size_mb,ok,note"]
            for name, data in items:
                entry = {"name": name, "ok": False}
                try:
                    if data is None:
                        data = self._fetch_url(name)
                    res = compress_image(data, **opts)
                    ext = _ext(res.get("format_out"))
                    base = os.path.splitext(os.path.basename(name.split("?")[0]))[0] or "image"
                    out_name = "compressed_" + base + ext
                    if out_name in used:
                        used[out_name] += 1
                        out_name = f"compressed_{base}_{used[out_name]}{ext}"
                    else:
                        used[out_name] = 0
                    zf.writestr(out_name, res["bytes"])
                    for k in ("format_in", "format_out", "width_in", "height_in",
                              "width_out", "height_out", "frames_in", "frames_out",
                              "colors_in", "colors_out", "size_in_mb", "size_mb", "note"):
                        entry[k] = res.get(k)
                    entry["ok"] = res.get("ok", False)
                except Exception as e:
                    entry["note"] = f"失败：{e}"
                    for k in ("format_in", "format_out", "width_in", "height_in",
                              "width_out", "height_out", "frames_in", "frames_out",
                              "colors_in", "colors_out", "size_in_mb", "size_mb"):
                        entry[k] = ""
                manifest.append(entry)
                report_rows.append(",".join(str(entry.get(k, "")) for k in
                    ["name", "format_in", "format_out", "width_in", "height_in", "width_out", "height_out",
                     "frames_in", "frames_out", "colors_in", "colors_out", "size_in_mb", "size_mb", "ok", "note"]))
            zf.writestr("report.csv", "\n".join(report_rows))

        zip_data = zip_buf.getvalue()
        # 服务端自动保存 ZIP 到 output 文件夹（传入 save_dir 则存到指定位置）
        saved_zip = ""
        save_dir = (pobj.get("save_dir") or "").strip()
        sd = (save_dir if os.path.isabs(save_dir) else os.path.join(HERE, save_dir)) \
            if save_dir else os.path.join(HERE, "output")
        try:
            os.makedirs(sd, exist_ok=True)
            zp = os.path.join(sd, "batch_compressed.zip")
            with open(zp, "wb") as f:
                f.write(zip_data)
            saved_zip = zp
        except Exception as se:
            saved_zip = f"__ERR__{se}"

        manifest_header = urllib.parse.quote(json.dumps(manifest, ensure_ascii=False))
        extra = {"X-Batch-Manifest": manifest_header, "X-Batch-Count": str(len(manifest))}
        if saved_zip:
            extra["X-Saved-Path"] = saved_zip
        self._send(200, zip_data, "application/zip",
                   extra=extra)

    def log_message(self, *args):
        pass  # 静默


# ----------------------------------------------------------------------------
# 极简 multipart 解析（Python 3.13 已移除 cgi 模块）
# ----------------------------------------------------------------------------
def _parse_multipart(body, boundary):
    parts = []
    if not boundary:
        return parts
    delim = b"--" + boundary
    for chunk in body.split(delim):
        if chunk in (b"", b"--", b"--\r\n"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if not chunk:
            continue
        idx = chunk.find(b"\r\n\r\n")
        if idx < 0:
            continue
        header = chunk[:idx].decode("utf-8", "replace")
        content = chunk[idx + 4:]
        name = filename = None
        for line in header.split("\r\n"):
            ll = line.lower()
            if ll.startswith("content-disposition:"):
                for tok in line.split(";"):
                    tok = tok.strip()
                    if tok.startswith("name="):
                        name = tok[5:].strip('"').strip("'")
                    elif tok.startswith("filename="):
                        filename = tok[9:].strip('"').strip("'")
        parts.append({"name": name, "filename": filename, "data": content})
    return parts


if __name__ == "__main__":
    print(f"图片压缩服务已启动: http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()
