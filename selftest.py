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

    # quiet_unknown：群里不 @ 时未知命令静默（返回空串，不刷屏）
    ctx5 = CommandContext(openid="X", is_admin=True, quiet_unknown=True)
    out5 = await handle("/nonexistent_cmd", ctx5)
    check("quiet_unknown → 未知命令返回空", out5 == "", f"实际 {out5!r}")

    # 但 quiet_unknown 下已知命令照样回
    ctx6 = CommandContext(openid="X", is_admin=True, quiet_unknown=True)
    out6 = await handle("/time", ctx6)
    check("quiet_unknown → 已知命令正常回", "本地时间" in out6, f"实际 {out6!r}")

    # 非 quiet 时未知命令给提示（老行为不变）
    ctx7 = CommandContext(openid="X", is_admin=True)
    out7 = await handle("/nonexistent_cmd", ctx7)
    check("非 quiet → 未知命令给提示", "未知命令" in out7, f"实际 {out7!r}")

    # 非管理员用管理类命令 + quiet_unknown → 也静默
    ctx8 = CommandContext(openid="X", is_admin=False, quiet_unknown=True)
    out8 = await handle("/restart", ctx8)
    check("quiet_unknown → 无权限命令静默", out8 == "", f"实际 {out8!r}")


async def test_media() -> None:
    print("\n== 富媒体（纯函数部分，不联网）==")
    from bot import media, attachments
    from bot.commands import CommandContext, handle

    # 1) 从回复文本里提取「整行就是图片 URL」的行
    urls = media.extract_image_urls(
        "看图\nhttps://pic.example.com/a.png?x=1\n正文里提到 https://x.com/b.jpg 不算",
        ("example.com",),
    )
    check("提取整行图片 URL", urls == ["https://pic.example.com/a.png?x=1"], f"实际 {urls}")

    # 2) 域名不在白名单 → 不提取（防 SSRF）
    urls = media.extract_image_urls(
        "https://evil.com/a.png", ("example.com",)
    )
    check("白名单外域名不提取", urls == [], f"实际 {urls}")

    # 3) strip 把整行图片 URL 删掉
    text = media.strip_image_lines(
        "看图\nhttps://pic.example.com/a.png\n结尾",
        ("example.com",),
    )
    check("strip 删掉整行 URL", text == "看图\n结尾", f"实际 {text!r}")

    # 4) 非整行的 URL 不删
    text = media.strip_image_lines(
        "见 https://x.com/b.jpg 详情", ("x.com",)
    )
    check("非整行 URL 保留", text == "见 https://x.com/b.jpg 详情", f"实际 {text!r}")

    # 5) file_type 推断：png/jpg 走真图片，gif 降级成文件
    check("png → file_type 1", media.file_type_for("image", "a.png") == 1)
    check("jpg → file_type 1", media.file_type_for("image", "a.jpg") == 1)
    check("gif → file_type 4（降级）", media.file_type_for("image", "a.gif") == 4)
    check("video → file_type 2", media.file_type_for("video", "a.mp4") == 2)
    check("voice → file_type 3", media.file_type_for("voice", "a.silk") == 3)

    # 6) host_allowed 子域名匹配
    check("子域名命中", media.host_allowed("https://a.example.com/x", ("example.com",)))
    check("同级域名不命中", not media.host_allowed("https://nexample.com/x", ("example.com",)))

    # 7) 入站图片提取：只认 image/*
    imgs = attachments.extract_images({
        "content": "",
        "attachments": [
            {"content_type": "image/png", "url": "https://qq.com/1.png"},
            {"content_type": "audio/ogg", "url": "https://qq.com/2.og"},
        ],
    })
    check("只提取图片附件", len(imgs) == 1 and imgs[0].content_type == "image/png", f"实际 {imgs}")

    # 8) 入站图片：引用消息里的图也能提
    imgs = attachments.extract_images({
        "content": "",
        "msg_elements": [
            {"attachments": [{"content_type": "image/jpeg", "url": "https://qq.com/r.jpg"}]},
        ],
    })
    check("引用消息里的图能提", len(imgs) == 1 and imgs[0].content_type == "image/jpeg", f"实际 {imgs}")

    # 9) /image 命令：URL 形式（加进 pending_media）
    ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                         image_domains=("example.com",))
    out = await handle("/image https://pic.example.com/a.png", ctx)
    check("/image URL 形式", out == "图来了。" and len(ctx.pending_media) == 1, f"实际 {out} {ctx.pending_media}")

    # 10) /image 命令：白名单外域名拒绝
    ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                         image_domains=("example.com",))
    out = await handle("/image https://evil.com/a.png", ctx)
    check("/image 白名单外拒绝", "不在允许" in out and not ctx.pending_media, f"实际 {out}")

    # 11) /image 命令：总闸关了
    ctx = CommandContext(openid="X", is_admin=True, media_enabled=False)
    out = await handle("/image https://pic.example.com/a.png", ctx)
    check("/image 总闸关", "已关闭" in out and not ctx.pending_media, f"实际 {out}")

    # 12) /image 命令：本地文件形式（临时目录）
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "cat.png").write_bytes(b"fake")
        ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                             media_dir=p, image_domains=("example.com",))
        out = await handle("/image cat", ctx)
        check("/image 本地文件", out == "图来了。" and len(ctx.pending_media) == 1, f"实际 {out} {ctx.pending_media}")

        # 13) 本地文件不存在
        ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                             media_dir=p, image_domains=("example.com",))
        out = await handle("/image dog", ctx)
        check("/image 本地文件不存在", "没找到" in out and not ctx.pending_media, f"实际 {out}")

        # 14) 目录穿越防御
        ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                             media_dir=p, image_domains=("example.com",))
        out = await handle("/image ../secret.png", ctx)
        check("/image 目录穿越拒绝", "不接受路径" in out and not ctx.pending_media, f"实际 {out}")


