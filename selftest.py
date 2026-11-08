"""离线自测：复读机 + B站链接提取（不需要联网、不需要 QQ 网关）。

用法：
    .\\venv\\Scripts\\python.exe selftest.py
"""
from __future__ import annotations

import asyncio
import sys

# 本机 stdout 是 GBK(936)，直接打 emoji 会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from bot import bili
from bot.qq.mentions import detect_at_bot, split_mentions
from bot.repeat import Repeater

FAILED = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAILED
    if cond:
        print(f"  [OK]   {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} {extra}")


def test_mentions() -> None:
    print("\n== @ 占位符识别 ==")
    APP = "1905646947"

    # 1) GROUP_AT_MESSAGE_CREATE：content 是干净的（官方自动去掉了 @ 前缀）
    clean, at = detect_at_bot(
        {"content": "/time"}, APP, "GROUP_AT_MESSAGE_CREATE"
    )
    check("AT事件 → 判为@机器人", at, f"实际 at={at}")
    check("AT事件 → 正文干净", clean == "/time", f"实际 {clean!r}")

    # 2) 全量事件里带 <@!appid> 占位符 → 也要判为 @ 机器人
    clean, at = detect_at_bot(
        {"content": " <@!1905646947> /bili"}, APP, "GROUP_MESSAGE_CREATE"
    )
    check("全量事件带占位符 → @机器人", at, f"实际 at={at}")
    check("全量事件 → 占位符被剥掉", clean == "/bili", f"实际 {clean!r}")

    # 3) 全量事件里 @ 别人 → 不算 @ 机器人，但占位符也要剥
    clean, at = detect_at_bot(
        {"content": " <@7A3B9C1D5E2F4A6B8C0D1E3F5A7B9C2D> 你好"}, APP, "GROUP_MESSAGE_CREATE"
    )
    check("@别人 → 不算@机器人", not at, f"实际 at={at}")
    check("@别人 → 占位符被剥掉", clean == "你好", f"实际 {clean!r}")

    # 4) <@all> → 算 @ 机器人（该响应）
    clean, at = detect_at_bot(
        {"content": " <@all> 大家看看"}, APP, "GROUP_MESSAGE_CREATE"
    )
    check("@全体成员 → 算@机器人", at, f"实际 at={at}")
    check("@全体成员 → 占位符被剥掉", clean == "大家看看", f"实际 {clean!r}")

    # 5) mentions 里含自己的 AppID → 双保险判为 @
    clean, at = detect_at_bot(
        {"content": " 在吗", "mentions": [{"id": APP}]}, APP, "GROUP_MESSAGE_CREATE"
    )
    check("mentions 命中 → @机器人", at, f"实际 at={at}")

    # 6) 普通群消息 → 不算 @ 机器人
    clean, at = detect_at_bot(
        {"content": "今天天气不错"}, APP, "GROUP_MESSAGE_CREATE"
    )
    check("普通消息 → 不算@机器人", not at, f"实际 at={at}")
    check("普通消息 → 正文不变", clean == "今天天气不错", f"实际 {clean!r}")

    # 7) 单独一个 @（后面没字）→ 剥完是空的
    clean, at = split_mentions(" <@!1905646947> ", APP)
    check("单独@ → 正文为空", clean == "", f"实际 {clean!r}")

    # 8) 多个 @ 混在一起
    clean, at = split_mentions(" <@!1905646947> <@ABC123> 帮我看看", APP)
    check("多个@ → 都剥掉", clean == "帮我看看", f"实际 {clean!r}")
    check("多个@ → 机器人仍被识别", at, f"实际 at={at}")


