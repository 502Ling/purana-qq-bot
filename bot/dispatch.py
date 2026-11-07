"""消息分发：不同的人可以并行处理，同一个人仍然按顺序。

原来 run.py 里是「收到一条 → 处理完 → 才回去收下一条」，一条慢的消息会把后面
全堵住。这里把每条消息丢成独立 task：
- 不同 openid：并发跑（受 max_concurrent 限制）
- 同一个 openid：拿同一把锁 → 严格按先后顺序，回复不会乱序
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("purana.dispatch")


class Dispatcher:
    def __init__(self, max_concurrent: int = 8) -> None:
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, key: str, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """把一条消息的处理丢到后台，立刻返回（不阻塞收消息的循环）。"""
        task = asyncio.create_task(self._run(key, coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, key: str, coro: Awaitable[Any]) -> Any:
        async with self._locks.setdefault(key, asyncio.Lock()):
            async with self._sem:
                return await coro

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def shutdown(self) -> None:
        """退出时把还在处理的都取消掉。"""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
