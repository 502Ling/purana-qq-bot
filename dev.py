"""开发用监督进程：源码一变就自动重启，省得手动关了再开。

用法（在项目根目录跑）：

    .\\venv\\Scripts\\python.exe dev.py            # 监视并重启 run.py
    .\\venv\\Scripts\\python.exe dev.py 某脚本.py   # 也可以盯别的入口

行为：
- 保存任意 ``.py`` / ``.env`` → 自动重启（venv / __pycache__ / data 不监视）
- 子进程自己崩了 → 自动拉起，且退避重试（1→2→4→8→16→30s 封顶）
- 按 **Esc** → 退出（并关掉子进程）；隐藏窗口跑时不监听（run.cmd 设 NICO_NO_ESC=1）
- 子进程里 Esc 退出被禁用（注入 NICO_NO_ESC=1），免得两个进程抢键盘
- 同时注入 NICO_SUPERVISED=1，子进程的 ``/restart`` 会交给本进程拉起
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCH_SUFFIXES = {".py", ".env"}
IGNORE_DIRS = {"venv", "__pycache__", "data", ".vscode", ".git"}
POLL = 1.0  # 秒，轮询文件改动的间隔（剪枝后一次只要 0.3ms，1 秒完全无感）

# 子进程崩了重启时的退避：跑不到 FAST_EXIT 秒就算"秒崩"，延迟翻倍，封顶 BACKOFF_MAX
FAST_EXIT = 5.0
BACKOFF_BASE = 1.0
BACKOFF_MAX = 30.0
BACKOFF_WARN = 5  # 连续秒崩这么多次就提醒


def snapshot() -> dict[str, float]:
    """项目里被监视文件的 (路径 -> mtime)。

    用 os.walk 并在遍历时就把 venv/__pycache__ 等目录从 dirs 里摘掉，
    否则光是走一遍 venv 里的几千个文件就要几十毫秒。
    """
    out: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            if Path(name).suffix not in WATCH_SUFFIXES:
                continue
            path = Path(dirpath) / name
            try:
                out[str(path)] = path.stat().st_mtime
            except OSError:
                pass
    return out


def esc_listener(stop: threading.Event) -> None:
    """按 Esc → 退出监督进程。

    隐藏窗口跑（run.cmd 会设 NICO_NO_ESC=1）或不是交互式终端时不监听——
    免得在看不见的控制台里被莫名其妙的按键/EOF 触发退出。
    """
    if os.getenv("NICO_NO_ESC") == "1":
        return
    if not (sys.stdin and sys.stdin.isatty()):
        return
    try:
        import msvcrt
    except ImportError:
        return
    while not stop.is_set():
        try:
            if msvcrt.kbhit() and msvcrt.getwch() == "\x1b":
                stop.set()
                return
        except Exception:
            return
        time.sleep(0.05)


def _force_utf8() -> None:
    """重定向到文件时 Python 默认按 GBK 写，中文日志会花掉——统一改 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _force_utf8()
    target = sys.argv[1] if len(sys.argv) > 1 else "run.py"

    stop = threading.Event()
    threading.Thread(target=esc_listener, args=(stop,), daemon=True).start()

    env = dict(
        os.environ, NICO_NO_ESC="1", NICO_SUPERVISED="1", PYTHONIOENCODING="utf-8"
    )
    child: subprocess.Popen | None = None

    def start() -> subprocess.Popen:
        print(f"[dev] 启动 {target} …", flush=True)
        return subprocess.Popen([sys.executable, target], cwd=ROOT, env=env)

    def stop_child(c: subprocess.Popen | None) -> None:
        if c is not None and c.poll() is None:
            print("[dev] 关闭旧的进程 …", flush=True)
            c.terminate()
            try:
                c.wait(timeout=5)
            except subprocess.TimeoutExpired:
                c.kill()

    last = snapshot()
    child = start()
    started_at = time.monotonic()
    fast_exits = 0
    reason = "正常结束"
    try:
        while not stop.is_set():
            time.sleep(POLL)
            current = snapshot()
            if current != last:
                changed = [k for k in current if last.get(k) != current[k]]
                last = current
                print(f"[dev] 检测到改动：{', '.join(Path(c).name for c in changed)} → 重启", flush=True)
                stop_child(child)
                child = start()
                started_at = time.monotonic()
                fast_exits = 0
            elif child.poll() is not None:
                lived = time.monotonic() - started_at
                if child.returncode == 0:
                    # 正常退出（多半是 /restart）→ 不算崩，快速拉起
                    fast_exits = 0
                    delay = BACKOFF_BASE
                    why = "正常退出（可能是 /restart）"
                else:
                    if lived < FAST_EXIT:
                        fast_exits += 1
                    else:
                        fast_exits = 0
                    if fast_exits >= BACKOFF_WARN:
                        print(
                            f"[dev] 已经连续秒崩 {fast_exits} 次了，先退避再说（按 Esc 退出，或者翻上面的日志找报错）",
                            flush=True,
                        )
                    delay = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** max(0, fast_exits - 1)))
                    why = f"崩了（code={child.returncode}）"
                print(
                    f"[dev] {target} {why}，活了 {lived:.1f}s，{delay:g} 秒后重启…",
                    flush=True,
                )
                time.sleep(delay)
                last = snapshot()
                child = start()
                started_at = time.monotonic()
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    finally:
        stop_child(child)

    if reason == "正常结束" and stop.is_set():
        reason = "Esc"
    print(f"[dev] 已退出（{reason}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
