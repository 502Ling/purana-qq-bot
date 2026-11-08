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
import re
import sys
from typing import Any

import aiohttp

from bot.agent import AgentClient
from bot import bili
from bot.commands import CommandContext, handle as handle_command
from bot.config import load_settings
from bot.dispatch import Dispatcher
from bot.ecust import EcustClient
from bot.history import History
from bot.keys import wait_for_escape
from bot.log import setup_logging
from bot.qq.api import send_c2c, send_group_card, send_group_msg
from bot.qq.auth import TokenManager
from bot.qq.client import QQBotClient
from bot.ratelimit import RateLimiter
from bot.repeat import Repeater
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
    bili_switch = bili.BiliSwitch(
        settings.data_dir / "bili_off.json",
        settings.bili_enabled,
        settings.bili_mode,
        settings.bili_off_groups,
        settings.bili_only_groups,
    )

    seen: set[str] = set()  # 去重：同一个 msg_id 只处理一次
    ecust_models: dict[str, str] = {}  # openid -> 该用户选中的 ECUST 模型
    restart_event = asyncio.Event()
    dispatcher = Dispatcher(MAX_CONCURRENT)
    rate_limit = RateLimiter(RAW_RATE_LIMIT, RAW_RATE_WINDOW)

    async with aiohttp.ClientSession() as session:

        async def compute_reply(
            text: str, openid: str, session_key: str, scene: str, group_openid: str
        ) -> tuple[str, bool]:
            """算出回复文本，以及是否请求重启。

            openid 用于权限/记忆标识；session_key 是 agent 或 ECUST 记忆隔离用的键
            （私聊=openid，群里=group_openid+member 组合，避免串上下文）。
            """
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
                    bili_switch=bili_switch,
                    group_openid=group_openid,
                    is_group=scene == "group",
                )
                return await handle_command(r.payload, cmd), cmd.restart_requested

            if settings.admin_openid and openid != settings.admin_openid:
                return "FOX-2正在执行任务，不能陪你聊天喵~", False  # 白名单外的人不接

            reply = await agent.ask(session, r.payload, session_key)
            return reply, False

        async def handle_message(
            scene: str,              # "c2c" 私聊 / "group" 群聊
            openid: str,             # 用户标识（私聊=user_openid；群里=member_openid）
            group_openid: str,       # 群聊才有的群 OpenID；私聊为空
            text: str,
            msg_id: str,
            session_key: str,        # agent/ECUST 记忆隔离键
            msg_ref_id: str = "",    # 群里被 @ 时，要引用的那条消息 REFIDX（可选）
        ) -> None:
            async def sender(message: str, seq: int) -> None:
                token = await tokens.get(session)
                if scene == "group":
                    await send_group_msg(
                        session, token, group_openid, message, msg_id,
                        msg_seq=seq, msg_ref_id=msg_ref_id,
                    )
                else:
                    await send_c2c(session, token, openid, message, msg_id, msg_seq=seq)

            flag: dict[str, bool] = {}

            async def reply_coro() -> str:
                reply, restart = await compute_reply(
                    text, openid, session_key, scene, group_openid
                )
                flag["restart"] = restart
                return reply

            await deliver(sender, reply_coro(), ack_after=ACK_AFTER, ack_text=ACK_TEXT, ack_after_long=ACK_AFTER_LONG, ack_text_long=ACK_TEXT_LONG)

            # 回复发出去之后再退，别把 /restart 的提示吞了
            if flag.get("restart"):
                log.info("收到 /restart，正在退出以便重启…")
                restart_event.set()

        # 群里被 @ 的消息，content 里会带 <@!bot_appid> 之类的占位符，剥掉
        AT_MENTION_RE = re.compile(r"<@![^>]*>")

        async def send_repeat(gid: str, text: str) -> None:
            """复读发送：纯文本、不带 msg_id、不带引用 → 主动消息。

            官方群被动回复窗口只有 5 分钟，复读本身是延后触发的，
            早就过期了，所以只能走主动消息（受频控限制，见 README）。
            """
            token = await tokens.get(session)
            await send_group_msg(session, token, gid, text)

        repeater = Repeater(settings.repeat_delay, send_repeat, settings.repeat_min_count)

        async def try_bili(
            data: dict,
            scene: str,
            openid: str,
            group_openid: str,
            msg_id: str,
            msg_ref_id: str = "",
        ) -> bool:
            """消息里是 B 站内容就发卡片/文本，返回 True 表示已处理。

            ⚠️ 这函数会发网络请求（B站接口最多 8s），**不能在收事件的循环里直接调**，
            会把 WebSocket 读循环卡住 → 丢消息。所以调用方必须把它丢到 dispatcher 里。
            """
            if not bili_switch.allowed(scene, group_openid):
                return False
            info = await bili.parse_event(data, session)
            if not info:
                return False
            token = await tokens.get(session)
            if scene == "group":
                card = bili.build_card(info)
                if card:
                    status = await send_group_card(
                        session, token, group_openid, card, msg_id,
                        msg_seq=1, msg_ref_id=msg_ref_id,
                    )
                else:
                    status = await send_group_msg(
                        session, token, group_openid, bili.format_video(info), msg_id,
                        msg_seq=1, msg_ref_id=msg_ref_id,
                    )
                if status >= 400:
                    # 卡片发不出去（比如没权限/字段不合法）→ 退一步发纯文本
                    log.warning("群卡片发送失败，退回纯文本（HTTP %s）", status)
                    await send_group_msg(
                        session, token, group_openid, bili.format_video(info), msg_id,
                        msg_seq=1, msg_ref_id=msg_ref_id,
                    )
            else:
                await send_c2c(
                    session, token, openid, bili.format_video(info), msg_id, msg_seq=1
                )
            log.info("识别到 B 站视频：%s", info.get("bvid"))
            return True

        def extract_msg_ref_id(data: dict) -> str:
            """从群事件里抠出要引用的消息 REFIDX（MSGExt 的 msg_idx=REFIDX_...）。

            QQ 官方群消息事件把「引用哪条消息」放在
            data.message_scene.ext[]（形如 "msg_idx=REFIDX_xxx"/"auth_token=..."）里，
            拿到的 REFIDX 传给发群消息接口的 message_reference.message_id 即可实现引用回复。
            """
            scene = data.get("message_scene") or {}
            for item in scene.get("ext") or []:
                s = str(item)
                if s.startswith("msg_idx="):
                    return s[len("msg_idx="):].strip()
            return ""

        async def handle_bili(
            data: dict,
            scene: str,
            openid: str,
            group_openid: str,
            msg_id: str,
            msg_ref_id: str = "",
            on_miss: Any = None,
        ) -> None:
            """B站识别的完整流程。

            整体跑在 dispatcher 的后台任务里 —— try_bili 要发网络请求（B站最多 8s），
            绝不能占着收消息的循环，否则会丢消息。
            on_miss：识别失败时的兜底（比如 @ 机器人时转去跟 agent 聊）。
            """
            if await try_bili(data, scene, openid, group_openid, msg_id, msg_ref_id):
                return
            if on_miss is not None:
                await on_miss()

        async def on_event(event: dict) -> None:
            event_type = event["type"]
            data = event["data"]
            msg_id = data.get("id") or ""

            if event_type == "C2C_MESSAGE_CREATE":
                author = data.get("author") or {}
                openid = author.get("user_openid")
                text = data.get("content") or ""
                scene, group_openid = "c2c", ""
                session_key = openid
                label = "私聊"
                msg_ref_id = ""
            elif event_type in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
                author = data.get("author") or {}
                openid = author.get("member_openid")
                group_openid = data.get("group_openid") or ""
                text = data.get("content") or ""
                scene = "group"
                session_key = f"{group_openid}:{openid}"
                label = "群聊"
                msg_ref_id = extract_msg_ref_id(data)
            else:
                return

            if not openid or not msg_id or (scene == "group" and not group_openid):
                log.debug("消息字段不完整，忽略")
                return
            if msg_id in seen:
                log.debug("重复消息，忽略：%s", msg_id)
                return
            seen.add(msg_id)
            if len(seen) > 1000:
                seen.clear()

            # B站分享：先做**同步**的链接判断（不发网络请求），
            # 真有链接才丢后台去查 B 站接口 —— 避免卡住收消息的循环。
            # 分享卡片的 content 可能是空的（链接在 ark_data/msg_elements 里），
            # 所以不能拿「content 为空」当过滤条件。
            bili_candidate = bili.has_link(data)

            # 群里没 @ 机器人的普通消息：只喂复读机 + B站识别，不烧 token
            if event_type == "GROUP_MESSAGE_CREATE":
                if author.get("bot"):
                    return   # 机器人自己发的，别自嗨
                if bili_candidate:
                    dispatcher.spawn(
                        group_openid,
                        handle_bili(data, "group", openid, group_openid, msg_id),
                    )
                    return
                repeater.observe(group_openid, text, openid)
                return

            # 被 @ 的消息：先看是不是 B 站分享，是就直接给卡片。
            # 同样不能阻塞收消息循环 → 丢后台；识别失败再转给 agent。
            if bili_candidate:
                async def _bili_miss() -> None:
                    await _handle_text(
                        scene, openid, group_openid, text, msg_id, session_key, msg_ref_id
                    )

                dispatcher.spawn(
                    group_openid if scene == "group" else openid,
                    handle_bili(
                        data, scene, openid, group_openid, msg_id, msg_ref_id, _bili_miss
                    ),
                )
                return

            dispatcher.spawn(
                group_openid if scene == "group" else openid,
                _handle_text(
                    scene, openid, group_openid, text, msg_id, session_key, msg_ref_id
                ),
            )

        async def _handle_text(
            scene: str,
            openid: str,
            group_openid: str,
            text: str,
            msg_id: str,
            session_key: str,
            msg_ref_id: str = "",
        ) -> None:
            """普通消息（@ 机器人的私聊/群聊）→ 走命令或 agent。

            这个函数是**同步等完**的，所以调用方必须保证自己已经不在收消息的循环里
            （要么丢给 dispatcher，要么本身就在后台任务里）。
            """
            # 群里被 @ 时剥离占位符，别把 <@!xxx> 当正文喂给模型
            if scene == "group":
                text = AT_MENTION_RE.sub("", text).strip()
                if not text:
                    return

            label = "群聊" if scene == "group" else "私聊"
            log.info("收到%s %s：%s", label, scene == "group" and f"group={group_openid[:100]}" or f"openid={openid[:10]}", text[:60])
            await handle_message(
                scene, openid, group_openid, text, msg_id, session_key, msg_ref_id
            )

        client = QQBotClient(session, tokens, on_event)
        if os.getenv("PURANA_NO_ESC") == "1":
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
            await repeater.close()
            await client.close()

    return restarting


if __name__ == "__main__":
    try:
        should_restart = asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)

    if should_restart:
        if os.getenv("PURANA_SUPERVISED") == "1":
            print("重启交给 dev.py 处理，本进程退出。")
        else:
            print("重启中…")
            os.execv(sys.executable, [sys.executable, *sys.argv])
