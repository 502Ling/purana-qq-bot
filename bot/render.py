"""把文字渲染成图片（Pillow）。

用途：QQ 的文字消息又长又窄，读起来费劲；把命令输出、说明、表格之类的东西
渲染成一张图发出去，体验好得多（比如 /help）。

⚠️ 两个坑：
1. **雅黑渲染不了 emoji** —— 会变成豆腐块（□）。所以渲染前把 emoji 去掉。
2. **中英文混排的换行**：中文没有空格，不能按单词断。所以这里按「字符」逐个量宽度，
   但英文单词尽量不拆开（拆成 "hel" + "lo" 很难看）。

只用 Pillow + Windows 自带字体，不额外拉依赖。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("purana.render")

# Pillow 是**可选依赖**：没装也不能让整个机器人起不来。
# 所以这里不硬 import，渲染时才 import 并检查。
try:  # pragma: no cover - 只是探测能不能用
    import PIL  # noqa: F401

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    PIL_AVAILABLE = False

    class _FakeImage:  # type: ignore
        @staticmethod
        def new(*a, **kw):
            raise RuntimeError("Pillow 未安装，无法把文字渲染成图片")

        @staticmethod
        def open(*a, **kw):
            raise RuntimeError("Pillow 未安装，无法把文字渲染成图片")

    Image = _FakeImage()  # type: ignore
else:
    from PIL import Image, ImageDraw, ImageFont

# 字体候选（Windows 自带）。第一个存在的就用。
FONT_REGULAR = [
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
    r"C:\Windows\Fonts\simsun.ttc",     # 宋体
    r"C:\Windows\Fonts\consola.ttf",    # Consolas（纯英文时的兜底）
]
FONT_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
]

# 配色（深色卡片）
BG = (24, 25, 33)
BG_TITLE = (32, 34, 44)
FG = (226, 228, 236)
FG_DIM = (140, 145, 165)
ACCENT = (122, 162, 247)

PADDING = 36
TITLE_SIZE = 30
BODY_SIZE = 22
LINE_SPACING = 1.62
MAX_WIDTH = 940          # 卡片最大内容宽度（超了会换行）
MIN_WIDTH = 320          # 内容很少时的最小宽度
CORNER = 18

# emoji / 符号区：雅黑渲染不了，直接剔掉（否则是豆腐块）
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoji 主区
    "\U0001F1E6-\U0001F1FF"   # 区域指示符（国旗）
    "\u2600-\u27BF"           # 杂项符号
    "\u2B00-\u2BFF"           # 箭头等
    "\uFE0F"                  # 变体选择符
    "\u200D"                  # 零宽连接符
    "\u2190-\u21FF"           # 箭头
    "\u2500-\u25FF"           # 制表符/方块
    "]+"
)

# 连续英文/数字算一个「词」，不拆开
_WORD_RE = re.compile(r"[A-Za-z0-9_\-+./:@#$%&*]+")


def _find_font(candidates: list[str]) -> str | None:
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


def _load_font(candidates: list[str], size: int):
    """加载字体；找不到就用 Pillow 默认字体（会丑，但不会崩）。"""
    path = _find_font(candidates)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as exc:
            log.warning("字体加载失败（%s）：%s", path, exc)
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def clean_text(text: str) -> str:
    """清掉渲染不出来的字符（emoji），并统一换行符。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _EMOJI_RE.sub("", text)
    # 去掉行尾空格（多行字符串拼接时常有）
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines)


def _tokenize(line: str) -> list[str]:
    """把一行拆成「不可再分的排版单元」。

    英文单词/数字串算一个（不拆），中文每个字算一个（可断）。
    """
    tokens: list[str] = []
    i = 0
    while i < len(line):
        m = _WORD_RE.match(line, i)
        if m:
            tokens.append(m.group(0))
            i = m.end()
        else:
            tokens.append(line[i])
            i += 1
    return tokens


