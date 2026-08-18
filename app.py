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
import json
import string
import subprocess
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from compress import compress_image, _norm_duration, _norm_loop  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000

EXT = {"GIF": ".gif", "PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp",
       "BMP": ".bmp", "TIFF": ".tiff", "UNKNOWN": ".img"}


def _ext(fmt):
    return EXT.get((fmt or "UNKNOWN").upper(), ".img")


def _resolve_save_dir(save_dir):
    """把用户填写的保存目录解析为绝对路径；留空则默认程序目录下的 output"""
    sd = (save_dir if os.path.isabs(save_dir) else os.path.join(HERE, save_dir)) \
        if save_dir else os.path.join(HERE, "output")
    return os.path.abspath(sd)


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
        "fps": num("fps", 0.0),
        "frames": int(num("frames", 0)),
        "allow_frame_skip": qs.get(prefix + "allow_frame_skip", ["1"])[0] != "0",
        "auto_colors": qs.get(prefix + "auto_colors", ["1"])[0] != "0",
        "auto_quality": qs.get(prefix + "auto_quality", ["1"])[0] != "0",
        "auto_fit": qs.get(prefix + "auto_fit", ["0"])[0] == "1",
        "dither": qs.get(prefix + "dither", ["0"])[0] == "1",
    }


def _preview_data_url(data, max_px=140):
    """为压缩结果生成小预览图。
    - GIF 动图：保持动画，缩到 max_px 内，返回 GIF base64 data URL
    - 静态图：取第一帧转 JPEG，返回 JPEG base64 data URL
    供批量结果表格直接显示压缩后的动态效果，无需再打开文件确认。"""
    try:
        import io
        import base64
        from PIL import Image, ImageSequence
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        frames = getattr(im, "n_frames", 1)
        if (im.format or "").upper() == "GIF" and frames > 1:
            # 计算等比缩放尺寸
            ratio = min(max_px / max(w, 1), max_px / max(h, 1), 1.0)
            nw, nh = max(1, round(w * ratio)), max(1, round(h * ratio))
            out_frames = []
            durs = []
            loop = _norm_loop(im.info.get("loop"), 0)
            for f in ImageSequence.Iterator(im):
                rgb = f.convert("RGB").resize((nw, nh), Image.LANCZOS)
                rgb.info.clear()
                out_frames.append(rgb)
                durs.append(_norm_duration(f.info.get("duration"), 80))
            buf = io.BytesIO()
            out_frames[0].save(
                buf, "GIF", save_all=True,
                append_images=out_frames[1:] if len(out_frames) > 1 else [],
                duration=durs, loop=loop, optimize=True
            )
            return "data:image/gif;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        # 静态图：取第一帧转 JPEG
        try:
            im.seek(0)
        except Exception:
            pass
        frame = im.convert("RGB")
        frame.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _preview_large_url(data, max_px=800):
    """生成大尺寸预览图，用于点击放大 lightbox。
    逻辑与 _preview_data_url 相同，但 max_px 更大以显示更多细节。"""
    return _preview_data_url(data, max_px=max_px)


def _meta_string(res):
    return (
        f"ok={int(res['ok'])};"
        f"size_in_mb={res.get('size_in_mb')};"
        f"size_mb={res['size_mb']};"
        f"format_in={res.get('format_in')};format_out={res.get('format_out')};"
        f"width_in={res.get('width_in')};height_in={res.get('height_in')};"
        f"width_out={res.get('width_out')};height_out={res.get('height_out')};"
        f"frames_in={res.get('frames_in')};frames_out={res.get('frames_out')};"
        f"fps_in={res.get('fps_in')};fps_out={res.get('fps_out')};"
        f"colors_in={res.get('colors_in')};colors_out={res.get('colors_out')};"
        f"duration_ms={res.get('duration_ms')};step={res.get('step')};"
        f"note={urllib.parse.quote(res.get('note') or '')}"
    )


def _known_roots():
    """常见位置的绝对路径（存在才列出）+ 可用磁盘根，供网页内目录选择器快捷跳转。"""
    roots = []
    home = os.path.expanduser("~")
    output_dir = os.path.join(HERE, "output")
    for label, p in [
        ("程序 output 文件夹", output_dir),
        ("桌面", os.path.join(home, "Desktop")),
        ("文档", os.path.join(home, "Documents")),
        ("下载", os.path.join(home, "Downloads")),
        ("图片", os.path.join(home, "Pictures")),
    ]:
        if p and os.path.isdir(p):
            roots.append({"label": label, "abs": os.path.normpath(p)})
    for d in string.ascii_uppercase:
        root = f"{d}:\\"
        if os.path.isdir(root):
            roots.append({"label": f"{d}: 盘", "abs": root})
    return roots


