"""拿 WebSocket 网关地址。"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("purana.qq.gateway")

GATEWAY_URL = "https://api.sgroup.qq.com/gateway"


async def get_ws_url(session: aiohttp.ClientSession, token: str) -> str:
    async with session.get(
        GATEWAY_URL, headers={"Authorization": f"QQBot {token}"}
    ) as resp:
        data = await resp.json(content_type=None)

    url = data.get("url")
    if not url:
        raise RuntimeError(f"获取网关地址失败：{data}")
    log.debug("网关地址：%s", url)
    return url
