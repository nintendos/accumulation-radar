#!/usr/bin/env python3
"""
跨平台启动脚本：用于在任意操作系统命令行启动 Web UI 服务。
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    web_ui = root / "web_ui.py"

    if not web_ui.exists():
        print(f"[ERROR] 未找到文件: {web_ui}")
        return 1

    os.chdir(root)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    print("[INFO] 启动庄家收筹雷达 Web 服务...")
    print("[INFO] 地址: http://127.0.0.1:8765")
    print()

    cmd = [sys.executable, str(web_ui)]
    try:
        return subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        print("\n[INFO] 已停止服务")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
