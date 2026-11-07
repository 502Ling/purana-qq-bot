"""学校网关（ECUST）客户端 —— 直连裸模型（普拉娜），完全绕开 OpenClaw。

跟 bot/agent.py 的区别：
- agent.py 打的是本机 OpenClaw 网关（``/v1/chat/completions``），回复来自「妮可本人」；
- 这里打的是学校网关，回复来自裸模型，**没有工具**；但带上 history 就能记住上文。
"""
from __future__ import annotations

import json
import logging
import time

import aiohttp

from .log import describe_exc, exc_kind, short

log = logging.getLogger("purana.ecust")

# 出错时给用户看的话（普拉娜那边比较平静，日志里另有详细版）
FRIENDLY = {
    "timeout": "（ECUST 网关超时了——它大概在忙，稍后再试）",
    "connect": "（ECUST 连不上，稍后再试）",
    "other": "（ECUST 咕了，稍后再试）",
}

TIMEOUT = aiohttp.ClientTimeout(total=180)


class EcustClient:
    """最小 OpenAI 兼容客户端。base 形如 https://ai.ecustcic.com/api/v1。"""

    def __init__(
        self, base: str, key: str, default_model: str, timeout: float = 180.0
    ) -> None:
        self._base = base.rstrip("/")
        self._key = key
        self._default_model = default_model
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def default_model(self) -> str:
        return self._default_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    async def models(self, session: aiohttp.ClientSession) -> list[str]:
        """拉取模型列表；失败返回空列表。"""
        try:
            async with session.get(
                f"{self._base}/models", headers=self._headers(), timeout=self._timeout
            ) as resp:
                data = await resp.json(content_type=None)
        except Exception as exc:
            log.warning("拉取 ECUST 模型列表失败：%s", describe_exc(exc))
            return []

        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            log.warning("ECUST 模型列表格式异常：%s", str(data)[:300])
            return []
        return [r["id"] for r in rows if isinstance(r, dict) and r.get("id")]

    async def ask(
        self,
        session: aiohttp.ClientSession,
        text: str,
        model: str | None = None,
        history: list[dict[str, str]] | None = None,
        system: str | None = None,
    ) -> tuple[str, bool]:
        """问裸模型。返回 (回复文本, 是否成功)。

        history 是之前的 {role, content} 列表，带上她就"记得"上文。
        """
        model = model or self._default_model

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        payload = {"model": model, "messages": messages}

        t0 = time.monotonic()
        try:
            async with session.post(
                f"{self._base}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            ) as resp:
                status = resp.status
                body = await resp.text()
        except Exception as exc:
            log.warning(
                "调用 ECUST 网关失败：%s（model=%s，prompt %d 字，用时 %.1fs，超时 %ss）",
                describe_exc(exc),
                model,
                len(text),
                time.monotonic() - t0,
                self._timeout.total,
            )
            return FRIENDLY[exc_kind(exc)], False

        elapsed = time.monotonic() - t0

        if status >= 400:
            log.warning(
                "ECUST HTTP %s（model=%s，用时 %.1fs）：%s",
                status,
                model,
                elapsed,
                short(body),
            )
            return f"（ECUST HTTP {status}，稍后再试）", False

        try:
            data = json.loads(body)
        except ValueError:
            log.warning(
                "ECUST 返回不是 JSON（HTTP %s，用时 %.1fs）：%s",
                status,
                elapsed,
                short(body),
            )
            return "（ECUST 返回格式异常）", False

        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            log.warning("ECUST 返回错误（用时 %.1fs）：%s", elapsed, short(err))
            return f"（ECUST 出错：{msg}）", False

        try:
            content = data["choices"][0]["message"]["content"] or "（空回复）"
        except (KeyError, IndexError, TypeError):
            log.warning("ECUST 返回格式异常（用时 %.1fs）：%s", elapsed, short(data))
            return "（ECUST 返回格式异常）", False
        return content, True
