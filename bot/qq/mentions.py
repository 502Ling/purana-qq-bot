"""群消息里的 @ 占位符识别与剥离。

QQ 官方协议有两种群消息事件，content 形态不一样：

- **GROUP_AT_MESSAGE_CREATE**（@ 机器人）：
  官方文档明说「content 字段已自动去除@机器人的前缀」→ content 里正文是**干净的**
  （比如 "/time"），**没有** <@...> 占位符。
- **GROUP_MESSAGE_CREATE**（全量模式）：
  官方文档明说「消息中 @ 某成员时，会替换为 <@ + 该成员的 member_openid + >」→
  content 里**带占位符**，@ 机器人时是 <@!appid> 形式。

⚠️ 由此得出三条必须遵守的规则（都踩过）：

1. **判断「是否 @ 了机器人」不能只看事件类型，也不能只看 content 有没有占位符** ——
   得两个都看：事件类型是 GROUP_AT_MESSAGE_CREATE 就一定是 @ 机器人；
   content 里有 <@!appid> / <@all> 也说明 @ 了机器人。
2. **同一条 @ 消息会被推两个事件**（AT + 全量），而且**先到哪个不确定**。
   如果按 msg_id 去重、先到的那个把去重表占了，后到的 @ 事件就被挡掉 →
   @ 机器人永远没反应（这坑踩过，表现为「@ 没反应、不 @ 反而有反应」）。
   所以去重必须允许**升级**：普通事件先到 → 后到的 @ 事件仍然要放行。
3. **@ 的占位符必须剥掉**再把正文喂给模型，否则模型会看到 <@!1905646947> 这种乱码。
"""

from __future__ import annotations

import re

# <@!12345>（@机器人 / app）或 <@12345>（@某个成员）
_AT_RE = re.compile(r"<@(!?)([0-9A-Za-z_-]+)>")


def split_mentions(raw: str, app_id: str = "") -> tuple[str, bool]:
    """剥掉 @ 占位符，返回 (干净正文, 是否@了机器人)。

    判定 @ 机器人的三种依据（任一成立即可）：
    - 占位符带 `!` 前缀（<@!appid>）—— QQ 用 `!` 区分 app/机器人引用和用户引用
    - 占位符就是自己的 AppID
    - <@all>（@全体成员）—— 这种也该响应
    """
    at_bot = False

    def _sub(m: re.Match) -> str:
        nonlocal at_bot
        token = m.group(2)
        if m.group(1) == "!" or token == "all" or (app_id and token == app_id):
            at_bot = True
        return " "

    clean = _AT_RE.sub(_sub, raw or "")
    return clean.strip(), at_bot


def detect_at_bot(data: dict, app_id: str = "", event_type: str = "") -> tuple[str, bool]:
    """从群消息事件里判断是否 @ 了机器人，返回 (干净正文, 是否@机器人)。

    依据（任一成立即算 @ 机器人）：
    1. 事件类型是 GROUP_AT_MESSAGE_CREATE —— 本身就是 @ 机器人的事件
    2. content 里有 <@!appid> / <@appid> / <@all> 占位符
    3. mentions 列表里有自己的 AppID
    """
    raw = data.get("content") or ""
    clean, at_bot = split_mentions(raw, app_id)

    if event_type == "GROUP_AT_MESSAGE_CREATE":
        at_bot = True

    # mentions 里出现自己的 AppID（官方对 mentions 的收录范围不完全一致，双保险）
    if not at_bot and app_id:
        for m in data.get("mentions") or []:
            if isinstance(m, dict) and str(m.get("id") or "") == str(app_id):
                at_bot = True
                break

    return clean, at_bot
