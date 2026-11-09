"""Phigros 曲库（离线，不需要账号、不需要网络）。

数据源是**共享的**自维护 CSV（见 bot/phigros/paths.py 里的资源根目录）：
- ``songs.csv`` ：一行一首 —— id, name, aliases, aliases_manual, composer, illustrator, pack, bpm
- ``charts.csv``：一行一首×难度 —— id, name, difficulty, const, charter, tap, drag, hold, flick, notes, bpm

⚠️ 从 Nico_21599 版移植过来的（那边是硬编码路径，这边改成配置驱动）。
只读为主；唯一的写操作是 ``add_manual_alias``（追加手动别名到共享 CSV），
写入的是**同一份数据**，所以两边看到的别名是一致的 —— 这正是「单源」的意义。
"""
from __future__ import annotations

import csv
import io
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

log = logging.getLogger("purana.phigros.data")

DIFF_ORDER = ("EZ", "HD", "IN", "AT")
ALL_DIFFS = ("EZ", "HD", "IN", "AT", "SP", "Legacy")
_PUNCT = " 　·・,，.。!！?？'\"“”‘’()（）[]【】-—_/+&"
_BASE = re.compile(r"\.\d+$")  # 去掉官方 id 尾部的版本号，如 "X.Y.0" -> "X.Y"


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch not in _PUNCT)


def _merge_aliases(*fields: str | None) -> list[str]:
    """合并 社区别名 / 手改别名（用 | 分隔），保序去重。"""
    out: list[str] = []
    for f in fields:
        for a in (f or "").split("|"):
            a = a.strip()
            if a and a not in out:
                out.append(a)
    return out


@dataclass
class Song:
    song_id: str
    name: str
    composer: str = ""
    illustrator: str = ""
    charters: dict[str, str] = field(default_factory=dict)  # {"EZ": "..."}
    consts: dict[str, float] = field(default_factory=dict)  # {"EZ": 1.0}
    pack: str = ""
    aliases: list[str] = field(default_factory=list)
    bpm: str = ""

    def const_of(self, label: str) -> float | None:
        return self.consts.get(label)

    def fmt_consts(self) -> str:
        return " / ".join(f"{d} {self.consts[d]:.1f}" for d in DIFF_ORDER if d in self.consts)

    def fmt_charters(self) -> str:
        return " / ".join(f"{d} {self.charters[d]}" for d in DIFF_ORDER if d in self.charters)


def _songs_csv() -> Path | None:
    d = paths.phigros_dir()
    return (d / "songs.csv") if d else None


def _charts_csv() -> Path | None:
    d = paths.phigros_dir()
    return (d / "charts.csv") if d else None


def _load() -> list[Song]:
    songs_csv = _songs_csv()
    charts_csv = _charts_csv()
    if not songs_csv or not songs_csv.is_file():
        return []

    songs: dict[str, Song] = {}
    try:
        rows = csv.DictReader(open(songs_csv, encoding="utf-8-sig"))
        for r in rows:
            sid = _BASE.sub("", (r.get("id") or "").strip())
            if not sid:
                continue
            songs[sid] = Song(
                song_id=sid,
                name=(r.get("name") or "").strip(),
                composer=(r.get("composer") or "").strip(),
                illustrator=(r.get("illustrator") or "").strip(),
                aliases=_merge_aliases(r.get("aliases"), r.get("aliases_manual")),
                pack=(r.get("pack") or "").strip(),
                bpm=(r.get("bpm") or "").strip(),
            )
    except OSError:
        return []

    if charts_csv and charts_csv.is_file():
        try:
            for r in csv.DictReader(open(charts_csv, encoding="utf-8-sig")):
                sid = _BASE.sub("", (r.get("id") or "").strip())
                s = songs.get(sid)
                if not s:
                    continue
                d = (r.get("difficulty") or "").strip()
                if not d:
                    continue
                c = (r.get("const") or "").strip()
                if c:
                    try:
                        s.consts[d] = float(c)
                    except ValueError:
                        pass
                ch = (r.get("charter") or "").strip()
                if ch:
                    s.charters[d] = ch
        except OSError:
            pass
    return list(songs.values())


