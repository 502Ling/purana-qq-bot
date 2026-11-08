"""群复读：不同人发相同内容 → 隔 REPEAT_DELAY 秒复读原文。

规则（都收在这里，跟传输层解耦，方便离线验证）：
- 调用方只在「群内非 @ 的普通消息」上调用 observe()；
- **连续 REPEAT_MIN_COUNT 条相同文本，且其中至少 2 个不同的人** → 触发
  （默认 3 条；一个人单刷不算）；
- 触发后不立即发：起一个 delay 秒的定时；期间同群出现**不同文本** → 取消
  （话题继续了就不追）；
- **同一串只复读一次**：已复读的文本会被记住，之后继续发相同内容不再触发，
  直到出现别的文本；
- 复读只发纯文本：不带 msg_id、不带引用 → 走「主动消息」，在群里看起来就是
  一条新消息，而不是回复某个人（官方协议下主动消息受频控限制，见 README）；
- 每个群各自维护状态，互不串扰。

比 Nico_21599 版多做的：
- 文本超长截断（官方接口有消息长度上限，超了直接 40054007 报错）；
- 复读前再查一次「这串是不是机器人自己发的」由调用方保证（run.py 过滤 bot 消息）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("purana.repeat")

# send(group_openid, text) —— 由调用方接到具体的发送实现上
Send = Callable[[str, str], Awaitable[None]]

# 官方群消息 content 的长度上限（保守取 400，超了会被截）
MAX_TEXT_LEN = 400


class Repeater:
    def __init__(self, delay: float, send: Send, min_count: int = 3) -> None:
        self.delay = max(0.0, delay)
        self.min_count = max(2, min_count)   # 至少 2 条才谈得上"复读"
        self._send = send
        # gid -> (当前串文本, 已连续条数, 该串第一个人, 是否出现过别的发送者)
        self._streak: dict[str, tuple[str, int, str, bool]] = {}
        self._pending: dict[str, asyncio.Task] = {}    # gid -> 待复读任务
        self._fired: dict[str, str] = {}               # gid -> 已复读的文本（同一串只复读一次）

    # ---------- 对外 ----------

    def observe(self, gid: str, text: str, sender: str) -> None:
        """收到一条群内普通消息（非 @ 由调用方保证；命令/空这里再兜一层）。"""
        text = (text or "").strip()
        if not text or text.startswith("/"):
            self._reset(gid)   # 命令/空 → 当作话题打断
            return
        if len(text) > MAX_TEXT_LEN:
            text = text[:MAX_TEXT_LEN]

        # 这串已经复读过了 → 不再响应，直到出现别的文本
        if self._fired.get(gid) == text:
            return

        st = self._streak.get(gid)
        if st is not None and st[0] == text:
            # 同一串继续：累加条数，记下换没换人
            first, has_other = st[2], st[3]
            count = st[1] + 1
            if sender != first:
                has_other = True
            self._streak[gid] = (text, count, first, has_other)
        else:
            # 新文本：撤销待发、解锁，然后开新串
            self._cancel(gid)
            self._fired.pop(gid, None)
            self._streak[gid] = (text, 1, sender, False)
            count, has_other = 1, False

        # 攒够条数 + 至少换过一个人 → 上弦
        if count >= self.min_count and has_other:
            self._fired[gid] = text      # 上锁：同一串只复读一次
            self._arm(gid, text)

    async def close(self) -> None:
        for t in list(self._pending.values()):
            t.cancel()
        if self._pending:
            await asyncio.gather(*self._pending.values(), return_exceptions=True)
        self._pending.clear()
        self._streak.clear()
        self._fired.clear()

    # ---------- 内部 ----------

    def _reset(self, gid: str) -> None:
        self._cancel(gid)
        self._streak.pop(gid, None)
        self._fired.pop(gid, None)

    def _cancel(self, gid: str) -> None:
        t = self._pending.pop(gid, None)
        if t is not None:
            t.cancel()

    def _arm(self, gid: str, text: str) -> None:
        self._pending[gid] = asyncio.create_task(self._fire(gid, text))

    async def _fire(self, gid: str, text: str) -> None:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            return
        self._pending.pop(gid, None)
        try:
            await self._send(gid, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("复读发送失败（群 %s）", gid)
