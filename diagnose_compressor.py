#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片压缩器诊断与修复工具

双击运行即可一键检测：服务是否运行、端口占用、进程状态、启动项、快捷方式、
浏览器错误（ERR_EMPTY_RESPONSE 等），并在需要时自动修复。

用法：
  python diagnose_compressor.py              # 诊断并尝试自动修复
  python diagnose_compressor.py --dry-run    # 只诊断，不修复
  python diagnose_compressor.py --error "ERR_EMPTY_RESPONSE"  # 指定浏览器错误码
"""
import os
import re
import sys
import time
import json
import socket
import shutil
import subprocess
import webbrowser
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------- 配置 ----------
HERE = Path(__file__).resolve().parent
PROJECT_NAME = "图片压缩器"
PORT = int(os.environ.get("PORT", "8765"))
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}/"
PING_URL = f"http://{HOST}:{PORT}/ping"
FETCH_URL = f"http://{HOST}:{PORT}/fetch_url?url=" + urllib.request.quote(
    "https://img.alicdn.com/imgextra/i1/2214199540950/O1CN01VnR0noDrUHJ32p9F_!!2214199540950.gif?getAvatar=1",
    safe="",
)

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
DESKTOP_DIR = Path.home() / "Desktop"

PYTHONW_CANDIDATES = [
    HERE / "venv/Scripts/pythonw.exe",
    HERE / "venv/Scripts/python.exe",
    Path(sys.executable).parent / "pythonw.exe",
    Path(sys.executable),
    Path("pythonw.exe"),
    Path("python.exe"),
]

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"diagnose_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

# 浏览器错误码 → 含义与修复建议
ERROR_KNOWLEDGE = {
    "ERR_EMPTY_RESPONSE": {
        "meaning": "浏览器成功连上了 127.0.0.1，但服务器没有返回任何数据后主动断开。",
        "causes": [
            "服务进程已崩溃/卡死，端口仍被僵尸进程占用",
            "端口被其他程序占用，该程序接受了连接但不说话",
            "app_server.py 启动后异常退出，但系统未立即释放端口",
        ],
        "fixes": [
            "使用本工具的「自动修复」结束残留进程并重新启动服务",
            "重启电脑后再次运行本工具检测",
        ],
    },
    "ERR_CONNECTION_REFUSED": {
        "meaning": "浏览器无法连接到 127.0.0.1 的端口，目标端口没有任何程序监听。",
        "causes": [
            "压缩器服务完全没有启动",
            "开机自启脚本失败或被安全软件拦截",
            "启动脚本路径错误/找不到 Python",
        ],
        "fixes": [
            "使用本工具「启动服务」",
            "检查启动目录里的自启.bat 是否存在",
            "把安全软件/Defender 对 pythonw 的拦截放行",
        ],
    },
    "ERR_CONNECTION_TIMED_OUT": {
        "meaning": "连接长时间没有响应，通常是防火墙或杀毒软件拦截。",
        "causes": ["Windows 防火墙拦截了 localhost 回环连接", "杀毒软件拦截了 pythonw 的网络请求"],
        "fixes": [
            "允许 Python/pythonw 通过 Windows Defender 防火墙",
            "把项目目录加入杀毒软件白名单",
        ],
    },
    "ERR_ADDRESS_IN_USE": {
        "meaning": "服务尝试启动时发现端口已被占用。",
        "causes": ["上一个实例没退出", "其他程序占用了 8765"],
        "fixes": ["结束占用 8765 的进程后重新启动"],
    },
    "ERR_CONNECTION_RESET": {
        "meaning": "连接被服务器强制重置，通常是服务异常退出。",
        "causes": ["app_server.py 运行时抛出未捕获异常"],
        "fixes": ["查看 logs/app_server_*.log 里的报错", "使用本工具重新启动服务"],
    },
}

# ---------- 报告结构 ----------
report = {
    "time": datetime.now().isoformat(),
    "project": str(HERE),
    "port": PORT,
    "checks": [],
    "findings": [],
    "actions": [],
    "error_code": None,
    "status": "unknown",  # ok / degraded / failed
}


def log(line: str):
    """同时打印到控制台并写入日志文件。"""
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def section(title: str):
    log("")
    log("=" * 60)
    log(title)
    log("=" * 60)


def run(cmd, shell=False, timeout=30, capture=True):
    """运行外部命令，返回 (returncode, stdout, stderr)。"""
    try:
        result = subprocess.run(
            cmd if isinstance(cmd, list) else cmd,
            shell=shell,
            capture_output=capture,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


def check_file(path: Path, label: str) -> dict:
    ok = path.exists()
    info = {"check": label, "ok": ok, "path": str(path)}
    if ok:
        info["size"] = path.stat().st_size
    return info


def find_pythonw() -> Path | None:
    for cand in PYTHONW_CANDIDATES:
        if cand.exists():
            return cand
    return None


def port_status() -> dict:
    """检测端口占用情况。"""
    info = {"port": PORT, "listening": False, "pid": None, "process": None, "path": None}
    code, out, err = run(["netstat", "-ano"], shell=False)
    if code != 0:
        return info
    for line in out.splitlines():
        if f"{HOST}:{PORT}" in line or f"0.0.0.0:{PORT}" in line:
            parts = line.split()
            if "LISTENING" in line:
                info["listening"] = True
                info["pid"] = parts[-1]
                break
    if info["pid"]:
        # 查进程名
        c2, o2, e2 = run(["tasklist", "/FI", f"PID eq {info['pid']}", "/FO", "CSV"], shell=False)
        if c2 == 0:
            lines = [l for l in o2.strip().splitlines() if l.strip()]
            if len(lines) >= 2:
                cols = lines[-1].strip('"').split('","')
                info["process"] = cols[0] if cols else "unknown"
        # 查完整路径（wmic 在部分 Win11 已弃用，用 PowerShell）
        c3, o3, e3 = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-Process -Id {info['pid']} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path",
            ],
            shell=False,
        )
        if c3 == 0:
            info["path"] = o3.strip()
    return info


def http_probe(url: str, timeout=10) -> dict:
    """探测 HTTP 端点，返回状态、耗时、错误信息。"""
    start = time.time()
    result = {"url": url, "ok": False, "status": None, "elapsed_ms": None, "error": None, "body_bytes": 0}
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            result["ok"] = True
            result["status"] = resp.status
            result["body_bytes"] = len(data)
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    result["elapsed_ms"] = round((time.time() - start) * 1000, 1)
    return result


def socket_probe(host: str, port: int, timeout=5) -> dict:
    """最底层的 socket 探测，用于区分「没进程」vs「有进程但不回数据」。"""
    result = {"host": host, "port": port, "connected": False, "sent": False, "responded": False, "error": None}
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            result["connected"] = True
            s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            result["sent"] = True
            s.settimeout(timeout)
            try:
                data = s.recv(1024)
                result["responded"] = len(data) > 0
            except socket.timeout:
                result["error"] = "socket timeout (no response)"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def kill_pid(pid: int) -> bool:
    code, _, _ = run(["taskkill", "/PID", str(pid), "/F"], shell=False)
    return code == 0


def start_service(pythonw: Path) -> dict:
    """用 pythonw 启动 app_server.py，无窗口后台运行。"""
    result = {"ok": False, "cmd": None, "error": None}
    script = HERE / "app_server.py"
    env = os.environ.copy()
    env["NO_BROWSER"] = "1"  # 诊断工具自己控制浏览器打开时机
    try:
        proc = subprocess.Popen(
            [str(pythonw), str(script)],
            cwd=str(HERE),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result["cmd"] = f'{pythonw} "{script}"'
        result["pid"] = proc.pid
        # 等待服务就绪
        for _ in range(15):
            time.sleep(0.5)
            ps = port_status()
            if ps["listening"]:
                result["ok"] = True
                result["port_pid"] = ps["pid"]
                break
        if not result["ok"]:
            result["error"] = "服务进程已启动，但端口 8765 仍未进入监听状态"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def recreate_shortcuts():
    """重新创建桌面 .url 与兜底 .bat，以及开机自启项。"""
    actions = []

    # 桌面 .url（主要入口）
    url_file = DESKTOP_DIR / f"{PROJECT_NAME}.url"
    url_content = f"""[InternetShortcut]
