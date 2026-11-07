"""日志：UTF-8、控制台 + 文件按天轮转。

本机 ANSI 代码页是 GBK(936)，Python 的 stdout 也是 GBK，
不显式改成 UTF-8 的话，打印中文会直接抛异常。
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import TimedRotatingFileHandler
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


def short(text: object, limit: int = 3000) -> str:
    """日志里截断长文本（网关报错动不动一屏 HTML）。"""
    s = str(text).replace("\r", " ").replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + f"…（共 {len(s)} 字）"


class DatedRotatingFileHandler(TimedRotatingFileHandler):
    """按天轮转，但文件名是 `purana-2026-10-23.log`，不是 `purana.log.2026-10-23`。

    默认实现把日期拼在完整文件名后面，日期就成了“扩展名”。这里改掉
    命名，并同步重写清理逻辑（默认的 getFilesToDelete 只认老格式，
    改名后会导致 backupCount 失效、旧日志永远不删）。
    """

    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self._base = Path(filename)
        self.namer = self._namer  # 轮转时由 TimedRotatingFileHandler 调用

    def _namer(self, default_name: str) -> str:
        # default_name 形如 .../purana.log.2026-10-23
        prefix = str(self._base) + "."
        date = default_name[len(prefix):] if default_name.startswith(prefix) else default_name.rsplit(".", 1)[-1]
        return str(self._base.with_name(f"{self._base.stem}-{date}{self._base.suffix}"))

    def _pattern(self) -> re.Pattern[str]:
        stem, suffix = self._base.stem, self._base.suffix
        return re.compile(rf"^{re.escape(stem)}-(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(suffix)}$")

    def getFilesToDelete(self) -> list[str]:
        """按新命名收集超出 backupCount 的旧日志（最老的先删）。"""
        if self.backupCount <= 0:
            return []
        pat = self._pattern()
        files: list[tuple[str, str]] = []
        for p in self._base.parent.glob(f"{self._base.stem}-*{self._base.suffix}"):
            m = pat.match(p.name)
            if m and self.extMatch.match(m.group(1)):
                files.append((m.group(1), str(p)))
        files.sort()
        if len(files) <= self.backupCount:
            return []
        return [path for _, path in files[: len(files) - self.backupCount]]


def _migrate_legacy_names(log_dir: Path) -> None:
    """把旧的 `purana.log.2026-10-22` 改成 `purana-2026-10-22.log`（幂等）。"""
    pat = re.compile(r"^(?P<stem>.+)\.log\.(?P<date>\d{4}-\d{2}-\d{2})$")
    for p in log_dir.glob("*.log.*"):
        m = pat.match(p.name)
        if not m:
            continue
        target = p.with_name(f"{m.group('stem')}-{m.group('date')}.log")
        if target.exists():
            continue
        try:
            p.rename(target)
        except OSError:
            pass  # 被占用就算了，不影响启动


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    log_dir.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_names(log_dir)

    # 每行都带完整日期，方便按天翻查
    fmt = logging.Formatter(
        "[%(asctime)s %(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 每天零点切一份（旧文件名如 purana-2026-10-11.log），保留 30 天
    file_handler = DatedRotatingFileHandler(
        log_dir / "purana.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # aiohttp 的访问日志太吵，调高一级
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
