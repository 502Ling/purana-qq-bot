"""把非命令消息转发给本机 OpenClaw 网关（就是妮可本人）。"""
from __future__ import annotations

import json
import logging
import time

import aiohttp

from .log import describe_exc, exc_kind, short

log = logging.getLogger("nico.agent")

TIMEOUT = aiohttp.ClientTimeout(total=180)

# 出错时给用户看的话（日志里另有一份详细版）
FRIENDLY = {
    "timeout": "（我这边还在忙上一条，排队等太久没插上话——稍后再喊我一次就好 (･ω･)）",
    "connect": "（我这边连不上，可能没在线上；稍后再喊我一次 (；ω；)）",
    "other": "（我这边咕了一下，稍后再喊我一次 (；ω；)）",
}


class AgentClient:
    def __init__(self, base: str, token: str, model: str) -> None:
        self._url = f"{base}/v1/chat/completions"
        self._token = token
        self._model = model

    async def ask(self, session: aiohttp.ClientSession, text: str, user: str) -> str:
        """user 用 qq:<openid> —— 同一个 user 值 = 同一个会话，天然按人隔离。"""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        payload = {
            "model": self._model,
            "user": user,
            "messages": [{"role": "user", "content": text}],
        }

        t0 = time.monotonic()
        try:
            async with session.post(
                self._url, json=payload, headers=headers, timeout=TIMEOUT
            ) as resp:
                status = resp.status
                body = await resp.text()
        except Exception as exc:
            log.warning(
                "调用网关失败：%s（model=%s，prompt %d 字，用时 %.1fs，超时 %ss）",
                describe_exc(exc),
                self._model,
                len(text),
                time.monotonic() - t0,
                TIMEOUT.total,
            )
            return FRIENDLY[exc_kind(exc)]

        elapsed = time.monotonic() - t0

        if status >= 500:
            log.warning(
                "网关 HTTP %s（model=%s，用时 %.1fs）：%s",
                status,
                self._model,
                elapsed,
                short(body),
            )
            return f"（我这边服务器出错了（HTTP {status}），稍后再喊我）"
        if status >= 400:
            log.warning(
                "网关 HTTP %s（model=%s，用时 %.1fs）：%s",
                status,
                self._model,
                elapsed,
                short(body),
            )
            return f"（这条请求被网关挡下来了（HTTP {status}））"

        try:
            data = json.loads(body)
        except ValueError:
            log.warning(
                "网关返回不是 JSON（HTTP %s，用时 %.1fs）：%s",
                status,
                elapsed,
                short(body),
            )
            return "（我这边回的东西看不懂，稍后再喊我）"

        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            log.warning("网关返回错误（用时 %.1fs）：%s", elapsed, short(err))
            return f"（我这边报了个错：{short(message, 80)}）"

        try:
            return data["choices"][0]["message"]["content"] or "（空回复）"
        except (KeyError, IndexError, TypeError):
            log.warning("网关返回格式异常（用时 %.1fs）：%s", elapsed, short(data))
            return "（我这边回的东西看不懂，稍后再喊我）"