URL=http://{HOST}:{PORT}/
IconIndex=0
"""
    url_file.write_text(url_content, encoding="utf-8")
    actions.append(f"已创建/更新桌面快捷方式: {url_file}")

    # 桌面兜底启动 bat
    bat_file = DESKTOP_DIR / f"{PROJECT_NAME}.bat"
    bat_content = f'''@echo off
chcp 65001 >nul
cd /d "{HERE}"
start "" "{HERE / 'venv/Scripts/pythonw.exe'}" "{HERE / 'app_server.py'}"
timeout /t 2 /nobreak >nul
start "" "{URL}"
'''
    bat_file.write_text(bat_content, encoding="utf-8")
    actions.append(f"已创建/更新桌面启动脚本: {bat_file}")

    # 开机自启 bat
    if STARTUP_DIR.exists():
        startup_bat = STARTUP_DIR / f"{PROJECT_NAME}自启.bat"
        startup_content = f'''@echo off
set NO_BROWSER=1
start "" "{HERE / 'venv/Scripts/pythonw.exe'}" "{HERE / 'app_server.py'}"
'''
        startup_bat.write_text(startup_content, encoding="utf-8")
        actions.append(f"已创建/更新开机自启项: {startup_bat}")
    else:
        actions.append(f"未找到开机启动目录: {STARTUP_DIR}")

    return actions


def diagnose(error_code: str | None = None, dry_run: bool = False) -> dict:
    dry_tag = "[模拟运行，不执行修复] " if dry_run else ""
    section(f"{dry_tag}{PROJECT_NAME} 诊断报告")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"项目目录: {HERE}")
    log(f"目标端口: {HOST}:{PORT}")
    if error_code:
        log(f"用户指定的浏览器错误码: {error_code}")
        report["error_code"] = error_code

    # ---------- 1. 环境与文件 ----------
    section("[1/6] 环境与必要文件检查")
    pythonw = find_pythonw()
    checks = [
        check_file(HERE / "app_server.py", "app_server.py 服务脚本"),
        check_file(HERE / "index.html", "index.html 前端页面"),
        check_file(HERE / "docs/index.html", "docs/index.html GitHub Pages 源"),
        check_file(HERE / "venv/Scripts/pythonw.exe", "venv pythonw 解释器"),
        check_file(HERE / "venv/Scripts/python.exe", "venv python 解释器"),
        check_file(DESKTOP_DIR / f"{PROJECT_NAME}.url", "桌面 .url 快捷方式"),
        check_file(DESKTOP_DIR / f"{PROJECT_NAME}.bat", "桌面启动脚本"),
        check_file(STARTUP_DIR / f"{PROJECT_NAME}自启.bat", "开机自启脚本"),
    ]
    for c in checks:
        report["checks"].append(c)
        status = "✅" if c["ok"] else "❌"
        size = f" ({c.get('size', 0)} bytes)" if "size" in c else ""
        log(f"{status} {c['check']}: {c['path']}{size}")
    if pythonw:
        log(f"✅ 找到可用的 Python: {pythonw}")
        report["checks"].append({"check": "python interpreter", "ok": True, "path": str(pythonw)})
    else:
        log("❌ 未找到可用的 pythonw/python 解释器")
        report["checks"].append({"check": "python interpreter", "ok": False})

    # ---------- 2. 端口与进程 ----------
    section("[2/6] 端口占用与进程检查")
    ps = port_status()
    report["port"] = ps
    if ps["listening"]:
        log(f"✅ 端口 {PORT} 正在监听")
        log(f"   PID:      {ps['pid']}")
        log(f"   进程名:   {ps['process'] or 'unknown'}")
        log(f"   完整路径: {ps['path'] or 'unknown'}")
        is_ours = ps["path"] and "app_server.py" in ps["path"] or (ps["process"] and "python" in ps["process"].lower())
        if is_ours:
            log("   判断: 该进程看起来是图片压缩器服务本身")
        else:
            log("   ⚠️ 警告: 占用该端口的进程不像是图片压缩器服务，可能导致冲突")
            report["findings"].append({
                "level": "warning",
                "summary": f"端口 {PORT} 被非本应用进程占用",
                "detail": f"PID={ps['pid']}, 进程={ps['process']}, 路径={ps['path']}",
            })
    else:
        log(f"❌ 端口 {PORT} 没有程序在监听")
        report["findings"].append({
            "level": "error",
            "summary": f"服务未在 {HOST}:{PORT} 上运行",
            "detail": "这是 ERR_EMPTY_RESPONSE / ERR_CONNECTION_REFUSED 的最常见原因。",
        })

    # ---------- 3. 网络层探测 ----------
    section("[3/6] 网络层探测（模拟浏览器请求）")
    sock = socket_probe(HOST, PORT)
    report["socket"] = sock
    if sock["connected"]:
        log(f"✅ TCP 连接成功 ({HOST}:{PORT})")
        if sock["responded"]:
            log("✅ 服务器回复了数据")
        else:
            log("❌ 服务器连接成功，但没有回复任何数据 → 对应浏览器 ERR_EMPTY_RESPONSE")
            report["findings"].append({
                "level": "error",
                "summary": "服务已连接但不响应 HTTP 请求",
                "detail": "与浏览器 ERR_EMPTY_RESPONSE 一致，通常是服务进程崩溃或端口被非 HTTP 程序占用。",
            })
    else:
        log(f"❌ TCP 连接失败: {sock['error']} → 对应浏览器 ERR_CONNECTION_REFUSED")

    ping = http_probe(PING_URL)
    report["http_ping"] = ping
    log(f"HTTP /ping: {ping['status'] if ping['status'] else 'N/A'} | {ping['elapsed_ms']}ms | {ping['error'] or 'OK'}")

    root = http_probe(URL)
    report["http_root"] = root
    log(f"HTTP /:     {root['status'] if root['status'] else 'N/A'} | {root['elapsed_ms']}ms | 返回 {root['body_bytes']} bytes | {root['error'] or 'OK'}")

    # ---------- 4. 代理功能探测 ----------
    section("[4/6] 防盗链代理功能检查")
    fetch = http_probe(FETCH_URL, timeout=30)
    report["http_fetch"] = fetch
    if fetch["ok"] and fetch["body_bytes"] > 1000:
        log(f"✅ /fetch_url 代理正常，测试图返回 {fetch['body_bytes']} bytes")
    else:
        fetch_detail = fetch['error'] or f"仅返回 {fetch['body_bytes']} bytes"
        log(f"⚠️ /fetch_url 代理异常: {fetch_detail}")
        report["findings"].append({
            "level": "warning",
            "summary": "图片链接代理可能不可用",
            "detail": "本地文件压缩不受影响；防盗链 URL 图片压缩会失败。",
        })

    # ---------- 5. 浏览器错误码解析 ----------
    section("[5/6] 浏览器错误码解析")
    matched_code = error_code or "ERR_EMPTY_RESPONSE" if (sock["connected"] and not sock["responded"]) else None
    if not matched_code and not ps["listening"]:
        matched_code = "ERR_CONNECTION_REFUSED"
    if matched_code and matched_code in ERROR_KNOWLEDGE:
        info = ERROR_KNOWLEDGE[matched_code]
        log(f"识别到错误码: {matched_code}")
        log(f"含义: {info['meaning']}")
        log("可能原因:")
        for c in info["causes"]:
            log(f"  - {c}")
        log("修复建议:")
        for f in info["fixes"]:
            log(f"  - {f}")
        report["error_code"] = matched_code
    else:
        log("根据当前检测结果，未匹配到典型浏览器错误码，或网络层已正常。")

    # ---------- 6. 自动修复 ----------
    section("[6/6] 自动修复")
    if dry_run:
        log("--dry-run 模式，跳过修复步骤")
        report["status"] = "dry_run"
        return report

    needs_restart = False

    # 6.1 如果端口被非本应用进程占用，结束它
    if ps["listening"] and ps["pid"]:
        is_ours = bool(
            (ps["path"] and ("app_server.py" in ps["path"] or str(HERE) in ps["path"]))
            or (ps["process"] and "python" in ps["process"].lower())
        )
        if not is_ours:
            log(f"结束非本应用进程 PID={ps['pid']} ({ps['process']})")
            if kill_pid(int(ps["pid"])):
                log("✅ 已结束占用端口的进程")
                report["actions"].append(f"kill foreign PID {ps['pid']}")
                needs_restart = True
                time.sleep(1)
            else:
                log("❌ 无法结束该进程，可能需要以管理员身份运行")
                report["actions"].append(f"failed to kill foreign PID {ps['pid']}")
        elif not sock["responded"]:
            # 是本应用但无响应，也重启
            log(f"服务进程无响应，准备重启 (PID={ps['pid']})")
            if kill_pid(int(ps["pid"])):
                log("✅ 已结束无响应的服务进程")
                report["actions"].append(f"kill unresponsive PID {ps['pid']}")
                needs_restart = True
                time.sleep(1)
            else:
                log("❌ 无法结束服务进程")

    # 6.2 如果端口未监听，启动服务
    if not port_status()["listening"]:
        needs_restart = True

    if needs_restart:
        if pythonw:
            log(f"正在启动服务: {pythonw} app_server.py")
            start_result = start_service(pythonw)
            report["start_service"] = start_result
            if start_result["ok"]:
                log("✅ 服务启动成功")
                log(f"   PID: {start_result['pid']}")
                log(f"   端口: {PORT} 已监听")
                report["actions"].append("start app_server.py")
            else:
                log(f"❌ 服务启动失败: {start_result['error']}")
                report["actions"].append(f"start failed: {start_result['error']}")
        else:
            log("❌ 找不到 pythonw，无法启动服务")
            report["actions"].append("cannot start: pythonw not found")

    # 6.3 重建快捷方式与自启项
    shortcut_actions = recreate_shortcuts()
    for a in shortcut_actions:
        log(f"✅ {a}")
    report["actions"].extend(shortcut_actions)

    # 6.4 最终验证
    section("修复后验证")
    final_ps = port_status()
    final_ping = http_probe(PING_URL)
    final_root = http_probe(URL)
    report["final"] = {"port": final_ps, "ping": final_ping, "root": final_root}
    if final_ps["listening"] and final_ping["ok"]:
        log("✅ 服务已正常运行")
        log(f"   /ping: {final_ping['status']} ({final_ping['elapsed_ms']}ms)")
        log(f"   /:     {final_root['status']} 返回 {final_root['body_bytes']} bytes")
        report["status"] = "ok"
        log(f"正在打开浏览器: {URL}")
        try:
            webbrowser.open(URL)
            report["actions"].append("open browser")
        except Exception as e:
            log(f"⚠️ 打开浏览器失败: {e}")
    else:
        log("❌ 修复后仍无法访问服务")
        report["status"] = "failed"
        report["findings"].append({
            "level": "error",
            "summary": "自动修复失败",
            "detail": "请尝试以管理员身份运行本工具，或把日志文件发送给技术支持。",
        })

    return report


def main():
    dry_run = "--dry-run" in sys.argv
    error_code = None
    for i, arg in enumerate(sys.argv):
        if arg == "--error" and i + 1 < len(sys.argv):
            error_code = sys.argv[i + 1]

    try:
        diagnose(error_code=error_code, dry_run=dry_run)
    except Exception as e:
        log(f"诊断过程出现未预期异常: {type(e).__name__}: {e}")
        report["status"] = "error"
        report["actions"].append(f"exception: {e}")

    # 输出 JSON 摘要到日志文件旁边
    summary_file = LOG_FILE.with_suffix(".json")
    summary_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    section("报告文件")
    log(f"文本日志: {LOG_FILE}")
    log(f"JSON 摘要: {summary_file}")
    log("")
    if report.get("status") == "ok":
        log("🟢 结论：问题已修复，浏览器应该已经打开。")
    elif dry_run:
        log("🟡 结论：模拟运行结束，未执行修复。去掉 --dry-run 即可自动修复。")
    else:
        log("🔴 结论：自动修复未完全成功，请查看上方具体失败项。")

    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
