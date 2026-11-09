"""把文字渲染成图片（Pillow）。

用途：QQ 的文字消息又长又窄，读起来费劲；把命令输出、说明、表格之类的东西
渲染成一张一张的图发出去，体验好得多（比如 /help）。

设计原则（502 定的）：**简约**、**字体统一**。

⚠️ 踩过的坑：
1. **雅黑渲染不了 emoji** —— 会变成豆腐块（□）。所以渲染前把 emoji 去掉。
2. **中英文混排的换行**：中文没有空格，不能按单词断。所以这里按「字符」逐个量宽度，
   但英文单词尽量不拆开（拆成 "hel" + "lo" 很难看）。
3. **繁简**：用 Windows 自带的 LCMapStringW 强制转简体（详见 to_simplified 的注释）。
4. **字体必须统一**：正文和标题要从**同一个字体家族**取，否则看着像两种字体。
   所以先定家族，再分别取 Regular / Bold —— 不能各自 fallback。

只用 Pillow + Windows 自带字体，不额外拉依赖。
"""
from __future__ import annotations

import ctypes
import logging
import re
from ctypes import wintypes
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

    Image = _FakeImage()  # type: ignore
else:
    from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------- 繁简转换

_LCMAP_SIMPLIFIED_CHINESE = 0x02000000
_LOCALE_ZH_CN = "zh-CN"

try:
    _kernel32 = ctypes.windll.kernel32
    _kernel32.LCMapStringEx.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.LPWSTR,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.LPARAM,
    ]
    _kernel32.LCMapStringEx.restype = ctypes.c_int
    HAS_LCMAP = True
except Exception:  # pragma: no cover - 非 Windows
    HAS_LCMAP = False


def to_simplified(text: str) -> str:
    """繁体 → 简体（Windows 自带的 LCMapStringW）。

    为什么需要：502 要求「字体统一」，那就顺带保证**字也统一**——
    不管源文本是繁是简，渲染出来的都是简体。

    ⚠️ **长度必须按 UTF-16 code unit 算，不能用 Python 的 len()**：
    Windows 那边字符串是 UTF-16，emoji 这类非 BMP 字符占 2 个 code unit，
    而 Python 的 len() 只算 1 个 → 算出来的长度偏小，**末尾的字符会被截掉**
    （踩过：'😀 test' 被截成 '😀 tes'）。

    转换失败就原样返回（宁可不变，也不要弄坏内容）。
    """
    if not text or not HAS_LCMAP:
        return text
    try:
        src = ctypes.create_unicode_buffer(text)
        # UTF-16 code unit 数（不含结尾的 \0）
        src_len = len(text.encode("utf-16-le")) // 2
        n = _kernel32.LCMapStringEx(
            _LOCALE_ZH_CN, _LCMAP_SIMPLIFIED_CHINESE,
            src, src_len, None, 0, None, None, 0,
        )
        if n <= 0:
            return text
        buf = ctypes.create_unicode_buffer(n)
        r = _kernel32.LCMapStringEx(
            _LOCALE_ZH_CN, _LCMAP_SIMPLIFIED_CHINESE,
            src, src_len, buf, n, None, None, 0,
        )
        if r <= 0:
            return text
        return buf[:r].rstrip("\x00")
    except Exception as exc:  # pragma: no cover
        log.warning("繁简转换失败，原样返回：%s", exc)
        return text


# ---------------------------------------------------------------- 字体

# 字体家族：正文和标题**必须来自同一个家族**，否则看着像两种字体。
# 每项 = (Regular 路径, Bold 路径)。找不到 Bold 就用 Regular。
FONT_FAMILIES: list[tuple[str, str]] = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),   # 微软雅黑
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),  # 黑体（无粗体）
    (r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simsunb.ttf"), # 宋体
    (r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\consolab.ttf"),  # Consolas（纯 ASCII 兜底）
]


def _resolve_family() -> tuple[str, str]:
    """选一个**完整可用**的字体家族，返回 (Regular, Bold)。"""
    for regular, bold in FONT_FAMILIES:
        if Path(regular).is_file() and Path(bold).is_file():
            return regular, bold
    # 退而求其次：只有 Regular 也能用
    for regular, _bold in FONT_FAMILIES:
        if Path(regular).is_file():
            return regular, regular
    return "", ""


# 配色（深色为主，简约）
BG = (24, 25, 33)
FG = (228, 230, 238)
TITLE_FG = (140, 168, 248)
SEPARATOR = (58, 61, 76)

PADDING = 36
TITLE_SIZE = 30
BODY_SIZE = 24
LINE_SPACING = 1.6
MAX_WIDTH = 980          # 卡片最大内容宽度（超了会换行）
MIN_WIDTH = 320          # 内容很少时的最小宽度

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


def _load_font(path: str, size: int):
    """加载字体；找不到就用 Pillow 默认字体（会丑，但不会崩）。"""
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
    """渲染前的清洗：统一换行 → 去 emoji → 转简体。

    顺序有讲究：
    - 先去 emoji：emoji 是非 BMP 字符，会影响后面繁简转换的长度计算
    - 再转简体：保证输出来源一致
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _EMOJI_RE.sub("", text)
    text = to_simplified(text)
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

    # 字体家族：正文和标题同源（只有粗细不同）
    regular_path, bold_path = _resolve_family()
    body_font = _load_font(regular_path, BODY_SIZE)
    title_font = _load_font(bold_path, TITLE_SIZE)

    # 先画到临时图上量宽度（ImageDraw 需要有画布才能量）
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

    title_h = int(TITLE_SIZE * 1.9) if title else 0
    line_h = int(BODY_SIZE * LINE_SPACING)
    img_w = content_w + PADDING * 2
    img_h = title_h + len(body_lines) * line_h + PADDING * 2

    bg, fg, title_fg, sep = BG, FG, TITLE_FG, SEPARATOR
    if theme == "light":
        bg, fg, title_fg, sep = (
            (250, 250, 252), (34, 36, 44), (48, 96, 205), (222, 225, 232)
        )

    img = Image.new("RGB", (img_w, img_h), bg)
    draw = ImageDraw.Draw(img)

    y = PADDING
    if title:
        # 简约：标题就是一行字 + 一条细分隔线，不做标题栏底色
        draw.text((PADDING, y), title, font=title_font, fill=title_fg)
        y += int(TITLE_SIZE * 1.45)
        draw.line([(PADDING, y), (img_w - PADDING, y)], fill=sep, width=1)
        y += int(PADDING * 0.5)

    for ln in body_lines:
        draw.text((PADDING, y), ln, font=body_font, fill=fg)
        y += line_h

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
