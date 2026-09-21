"""本地斜杠命令：只在这里处理，永不转发给 agent。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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


HELP_TEXT = """可用命令：
/test502 - 测试机器人是否在线
/time - 显示当前时间（含 Phigros 4.0 倒计时）
/raw <内容> - 和普拉娜聊天（会记住上文）
/forget - 清空你和普拉娜的对话记忆
/help - 显示本帮助
—— 以下仅管理员 ——
/echo <内容> - 复读你输入的内容
/models - 列出学校网关(ECUST)的可用模型
/model [名字] - 查看 / 切换 ECUST 模型
/ping - 测当前 ECUST 模型连通性（不记入上下文）
/restart - 重启机器人"""

# 非管理员也能用的命令（其余仅管理员）。可用 .env 的 PUBLIC_COMMANDS 覆盖。
PUBLIC_COMMANDS = {
    c.strip().lower()
    for c in os.getenv("PUBLIC_COMMANDS", "help,test502,time,raw,forget").split(",")
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


def current_model(ctx: CommandContext) -> str:
    """当前该用户生效的 ECUST 模型（没选过就用 .env 的默认值）。"""
    return ctx.models.get(ctx.openid) or (ctx.ecust.default_model if ctx.ecust else "")


async def handle(text: str, ctx: CommandContext) -> str:
    """处理一条斜杠命令，返回要回复的文本。未知命令给提示。"""
    parts = text[1:].split()
    if not parts:
        return "输入 /help 查看可用指令"
    name, args = parts[0].lower(), parts[1:]

    # A：管理类命令只给管理员（/help /time /raw /forget 等公开命令例外）
    if not ctx.is_admin and name not in PUBLIC_COMMANDS:
        return f"只有管理员能用 /{name}。你可以用 /raw 找普拉娜聊天 🦊"

    if name == "test502":
        return "Here is FOX-2!"
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
        return HELP_TEXT
    if name == "restart":
        if not ctx.is_admin:
            return "只有管理员能重启机器人"
        ctx.restart_requested = True
        return "收到，正在重启…（几秒后回来）"
    if name in ("models", "model", "raw", "forget", "ping"):
        return await _handle_ecust(name, args, ctx)
    return f"未知命令 /{name}，输入 /help 查看可用指令"


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
