"""QQ 开放平台凭证：取 access_token + 本地缓存（提前 60 秒过期）。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

log = logging.getLogger("nico.qq.auth")

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
EXPIRE_MARGIN = 60  # 提前 60 秒过期，避免临界点


class TokenManager:
    def __init__(self, app_id: str, app_secret: str, cache_path: Path) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._cache_path = cache_path
        self._token = ""
        self._expire_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, session: aiohttp.ClientSession) -> str:
        async with self._lock:
            if self._token and time.time() < self._expire_at:
                return self._token
            if self._load_cache():
                return self._token
            return await self._fetch(session)

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if data.get("token") and float(data.get("expire_at", 0)) > time.time():
                self._token = data["token"]
                self._expire_at = float(data["expire_at"])
                log.debug("使用本地缓存的 token")
                return True
        except FileNotFoundError:
            pass
        except Exception as exc:  # 缓存坏了不该影响启动
            log.warning("读取 token 缓存失败：%s", exc)
        return False

    def _save_cache(self) -> None:
        try:
            self._cache_path.write_text(
                json.dumps({"token": self._token, "expire_at": self._expire_at}),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("写入 token 缓存失败：%s", exc)

    async def _fetch(self, session: aiohttp.ClientSession) -> str:
        log.info("请求新的 access_token…")
        async with session.post(
            TOKEN_URL,
            json={"appId": self._app_id, "clientSecret": self._app_secret},
        ) as resp:
            data = await resp.json(content_type=None)

        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"获取 token 失败：{data.get('message') or data}")

        expires_in = int(data.get("expires_in", 7200))
        self._token = token
        self._expire_at = time.time() + expires_in - EXPIRE_MARGIN
        self._save_cache()
        log.info("获取 token 成功（%s 秒后过期）", expires_in)
        return token