SONGS: list[Song] = _load()

_SOURCES = ("songs.csv", "charts.csv")


def _signature() -> float:
    latest = 0.0
    d = paths.phigros_dir()
    if not d:
        return 0.0
    for name in _SOURCES:
        try:
            latest = max(latest, (d / name).stat().st_mtime)
        except OSError:
            pass
    return latest


_MTIME: float = _signature()


def maybe_reload() -> bool:
    """数据文件变了就重读（微秒级）。返回是否重载了。"""
    global SONGS, _MTIME
    sig = _signature()
    if sig != _MTIME:
        SONGS = _load()
        _MTIME = sig
        return True
    return False


def ok() -> bool:
    return len(SONGS) > 0


def find(query: str, limit: int = 6) -> list[Song]:
    """按曲名/别名/作曲/ID 模糊查找（不区分大小写）。精确 > 前缀 > 包含。"""
    q = _norm(query)
    if not q:
        return []
    scored: list[tuple[int, Song]] = []
    for s in SONGS:
        names = [_norm(s.name)] + [_norm(a) for a in s.aliases]
        comp = _norm(s.composer)
        sid = _norm(s.song_id)
        if q in names:
            rank = 0
        elif any(n.startswith(q) for n in names) or comp == q:
            rank = 1
        elif any(q in n for n in names):
            rank = 2
        elif q in comp or q in sid:
            rank = 3
        else:
            continue
        scored.append((rank, s))
    scored.sort(key=lambda t: (t[0], t[1].name.lower()))
    return [s for _, s in scored[:limit]]


def random_song(diff: str | None = None) -> Song | None:
    d = diff.upper() if diff else None
    pool = [s for s in SONGS if (d in s.consts if d else True)]
    return random.choice(pool) if pool else None


def hardest(diff: str = "IN", n: int = 5) -> list[Song]:
    d = diff.upper()
    pool = [s for s in SONGS if d in s.consts]
    pool.sort(key=lambda s: s.consts[d], reverse=True)
    return pool[:n]


def add_manual_alias(song_id: str, alias: str) -> tuple[bool, str]:
    """把手动别名追加到 `songs.csv` 的 `aliases_manual` 列（`|` 分隔，去重）。

    ⚠️ 写的是**共享的** CSV（和 Nico 用的是同一份）—— 这正是单源的好处：
    两边加的别名互相可见。

    返回 (ok, msg)：成功 msg=新的别名串；失败 msg=原因。
    整体重写整张表（保持行序/列序/BOM/CRLF），写完自动 reload。
    """
    alias = (alias or "").strip()
    if not alias:
        return False, "别名为空"
    songs_csv = _songs_csv()
    if not songs_csv or not songs_csv.is_file():
        return False, "找不到 songs.csv"
    try:
        raw = songs_csv.read_bytes()
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"读取失败：{exc}"
    if not rows:
        return False, "表是空的"
    header = rows[0]
    if "aliases_manual" not in header or "id" not in header:
        return False, "songs.csv 缺少 aliases_manual / id 列"
    i_id, i_man = header.index("id"), header.index("aliases_manual")
    i_ali = header.index("aliases") if "aliases" in header else None

    target = next(
        (
            r
            for r in rows[1:]
            if len(r) > i_id and _BASE.sub("", r[i_id].strip()) == song_id
        ),
        None,
    )
    if target is None:
        return False, f"表里没找到 id={song_id}"

    while len(target) < len(header):
        target.append("")
    manual = [a.strip() for a in target[i_man].split("|") if a.strip()]
    seen = set(manual)
    if i_ali is not None:
        seen |= {a.strip() for a in target[i_ali].split("|") if a.strip()}
    if alias in seen:
        return False, "已存在"
    manual.append(alias)
    target[i_man] = "|".join(manual)

    try:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerows(rows)
        out = buf.getvalue().encode("utf-8")
        if raw[:3] == b"\xef\xbb\xbf":
            out = b"\xef\xbb\xbf" + out
        songs_csv.write_bytes(out)
    except OSError as exc:
        return False, f"写入失败：{exc}"
    maybe_reload()
    return True, "|".join(manual)
