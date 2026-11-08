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
from bot.repeat import Repeater

FAILED = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAILED
    if cond:
        print(f"  [OK]   {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} {extra}")


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
    print()
    if FAILED:
        print(f"❌ {FAILED} 项失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
