"""发消息（目前只有私聊 C2C）。"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("nico.qq.api")

C2C_URL = "https://api.sgroup.qq.com/v2/users/{openid}/messages"


async def send_c2c(
    session: aiohttp.ClientSession,
    token: str,
    openid: str,
    content: str,
    msg_id: str = "",
    msg_seq: int = 1,
) -> None:
    """发一条私聊文本。

    带 msg_id 是「被动回复」（回复用户刚发的消息，额度更宽）；
    不带是「主动消息」。msg_seq 是同一 msg_id 下多条回复的序号。
    """
    payload = {"content": content, "msg_type": 0, "msg_seq": msg_seq}
    if msg_id:
        payload["msg_id"] = msg_id

    async with session.post(
        C2C_URL.format(openid=openid),
        json=payload,
        headers={"Authorization": f"QQBot {token}"},
    ) as resp:
        body = await resp.text()

    log.info("已发送私聊消息（HTTP %s）", resp.status)
    log.debug("发送结果：%s", body[:200])
