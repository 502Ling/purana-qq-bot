"""Phigros 资源路径：统一从一个「资源根目录」解析。

设计要点（502 定的）：
- 曲库 + 曲绘**不复制**，直接引用一份共享的数据（现在指向 Nico_21599 仓库）。
- 资源根目录由 ``.env`` 的 ``PGR_DATA_ROOT`` 指定，**代码里不写死机器路径**。
- 将来把曲库数据挪到独立目录（比如 ``D:\\CIC\\QQbot\\phigros-data``），
  让 Nico 和普拉娜都引用它 → 只需改 ``.env`` 一行，**代码不用动**，
  而且就彻底跟 Nico 没关系了。

目录约定（相对资源根）：
- ``resources/phigros/``  —— songs.csv / charts.csv
- ``resources/Illustration/`` —— 曲绘 PNG（文件名 = 曲目 id）
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("purana.phigros.paths")

_ROOT: Path | None = None


def configure(root: str | Path | None = None) -> Path | None:
    """设置资源根目录（启动时由 run.py 调用）。"""
    global _ROOT
    if root:
        p = Path(str(root).strip().strip('"').strip("'"))
        if not p.is_absolute():
            p = p.resolve()
        _ROOT = p
    return _ROOT


def root() -> Path | None:
    """资源根目录。没 configure 就退回读环境变量（此时 dotenv 已加载）。"""
    global _ROOT
    if _ROOT is None:
        env = (os.getenv("PGR_DATA_ROOT") or "").strip()
        if env:
            p = Path(env.strip('"').strip("'"))
            if not p.is_absolute():
                p = p.resolve()
            _ROOT = p
    return _ROOT


def phigros_dir() -> Path | None:
    """曲库 CSV 目录（resources/phigros）。"""
    r = root()
    return (r / "resources" / "phigros") if r else None


def illus_dir() -> Path | None:
    """曲绘目录（resources/Illustration）。"""
    r = root()
    return (r / "resources" / "Illustration") if r else None


def status() -> str:
    """给用户看的状态说明（配置缺了 / 目录不存在时用）。"""
    r = root()
    if not r:
        return (
            "Phigros 曲库没配：请在 .env 里设 PGR_DATA_ROOT，"
            "指向曲库所在目录（该目录下要有 resources/phigros/ 和 resources/Illustration/）。"
        )
    pdir = phigros_dir()
    idir = illus_dir()
    if not pdir or not pdir.is_dir():
        return f"Phigros 曲库目录不存在：{pdir}"
    if not (pdir / "songs.csv").is_file():
        return f"缺 songs.csv：{pdir / 'songs.csv'}"
    if not idir or not idir.is_dir():
        return f"Phigros 曲绘目录不存在：{idir}"
    return ""
