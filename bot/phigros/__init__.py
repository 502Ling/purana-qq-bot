"""Phigros 工具（不依赖玩家存档的那部分）。

只做三件事：查曲、查谱面、发曲绘。
数据来自**共享**的曲库目录（``.env`` 的 ``PGR_DATA_ROOT``），只读。
"""
from __future__ import annotations

from . import commands, data, illus, notes, paths

__all__ = ["commands", "data", "illus", "notes", "paths"]
