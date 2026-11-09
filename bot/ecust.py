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
        return await self.ask_content(
            session,
            [{"type": "text", "text": text}],
            model=model,
            history=history,
            system=system,
        )

    async def ask_images(
        self,
        session: aiohttp.ClientSession,
        text: str,
        images: list[str],
        model: str | None = None,
        history: list[dict[str, str]] | None = None,
        system: str | None = None,
    ) -> tuple[str, bool]:
        """带图片地问裸模型。images 是 data URL 列表（如 data:image/png;base64,...）。

        ⚠️ **只有视觉模型能用**（实测 ECUST 上 glm-4.7 / qwen3.5 可看图，
        deepseek-v4.1-flash 不行）。传错模型会得到一句空回复或格式报错。
        """
        content: list[dict[str, object]] = [{"type": "text", "text": text}]
        for url in images:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return await self.ask_content(
            session, content, model=model, history=history, system=system
        )

    async def ask_content(
        self,
        session: aiohttp.ClientSession,
        content: list[dict[str, object]],
        model: str | None = None,
        history: list[dict[str, str]] | None = None,
        system: str | None = None,
    ) -> tuple[str, bool]:
        """发一条多模态消息。content 是 OpenAI 格式的内容块列表。

        纯文本时 content 就是 [{"type": "text", "text": "..."}]，
        带图时再追加 {"type": "image_url", ...} 块。
        """
        model = model or self._default_model

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": content})

        payload = {"model": model, "messages": messages}

        # 日志里的字数统计：图片块算长度但不是字数，估个够用的数就行
        prompt_len = sum(
            len(str(c.get("text") or "")) for c in content if c.get("type") == "text"
        )

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
                prompt_len,
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
            content_out = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("ECUST 返回格式异常（用时 %.1fs）：%s", elapsed, short(data))
            return "（ECUST 返回格式异常）", False

        # content 可能是 str，也可能是内容块数组（部分网关这么返）
        if isinstance(content_out, list):
            content_out = "".join(
                str(c.get("text") or "") for c in content_out if isinstance(c, dict)
            )
        content_out = (content_out or "").strip()
        if not content_out:
            # 视觉模型遇到不支持的模型时，常见就是返回空 —— 给个能看懂的提示
            log.warning("ECUST 返回空回复（model=%s，可能不支持多模态）", model)
            return "（她没能看清这张图，换个说法或换个别的问题试试）", False
        return content_out, True
