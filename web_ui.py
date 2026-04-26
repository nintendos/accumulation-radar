#!/usr/bin/env python3
"""
Web 控制台：把 accumulation_radar.py 的终端输出改为网页可见。
"""

import json
import mimetypes
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn


ROOT = Path(__file__).parent
SCRIPT_PATH = ROOT / "accumulation_radar.py"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8765
VALID_MODES = {"pool", "oi", "full"}


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.mode = ""
        self.started_at = ""
        self.ended_at = ""
        self.exit_code = None
        self.logs = []

    def reset(self, mode: str):
        with self.lock:
            self.running = True
            self.mode = mode
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.ended_at = ""
            self.exit_code = None
            self.logs = [f"[{self.started_at}] 启动模式: {mode}"]

    def append(self, line: str):
        with self.lock:
            self.logs.append(line.rstrip("\n"))

    def finish(self, code: int):
        with self.lock:
            self.running = False
            self.exit_code = code
            self.ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logs.append(f"[{self.ended_at}] 任务结束，退出码: {code}")

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "exit_code": self.exit_code,
                "logs": "\n".join(self.logs),
            }


STATE = AppState()


def run_mode(mode: str):
    STATE.reset(mode)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        ["python", str(SCRIPT_PATH), mode],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        STATE.append(line)

    code = proc.wait()
    STATE.finish(code)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_binary_file(path: Path) -> bytes:
    return path.read_bytes()


async def app(scope, receive, send):
    """最小 ASGI 应用，由 Uvicorn 托管。"""
    if scope["type"] != "http":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        return

    method = scope.get("method", "GET").upper()
    path = scope.get("path", "")
    query = scope.get("query_string", b"").decode("utf-8")

    async def send_json(payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json; charset=utf-8")]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def send_html(content: str, status=200):
        body = content.encode("utf-8")
        headers = [(b"content-type", b"text/html; charset=utf-8")]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def send_bytes(body: bytes, content_type: str, status=200):
        headers = [(b"content-type", content_type.encode("utf-8"))]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    if method == "GET" and path == "/":
        index_path = TEMPLATES_DIR / "index.html"
        if not index_path.exists():
            await send_html("index.html 不存在", status=500)
            return
        await send_html(read_text_file(index_path))
        return

    if method == "GET" and path == "/status":
        await send_json(STATE.snapshot())
        return

    if method == "GET" and path.startswith("/static/"):
        rel = path[len("/static/"):].lstrip("/")
        static_path = (STATIC_DIR / rel).resolve()
        base = STATIC_DIR.resolve()

        if not str(static_path).startswith(str(base)):
            await send_json({"ok": False, "message": "非法路径"}, status=400)
            return
        if not static_path.exists() or not static_path.is_file():
            await send_json({"ok": False, "message": "静态文件不存在"}, status=404)
            return

        ctype, _ = mimetypes.guess_type(str(static_path))
        if not ctype:
            ctype = "application/octet-stream"
        await send_bytes(read_binary_file(static_path), ctype)
        return

    if method == "POST" and path == "/run":
        mode = parse_qs(query).get("mode", [""])[0].strip().lower()
        if mode not in VALID_MODES:
            await send_json({"ok": False, "message": "mode 仅支持 pool / oi / full"}, status=400)
            return

        if STATE.snapshot()["running"]:
            await send_json({"ok": False, "message": "已有任务在运行，请等待结束"}, status=409)
            return

        t = threading.Thread(target=run_mode, args=(mode,), daemon=True)
        t.start()
        await send_json({"ok": True, "message": f"已启动 {mode}"})
        return

    await send_json({"ok": False, "message": "Not Found"}, status=404)


def main():
    if not SCRIPT_PATH.exists():
        raise FileNotFoundError(f"未找到脚本: {SCRIPT_PATH}")
    if not (TEMPLATES_DIR / "index.html").exists():
        raise FileNotFoundError(f"未找到模板文件: {TEMPLATES_DIR / 'index.html'}")
    print(f"Web 控制台启动(Uvicorn): http://{HOST}:{PORT}")
    print("按 Ctrl+C 退出")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