def _wrap_line(draw, line: str, font, max_width: int) -> list[str]:
    """按宽度折行。"""
    if not line:
        return [""]
    tokens = _tokenize(line)
    out: list[str] = []
    cur = ""
    cur_w = 0
    for tok in tokens:
        w = draw.textlength(tok, font=font)
        if cur and cur_w + w > max_width:
            out.append(cur)
            cur, cur_w = "", 0
        cur += tok
        cur_w += w
    if cur or not out:
        out.append(cur)
    return out


def _measure(draw, body_font, title_font, title: str, body_lines: list[str], max_width: int) -> tuple[int, int]:
    """算内容尺寸：(宽, 高)。"""
    line_h = int(BODY_SIZE * LINE_SPACING)
    content_w = 0

    for ln in body_lines:
        content_w = max(content_w, int(draw.textlength(ln, font=body_font)))

    title_h = 0
    if title:
        content_w = max(content_w, int(draw.textlength(title, font=title_font)))
        title_h = int(TITLE_SIZE * 1.7)

    width = max(MIN_WIDTH, min(MAX_WIDTH, content_w))
    height = title_h + len(body_lines) * line_h
    return width, height


def render_text(
    text: str,
    title: str = "",
    width: int = 0,
    theme: str = "dark",
):
    """把文字渲染成一张图。返回 PIL Image。"""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow 未安装，无法把文字渲染成图片")
    text = clean_text(text)
    title = clean_text(title).replace("\n", " ").strip()

    body_font = _load_font(FONT_REGULAR, BODY_SIZE)
    title_font = _load_font(FONT_BOLD, TITLE_SIZE)

    # 先画到一张临时图上量宽度（ImageDraw 需要有画布才能量）
    probe = Image.new("RGB", (10, 10))
    pdraw = ImageDraw.Draw(probe)

    # 折行
    limit = width - PADDING * 2 if width else MAX_WIDTH - PADDING * 2
    body_lines: list[str] = []
    for raw in text.split("\n"):
        body_lines.extend(_wrap_line(pdraw, raw, body_font, limit))

    if width:
        content_w = width - PADDING * 2
    else:
        content_w = max(
            [int(pdraw.textlength(ln, font=body_font)) for ln in body_lines] or [0]
        )
        if title:
            content_w = max(content_w, int(pdraw.textlength(title, font=title_font)))
        content_w = max(MIN_WIDTH - PADDING * 2, min(MAX_WIDTH - PADDING * 2, content_w))

    title_h = int(TITLE_SIZE * 1.7) if title else 0
    line_h = int(BODY_SIZE * LINE_SPACING)
    img_w = content_w + PADDING * 2
    img_h = title_h + len(body_lines) * line_h + PADDING * 2

    bg = BG
    fg = FG
    accent = ACCENT
    bg_title = BG_TITLE
    if theme == "light":
        bg, fg, accent, bg_title = (250, 250, 252), (32, 34, 42), (52, 100, 210), (238, 240, 246)

    img = Image.new("RGB", (img_w, img_h), bg)
    draw = ImageDraw.Draw(img)

    # 圆角背景（画在一张 mask 上）
    mask = Image.new("L", (img_w, img_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img_w - 1, img_h - 1], radius=CORNER, fill=255
    )

    y = PADDING
    if title:
        # 标题栏
        draw.rectangle([0, 0, img_w, title_h], fill=bg_title)
        draw.rectangle([0, title_h - 2, img_w, title_h], fill=accent)
        draw.text((PADDING, int(title_h * 0.28)), title, font=title_font, fill=fg)
        y = title_h + int(PADDING * 0.6)

    for ln in body_lines:
        draw.text((PADDING, y), ln, font=body_font, fill=fg)
        y += line_h

    # 圆角：把圆角外的像素涂成和背景同色（PNG 不支持透明圆角时用）
    flat = Image.new("RGB", (img_w, img_h), bg)
    img = Image.composite(img, flat, mask)
    return img


def render_to_file(
    text: str,
    path: Path,
    title: str = "",
    width: int = 0,
    theme: str = "dark",
) -> Path:
    """渲染并保存成 PNG。返回文件路径。"""
    img = render_text(text, title=title, width=width, theme=theme)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
    log.info("文字渲染成图：%s（%sx%s）", path.name, img.width, img.height)
    return path
