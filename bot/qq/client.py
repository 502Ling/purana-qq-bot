"""QQ 网关长连接：心跳、事件分发、断线重连。

协议要点（opcode）：
  10 hello          → 拿 heartbeat_interval，然后发 op2 identify
   2 identify       → {"token": "QQBot xxx", "intents": 1<<25, "shard": [0,1]}
   1 heartbeat      → 按 heartbeat_interval 定时发，d 带上一次的 seq
  11 ack            → 心跳确认
   0 event          → t=READY / C2C_MESSAGE_CREATE ...
   7 reconnect      → 服务端要求重连（这里直接重连，不做 resume）
   9 invalid session → 重新鉴权
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aiohttp

from .auth import TokenManager
from .gateway import get_ws_url

log = logging.getLogger("nico.qq.client")

INTENTS = 1 << 25  # 私聊 + 群聊事件
RECONNECT_DELAY = 5  # 秒

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class QQBotClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: TokenManager,
        on_event: EventHandler,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._on_event = on_event
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._heartbeat_interval = 45.0
        self._seq: int | None = None
        self._closing = False

    async def run(self) -> None:
        """主循环：连上 → 收消息 → 断了就重连，直到被取消。"""
        while not self._closing:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("连接异常：%s", exc)
            if self._closing:
                break
            log.info("%s 秒后重连…", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    async def close(self) -> None:
        self._closing = True
        await self._stop_heartbeat()
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ---------- 连接 ----------

    async def _connect_once(self) -> None:
        token = await self._tokens.get(self._session)
        ws_url = await get_ws_url(self._session, token)
        log.info("正在连接网关…")
        async with self._session.ws_connect(ws_url, heartbeat=None) as ws:
            self._ws = ws
            log.info("网关已连接")
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(json.loads(msg.data), token)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.warning("WebSocket 错误：%s", ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    log.info("WebSocket 已关闭")
                    break
        self._ws = None

    async def _handle(self, payload: dict[str, Any], token: str) -> None:
        op = payload.get("op")
        if payload.get("s") is not None:
            self._seq = payload["s"]

        if op == 10:  # hello
            self._heartbeat_interval = payload["d"]["heartbeat_interval"] / 1000
            log.info("收到 hello，心跳间隔 %.0f 秒", self._heartbeat_interval)
            await self._identify(token)
        elif op == 11:  # 心跳确认
            log.debug("心跳已确认")
        elif op == 7:  # 服务端要求重连
            log.info("服务端要求重连")
            if self._ws:
                await self._ws.close()
        elif op == 9:  # invalid session
            log.warning("会话失效，重连后将重新鉴权")
            self._seq = None
            if self._ws:
                await self._ws.close()
        elif op == 0:  # 事件
            await self._dispatch(payload)
        else:
            log.debug("未处理的 opcode：%s", op)

    async def _identify(self, token: str) -> None:
        await self._send(
            {
                "op": 2,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": INTENTS,
                    "shard": [0, 1],
                    "properties": {},
                },
            }
        )
        log.info("已发送鉴权信息（identify）")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("t")
        data = payload.get("d") or {}
        if event_type == "READY":
            session_id = data.get("session_id", "")
            log.info("READY，会话已就绪（session_id=%s…）", str(session_id)[:8])
            self._start_heartbeat()
        try:
            await self._on_event({"type": event_type, "data": data})
        except Exception:
            log.exception("处理事件 %s 时出错", event_type)

    # ---------- 心跳 ----------

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _stop_heartbeat(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                await self._send({"op": 1, "d": self._seq})
                log.debug("已发送心跳（seq=%s）", self._seq)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("心跳异常：%s", exc)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_json(payload)
