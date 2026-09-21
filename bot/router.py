"""分流：斜杠命令走本地，其余走 agent。

这里刻意做成「纯判断、无状态」的函数 —— 两边互不影响，
从结构上就成立了，不靠自觉。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Route:
    kind: str      # "command" 或 "agent"
    payload: str   # command: 原始命令文本；agent: 去掉首尾空白的文本


def route(text: str) -> Route:
    stripped = text.strip()
    if stripped.startswith("/"):
        return Route("command", stripped)
    return Route("agent", stripped)