def test_ecust_vision() -> None:
    print("\n== ECUST 视觉（构造请求体，不联网）==")
    from bot.ecust import EcustClient

    client = EcustClient("https://x/api/v1", "key", "glm-4.7")
    # ask_images 会构造多模态内容块 —— 这里只验证它不报错、能构造
    import asyncio

    # 用一个假的 session 跑不通，所以只验证方法存在和签名
    check("ask_images 存在", hasattr(client, "ask_images"))
    check("ask_content 存在", hasattr(client, "ask_content"))


def test_render() -> None:
    print("\n== 文字渲染成图 ==")
    from bot import render

    check("Pillow 可用", render.PIL_AVAILABLE)

    # 1) emoji 要被去掉（雅黑渲染不了，会变豆腐块）
    out = render.clean_text("emoji 🐟 测试")
    check("emoji 被清理", "\U0001F41F" not in out, f"实际 {out!r}")
    check("emoji 清理后文字还在", "测试" in out, f"实际 {out!r}")

    # 2) 换行符统一
    out = render.clean_text("a\r\nb\rc")
    check("换行符统一为 \\n", out == "a\nb\nc", f"实际 {out!r}")

    # 3) 英文单词不被拆开
    toks = render._tokenize("Mixed 中英文 content 混排")
    check("英文单词完整", "Mixed" in toks and "content" in toks, f"实际 {toks}")
    check("中文逐字", "中" in toks and "排" in toks, f"实际 {toks}")

    # 4) 繁简转换
    check("繁转简可用", render.HAS_LCMAP)
    if render.HAS_LCMAP:
        check("繁体转简体", render.to_simplified("僅顯示幫助資訊") == "仅显示帮助资讯",
              f"实际 {render.to_simplified('僅顯示幫助資訊')!r}")
        check("简体保持不变", render.to_simplified("仅显示帮助") == "仅显示帮助",
              f"实际 {render.to_simplified('仅显示帮助')!r}")
        # ⚠️ 回归：emoji（非 BMP）不能被截断
        #    Windows 那边按 UTF-16 code unit 算长度，Python len() 会少算
        for case in ["\U0001F600 test", "\U0001F600\U0001F600 abc", "a\U0001F600b",
                     "hello \U0001F600 world end"]:
            check(f"emoji 不被截断 {case!r}", render.to_simplified(case) == case,
                  f"实际 {render.to_simplified(case)!r}")
        check("英文数字不受影响", render.to_simplified("hello world 123 /help img")
              == "hello world 123 /help img")

    # 5) 字体统一：正文和标题必须来自同一个家族
    regular, bold = render._resolve_family()
    check("字体家族有 Regular", bool(regular), f"实际 {regular!r}")
    check("字体家族有 Bold", bool(bold), f"实际 {bold!r}")
    # ⚠️ 判据是字体**家族名**（getname），不是文件名：
    #    雅黑 Regular 是 msyh.ttc、Bold 是 msyhbd.ttc，文件名不同但同源。
    if regular and bold and render.PIL_AVAILABLE:
        from PIL import ImageFont

        fam_r = ImageFont.truetype(regular, 20).getname()
        fam_b = ImageFont.truetype(bold, 20).getname()
        check("正文标题同源（家族名一致）", fam_r[0] == fam_b[0],
              f"实际 {fam_r} vs {fam_b}")

    # 6) 渲染出图（尺寸合理、不报错）
    if render.PIL_AVAILABLE:
        img = render.render_text("hello 普拉娜\n第二行", title="测试")
        check("渲染出图", img.width > 0 and img.height > 0, f"实际 {img.size}")
        check("有标题栏时图更高",
              render.render_text("x", title="T").height >
              render.render_text("x").height,
              "标题栏高度不对")

        # 7) 长文本会折行（行数变多）
        img2 = render.render_text("很长的文字" * 100)
        check("长文本折行后变高", img2.height > render.render_text("短").height,
              f"实际 {img2.size}")

        # 8) 存文件
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.png"
            render.render_to_file("测试", p, title="T")
            check("存成 PNG", p.is_file() and p.stat().st_size > 0, f"实际 {p}")

            # 9) 缓存：同一内容只渲染一次（路径相同）
            p1 = render.render_cached("同样的内容", title="T", cache_dir=Path(td))
            p2 = render.render_cached("同样的内容", title="T", cache_dir=Path(td))
            check("渲染缓存命中", p1 == p2 and p1.is_file(), f"实际 {p1} vs {p2}")

            # 10) 内容不同 → 缓存不串
            p3 = render.render_cached("不同的内容", title="T", cache_dir=Path(td))
            check("不同内容缓存不串", p3 != p1, f"实际 {p3}")