def _list_dir(dirpath=""):
    """列出目录内容，供网页内目录选择器使用（替代系统文件夹对话框，避免置顶问题）。
    返回 {'ok', 'abs', 'parent', 'dirs', 'roots'}；parent 为空表示已到磁盘根。"""
    try:
        if not dirpath.strip():
            return {"ok": True, "abs": "", "parent": "", "dirs": [], "roots": _known_roots()}
        abs_dir = os.path.abspath(os.path.expanduser(dirpath.strip()))
        if not os.path.isdir(abs_dir):
            return {"ok": False, "error": f"目录不存在：{abs_dir}"}
        parent = os.path.dirname(abs_dir)
        if os.path.dirname(parent) == parent:  # 已到盘根
            parent = ""
        dirs = []
        try:
            for name in sorted(os.listdir(abs_dir), key=str.lower):
                if name.startswith((".", "$")):
                    continue
                try:
                    if os.path.isdir(os.path.join(abs_dir, name)):
                        dirs.append(name)
                except OSError:
                    pass
        except OSError as e:
            return {"ok": False, "error": f"无法读取目录：{e}"}
        return {"ok": True, "abs": abs_dir, "parent": parent, "dirs": dirs, "roots": _known_roots()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _mkdir(parent, name):
    """在 parent 下创建文件夹 name，返回新文件夹完整路径。"""
    try:
        base = os.path.abspath(os.path.expanduser((parent or "").strip()))
        if not os.path.isdir(base):
            return {"ok": False, "error": f"父目录不存在：{base}"}
        name = (name or "").strip().strip("/\\")
        if not name:
            return {"ok": False, "error": "文件夹名不能为空"}
        new_dir = os.path.join(base, name)
        os.makedirs(new_dir, exist_ok=True)
        return {"ok": True, "abs": os.path.abspath(new_dir)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _pick_folder():
    """调用 Windows 原生"新式"文件夹选择对话框（IFileOpenDialog + FOS_PICKFOLDERS，
    Win10/11 资源管理器风格：面包屑导航、搜索框、快速访问），返回 {'abs': 完整路径}
    或 {'abs': '', 'error': ...}

    前台激活策略（关键）：Windows 只允许持有"前台锁"的进程激活窗口，后台服务进程
    弹的对话框天然抢不到前台。破解方法：模拟一次 Alt 键按下/抬起（keybd_event），
    系统会授予当前线程前台权限；再配合 AttachThreadInput + SetForegroundWindow +
    SetWindowPos(HWND_TOPMOST) 轮询，强制对话框保持在所有窗口最前面。
    """
    default_dir = os.path.join(HERE, "output")
    ps = r'''
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
# 编译缓存：首次把 C# 程序集编译成 dll 存到 %TEMP%，之后直接加载，省去每次编译（约 1~2 秒）
$cacheDll = Join-Path $env:TEMP 'WorkBuddy_NewFolderDialog_v2.dll'
$loaded = $false
if(Test-Path $cacheDll){
  try { Add-Type -Path $cacheDll; $loaded = $true } catch { Remove-Item $cacheDll -Force -ErrorAction SilentlyContinue }
}
if(-not $loaded){
Add-Type -OutputAssembly $cacheDll @"
using System;
using System.Runtime.InteropServices;
using System.Threading;

public static class NewFolderDialog {
  // ---------- Win32 ----------
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindowEx(IntPtr hwndParent, IntPtr hwndChildAfter, string lpszClass, string lpszWindow);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("kernel32.dll")] public static extern uint GetCurrentProcessId();

  private const byte VK_MENU = 0x12;
  private const uint KEYEVENTF_KEYUP = 0x0002;
  private static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
  private const uint SWP_NOMOVE = 0x0002;
  private const uint SWP_NOSIZE = 0x0001;

  public static void Force(IntPtr h) {
    if (h == IntPtr.Zero || !IsWindow(h)) return;
    uint fgT, curT, pid;
    GetWindowThreadProcessId(h, out pid);
    if (pid != GetCurrentProcessId()) return;
    IntPtr fg = GetForegroundWindow();
    if (fg == h) return;
    GetWindowThreadProcessId(fg, out fgT);
    curT = GetCurrentThreadId();
    keybd_event(VK_MENU, 0, 0, UIntPtr.Zero);          // 模拟 Alt 按下：获取前台权限
    bool att = (fgT != curT);
    if (att) AttachThreadInput(curT, fgT, true);
    SetForegroundWindow(h);
    SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
    if (att) AttachThreadInput(curT, fgT, false);
    keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);  // Alt 抬起
  }

  // ---------- IFileOpenDialog（Vista+ 新式对话框，Win10/11 资源管理器风格） ----------
  [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"), ClassInterface(ClassInterfaceType.None)]
  private class FileOpenDialogRCW { }

  [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  private interface IFileOpenDialog {
    [PreserveSig] int Show(IntPtr hwndOwner);
    [PreserveSig] int SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
    [PreserveSig] int SetFileTypeIndex(uint iFileType);
    [PreserveSig] int GetFileTypeIndex(out uint piFileType);
    [PreserveSig] int Advise(IntPtr pfde, out uint pdwCookie);
    [PreserveSig] int Unadvise(uint dwCookie);
    [PreserveSig] int SetOptions(uint fos);
    [PreserveSig] int GetOptions(out uint pfos);
    [PreserveSig] int SetDefaultFolder(IntPtr psi);
    [PreserveSig] int SetFolder(IntPtr psi);
    [PreserveSig] int GetFolder(out IntPtr ppsi);
    [PreserveSig] int GetCurrentSelection(out IntPtr ppsi);
    [PreserveSig] int SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    [PreserveSig] int GetFileName(out IntPtr pszName);
    [PreserveSig] int SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    [PreserveSig] int SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
    [PreserveSig] int SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    [PreserveSig] int GetResult(out IntPtr ppsi);
    [PreserveSig] int AddPlace(IntPtr psi, uint fdap);
    [PreserveSig] int SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    [PreserveSig] int Close(int hr);
    [PreserveSig] int SetClientGuid(ref Guid guid);
    [PreserveSig] int GetResults(out IntPtr ppenum);
    [PreserveSig] int GetSelectedItems(out IntPtr ppsai);
  }

  [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C3BFE2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  private interface IShellItem {
    [PreserveSig] int BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    [PreserveSig] int GetParent(out IntPtr ppsi);
    [PreserveSig] int GetDisplayName(uint sigdnName, out IntPtr ppszName);
    [PreserveSig] int GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    [PreserveSig] int Compare(IntPtr psi, uint hint, out int piOrder);
  }

  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  private static extern int SHCreateItemFromParsingName([MarshalAs(UnmanagedType.LPWStr)] string pszPath, IntPtr pbc, ref Guid riid, out IntPtr ppv);
  // 从 IUnknown/IShellItem 对象取 PIDL，再转路径 —— 完全避开 CLR 对 COM 接口的 QueryInterface 校验
  [DllImport("shell32.dll")]
  private static extern int SHGetIDListFromObject(IntPtr punk, out IntPtr ppidl);
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  private static extern bool SHGetPathFromIDListW(IntPtr pidl, [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszPath);

  private const uint FOS_PICKFOLDERS = 0x00000020;
  private const uint FOS_FORCEFILESYSTEM = 0x00000040;
  private const uint FOS_PATHMUSTEXIST = 0x00000800;
  private const uint FOS_NOCHANGEDIR = 0x00000008;
  private static readonly Guid IID_IShellItem = new Guid("43826D1E-E718-42EE-BC55-A1E261C3BFE2");

  private static IntPtr FindDlg() {
    IntPtr h = IntPtr.Zero;
    while (true) {
      h = FindWindowEx(IntPtr.Zero, h, "#32770", null);
      if (h == IntPtr.Zero) return IntPtr.Zero;
      uint pid;
      GetWindowThreadProcessId(h, out pid);
      if (pid == GetCurrentProcessId()) return h;
    }
  }

  public static string Pick(string title, string initialDir) {
    IFileOpenDialog dlg = (IFileOpenDialog)(new FileOpenDialogRCW());
    try {
      dlg.SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR);
      if (!string.IsNullOrEmpty(title)) dlg.SetTitle(title);
      dlg.SetOkButtonLabel("选择文件夹");
      if (!string.IsNullOrEmpty(initialDir) && System.IO.Directory.Exists(initialDir)) {
        try {
          IntPtr item;
          Guid iid = IID_IShellItem;
          int h = SHCreateItemFromParsingName(initialDir, IntPtr.Zero, ref iid, out item);
          if (h == 0 && item != IntPtr.Zero) {
            dlg.SetFolder(item);   // IntPtr 裸指针透传，不触发 CLR 的 QueryInterface 校验
            Marshal.Release(item);
          }
        } catch { }
      }
      // 对话框是模态的；后台线程持续把对话框窗口置顶置前，直到用户关闭
      bool running = true;
      Thread t = new Thread(delegate () {
        IntPtr hwnd = IntPtr.Zero;
        while (running) {
          if (hwnd == IntPtr.Zero) hwnd = FindDlg();
          if (hwnd != IntPtr.Zero) Force(hwnd);
          Thread.Sleep(120);
        }
      });
      t.IsBackground = true;
      t.Start();
      int hr = dlg.Show(IntPtr.Zero);
      running = false;
      if (hr == unchecked((int)0x800704C7)) return null;   // 用户取消
      if (hr != 0) return null;
      IntPtr result;
      if (dlg.GetResult(out result) != 0 || result == IntPtr.Zero) return null;
      try {
        IntPtr pidl;
        if (SHGetIDListFromObject(result, out pidl) != 0 || pidl == IntPtr.Zero) return null;
        try {
          var sb = new System.Text.StringBuilder(320);
          if (!SHGetPathFromIDListW(pidl, sb)) return null;
          return sb.ToString();
        } finally { Marshal.FreeCoTaskMem(pidl); }
      } finally { Marshal.Release(result); }
    } finally {
      Marshal.FinalReleaseComObject(dlg);
    }
  }
}
"@
Add-Type -Path $cacheDll   # 编译产物需显式加载到当前会话
}
$dir = [NewFolderDialog]::Pick('选择图片压缩后的保存文件夹', '__DEFAULT_DIR__')
if($dir){ $dir }
'''.replace("__DEFAULT_DIR__", default_dir)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        out = (r.stdout or "").strip()
        # 防御性处理：PowerShell stdout 偶尔会被其他输出（如底层库错误）污染，
        # 只取第一行作为路径；若第一行不是合法路径格式则返回空。
        if out:
            line = out.splitlines()[0].strip()
            # Windows 绝对路径至少为 "X:\"；过滤掉明显不是路径的内容
            if len(line) >= 3 and line[1:3] == ":\\":
                return {"abs": line}
        err = (r.stderr or "").strip()
        if err:
            return {"abs": "", "error": err[-300:]}
        return {"abs": ""}  # 用户取消
    except Exception as e:  # noqa: BLE001
        return {"abs": "", "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "X-Compress-Meta, X-Filename, X-Saved-Path, X-Batch-Manifest, X-Batch-Count, X-Saved-Count, X-Saved-Error")
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
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/info":
            body = json.dumps({
                "app_dir": HERE,
                "output_dir": os.path.join(HERE, "output"),
            }, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/resolve_dir":
            d = (qs.get("dir", [""])[0] or "").strip()
            body = json.dumps({"abs": _resolve_save_dir(d)}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/pick_dir":
            body = json.dumps(_pick_folder(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/list_dir":
            body = json.dumps(_list_dir(qs.get("dir", [""])[0]), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/mkdir":
            body = json.dumps(_mkdir(qs.get("dir", [""])[0], qs.get("name", [""])[0]),
                              ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
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
            elif path == "/probe":
                self._post_probe()
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
    def _probe_image(self, raw):
        """读取图片信息：尺寸 / 帧数 / 帧率 / 颜色数 / 格式 / 体积"""
        import io
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        frames = getattr(im, "n_frames", 1) or 1
        dur = _norm_duration(im.info.get("duration"), 0)
        fps = round(1000.0 / dur, 1) if (frames > 1 and dur > 0) else 0
        try:
            if im.mode in ("P", "1"):
                # 调色板图：统计各帧实际使用的调色板索引（并集），精确且快
                used = set()
                for fi in range(min(frames, 50)):
                    im.seek(fi)
                    used.update(im.getdata())
                    if len(used) >= 256:
                        break
                colors = len(used)
            else:
                f = im.convert("RGB")
                cnt = f.getcolors(1 << 24)
                if cnt is not None:
                    colors = len(cnt)
                else:
                    colors = 1 << 24  # 超过统计上限
        except Exception:
            colors = 0
        return {
            "ok": True, "width": w, "height": h, "frames": frames,
            "fps": fps, "colors": colors, "format": im.format or "",
            "size_mb": round(len(raw) / 1024 / 1024, 3),
        }

    def _post_probe(self):
        import io
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        url = (qs.get("url", [""])[0] or "").strip()
        if url:
            raw = self._fetch_url(url)
        else:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
        if not raw:
            raise ValueError("空文件")
        try:
            info = self._probe_image(raw)
        except Exception as e:  # noqa: BLE001
            info = {"ok": False, "error": str(e)}
        self._send(200, json.dumps(info, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ------------------------------------------------------------------
    def _try_save_dir(self, qs, fname, data):
        # 默认自动保存到程序目录下的 output 文件夹；显式传入 save_dir 则存到指定位置
        save_dir = (qs.get("save_dir", [""])[0] or "").strip()
        sd = _resolve_save_dir(save_dir)
        try:
            os.makedirs(sd, exist_ok=True)
            out_name = ("compressed_" + fname) if not fname.startswith("compressed_") else fname
            out_path = os.path.join(sd, out_name)
            with open(out_path, "wb") as f:
                f.write(data)
            return out_path
        except Exception as se:
            return f"__ERR__{se}"

    def _send_chunked_headers(self, ctype="application/x-ndjson"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "X-Compress-Meta, X-Filename, X-Saved-Path, X-Batch-Manifest, X-Batch-Count, X-Saved-Count, X-Saved-Error")
        self.end_headers()

    def _chunk(self, data: bytes):
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _chunk_json(self, obj):
        line = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self._chunk(line)

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

        # 每张图的独立参数（与 items 顺序对齐）：null=跟随全局参数，对象=覆盖全局
        per_params = form.get("per_params", b"").decode("utf-8", "replace") or ""
        per_list = []
        if per_params.strip():
            try:
                per_list = json.loads(per_params)
                if not isinstance(per_list, list):
                    per_list = []
            except Exception:
                per_list = []

        # 收集待处理项：(显示名, bytes)
        items = []
        for fn, data in files:
            if _looks_like_image(data, "") or fn.lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                items.append((fn or "image", data))
        for line in urls_text.splitlines():
            u = line.strip()
            if u and u.lower().startswith(("http://", "https://")):
                items.append((u, None))  # None 标记需下载

        # 解析保存目录（默认程序目录下 output，可传绝对/相对路径）
        save_dir = (pobj.get("save_dir") or "").strip()
        sd = _resolve_save_dir(save_dir)
        save_err = ""
        try:
            os.makedirs(sd, exist_ok=True)
        except Exception as se:
            sd = None
            save_err = str(se)

        self._send_chunked_headers()
        used = {}
        saved_count = 0
        # 逐文件保存压缩结果（不再打包 zip），每处理完一个立即向客户端推送进度
        for idx, (name, data) in enumerate(items):
            entry = {"name": name, "ok": False}
            try:
                if data is None:
                    data = self._fetch_url(name)
                # 每图独立参数：仅覆盖该图提交的字段，其余沿用全局
                popts = opts
                if idx < len(per_list) and isinstance(per_list[idx], dict) and per_list[idx]:
                    pqs = {k: [str(v)] for k, v in per_list[idx].items()}
                    popts = dict(opts)
                    popts.update(_parse_opts(pqs))
                res = compress_image(data, **popts)
                ext = _ext(res.get("format_out"))
                base = os.path.splitext(os.path.basename(name.split("?")[0]))[0] or "image"
                out_name = "compressed_" + base + ext
                if out_name in used:
                    used[out_name] += 1
                    out_name = f"compressed_{base}_{used[out_name]}{ext}"
                else:
                    used[out_name] = 0
                for k in ("format_in", "format_out", "width_in", "height_in",
                          "width_out", "height_out", "frames_in", "frames_out",
                          "fps_in", "fps_out",
                          "colors_in", "colors_out", "size_in_mb", "size_mb", "note"):
                    entry[k] = res.get(k)
                entry["ok"] = res.get("ok", False)
                entry["preview"] = _preview_data_url(res["bytes"])   # 压缩结果小预览图（表格用）
                entry["preview_large"] = _preview_large_url(res["bytes"])  # 大预览图（点击放大用）
                if sd is not None:
                    out_path = os.path.join(sd, out_name)
                    with open(out_path, "wb") as f:
                        f.write(res["bytes"])
                    entry["saved_path"] = out_path
                    saved_count += 1
            except Exception as e:
                entry["note"] = f"失败：{e}"
                for k in ("format_in", "format_out", "width_in", "height_in",
                          "width_out", "height_out", "frames_in", "frames_out",
                          "colors_in", "colors_out", "size_in_mb", "size_mb"):
                    entry[k] = ""
            self._chunk_json({"done": False, "idx": idx, "entry": entry, "total": len(items)})

        summary = {"done": True, "count": len(items), "saved_count": saved_count}
        if sd is not None:
            summary["saved_path"] = sd
        else:
            summary["save_error"] = save_err
        self._chunk_json(summary)
        self._chunk(b"")  # 结束 chunked

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
