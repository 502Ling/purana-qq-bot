"""谱面音符统计：读共享曲库的 charts.csv。

charts.csv 一行一首×难度，含 tap/drag/hold/flick/notes。带 mtime 热重载。
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from . import paths

log = logging.getLogger("purana.phigros.notes")

DIFF_ORDER = ("EZ", "HD", "IN", "AT", "SP", "Legacy")
_TYPES = ("tap", "drag", "hold", "flick")
_BASE = re.compile(r"\.\d+$")

_INDEX: dict[str, dict] = {}
_MTIME: float = -1.0


def _charts_csv() -> Path | None:
    d = paths.phigros_dir()
    return (d / "charts.csv") if d else None


def _sig() -> float:
    p = _charts_csv()
    if not p:
        return -1.0
    try:
        return p.stat().st_mtime
    except OSError:
        return -1.0


def maybe_reload() -> bool:
    global _INDEX, _MTIME
    sig = _sig()
    if sig == _MTIME and _INDEX:
        return False
    _MTIME = sig
    idx: dict[str, dict] = {}
    p = _charts_csv()
    if p and p.is_file():
        try:
            for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                sid = _BASE.sub("", (r.get("id") or "").strip())
                d = (r.get("difficulty") or "").strip()
                if not sid or not d:
                    continue
                rec = {}
                for k in _TYPES:
                    try:
                        rec[k] = int(r.get(k) or 0)
                    except ValueError:
                        rec[k] = 0
                try:
                    rec["total"] = int(r.get("notes") or 0)
                except ValueError:
                    rec["total"] = sum(rec[k] for k in _TYPES)
                idx.setdefault(sid, {})[d] = rec
        except OSError:
            idx = {}
    _INDEX = idx
    return True


def ok() -> bool:
    maybe_reload()
    return bool(_INDEX)


def lookup(song_id: str) -> dict | None:
    """返回 {难度: {tap,drag,hold,flick,total}}；没有就 None。"""
    maybe_reload()
    return _INDEX.get(song_id)


def count() -> int:
    maybe_reload()
    return len(_INDEX)


def format_song(song, diff: str | None = None) -> str:
    """给一首歌（data.Song）出各难度的音符分布。"""
    diffs = lookup(song.song_id)
    if not diffs:
        return f"《{song.name}》暂时没有音符数据（曲库未覆盖）"

    keys = [d for d in DIFF_ORDER if d in diffs]
    if diff:
        keys = [d for d in keys if d == diff.upper()]
        if not keys:
            return f"《{song.name}》没有 {diff.upper()} 难度的音符数据"

    lines = [f"《{song.name}》音符分布（tap / drag / hold / flick）"]
    tot = {k: 0 for k in _TYPES}
    total_all = 0
    shown = 0
    for d in keys:
        r = diffs[d]
        # ⚠️ 共享曲库里可能有「难度行存在但数值全空」的情况（该难度没导出）。
        #    直接显示 0/0/0/0 会让人误以为「这歌全是0音符」，所以明确说未覆盖。
        if not r.get("total"):
            lines.append(f"{d:<6} （该难度数据未覆盖）")
            continue
        shown += 1
        vals = "/".join(str(r.get(k, 0)) for k in _TYPES)
        lines.append(f"{d:<6} {vals} = {r.get('total', 0)}")
        for k in _TYPES:
            tot[k] += r.get(k, 0)
        total_all += r.get("total", 0)

    if shown == 0:
        return f"《{song.name}》的音符数据暂未覆盖"
    if shown > 1:
        s = "/".join(str(tot[k]) for k in _TYPES)
        lines.append(f"{'合计':<6} {s} = {total_all}")
    return "\n".join(lines)