async def test_help_image() -> None:
    print("\n== /help 默认出图 ==")
    import tempfile
    from pathlib import Path

    from bot import render
    from bot.commands import CommandContext, handle

    cache = Path(tempfile.gettempdir()) / "purana_selftest_cache"

    # 1) /help → 图片（空文本回复）
    ctx = CommandContext(openid="X", is_admin=True, media_enabled=True,
                         render_available=render.PIL_AVAILABLE,
                         render_theme="dark", render_dir=cache)
    out = await handle("/help", ctx)
    check("/help 默认出图", len(ctx.pending_media) == 1, f"实际 {ctx.pending_media}")
    check("/help 文本回复为空（不刷屏）", out == "", f"实际 {out!r}")
    if ctx.pending_media:
        import os
        p = ctx.pending_media[0]["source"]
        check("/help 图已生成", os.path.isfile(p) and os.path.getsize(p) > 0, f"实际 {p}")

    # 2) /help text → 纯文本，不出图
    ctx2 = CommandContext(openid="X", is_admin=True, media_enabled=True,
                          render_available=render.PIL_AVAILABLE)
    out2 = await handle("/help text", ctx2)
    check("/help text 是纯文本", "可用命令" in out2, f"实际 {out2[:40]!r}")
    check("/help text 不出图", not ctx2.pending_media, f"实际 {ctx2.pending_media}")

    # 3) 没装 Pillow → 退回纯文本（不能失败）
    ctx3 = CommandContext(openid="X", is_admin=True, media_enabled=True,
                          render_available=False)
    out3 = await handle("/help", ctx3)
    check("没 Pillow → 退回纯文本", "可用命令" in out3, f"实际 {out3[:40]!r}")

    # 4) 发图总闸关了 → 退回纯文本
    ctx4 = CommandContext(openid="X", is_admin=True, media_enabled=False,
                          render_available=render.PIL_AVAILABLE)
    out4 = await handle("/help", ctx4)
    check("发图关闭 → 退回纯文本", "可用命令" in out4, f"实际 {out4[:40]!r}")


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
    await test_media()
    test_render()
    await test_help_image()
    test_ecust_vision()
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
