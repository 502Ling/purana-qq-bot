"""发消息（私聊 C2C + 群聊）。"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("purana.qq.api")

C2C_URL = "https://api.sgroup.qq.com/v2/users/{openid}/messages"
GROUP_URL = "https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"


async def send_c2c(
    session: aiohttp.ClientSession,
    token: str,
    openid: str,
    content: str,
    msg_id: str = "",
    msg_seq: int = 1,
) -> int:
    """发一条私聊文本。返回 HTTP 状态码。

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
        status = resp.status
        body = await resp.text()

    log.info("已发送私聊消息（HTTP %s）", status)
    if status >= 400:
        log.warning("私聊发送失败 %s：%s", status, body[:200])
    else:
        log.debug("发送结果：%s", body[:200])
    return status


async def send_group_card(
    session: aiohttp.ClientSession,
    token: str,
    group_openid: str,
    card: dict,
    msg_id: str = "",
    msg_seq: int = 1,
    msg_ref_id: str = "",
) -> int:
    """发一条群聊图文卡片（msg_type=8, type=tuwen）。返回 HTTP 状态码。

    card = {"type": "tuwen", "content": {title, description, url, pic_url}}
    官方要求 url/pic_url 必须是 https、长度 ≤1024，title ≤50 字、description ≤200 字。
    """
    payload = {"msg_type": 8, "card": card, "msg_seq": msg_seq}
    if msg_id:
        payload["msg_id"] = msg_id
    if msg_ref_id:
        payload["message_reference"] = {"message_id": msg_ref_id}

    async with session.post(
        GROUP_URL.format(group_openid=group_openid),
        json=payload,
        headers={"Authorization": f"QQBot {token}"},
    ) as resp:
        status = resp.status
        body = await resp.text()

    log.info("已发送群卡片（HTTP %s，group=%s…）", status, str(group_openid)[:10])
    if status >= 408:
        log.warning("群卡片发送失败 %s：%s", status, body[:200])
    else:
        log.debug("发送结果：%s", body[:200])
    return status


async def send_group_msg(
    session: aiohttp.ClientSession,
    token: str,
    group_openid: str,
    content: str,
    msg_id: str = "",
    msg_seq: int = 1,
    msg_ref_id: str = "",
) -> int:
    """发一条群聊文本。返回 HTTP 状态码。

    QQ 官方群聊被动回复：5 分钟内有效、每条消息最多回 5 次；
    带 msg_id 即被动回复，不带是主动消息（频控更严）。
    msg_ref_id 若给，就带上 message_reference 显式引用那条消息（形如 REFIDX_xxx）。
    """
    payload = {"content": content, "msg_type": 0, "msg_seq": msg_seq}
    if msg_id:
        payload["msg_id"] = msg_id
    if msg_ref_id:
        payload["message_reference"] = {"message_id": msg_ref_id}

    async with session.post(
        GROUP_URL.format(group_openid=group_openid),
        json=payload,
        headers={"Authorization": f"QQBot {token}"},
    ) as resp:
        status = resp.status
        body = await resp.text()

    log.info("已发送群消息（HTTP %s，group=%s…）", status, str(group_openid)[:10])
    if status >= 408:
        log.warning("群消息发送失败 %s：%s", status, body[:200])
    else:
        log.debug("发送结果：%s", body[:200])
    return status