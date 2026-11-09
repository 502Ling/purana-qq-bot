"""本地斜杠命令：只在这里处理，永不转发给 agent。"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("purana.commands")

# 中国没有夏令时，用固定 +08:00（比 ZoneInfo 稳：Windows 上 ZoneInfo 需要额外的 tzdata 包）
TZ_CN = timezone(timedelta(hours=8))

# Phigros 4.0 上线时间（官方北京时间 2026-09-25 17:00）
PHIGROS_LAUNCH = datetime(2026, 9, 25, 17, 0, tzinfo=TZ_CN)


def fmt_delta(delta: timedelta) -> str:
    """把时间差格式化成「X 天 X 小时 X 分钟」，为零的高位省略。"""
    days, hours, minutes = delta.days, delta.seconds // 3600, (delta.seconds % 3600) // 60
    if days:
        return f"{days} 天 {hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


CHOOSE_MAX = 10  # /choose 最多接受的选项数量

HELP_TEXT = """可用命令：
/echo <内容> - 复读你输入的内容
/time - 显示当前时间（含 Phigros 4.0 倒计时）
/choose <选项1> <选项2> [...] - 从选项里随机选一个（最多 {CHOOSE_MAX} 个）
/whoami - 显示你的 openid（加管理员白名单用）
/help text - 显示本帮助的纯文本版
—— 以下仅管理员 ——
/test502 - 测试机器人是否在线
/raw <内容> - 和普拉娜聊天（会记住上文）
/forget - 清空你和普拉娜的对话记忆
/models - 列出学校网关(ECUST)的可用模型
/model [名字] - 查看 / 切换 ECUST 模型
/ping - 测当前 ECUST 模型连通性（不记入上下文）
/bili - 查看 / 开关本群的 B 站识别
/asimg <文字> - 把文字渲染成图片发出来
/image <网址|图片名> - 发指定图片
/restart - 重启机器人""".format(CHOOSE_MAX=CHOOSE_MAX)


# 公开命令白名单（非管理员也能用），其余默认仅管理员。可用 .env 的
# PUBLIC_COMMANDS 覆盖（逗号分隔）。做成函数式读取：模块级读会在 config 的
# load_dotenv 之前执行（bot.commands 常先于 bot.config 被 import），会让 .env 覆盖失效。
def public_commands() -> set[str]:
    return {
        c.strip().lower()
        for c in os.getenv("PUBLIC_COMMANDS", "help,time,choose,echo,whoami").split(",")
        if c.strip()
    }


@dataclass
class CommandContext:
    """命令处理所需的外部依赖（由 run.py 注入）。"""

    openid: str
    session: object = None                      # aiohttp.ClientSession
    ecust: object = None                        # bot.ecust.EcustClient
    models: dict[str, str] = field(default_factory=dict)   # openid -> 选中的 ECUST 模型
    history: object = None                      # bot.history.History
    system: str = ""                            # 普拉娜的人设（.env: ECUST_SYSTEM_PROMPT）
    is_admin: bool = False                      # 当前发信人是不是管理员
    restart_requested: bool = False             # /restart 会把它置 True，由 run.py 善后
    rate_limit: object = None                   # bot.ratelimit.RateLimiter（给 /raw 防刷）
    bili_switch: object = None                  # bot.bili.BiliSwitch（/bili 按群开关）
    group_openid: str = ""                      # 群聊才有（/bili 是按群开关）
    is_group: bool = False                      # 当前是不是群聊
    user_openid: str = ""                       # 私聊场景的用户 openid（群里没这个值）
    admin_openids: tuple[str, ...] = ()         # 管理员白名单（可以有多个）
    author_ids: tuple[str, ...] = ()            # 本条消息里能拿到的所有用户标识
    quiet_unknown: bool = False                 # 未知命令静默（群里不 @ 时用）
    # 富媒体：/image 命令用
    media_enabled: bool = True                  # 富媒体总闸
    media_dir: object = None                    # 本地固定图片目录（Path）
    image_domains: tuple[str, ...] = ()         # 出站图片来源域名白名单
    pending_media: list = field(default_factory=list)   # 本次要发出去的图（路径或 URL）
    render_available: bool = False              # 能不能把文字渲染成图（Pillow）
    asimg_title: str = ""                       # /asimg 的标题（可留空）
    render_theme: str = "dark"                  # 渲染配色（dark / light）
    render_dir: object = None                   # 渲染缓存目录（Path）


def current_model(ctx: CommandContext) -> str:
    """当前该用户生效的 ECUST 模型（没选过就用 .env 的默认值）。"""
    return ctx.models.get(ctx.openid) or (ctx.ecust.default_model if ctx.ecust else "")


async def handle(text: str, ctx: CommandContext) -> str:
    """处理一条斜杠命令，返回要回复的文本。未知命令给提示。

    ctx.quiet_unknown=True 时未知命令返回空串（调用方不发消息）——
    群里不 @ 的场景用这个，免得刷屏。
    """
    parts = text[1:].split()
    if not parts:
        return "输入 /help 查看可用指令"
    name, args = parts[0].lower(), parts[1:]

    # A：管理类命令只给管理员（public_commands() 里的公开命令例外）
    if not ctx.is_admin and name not in public_commands():
        if ctx.quiet_unknown:
            return ""
        return f"只有管理员能用 /{name}。你可以用 /raw 找普拉娜聊天 🦊"

    if name == "test502":
        # 「Here is FOX-2!」是从 Nico_21599 抄过来的，那个项目里的角色代号是 FOX-2。
        # 本项目的角色是普拉娜（Plana），代号不能乱用。
        return "普拉娜，待命中。"
    if name == "choose":
        # 从选项里随机选一个（/choose 甲 乙 丙 ...），对齐 Nico_21599 行为
        nums = len(args)
        if nums == 0:
            return "用法：/choose <选项1> <选项2> [...]"
        if nums == 1:
            return "老师...一个选项也会犯难吗？"
        if nums > CHOOSE_MAX:
            return f"选项太多了（最多 {CHOOSE_MAX} 个），普拉娜也要搞晕了..."
        return random.choice(args)
    if name == "echo":
        return " ".join(args) if args else "用法：/echo <要复读的内容>"
    if name == "time":
        now = datetime.now(timezone.utc)
        res = f"本地时间: {now.astimezone():%Y-%m-%d %H:%M:%S}，UTC: {now:%Y-%m-%d %H:%M:%S}"
        delta = PHIGROS_LAUNCH - now
        if delta.total_seconds() > 0:
            res += f"\n距离 Phigros 4.0 上线还有 {fmt_delta(delta)}"
        else:
            res += f"\nPhigros 4.0 已上线 {fmt_delta(-delta)}"
        return res
    if name == "help":
        # 502 定：/help 默认回图片（手机上看着舒服）。
        # 想拿纯文本（方便复制）用 /help text。
        if args and args[0].lower() in ("text", "txt", "文字"):
            return HELP_TEXT
        return await _handle_help_image(ctx)
    if name == "asimg":
        return await _handle_asimg(args, ctx)
    if name == "whoami":
        return await _handle_whoami(ctx)
    if name == "bili":
        return await _handle_bili(args, ctx)
    if name == "image":
        return await _handle_image(args, ctx)
    if name == "restart":
        if not ctx.is_admin:
            return "只有管理员能重启机器人"
        ctx.restart_requested = True
        return "收到，正在重启…（几秒后回来）"
    if name in ("models", "model", "raw", "forget", "ping"):
        return await _handle_ecust(name, args, ctx)
    if ctx.quiet_unknown:
        return ""   # 群里不 @ 时不刷屏
    return f"未知命令 /{name}，输入 /help 查看可用指令"


async def _handle_help_image(ctx: CommandContext) -> str:
    """/help —— 把命令列表渲染成图片发出去。

    502 定：/help 默认就是图片版（手机上看着舒服）。
    纯文本版用 /help text（方便复制）。

    渲染失败 / 没装 Pillow → 退回纯文本，不让命令失败。
    """
    if not ctx.media_enabled:
        return HELP_TEXT
    if not ctx.render_available:
        return HELP_TEXT

    from . import render

    try:
        path = render.render_cached(
            HELP_TEXT,
            title="PURANA · 命令帮助",
            theme=ctx.render_theme,
            cache_dir=ctx.render_dir,
        )
    except Exception as exc:
        log.warning("渲染帮助图失败，退回纯文本：%s", exc)
        return HELP_TEXT

    ctx.pending_media.append({"source": str(path), "kind": "image", "name": "help.png"})
    # 空字符串 = 只发图，不发文字气泡（run.py 会识别）
    return ""


async def _handle_asimg(args: list[str], ctx: CommandContext) -> str:
    """/asimg <文字> —— 把任意文字渲染成图片发出去。

    用途：QQ 的纯文本消息又窄又长，长内容（比如命令列表、报错栈、表格）
    渲染成图看着舒服多了。

    只允许管理员 —— 这是个「把任意内容变成图」的能力，
    公开放群里容易被拿去刷屏。
    """
    if not ctx.media_enabled:
        return "发图功能已关闭。"
    if not ctx.render_available:
        return "这台机器没装 Pillow，渲染不了图片。"
    if not ctx.is_admin:
        return "只有管理员能用 /asimg。"
    if not args:
        return "用法：/asimg <要变成图片的文字>"

    text = " ".join(args)
    if len(text) > 4000:
        return "文字太长了（上限 4000 字），截短了。"
        text = text[:4000]

    import tempfile

    from . import render

    path = Path(tempfile.gettempdir()) / f"purana_asimg_{ctx.openid[-8:]}.png"
    try:
        render.render_to_file(
            text, path, title=ctx.asimg_title or "", theme=ctx.render_theme
        )
    except Exception as exc:
        log.warning("渲染文字成图失败：%s", exc)
        return "渲染失败，稍后再试。"

    ctx.pending_media.append({"source": str(path), "kind": "image", "name": "asimg.png"})
    return ""


async def _handle_whoami(ctx: CommandContext) -> str:
    """/whoami —— 显示当前会话里的各个 openid。

    为什么要这个：官方协议里**私聊用 user_openid、群里用 member_openid**，
    两个值不一样，但只看到其中一个时很容易搞错。
    这个命令把所有能拿到的标识都列出来，方便挑一个填进 ADMIN_OPENID。
    """
    ids = [i for i in (ctx.author_ids or ()) if i]
    if not ids:
        return "拿不到你的标识……（可能是官方那边没下发）"
    lines = ["你的标识（复制其中一个填到 .env 的 ADMIN_OPENID）："]
    for i in ids:
        lines.append(i)
    lines.append("")
    lines.append("提醒：私聊用 user_openid，群里用 member_openid，两者不一样。")
    lines.append("ADMIN_OPENID 可以写多个，用逗号隔开。")
    return "\n".join(lines)


async def _handle_bili(args: list[str], ctx: CommandContext) -> str:
    """/bili —— 查看 / 开关本群的 B 站识别（仅管理员）。"""
    sw = ctx.bili_switch
    if sw is None:
        return "B站识别开关不可用"
    if not ctx.is_admin:
        return "只有管理员能改这个开关。"
    gid = ctx.group_openid

    if not args:
        if not sw.enabled:
            return "B站识别：全局已关闭（BILI_ENABLE=0），单群开了也不会跑。"
        if not ctx.is_group:
            return "B站识别：私聊一直开，只受全局开关控制。"
        state = "已开启" if sw.allowed("group", gid) else "已关闭"
        return (
            f"本群 B站识别：{state}\n当前模式：{sw.mode_label}\n"
            "用 /bili on 或 /bili off 切换。"
        )

    head = args[0].lower()
    if head in ("on", "开", "开启"):
        if not ctx.is_group:
            return "这是按群开关，私聊不用设。"
        sw.set_group(gid, True)
        return f"已开启本群（{gid[:12]}…）的 B站识别。"
    if head in ("off", "关", "关闭"):
        if not ctx.is_group:
            return "这是按群开关，私聊不用设。"
        sw.set_group(gid, False)
        return f"已关闭本群（{gid[:12]}…）的 B站识别。"
    return "用法：/bili 看状态；/bili on | /bili off 开关本群"


async def _handle_image(args: list[str], ctx: CommandContext) -> str:
    """/image —— 发图。

    两种用法：
    - ``/image https://example.com/a.png`` —— 发一个公网可访问的图（域名要在白名单里）
    - ``/image cat`` —— 发本地固定目录里名为 cat 的图（.png/.jpg 都行）

    发图的活儿不在这里做：命令处理只负责**收集要发的图**（ctx.pending_media），
    真正上传+发送由 run.py 在拿到 token 后做 —— 因为上传需要 access_token，
    而命令层拿不到 session 之外的东西。
    """
    if not ctx.media_enabled:
        return "发图功能已关闭。"

    if not args:
        return "用法：/image <图片网址 | 图片名>"

    target = args[0].strip()
    if not target:
        return "用法：/image <图片网址 | 图片名>"

    # 1) URL 形式
    if target.startswith(("http://", "https://")):
        if not _host_allowed(target, ctx.image_domains):
            return (
                "这个网址不在允许的域名里，下载不了。\n允许的域名："
                + "、".join(ctx.image_domains or ("（无）",))
            )
        ctx.pending_media.append({"source": target, "kind": "image", "name": ""})
        return "图来了。"

    # 2) 本地文件形式
    media_dir = ctx.media_dir
    if media_dir is None:
        return "图片目录没配置。"
    name = target
    # 补全扩展名（用户只打名字时）
    candidates = [name]
    if not any(name.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg")):
        candidates = [name + ext for ext in (".png", ".jpg", ".jpeg")]

    for cand in candidates:
        # 防目录穿越：文件名里不许带路径分隔符
        if "/" in cand or "\\" in cand or ".." in cand:
            return "只能用图片名，不接受路径。"
        p = media_dir / cand
        if p.is_file():
            ctx.pending_media.append({"source": str(p), "kind": "image", "name": cand})
            return "图来了。"

    return f"没找到 {target}（在 {media_dir} 里）"


def _host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    """域名白名单检查（防 SSRF）。"""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


async def _handle_ecust(name: str, args: list[str], ctx: CommandContext) -> str:
    if ctx.ecust is None or ctx.session is None:
        return "ECUST 网关未配置，检查 .env（ECUST_BASE_URL / ECUST_API_KEY）"

    if name == "models":
        ids = await ctx.ecust.models(ctx.session)
        if not ids:
            return "拉取模型列表失败，检查 ECUST_API_KEY 或网络"
        cur = current_model(ctx)
        lines = [("★ " if m == cur else "· ") + m for m in ids]
        return "ECUST 可用模型：\n" + "\n".join(lines)

    if name == "model":
        if not args:
            return f"当前 ECUST 模型：{current_model(ctx)}"
        want = args[0]
        ids = await ctx.ecust.models(ctx.session)
        if ids and want not in ids:
            return f"没有模型 {want}，用 /models 看列表"
        ctx.models[ctx.openid] = want
        return f"已切换到 ECUST 模型：{want}"

    if name == "forget":
        if ctx.history is not None:
            ctx.history.clear(ctx.openid)
        return "已清空你和她的对话记忆"

    if name == "ping":
        model = current_model(ctx)
        t0 = time.perf_counter()
        reply, ok = await ctx.ecust.ask(ctx.session, "回复一个字：好", model)
        dt = time.perf_counter() - t0
        if ok:
            return f"普拉娜在线 ✅  模型 {model}，{dt:.1f}s"
        return f"普拉娜无响应 ❌  模型 {model}，等了 {dt:.1f}s：{reply}"

    # name == "raw"
    if not args:
        return "用法：/raw <内容>"
    # B：防刷（管理员不受限）
    if ctx.rate_limit is not None and not ctx.is_admin:
        allowed, wait = ctx.rate_limit.check(ctx.openid)
        if not allowed:
            return f"说得太快啦，{max(1, round(wait))} 秒后再试～"
    text = " ".join(args)
    hist = ctx.history.get(ctx.openid) if ctx.history is not None else []
    reply, ok = await ctx.ecust.ask(
        ctx.session, text, current_model(ctx), hist, ctx.system or None
    )
    if ok and ctx.history is not None:
        ctx.history.append(ctx.openid, "user", text)
        ctx.history.append(ctx.openid, "assistant", reply)
    return reply
