"""监听键盘 Esc —— 给 run.py 用的「按一下 Esc 就退出」。

只在 Windows 的交互式控制台里生效；非交互（重定向 / 无控制台）或非 Windows
平台就直接"永久等待"，不影响主流程（Ctrl+C 依旧可用）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time

log = logging.getLogger("nico.keys")

ESC = "\x1b"


async def wait_for_escape(poll: float = 0.05) -> None:
    """等用户按 Esc 键（阻塞直到按下）。不支持的环境则一直等下去。"""
    if os.getenv("NICO_NO_ESC") == "1":
        log.info("NICO_NO_ESC=1，Esc 监听已禁用（由 dev.py 那样的监督进程接管）")
        await asyncio.Event().wait()
        return

    if not (sys.stdin and sys.stdin.isatty()):
        log.info("标准输入不是控制台，Esc 退出不可用（Ctrl+C 仍可退出）")
        await asyncio.Event().wait()
        return

    try:
        import msvcrt  # Windows only
    except ImportError:
        log.info("非 Windows 平台，Esc 退出不可用（Ctrl+C 仍可退出）")
        await asyncio.Event().wait()
        return

    loop = asyncio.get_running_loop()
    event = asyncio.Event()

    def _read_keys() -> None:
        while not event.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch == ESC:
                        loop.call_soon_threadsafe(event.set)
                        return
            except Exception:  # 控制台没了之类
                return
            time.sleep(poll)

    threading.Thread(target=_read_keys, daemon=True, name="esc-watch").start()
    try:
        await event.wait()
    finally:
        event.set()  # 让监听线程尽快收工