async def test_repeat() -> None:
    print("\n== 复读机 ==")
    sent: list[tuple[str, str]] = []

    async def send(gid: str, text: str) -> None:
        sent.append((gid, text))

    # 1) 一个人刷 3 条 → 不复读
    r = Repeater(0.05, send, min_count=3)
    for _ in range(3):
        r.observe("g1", "顶", "u1")
    await asyncio.sleep(0.2)
    check("同一人刷屏不复读", sent == [], f"实际发了 {sent}")

    # 2) 两个不同的人刷 3 条 → 复读一次
    sent.clear()
    r = Repeater(0.05, send, min_count=3)
    r.observe("g1", "顶", "u1")
    r.observe("g1", "顶", "u2")
    r.observe("g1", "顶", "u1")
    await asyncio.sleep(0.2)
    check("两人刷够条数会复读", sent == [("g1", "顶")], f"实际发了 {sent}")

    # 3) 同一串只复读一次
    sent.clear()
    r.observe("g1", "顶", "u3")
    r.observe("g1", "顶", "u4")
    await asyncio.sleep(0.2)
    check("同一串不复读第二次", sent == [], f"实际发了 {sent}")

    # 4) 话题变了 → 旧串取消，新串重新开始
    sent.clear()
    r = Repeater(0.2, send, min_count=3)
    r.observe("g1", "aaa", "u1")
    r.observe("g1", "aaa", "u2")
    r.observe("g1", "aaa", "u3")   # 够条数，上弦
    r.observe("g1", "bbb", "u1")   # 话题变了 → 取消
    await asyncio.sleep(0.35)
    check("话题变了取消待发", sent == [], f"实际发了 {sent}")

    # 5) 不同群互不串扰
    sent.clear()
    r = Repeater(0.05, send, min_count=3)
    r.observe("g1", "x", "u1")
    r.observe("g2", "x", "u2")
    r.observe("g1", "x", "u3")
    r.observe("g2", "x", "u4")
    r.observe("g1", "x", "u5")
    r.observe("g2", "x", "u6")
    await asyncio.sleep(0.2)
    check("两个群各自触发", sorted(sent) == [("g1", "x"), ("g2", "x")], f"实际发了 {sent}")

    # 6) 命令不参与复读
    sent.clear()
    r = Repeater(0.05, send, min_count=2)
    r.observe("g1", "/help", "u1")
    r.observe("g1", "/help", "u2")
    await asyncio.sleep(0.2)
    check("命令不触发复读", sent == [], f"实际发了 {sent}")

    await r.close()


def test_bili_extract() -> None:
    print("\n== B站链接提取 ==")

    # 1) 纯文本里的 b23 短链
    links = bili.extract_links({"content": "这个视频好看 https://b23.tv/abcdef"})
    check("文本里的短链", links == ["https://b23.tv/abcdef"], f"实际 {links}")

    # 2) 文本里的完整 BV 链接
    links = bili.extract_links(
        {"content": "https://www.bilibili.com/video/BV1xx411c7mD?p=1"}
    )
    check("文本里的完整链接", len(links) == 1 and "BV1xx411c7mD" in links[0], f"实际 {links}")

    # 3) ark_data 里的 jump_url
    links = bili.extract_links({
        "content": " ",
        "ark_data": {
            "ark_type": "tuwen",
            "fields": {"title": "视频", "jump_url": "https://b23.tv/xyz123"},
        },
    })
    check("ark_data 里的短链", links == ["https://b23.tv/xyz123"], f"实际 {links}")

    # 4) msg_elements 嵌套里的链接（引用消息）
    links = bili.extract_links({
        "content": " ",
        "message_type": 103,
        "msg_elements": [
            {"content": "看看这个 https://www.bilibili.com/video/BV1aa411a7aa"},
        ],
    })
    check("引用消息里的链接", len(links) == 1 and "BV1aa411a7aa" in links[0], f"实际 {links}")

    # 5) 无关消息不误报
    links = bili.extract_links({"content": "今天天气不错"})
    check("无关消息不误报", links == [], f"实际 {links}")

    # 6) 非 B 站链接不认
    links = bili.extract_links({"content": "https://example.com/foo"})
    check("非 B 站链接不认", links == [], f"实际 {links}")

    # 7) 卡片构造
    card = bili.build_card({
        "title": "标题", "owner": "UP主", "duration": "3:21",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "pic": "https://i0.hdslb.com/cover.jpg",
    })
    check("卡片构造成功", card is not None and card["type"] == "tuwen", f"实际 {card}")
    check("卡片字段完整",
          card is not None and set(card["content"]) == {"title", "description", "url", "pic_url"},
          f"实际 {card}")

    # 8) 没封面 → 不发卡片（走文本兜底）
    card = bili.build_card({
        "title": "标题", "url": "https://www.bilibili.com/video/BV1xx411c7mD", "pic": "",
    })
    check("缺封面不构造卡片", card is None, f"实际 {card}")

    # 9) 文本兜底格式
    text = bili.format_video({
        "title": "某视频", "owner": "某UP", "duration": "1:02",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
    })
    check("文本格式含标题和链接",
          "某视频" in text and "BV1xx411c7mD" in text, f"实际 {text}")

    # 10) 默认**不带** URL（QQ 会把链接渲染成小程序预览）
    check("默认不带链接", "https://" not in text, f"实际 {text}")

    # 11) BV 号单独一行、不带前缀（双击就能复制）
    lines = text.split("\n")
    check("BV号单独一行", "BV1xx411c7mD" in lines, f"实际 {lines}")
    check("BV号行无前缀", all(
        not l.startswith("BV号") for l in lines
    ), f"实际 {lines}")

    # 12) with_link=True 才带链接
    text2 = bili.format_video({
        "title": "某视频", "url": "https://www.bilibili.com/video/BV1xx411c7mD",
    }, with_link=True)
    check("with_link=True 带链接", "https://www.bilibili.com" in text2, f"实际 {text2}")


