"""入口：连 QQ 网关 → 收消息 → 命令本地处理 / 其余转发给 OpenClaw。

用法：
    python run.py

⚠️ 跑之前先确认：OpenClaw 的 qqbot 插件已经停掉。
   同一个 AppID 只能有一个消费者，两个一起跑会抢消息。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import aiohttp

from bot.agent import AgentClient
from bot.commands import CommandContext, handle as handle_command
from bot.config import load_settings
from bot.dispatch import Dispatcher
from bot.ecust import EcustClient
from bot.history import History
from bot.keys import wait_for_escape
from bot.log import setup_logging
from bot.qq.api import send_c2c
from bot.qq.auth import TokenManager
from bot.qq.client import QQBotClient
from bot.ratelimit import RateLimiter
from bot.reply import deliver
from bot.router import route

log = logging.getLogger("PURANA_ECUST")

# 同时在处理的消息数上限（不同人可并行；同一个人仍然按顺序）
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "8"))
# 超过这么多秒还没算完，就先回一句 ACK_TEXT
ACK_AFTER = float(os.getenv("ACK_AFTER", "15"))
ACK_TEXT = os.getenv("ACK_TEXT", "（PURANA确认已阅，正在处理...）")
ACK_AFTER_LONG = float(os.getenv("ACK_AFTER_LONG", "120"))
ACK_TEXT_LONG = os.getenv("ACK_TEXT_LONG", "（基沃托斯的网络不太稳定，可能是出问题了）")
# 防刷：非管理员每人每 RAW_RATE_WINDOW 秒最多 RAW_RATE_LIMIT 次 /raw（0 = 不限）
RAW_RATE_LIMIT = int(os.getenv("RAW_RATE_LIMIT", "3"))
RAW_RATE_WINDOW = float(os.getenv("RAW_RATE_WINDOW", "60"))


async def main() -> bool:
    """跑主循环。返回 True = 收到 /restart，需要在退出后重启。"""
    settings = load_settings()
    setup_logging(settings.data_dir / "logs")
    log.info("PURANA_ECUST 启动中…（并发上限 %s，%s 秒后先回执）", MAX_CONCURRENT, ACK_AFTER)

    tokens = TokenManager(settings.app_id, settings.app_secret, settings.token_cache)
    agent = AgentClient(
        settings.openclaw_base, settings.openclaw_token, settings.openclaw_model
    )
    ecust = EcustClient(
        settings.ecust_base_url, settings.ecust_api_key, settings.ecust_model
    )
    history = History(settings.data_dir / "ecust_history.json")

    seen: set[str] = set()  # 去重：同一个 msg_id 只处理一次
    ecust_models: dict[str, str] = {}  # openid -> 该用户选中的 ECUST 模型
    restart_event = asyncio.Event()
    dispatcher = Dispatcher(MAX_CONCURRENT)
    rate_limit = RateLimiter(RAW_RATE_LIMIT, RAW_RATE_WINDOW)

    async with aiohttp.ClientSession() as session:

        async def compute_reply(text: str, openid: str) -> tuple[str, bool]:
            """算出回复文本，以及是否请求重启。"""
            r = route(text)
            if r.kind == "command":
                cmd = CommandContext(
                    openid=openid,
                    session=session,
                    ecust=ecust,
                    models=ecust_models,
                    history=history,
                    system=settings.ecust_system_prompt,
                    # 没设白名单 = 所有人都算管理员（跟转发逻辑保持一致）
                    is_admin=not settings.admin_openid
                    or openid == settings.admin_openid,
                    rate_limit=rate_limit,
                )
                return await handle_command(r.payload, cmd), cmd.restart_requested

            if settings.admin_openid and openid != settings.admin_openid:
                return "FOX-2正在执行任务，不能陪你聊天喵~", False  # 白名单外的人不接

            reply = await agent.ask(session, r.payload, settings.session_key(openid))
            return reply, False

        async def handle_message(openid: str, text: str, msg_id: str) -> None:
            async def sender(message: str, seq: int) -> None:
                token = await tokens.get(session)
                await send_c2c(session, token, openid, message, msg_id, msg_seq=seq)

            flag: dict[str, bool] = {}

            async def reply_coro() -> str:
                reply, restart = await compute_reply(text, openid)
                flag["restart"] = restart
                return reply

            await deliver(sender, reply_coro(), ack_after=ACK_AFTER, ack_text=ACK_TEXT, ack_after_long=ACK_AFTER_LONG, ack_text_long=ACK_TEXT_LONG)

            # 回复发出去之后再退，别把 /restart 的提示吞了
            if flag.get("restart"):
                log.info("收到 /restart，正在退出以便重启…")
                restart_event.set()

        async def on_event(event: dict) -> None:
            if event["type"] != "C2C_MESSAGE_CREATE":
                return

            data = event["data"]
            openid = (data.get("author") or {}).get("user_openid")
            text = data.get("content") or ""
            msg_id = data.get("id") or ""

            if not openid or not text or not msg_id:
                log.debug("消息字段不完整，忽略")
                return
            if msg_id in seen:
                log.debug("重复消息，忽略：%s", msg_id)
                return
            seen.add(msg_id)
            if len(seen) > 1000:
                seen.clear()

            log.info("收到私聊 openid=%s：%s", openid[:10], text[:60])
            # 关键：丢给后台就立刻返回，收消息的循环不再被堵住
            dispatcher.spawn(openid, handle_message(openid, text, msg_id))

        client = QQBotClient(session, tokens, on_event)
        if os.getenv("NICO_NO_ESC") == "1":
            log.info("已就绪（在 dev.py 监督下运行，退出请按那里的 Esc）")
        else:
            log.info("已就绪：按 Esc 退出（Ctrl+C 也可以）")

        run_task = asyncio.create_task(client.run(), name="qq-run")
        esc_task = asyncio.create_task(wait_for_escape(), name="esc-watch")
        restart_task = asyncio.create_task(restart_event.wait(), name="restart-watch")
        tasks = (run_task, esc_task, restart_task)

        restarting = False
        try:
            done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if esc_task in done:
                log.info("收到 Esc，正在退出…")
            if restart_task in done:
                restarting = True
            for task in done:
                if task is restart_task:
                    continue
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if dispatcher.pending:
                log.info("还有 %s 条消息在处理中，取消掉…", dispatcher.pending)
            await dispatcher.shutdown()
            await client.close()

    return restarting


if __name__ == "__main__":
    try:
        should_restart = asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)

    if should_restart:
        if os.getenv("NICO_SUPERVISED") == "1":
            print("重启交给 dev.py 处理，本进程退出。")
        else:
            print("重启中…")
            os.execv(sys.executable, [sys.executable, *sys.argv])
