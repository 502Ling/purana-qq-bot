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
from bot import attachments, bili, media, render
from bot.commands import CommandContext, handle as handle_command
from bot.phigros import paths as pgr_paths
from bot.config import load_settings
from bot.dispatch import Dispatcher
from bot.ecust import EcustClient
from bot.history import History
from bot.keys import wait_for_escape
from bot.log import setup_logging
from bot.qq.api import send_c2c, send_group_card, send_group_msg
from bot.qq.auth import TokenManager
from bot.qq.client import QQBotClient
from bot.qq.mentions import detect_at_bot
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
    # 固定图片目录（/image 命令从这里找图）
    try:
        settings.image_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("图片目录创建失败：%s", settings.image_dir)

    # Phigros 资源根目录（曲库+曲绘，共享一份）
    # ⚠️ 没配就**不开** Phigros 功能，不报错、不影响其他功能。
    pgr_ok = False
    if settings.pgr_data_root:
        pgr_paths.configure(settings.pgr_data_root)
        err = pgr_paths.status()
        if err:
            log.warning("Phigros 资源不可用：%s", err)
        else:
            pgr_ok = True
            log.info("Phigros 资源已就绪：%s", settings.pgr_data_root)
    else:
        log.info("未配置 PGR_DATA_ROOT，Phigros 功能关闭")
    bili_switch = bili.BiliSwitch(
        settings.data_dir / "bili_off.json",
        settings.bili_enabled,
        settings.bili_mode,
        settings.bili_off_groups,
        settings.bili_only_groups,
    )

    # 去重：同一个 msg_id 只处理一次。值 = 这条消息是不是 @ 了机器人
    # （用来决定后到的 @ 事件能不能「升级」放行）
    seen: dict[str, bool] = {}
    ecust_models: dict[str, str] = {}  # openid -> 该用户选中的 ECUST 模型
    restart_event = asyncio.Event()
    dispatcher = Dispatcher(MAX_CONCURRENT)
    rate_limit = RateLimiter(RAW_RATE_LIMIT, RAW_RATE_WINDOW)

    def is_admin_of(
        author_ids: tuple[str, ...],
        member_role: str = "",
        scene: str = "group",
    ) -> bool:
        """这个人是不是管理员。

        两条路，任一成立就算：
        1. **群角色**：群里 author.member_role 是 owner/admin → 群主/群管理员就是管理员。
           这是群里最可靠的判据。
        2. **openid 白名单**：官方协议里同一个人在**私聊和群里是两个不同的 openid**
           （私聊 user_openid / 群里 member_openid），只有 author.id 是跨场景稳定的。
           所以这里拿一组候选标识去比对白名单，填哪个都能命中。

        没设白名单 = 所有人都算管理员（保留原来的宽松行为）。
        """
        if scene == "group" and str(member_role or "").lower() in ("owner", "admin"):
            return True
        if not settings.admin_openids:
            return True
        return any(a and a in settings.admin_openids for a in author_ids)

    async with aiohttp.ClientSession() as session:

        async def compute_reply(
            text: str,
            openid: str,
            session_key: str,
            scene: str,
            group_openid: str,
            author_ids: tuple[str, ...],
            member_role: str = "",
            quiet_unknown: bool = False,
            images: list[str] = None,      # 入站图片（data URL），空 = 没图
        ) -> tuple[str, bool, list[dict]]:
            """算出回复文本、是否请求重启、要附带发出去的图。

            openid 用于权限/记忆标识；session_key 是 agent 或 ECUST 记忆隔离用的键
            （私聊=openid，群里=group_openid+member 组合，避免串上下文）。
            quiet_unknown=True 时，未知/无权限的命令返回空串（不刷屏）。
            images 是入站图片（已转成 data URL）—— 有图且配了视觉模型时走 ECUST 视觉模型。
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
                    is_admin=is_admin_of(author_ids, member_role, scene),
                    rate_limit=rate_limit,
                    bili_switch=bili_switch,
                    group_openid=group_openid,
                    is_group=scene == "group",
                    author_ids=author_ids,
                    admin_openids=settings.admin_openids,
                    quiet_unknown=quiet_unknown,
                    media_enabled=settings.media_enabled,
                    media_dir=settings.image_dir,
                    image_domains=settings.image_domains,
                    render_available=render.PIL_AVAILABLE,
                    asimg_title=settings.asimg_title,
                    render_theme=settings.render_theme,
                    render_dir=settings.render_dir,
                    pgr_enabled=pgr_ok,
                )
                reply = await handle_command(r.payload, cmd)
                return reply, cmd.restart_requested, list(cmd.pending_media)

            if not is_admin_of(author_ids, member_role, scene):
                # 白名单外的人不接。（原来写的是「FOX-2」，那是 Nico_21599 的代号，
                # 本项目的角色是普拉娜。）
                return "普拉娜正在执行任务，暂时不能聊天。", False, []

            # 带图：走 ECUST 视觉模型（agent 那边的模型不支持多模态）
            if images and settings.vision_model:
                reply, _ok = await ecust.ask_images(
                    session,
                    r.payload or "这张图里有什么？",
                    images,
                    model=settings.vision_model,
                    system=settings.ecust_system_prompt or None,
                )
                return reply, False, []

            reply = await agent.ask(session, r.payload, session_key)

            # 回复里「整行就是图片网址」→ 把图发出去，并从文本里删掉那行
            pending: list[dict] = []
            if settings.media_enabled:
                urls = media.extract_image_urls(reply, settings.image_domains)
                if urls:
                    pending = [{"source": u, "kind": "image", "name": ""} for u in urls]
                    reply = media.strip_image_lines(reply, settings.image_domains)
            return reply, False, pending

        async def handle_message(
            scene: str,              # "c2c" 私聊 / "group" 群聊
            openid: str,             # 用户标识（私聊=user_openid；群里=member_openid）
            group_openid: str,       # 群聊才有的群 OpenID；私聊为空
            text: str,
            msg_id: str,
            session_key: str,        # agent/ECUST 记忆隔离键
            msg_ref_id: str = "",    # 群里被 @ 时，要引用的那条消息 REFIDX（可选）
            author_ids: tuple[str, ...] = (),  # 候选用户标识（判管理员用）
            member_role: str = "",               # 群里身份：owner/admin/member
            images: list[str] = None,           # 入站图片（data URL）
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
            pending_media: list[dict] = []

            async def reply_coro() -> str:
                reply, restart, pending = await compute_reply(
                    text, openid, session_key, scene, group_openid, author_ids,
                    member_role, images=images,
                )
                flag["restart"] = restart
                pending_media.extend(pending)
                return reply

            async def send_extras(seq: int) -> None:
                """把要发的图发出去（跟在文本回复后面，用下一个 msg_seq）。"""
                if not pending_media:
                    return
                if not settings.media_enabled:
                    return
                scope = media.SCOPE_GROUP if scene == "group" else media.SCOPE_C2C
                target = group_openid if scene == "group" else openid
                token = await tokens.get(session)
                for item in pending_media:
                    try:
                        await media.send_media_file(
                            session, token, scope, target,
                            item["source"],
                            kind=item.get("kind", "image"),
                            file_name=item.get("name", ""),
                            msg_id=msg_id,
                            msg_seq=seq,
                            content=item.get("caption", ""),
                            msg_ref_id=msg_ref_id,
                        )
                    except media.MediaError as exc:
                        log.warning("发图失败：%s", exc)
                    except Exception:
                        log.exception("发图时出错")
                    seq += 1

            await deliver(
                sender, reply_coro(),
                ack_after=ACK_AFTER, ack_text=ACK_TEXT,
                ack_after_long=ACK_AFTER_LONG, ack_text_long=ACK_TEXT_LONG,
                on_reply=send_extras,
            )

            # 回复发出去之后再退，别把 /restart 的提示吞了
            if flag.get("restart"):
                log.info("收到 /restart，正在退出以便重启…")
                restart_event.set()

        # 群里 @ 的占位符处理见 bot/qq/mentions.py（那边有详细的坑位说明）

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
            text_out = bili.format_video(info, with_link=settings.bili_link)
            if scene == "group":
                # 默认发**纯文本**：BV 号单独一行，双击就能复制到 APP 里搜。
                # 图文卡片点进去是 B 站小程序，不方便复制 —— 要卡片得显式开 BILI_CARD=1。
                if settings.bili_card:
                    card = bili.build_card(info)
                    if card:
                        status = await send_group_card(
                            session, token, group_openid, card, msg_id,
                            msg_seq=1, msg_ref_id=msg_ref_id,
                        )
                        if status < 400:
                            log.info("识别到 B 站视频：%s（卡片）", info.get("bvid"))
                            return True
                        log.warning("群卡片发送失败，退回纯文本（HTTP %s）", status)
                await send_group_msg(
                    session, token, group_openid, text_out, msg_id,
                    msg_seq=1, msg_ref_id=msg_ref_id,
                )
            else:
                await send_c2c(session, token, openid, text_out, msg_id, msg_seq=1)
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

            # 候选用户标识：官方协议里同一个人在私聊/群里是不同的 openid
            # （私聊 user_openid / 群里 member_openid），只有 author.id 跨场景稳定。
            # 白名单里填哪个都能匹配上。
            author_ids = tuple(
                dict.fromkeys(
                    x for x in (
                        author.get("id"),
                        author.get("user_openid"),
                        author.get("member_openid"),
                    ) if x
                )
            )
            # 群里身份：owner（群主）/ admin（管理员）/ member（普通成员）
            member_role = str(author.get("member_role") or "")

            if not openid or not msg_id or (scene == "group" and not group_openid):
                log.debug("消息字段不完整，忽略")
                return

            # **是否 @ 了机器人 + 干净正文**：不能只看事件类型。
            # QQ 会给一条 @ 消息推**两个事件**（GROUP_AT_MESSAGE_CREATE +
            # GROUP_MESSAGE_CREATE），后者 content 里带 <@!appid> 占位符。
            # 两个事件都按内容判一次，拿到统一的正文。详见 bot/qq/mentions.py。
            clean_text, at_bot = (text, False)
            if scene == "group":
                clean_text, at_bot = detect_at_bot(data, settings.app_id, event_type)

            # 去重：**同一个 msg_id 只处理一次**，但要允许「升级」。
            # 如果先到的是普通群消息、后到的是 @ 消息，必须放行后者 ——
            # 否则 @ 机器人永远没反应（先到的事件把去重表占了）。
            if msg_id in seen:
                if at_bot and not seen[msg_id]:
                    seen[msg_id] = True
                    log.info("同一条消息的 @ 事件补到，放行：%s", msg_id[:24])
                else:
                    log.debug("重复消息，忽略：%s（at=%s）", msg_id[:24], at_bot)
                    return
            else:
                seen[msg_id] = at_bot
                if len(seen) > 1000:
                    seen.clear()

            log.info(
                "收到事件 %s at=%s 群/私=%s：%s",
                event_type, at_bot, label, (clean_text or text)[:50],
            )

            # B站分享：先做**同步**的链接判断（不发网络请求），
            # 真有链接才丢后台去查 B 站接口 —— 避免卡住收消息的循环。
            # 分享卡片的 content 可能是空的（链接在 ark_data/msg_elements 里），
            # 所以不能拿「content 为空」当过滤条件。
            bili_candidate = bili.has_link(data)

            # 入站图片：也是**同步**判断（不发网络请求），真有图才丢后台去下载。
            inbound_images = attachments.extract_images(data)

            # 群里**没** @ 机器人的普通消息：只喂复读机 + B站识别，不烧 token
            if scene == "group" and not at_bot:
                if author.get("bot"):
                    return   # 机器人自己发的，别自嗨
                stripped = (clean_text or "").strip()
                # 群里不 @ 但发的是斜杠命令：
                #   - 总闸 GROUP_COMMAND_ENABLE 关了 → 一律当普通群消息（静默）
                #   - 管理员 → 一律响应（/bili /restart 这些都得能用）
                #   - 其他人 → 只响应 GROUP_COMMANDS 里列的公开命令
                #   别的 → 静默忽略（别在群里刷屏说「只有管理员能用」）
                if stripped.startswith("/") and settings.group_command_enable:
                    parts = stripped[1:].split()
                    cmd_name = parts[0].lower() if parts else ""
                    allowed = cmd_name in settings.group_commands
                    if not allowed and is_admin_of(author_ids, member_role, "group"):
                        allowed = True
                    if allowed:
                        dispatcher.spawn(
                            group_openid,
                            handle_group_command(
                                openid, group_openid, stripped, msg_id,
                                session_key, author_ids, member_role,
                            ),
                        )
                        return
                    repeater.observe(group_openid, text, openid, msg_id)
                    return
                if bili_candidate:
                    dispatcher.spawn(
                        group_openid,
                        handle_bili(data, "group", openid, group_openid, msg_id),
                    )
                    return
                repeater.observe(group_openid, text, openid, msg_id)
                return

            # 被 @ 的消息：先看是不是 B 站分享，是就直接给卡片。
            # 同样不能阻塞收消息循环 → 丢后台；识别失败再转给 agent。
            if bili_candidate:
                async def _bili_miss() -> None:
                    await _handle_text(
                        scene, openid, group_openid, text, msg_id, session_key, msg_ref_id,
                        author_ids, member_role, images=inbound_images,
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
                    scene, openid, group_openid, text, msg_id, session_key, msg_ref_id,
                    author_ids, member_role, images=inbound_images,
                ),
            )

        async def handle_group_command(
            openid: str,
            group_openid: str,
            text: str,
            msg_id: str,
            session_key: str,
            author_ids: tuple[str, ...],
            member_role: str = "",
        ) -> None:
            """群里没 @ 机器人、但发的是允许的命令 → 直接回。

            先试被动回复（带 msg_id，额度更宽）；被拒（过期/越权）就退成主动消息重发。
            quiet_unknown=True：未知/无权限的命令不刷屏（返回空串就不发）。
            """
            reply, restart, pending = await compute_reply(
                text, openid, session_key, "group", group_openid, author_ids,
                member_role, quiet_unknown=True,
            )
            if restart:
                restart_event.set()
            if not reply and not pending:
                return
            token = await tokens.get(session)
            # ⚠️ 只有图没有文字时（/help 现在就是）**不要发空气泡**
            #    —— 空 content 的消息 QQ 会显示成一个空白气泡。
            if reply:
                status = await send_group_msg(
                    session, token, group_openid, reply, msg_id, msg_seq=1
                )
                if status >= 400:
                    log.warning("群命令被动回复失败（HTTP %s），改发主动消息", status)
                    await send_group_msg(session, token, group_openid, reply)
            log.info("群命令已回复：%s", text[:40])

            # 附带的图（/help 现在就是纯图片）：接在文字后面发。
            # ⚠️ msg_seq 要接着上一条用：
            #    有文字 → 文字用了 seq=1，图用 seq=2；
            #    没文字（只有图）→ 图就用 seq=1，不能占用一个空号。
            seq = 2 if reply else 1
            for item in pending:
                try:
                    await media.send_media_file(
                        session, token, media.SCOPE_GROUP, group_openid,
                        item["source"], kind=item.get("kind", "image"),
                        file_name=item.get("name", ""),
                        msg_id=msg_id, msg_seq=seq,
                        content=item.get("caption", ""),
                    )
                except media.MediaError as exc:
                    log.warning("群命令发图失败：%s", exc)
                seq += 1

        async def _handle_text(
            scene: str,
            openid: str,
            group_openid: str,
            text: str,
            msg_id: str,
            session_key: str,
            msg_ref_id: str = "",
            author_ids: tuple[str, ...] = (),
            member_role: str = "",
            images: list = None,          # 入站图片附件（attachments.InboundImage）
        ) -> None:
            """普通消息（@ 机器人的私聊/群聊）→ 走命令或 agent。

            这个函数是**同步等完**的，所以调用方必须保证自己已经不在收消息的循环里
            （要么丢给 dispatcher，要么本身就在后台任务里）。
            """
            # 群里被 @ 时剥离占位符，别把 <@!xxx> 当正文喂给模型。
            # （正文在 on_event 里已经用 detect_at_bot 剥过了，这里再剥一次兼底：
            #  单独处理 @ 而后面没别的字时，剥完是空的 → 直接不回复）
            if scene == "group":
                text, _at = detect_at_bot({"content": text}, settings.app_id)
                if not text and not images:
                    return

            # 下载入站图片（转 data URL）—— 给视觉模型用。
            # 这里会发网络请求，但本函数已经不在收消息的循环里了（丢给 dispatcher）。
            image_urls: list[str] = []
            if images and settings.vision_model:
                image_urls = await attachments.download_images(session, images)
                if image_urls:
                    log.info("收到 %s 张图，走视觉模型 %s", len(image_urls), settings.vision_model)

            label = "群聊" if scene == "group" else "私聊"
            log.info("收到%s %s：%s", label, scene == "group" and f"group={group_openid[:100]}" or f"openid={openid[:10]}", text[:60])
            await handle_message(
                scene, openid, group_openid, text, msg_id, session_key, msg_ref_id,
                author_ids, member_role, images=image_urls,
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
