"""日志：UTF-8、控制台 + 文件轮转。

本机 ANSI 代码页是 GBK(936)，Python 的 stdout 也是 GBK，
不显式改成 UTF-8 的话，打印中文会直接抛异常。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def describe_exc(exc: BaseException) -> str:
    """把异常写成一能查案的一行：类型 + 消息（+ httpx/aiohttp 的请求信息）。

    只写 %s 是不行的：TimeoutError 这类异常的 str() 就是空字符串，
    日志里会只剩一个冒号，事后根本查不出来。
    """
    name = type(exc).__name__
    msg = str(exc).strip()
    out = f"{name}: {msg}" if msg else f"{name}（异常自带消息为空）"

    request = getattr(exc, "request", None)  # httpx
    if request is not None:
        try:
            out += f" [{request.method} {request.url}]"
        except Exception:
            pass
    response = getattr(exc, "response", None)  # aiohttp / httpx
    status = getattr(response, "status", None) or getattr(response, "status_code", None)
    if status:
        out += f" <- HTTP {status}"
    return out


def exc_kind(exc: BaseException) -> str:
    """粗分类：timeout / connect / other，给“人看的提示语”用。

    aiohttp 的异常几乎都继承 OSError（连不上、被断开），
    所以 OSError 一并归到 connect。
    """
    if isinstance(exc, TimeoutError):  # asyncio/aiohttp 的各种超时（Py 3.11+ 同名）
        return "timeout"
    if isinstance(exc, OSError):
        return "connect"
    return "other"


def short(text: object, limit: int = 300) -> str:
    """日志里截断长文本（网关报错动不动一屏 HTML）。"""
    s = str(text).replace("\r", " ").replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + f"…（共 {len(s)} 字）"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s %(levelname)s] %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "nico.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # aiohttp 的访问日志太吵，调高一级
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
