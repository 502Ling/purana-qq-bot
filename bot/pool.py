"""随机图片池：把图片按「子目录 = 池子」组织，发的时候随机抽一张。

为什么要有这个：
`/image cat` 要人报精确文件名，表情包一多根本记不住。这里把图丢进
`data/pool/表情包/` 这种子目录就行，命令直接 `/img 表情包` 随机抽一张。

设计要点：
1. **一个池子 = 池根目录下的一个子目录**，子目录里可以再分层（递归扫）。
2. **只有 png/jpg/jpeg 能当图片发** —— QQ 官方图片接口不收 gif/webp/bmp，
   那些只能降级成「文件」发出去（还在，但就不是图片了）。
   所以扫描时把它们单独统计，在提示里说明，不混进可发列表。
3. **别名**：`.env` 的 POOL_ALIASES 可以把 `猫` 映射到 `表情包`，
   免得为了一个常用池子专门改目录名。
4. **TTL 缓存**：目录树扫一遍不贵，但没必要每次都扫。默认缓存 5 秒，
   刚丢进去的图最多 5 秒后就能抽到（不用重启机器人）。

⚠️ 池子目录在 .gitignore 的 data/ 下面，不会进仓库。
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("purana.pool")

# QQ 官方图片接口只认这三种（见 bot/media.py 的 IMAGE_EXTS）
SENDABLE_EXTS = {".png", ".jpg", ".jpeg"}
# 认识但发不出「图片」的格式（会降级成文件）
OTHER_IMAGE_EXTS = {".gif", ".webp", ".bmp", ".jfif", ".avif"}

# 扫描结果缓存多久（秒）—— 刚丢进去的图最多等这么久
SCAN_TTL = 5.0


class PoolError(RuntimeError):
    """池子相关的错误（找不到池子、池子是空的……）。"""


@dataclass
class Pool:
    """一个池子。"""

    name: str                 # 池子名（子目录名）
    dir: Path                 # 池子目录
    files: list[Path] = field(default_factory=list)      # 能当图片发的
    others: list[Path] = field(default_factory=list)     # gif 之类（发不了图片）
    skipped: int = 0                                   # 跳过的非图片文件数

    @property
    def count(self) -> int:
        return len(self.files)


def parse_aliases(raw: str) -> dict[str, str]:
    """解析 POOL_ALIASES：`猫=表情包,表情=表情包` → {"猫": "表情包", ...}。

    格式：别名=池子名，逗号分隔。左边是用户敲的，右边是**子目录名**。
    允许别名指向另一个别名（解析时会一直追到最终池子，最多追 5 步防环）。
    """
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        left, right = item.split("=", 1)
        left, right = left.strip(), right.strip()
        if left and right:
            out[left.lower()] = right
    return out


class PoolManager:
    """管理所有图片池。"""

    def __init__(self) -> None:
        self._root: Path | None = None
        self._aliases: dict[str, str] = {}
        self._default: str = ""
        self._cache: dict[str, Pool] = {}
        self._cache_at: float = 0.0
        self._last_pick: dict[str, str] = {}   # 池子名 -> 上次抽到的文件（避免连着重复）

    # ------------------------------------------------------------ 配置

    def configure(
        self,
        root: Path | str | None,
        aliases: dict[str, str] | None = None,
        default_pool: str = "",
    ) -> None:
        """设置池根目录 + 别名 + 默认池子。"""
        self._root = Path(root) if root else None
        self._aliases = {k.lower(): v for k, v in (aliases or {}).items()}
        self._default = (default_pool or "").strip()
        self.invalidate()

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def default_pool(self) -> str:
        return self._default

    def invalidate(self) -> None:
        """清掉缓存（下次调用重新扫盘）。"""
        self._cache_at = 0.0

    # ------------------------------------------------------------ 扫描

    def _scan_pool(self, name: str, directory: Path) -> Pool:
        pool = Pool(name=name, dir=directory)
        try:
            entries = list(directory.rglob("*"))
        except OSError as exc:
            log.warning("扫池子 %s 失败：%s", name, exc)
            return pool

        for p in entries:
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext in SENDABLE_EXTS:
                pool.files.append(p)
            elif ext in OTHER_IMAGE_EXTS:
                pool.others.append(p)
            else:
                pool.skipped += 1
        pool.files.sort()
        pool.others.sort()
        return pool

    def pools(self, force: bool = False) -> dict[str, Pool]:
        """扫出所有池子（带 TTL 缓存）。"""
        now = time.monotonic()
        if not force and self._cache and (now - self._cache_at) < SCAN_TTL:
            return self._cache
        if self._root is None or not self._root.is_dir():
            self._cache = {}
            self._cache_at = now
            return self._cache

        out: dict[str, Pool] = {}
        try:
            children = sorted(self._root.iterdir())
        except OSError as exc:
            log.warning("列池子目录失败：%s", exc)
            children = []
        for d in children:
            if not d.is_dir() or d.name.startswith("."):
                continue
            out[d.name] = self._scan_pool(d.name, d)
        self._cache = out
        self._cache_at = now
        return out

    # ------------------------------------------------------------ 找池子

    def _follow_alias(self, name: str) -> str:
        """把别名一路追到最终的池子名（最多 5 步，防环）。"""
        cur = name
        for _ in range(5):
            nxt = self._aliases.get(cur.lower())
            if not nxt or nxt == cur:
                break
            cur = nxt
        return cur

    def resolve(self, name: str) -> str | None:
        """把用户敲的名字解析成池子名。找不到返回 None。

        匹配顺序：完全同名 → 别名 → 忽略大小写同名 → 唯一包含匹配。
        """
        pools = self.pools()
        if not pools:
            return None

        key = (name or "").strip()
        if not key:
            return None

        # 1) 完全同名
        if key in pools:
            return key

        # 2) 别名
        aliased = self._follow_alias(key)
        if aliased in pools:
            return aliased

        # 3) 忽略大小写
        lower = key.lower()
        for pname in pools:
            if pname.lower() == lower:
                return pname

        # 4) 唯一包含匹配（"表情" → "表情包"）
        hits = [p for p in pools if lower in p.lower() or p.lower() in lower]
        if len(hits) == 1:
            return hits[0]
        return None

    def similar(self, name: str) -> list[str]:
        """列出名字相近的池子（给「没找到」的时候提示用）。"""
        pools = self.pools()
        key = (name or "").strip().lower()
        if not key:
            return sorted(pools)
        hits = [p for p in pools if key in p.lower() or p.lower() in key]
        return sorted(hits) if hits else sorted(pools)

    # ------------------------------------------------------------ 抽图

    def pick(self, name: str = "") -> tuple[Path, str]:
        """随机抽一张图。返回 (文件路径, 池子名)。

        name 为空 → 用默认池子（.env 的 POOL_DEFAULT）。
        """
        pools = self.pools()
        if not pools:
            raise PoolError("图片池还没建好（目录是空的）。")

        if name:
            pool_name = self.resolve(name)
            if pool_name is None:
                near = self.similar(name)
                hint = ("、".join(near) if near else "（一个都没有）")
                raise PoolError(f"没有叫「{name}」的池子。现在有的池子：{hint}")
        else:
            pool_name = self._default
            if not pool_name:
                raise PoolError(
                    "没指定池子。用法：/img <池子名>；看有哪些池子用 /img list"
                )
            resolved = self.resolve(pool_name)
            if resolved is None:
                raise PoolError(f"默认池子「{pool_name}」不存在。")
            pool_name = resolved

        pool = pools[pool_name]
        if not pool.files:
            if pool.others:
                raise PoolError(
                    f"池子「{pool_name}」里只有 gif/webp 这种格式，"
                    "QQ 图片接口发不了 —— 转成 png/jpg 才行。"
                )
            raise PoolError(f"池子「{pool_name}」是空的。")

        # 尽量不连着抽到同一张（池子里只有一张时没办法）
        choices = pool.files
        if len(choices) > 1:
            last = self._last_pick.get(pool_name)
            others = [p for p in choices if str(p) != last]
            if others:
                choices = others

        picked = random.choice(choices)
        self._last_pick[pool_name] = str(picked)
        return picked, pool_name

    # ------------------------------------------------------------ 展示

    def summary(self) -> str:
        """列出所有池子（名字 + 数量）。"""
        pools = self.pools()
        if not pools:
            return "还没有任何图片池。"
        lines = ["图片池："]
        for name, pool in sorted(pools.items()):
            extra = ""
            if pool.others:
                extra = f"（另有 {len(pool.others)} 张 gif/webp 发不了）"
            lines.append(f"· {name}：{pool.count} 张{extra}")
        return "\n".join(lines)


# 全局单例（命令层用它，run.py 负责 configure）
POOLS = PoolManager()
