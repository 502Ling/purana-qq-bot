"""发回复的小套路：先看能不能马上给出，太慢就先回一句「在想…」。

QQ 的被动回复允许对同一条消息发多条（用 msg_seq 递增），所以：
- 快 → 只发一条（msg_seq=1）
- 慢 → 先发 ack（msg_seq=1），正式回复再发（msg_seq=2）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("purana.reply")

Send = Callable[[str, int], Awaitable[None]]  # send(文本, msg_seq)


async def deliver(
    send: Send,
    reply: Awaitable[str],
    *,
    ack_after: float = 2.5,
    ack_text: str = "（在想…）",

    ack_after_long: float = 120,
    ack_text_long: str = "（基沃托斯的网络不太稳定，可能是出问题了）",

    on_reply: Callable[[int], Awaitable[None]] | None = None,
) -> str:
    """等 reply 算出来；超过 ack_after 秒还没好就先发一句 ack_text。返回最终回复。

    on_reply：文本回复**发出去之后**调用的回调，入参是「下一个可用的 msg_seq」。
    用来追加发图之类的附加内容 —— 同一条 msg_id 下 msg_seq 不能重复，
    所以必须接着上面的序号发，不能自己从头数。
    """
    task = asyncio.ensure_future(reply)

    done, _pending = await asyncio.wait({task}, timeout=ack_after)
    doneLong, _pendingLong = await asyncio.wait({task}, timeout=ack_after_long)
    seq = 1
    if task not in done:
        await send(ack_text, seq)
        seq += 1
    if task not in doneLong:
        await send(ack_text_long, seq)
        seq += 1

    try:
        text = await task
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("生成回复时出错")
        text = "抱歉，我这边出了点问题，稍后再试。"

    if not text:
        text = "（空回复）"
    await send(text, seq)

    # 附加内容（发图等）：跟在文本后面，用下一个序号
    if on_reply is not None:
        try:
            await on_reply(seq + 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("发送附加内容时出错")

    return text