async def test_admin_ids() -> None:
    print("\n== 管理员 openid 多值匹配 ==")
    from bot.commands import CommandContext, handle

    # 群里的 member_openid 和私聊的 user_openid 不一样，
    # 白名单里填哪个都得能匹配上
    ctx = CommandContext(
        openid="MEMBER_OPENID",
        is_admin=True,
        author_ids=("AUTHOR_ID", "USER_OPENID", "MEMBER_OPENID"),
    )
    out = await handle("/whoami", ctx)
    check("whoami 列出所有标识",
          "AUTHOR_ID" in out and "USER_OPENID" in out and "MEMBER_OPENID" in out,
          f"实际 {out}")
    check("whoami 没有空行异常", "\n\n\n" not in out, f"实际 {out}")

    # 非管理员跑 /bili → 拒绝
    ctx2 = CommandContext(openid="X", is_admin=False, is_group=True, group_openid="G")
    out2 = await handle("/bili", ctx2)
    check("非管理员 /bili 被拒", "管理员" in out2, f"实际 {out2}")

    # 管理员跑 /bili → 正常
    ctx3 = CommandContext(openid="X", is_admin=True, is_group=True, group_openid="G")
    out3 = await handle("/bili", ctx3)
    check("管理员 /bili 正常", "B站识别" in out3, f"实际 {out3}")

    # /whoami 是公开命令，非管理员也能用
    ctx4 = CommandContext(openid="X", is_admin=False, author_ids=("U2",))
    out4 = await handle("/whoami", ctx4)
    check("非管理员可用 /whoami", "U2" in out4, f"实际 {out4}")


def test_switch() -> None:
    print("\n== B站开关 ==")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bili_off.json"
        sw = bili.BiliSwitch(p, enabled=True, mode="black")
        check("黑名单模式默认开", sw.allowed("group", "g1"))
        sw.set_group("g1", False)
        check("关掉后不再识别", not sw.allowed("group", "g1"))
        check("其他群不受影响", sw.allowed("group", "g2"))
        check("私聊不受按群开关影响", sw.allowed("c2c", ""))

        # 重新加载要能读回
        sw2 = bili.BiliSwitch(p, enabled=True, mode="black")
        check("名单持久化", not sw2.allowed("group", "g1"))

        sw3 = bili.BiliSwitch(Path(td) / "w.json", enabled=True, mode="white")
        check("白名单模式默认关", not sw3.allowed("group", "g1"))
        sw3.set_group("g1", True)
        check("白名单开启后可用", sw3.allowed("group", "g1"))

        sw4 = bili.BiliSwitch(p, enabled=False, mode="black")
        check("全局关闸时不识别", not sw4.allowed("c2c", ""))


async def main() -> int:
    await test_repeat()
    test_bili_extract()
    test_switch()
    await test_admin_ids()
    test_mentions()
    print()
    if FAILED:
        print(f"❌ {FAILED} 项失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
