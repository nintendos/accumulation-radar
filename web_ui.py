#!/usr/bin/env python3
"""
Web 控制台：把 accumulation_radar.py 的终端输出改为网页可见。
"""

import asyncio
import concurrent.futures
import json
import mimetypes
import os
import sqlite3
import subprocess
import threading
import time
from io import BytesIO
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import matplotlib
import pandas as pd
import requests
import uvicorn
import mplfinance as mpf

matplotlib.use("Agg")


ROOT = Path(__file__).parent
SCRIPT_PATH = ROOT / "accumulation_radar.py"
DB_PATH = ROOT / "accumulation.db"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8765
VALID_MODES = {"pool", "oi", "full"}
FAPI = "https://fapi.binance.com"
VALID_INTERVALS = {"15m", "1h", "4h", "1d"}

CHART_CACHE = {}
CACHE_TTL_SEC = 180

_STRATEGIES_EXEC = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="strategies"
)


def _compute_three_strategies_blocking():
    import accumulation_radar as ar

    conn = ar.init_db()
    try:
        return ar.compute_three_strategies(conn)
    finally:
        conn.close()


def _strategies_api_payload(raw: dict, top_n: int) -> dict:
    if not raw.get("ok"):
        return {"ok": False, "error": raw.get("error", "失败")}
    return {
        "ok": True,
        "generated_at": raw.get("generated_at"),
        "dual_heat": raw.get("dual_heat") or [],
        "vol_surge_count": raw.get("vol_surge_count", 0),
        "mcap_count": raw.get("mcap_count", 0),
        "cg_trending_count": raw.get("cg_trending_count", 0),
        "highlights": raw.get("highlights") or [],
        "hot_coins": (raw.get("hot_coins") or [])[:top_n],
        "chase": (raw.get("chase") or [])[:top_n],
        "combined": (raw.get("combined") or [])[:top_n],
        "ambush": (raw.get("ambush") or [])[:top_n],
        "meta": {
            "hot_coins_total": len(raw.get("hot_coins") or []),
            "chase_total": len(raw.get("chase") or []),
            "combined_total": len(raw.get("combined") or []),
            "ambush_total": len(raw.get("ambush") or []),
        },
    }


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


def fetch_watchlist_rows(limit: int):
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            """SELECT symbol, coin, score, status, sideways_days, range_pct, current_price, avg_vol, added_date
               FROM watchlist
               WHERE status != 'removed'
               ORDER BY score DESC
               LIMIT ?""",
            (limit,),
        )
        rows = c.fetchall()
        items = []
        for row in rows:
            items.append(
                {
                    "symbol": row[0],
                    "coin": row[1],
                    "score": round(float(row[2]), 2) if row[2] is not None else 0,
                    "status": row[3] or "",
                    "sideways_days": int(row[4]) if row[4] is not None else 0,
                    "range_pct": round(float(row[5]), 2) if row[5] is not None else 0,
                    "current_price": float(row[6]) if row[6] is not None else 0,
                    "avg_vol": float(row[7]) if row[7] is not None else 0,
                    "added_date": row[8] or "",
                }
            )
        return items
    finally:
        conn.close()


def binance_get(endpoint: str, params: dict):
    url = f"{FAPI}{endpoint}"
    for _ in range(3):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(1.5)
                continue
            return None
        except Exception:
            time.sleep(0.5)
    return None


def build_mini_kline_png(symbol: str, interval: str = "1h", limit: int = 48) -> bytes:
    now = time.time()
    cache_key = f"{symbol}|{interval}|{limit}"
    cached = CHART_CACHE.get(cache_key)
    if cached and (now - cached["ts"] < CACHE_TTL_SEC):
        return cached["png"]

    klines = binance_get(
        "/fapi/v1/klines",
        {"symbol": symbol.upper(), "interval": interval, "limit": limit},
    )
    if not klines or not isinstance(klines, list):
        raise RuntimeError("获取K线失败")

    df = pd.DataFrame(
        {
            "Date": pd.to_datetime([int(k[0]) for k in klines], unit="ms"),
            "Open": [float(k[1]) for k in klines],
            "High": [float(k[2]) for k in klines],
            "Low": [float(k[3]) for k in klines],
            "Close": [float(k[4]) for k in klines],
            "Volume": [float(k[5]) for k in klines],
        }
    ).set_index("Date")

    dark_style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#22c55e",
            down="#ef4444",
            edge="inherit",
            wick="inherit",
            volume="inherit",
        ),
        facecolor="#0b1220",
        figcolor="#0b1220",
        gridcolor="#1e293b",
        gridstyle="-",
    )

    fig, _ = mpf.plot(
        df,
        type="candle",
        style=dark_style,
        volume=False,
        ylabel="",
        xrotation=0,
        datetime_format="%m-%d",
        returnfig=True,
        figsize=(3.2, 1.2),
        tight_layout=True,
        axisoff=True,
    )
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", pad_inches=0.05)
    png = buf.getvalue()
    buf.close()
    fig.clf()

    CHART_CACHE[cache_key] = {"ts": now, "png": png}
    return png


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

    async def send_bytes(body: bytes, content_type: str, status=200, extra_headers=None):
        headers = [(b"content-type", content_type.encode("utf-8"))]
        if extra_headers:
            headers.extend(extra_headers)
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

    if method == "GET" and path == "/api/results":
        q = parse_qs(query)
        try:
            limit = int(q.get("limit", ["12"])[0])
        except ValueError:
            limit = 12
        limit = max(1, min(limit, 50))
        rows = fetch_watchlist_rows(limit)
        await send_json({"ok": True, "count": len(rows), "items": rows})
        return

    if method == "GET" and path == "/api/strategies":
        q = parse_qs(query)
        try:
            top_n = int(q.get("top", ["10"])[0])
        except ValueError:
            top_n = 10
        top_n = max(1, min(top_n, 30))
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(_STRATEGIES_EXEC, _compute_three_strategies_blocking)
        await send_json(_strategies_api_payload(raw, top_n))
        return

    if method == "GET" and path == "/api/chart":
        q = parse_qs(query)
        symbol = q.get("symbol", [""])[0].strip().upper()
        interval = q.get("interval", ["1h"])[0].strip()
        try:
            limit = int(q.get("limit", ["48"])[0])
        except ValueError:
            limit = 48
        limit = max(24, min(limit, 200))

        if not symbol.endswith("USDT"):
            await send_json({"ok": False, "message": "symbol 需为 USDT 合约，如 BTCUSDT"}, status=400)
            return
        if interval not in VALID_INTERVALS:
            await send_json({"ok": False, "message": f"interval 仅支持 {sorted(VALID_INTERVALS)}"}, status=400)
            return

        try:
            png = build_mini_kline_png(symbol, interval, limit)
        except Exception as exc:
            await send_json({"ok": False, "message": f"图表生成失败: {exc}"}, status=500)
            return

        await send_bytes(
            png,
            "image/png",
            status=200,
            extra_headers=[
                (b"cache-control", b"no-store, max-age=0, must-revalidate"),
                (b"pragma", b"no-cache"),
            ],
        )
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
