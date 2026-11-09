"""曲绘查询：按曲目 id 找共享目录里的 PNG。

文件名 = 曲目 id（例 `DistortedFate.Sakuzyo.png`）。部分曲目有**难度变体**曲绘
（`<id>.EZ/.HD/.IN/.AT.png`）：
- 不指定难度：优先基础图 `<id>.png`，没有才依次退 EZ/HD/IN/AT；
- 指定难度：优先 `<id>.<难度>.png`，没有才退基础图 / 其他难度。
带目录 mtime 热重载。

⚠️ 曲绘目录是**共享**的（见 bot/phigros/paths.py）—— 这里只读，绝不写入。
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import paths

log = logging.getLogger("purana.phigros.illus")

_DIFFS = ("EZ", "HD", "IN", "AT")

_NAMES: set[str] = set()
_MTIME: float = -1.0


def _safe_id(song_id) -> str:
    """与 Nico 那边同一套（保证文件名对得上）。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(song_id))


def _dir() -> Path | None:
    return paths.illus_dir()


def _sig() -> float:
    d = _dir()
    if not d:
        return -1.0
    try:
        return d.stat().st_mtime
    except OSError:
        return -1.0


def maybe_reload() -> bool:
    global _NAMES, _MTIME
    sig = _sig()
    if sig == _MTIME and _NAMES:
        return False
    _MTIME = sig
    d = _dir()
    names: set[str] = set()
    if d and d.is_dir():
        try:
            names = {p.name for p in d.glob("*.png")}
        except OSError:
            names = set()
    _NAMES = names
    return True


def ok() -> bool:
    maybe_reload()
    return bool(_NAMES)


def variants(song_id: str) -> list[str]:
    """这首歌有哪些难度变体曲绘（如 ['EZ','HD','IN','AT']；没有则 []）。"""
    maybe_reload()
    base = _safe_id(song_id)
    return [d for d in _DIFFS if f"{base}.{d}.png" in _NAMES]


def lookup(song_id: str, diff: str | None = None) -> str | None:
    """返回曲绘 PNG 的绝对路径；没有就 None。

    指定 diff 时优先该难度变体，再退基础图和其他难度；不指定时优先基础图。
    """
    maybe_reload()
    base = _safe_id(song_id)
    order: list[str] = []
    d = (diff or "").strip().upper()
    if d:
        order.append(f"{base}.{d}.png")
    order.append(f"{base}.png")
    for x in reversed(_DIFFS):  # 无指定难度时，变体按 AT→IN→HD→EZ 退（默认 AT）
        order.append(f"{base}.{x}.png")
    seen: set[str] = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        if name in _NAMES:
            d2 = _dir()
            return str(d2 / name) if d2 else None
    return None


def count() -> int:
    maybe_reload()
    return len(_NAMES)
