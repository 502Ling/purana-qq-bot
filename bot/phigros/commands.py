"""Phigros 命令层 —— 只搬**不依赖玩家存档**的功能。

移植自 Nico_21599 的 bot/phigros/commands.py，但**砍掉了所有依赖存档的子命令**：
- ❌ 砍掉：bind / unbind / refresh / info / b30 / b30img / rks / score / grade
  （这些要登录 + 拉云端存档，牵扯账号体系，超出本次范围）
- ✅ 保留：song（查曲）/ random（随机一首）/ hardest（定数最高）
        / notes（音符分布）/ cover（曲绘）/ alias（手动别名）

跟 Nico 版的差异：
1. **路径不写死** —— 曲库/曲绘从 ``PGR_DATA_ROOT`` 解析（见 paths.py）
2. **发图走 QQ 官方协议** —— Nico 用 NapCat 的 CQ 码，这里交回 run.py 用
   ``media.send_media_file``（msg_type=7）
3. **不发合并转发** —— 官方协议的合并转发要另一套接口，这次不碰；文字直接回
"""
from __future__ import annotations

import logging

from . import data, illus, notes, paths

log = logging.getLogger("purana.phigros.commands")

DIFF_ORDER = data.DIFF_ORDER

HELP_TEXT = """Phigros 工具（无需绑定，随时可用）：
/pgr song <曲名|别名|作曲> - 查曲
/pgr random [难度] - 随机来一首（EZ/HD/IN/AT）
/pgr hardest [难度] [N] - 定数最高的 N 首（默认 IN 前 5）
/pgr notes <曲名> [难度] - 看某首歌的音符分布（tap/drag/hold/flick）
/pgr cover <曲名> [难度] - 发某首歌的曲绘图片
/pgr alias <曲名> <别名> - 给曲目加手动别名（仅管理员）"""


def _song_block(s: data.Song) -> str:
    lines = [s.name]
    meta = []
    if s.pack:
        meta.append(f"曲包：{s.pack}")
    if s.bpm:
        meta.append(f"BPM：{s.bpm}")
    if meta:
        lines.append("｜".join(meta))
    if s.consts:
        lines.append(f"定数：{s.fmt_consts()}")
    who = []
    if s.composer:
        who.append(f"作曲：{s.composer}")
    if s.illustrator:
        who.append(f"曲绘：{s.illustrator}")
    if who:
        lines.append("｜".join(who))
    if s.charters:
        lines.append(f"谱师：{s.fmt_charters()}")
    if s.aliases:
        lines.append("别名：" + "、".join(s.aliases[:6]))
    return "\n".join(lines)


def _split_diff(args: list[str]) -> tuple[list[str], str | None]:
    """把难度参数（EZ/HD/IN/AT/SP/LEGACY）从参数里摘出来。"""
    toks = list(args)
    diff = None
    for t in list(toks):
        if t.upper() in data.ALL_DIFFS:
            diff = t.upper()
            toks.remove(t)
            break
    return toks, diff


class PgrResult:
    """命令处理结果。

    text  = 要回复的文字（可为空）
    image = 要发的图（本地路径），配合 caption 一起发
    """

    def __init__(self, text: str = "", image: str = "", caption: str = "") -> None:
        self.text = text
        self.image = image
        self.caption = caption


def handle(args: list[str], is_admin: bool = False) -> PgrResult:
    """处理一条 /pgr 命令。返回 PgrResult。"""
    data.maybe_reload()

    if not args:
        return PgrResult(HELP_TEXT)

    # 配置/数据自检
    err = paths.status()
    if err:
        return PgrResult(err)

    sub = args[0].lower()
    rest = args[1:]

    if sub in ("help", "phelp", "-h", "--help"):
        return PgrResult(HELP_TEXT)

    if sub == "song":
        if not rest:
            return PgrResult("用法：/pgr song <曲名 / 别名 / 作曲>")
        hits = data.find(" ".join(rest))
        if not hits:
            return PgrResult(f"没找到「{' '.join(rest)}」，换个关键词试试")
        if len(hits) == 1:
            return PgrResult(_song_block(hits[0]))
        head = _song_block(hits[0])
        more = "、".join(s.name for s in hits[1:])
        return PgrResult(f"{head}\n\n还有相似：{more}")

    if sub == "random":
        diff = rest[0].upper() if rest else None
        if diff and diff not in DIFF_ORDER:
            return PgrResult(f"难度只能是 {'/'.join(DIFF_ORDER)}")
        s = data.random_song(diff)
        if s is None:
            return PgrResult("没找到曲目")
        tag = f"[{diff}] " if diff else ""
        return PgrResult(f"随机来一首 {tag}\n" + _song_block(s))

    if sub == "hardest":
        diff = "IN"
        n = 5
        nums = []
        for a in rest:
            if a.upper() in DIFF_ORDER:
                diff = a.upper()
            elif a.isdigit():
                nums.append(int(a))
        if nums:
            n = max(1, min(20, nums[0]))
        rows = data.hardest(diff, n)
        if not rows:
            return PgrResult(f"没有 {diff} 难度的曲目")
        lines = [f"{diff} 定数最高前 {len(rows)}："]
        for i, s in enumerate(rows, 1):
            lines.append(f"{i}. {s.name} — {s.const_of(diff):.1f}")
        return PgrResult("\n".join(lines))

    if sub == "notes":
        if not rest:
            return PgrResult("用法：/pgr notes <曲名> [难度]")
        toks, diff = _split_diff(rest)
        if not toks:
            return PgrResult("用法：/pgr notes <曲名> [难度]")
        hits = data.find(" ".join(toks))
        if not hits:
            return PgrResult(f"没找到「{' '.join(toks)}」")
        return PgrResult(notes.format_song(hits[0], diff))

    if sub == "cover":
        if not rest:
            return PgrResult("用法：/pgr cover <曲名> [难度]（难度：EZ/HD/IN/AT）")
        toks, diff = _split_diff(rest)
        if not toks:
            return PgrResult("用法：/pgr cover <曲名> [难度]")
        hits = data.find(" ".join(toks))
        if not hits:
            return PgrResult(f"没找到「{' '.join(toks)}」")
        s = hits[0]
        vars_ = illus.variants(s.song_id)
        path = illus.lookup(s.song_id, diff)
        if not path:
            if diff:
                extra = f"（有：{'/'.join(vars_)}）" if vars_ else ""
                return PgrResult(f"《{s.name}》没有 {diff} 难度的曲绘{extra}")
            return PgrResult(f"没找到《{s.name}》的曲绘（可能还没导出）")
        cap = s.name + (f" — {s.composer}" if s.composer else "")
        if diff:
            cap += f" [{diff}]"
        elif vars_:
            cap += f"（可加难度：{'/'.join(vars_)}）"
        return PgrResult("", image=path, caption=cap)

    if sub == "alias":
        if not is_admin:
            return PgrResult("别名命令仅管理员可用")
        usage = (
            "用法：/pgr alias <曲名> <别名>   （多个别名用空格分隔；"
            "曲名含空格时用 /pgr alias <曲名> = <别名1> <别名2>）"
        )
        if not rest:
            return PgrResult(usage)
        # 支持「曲名 = 别名1 别名2」（曲名含空格时用）
        import re as _re

        parts = _re.split(r"\s*[=＝:：]\s*", " ".join(rest), maxsplit=1)
        if len(parts) == 2:
            q, alias_part = parts[0], parts[1]
        else:
            q, alias_part = rest[0], " ".join(rest[1:])
        aliases = [a for a in _re.split(r"[\s,，、|]+", alias_part) if a]
        if not q or not aliases:
            return PgrResult(usage)
        hits = data.find(q)
        if not hits:
            return PgrResult(f"没找到「{q}」")
        s = hits[0]
        lines = [f"《{s.name}》（{s.song_id}）"]
        if len(hits) > 1:
            lines.append("（匹配到多首，取了最接近的一首；不对就用更精确的名字）")
        added, skipped = [], []
        for a in aliases:
            ok, msg = data.add_manual_alias(s.song_id, a)
            (added.append(a) if ok else skipped.append(f"{a}（{msg}）"))
        s = next((x for x in data.SONGS if x.song_id == s.song_id), s)
        if added:
            lines.append("已添加别名：" + "、".join(added))
        if skipped:
            lines.append("跳过：" + "、".join(skipped))
        lines.append("现用别名：" + ("、".join(s.aliases) if s.aliases else "（无）"))
        return PgrResult("\n".join(lines))

    return PgrResult(f"不认识 /pgr {sub}，看下 /pgr help")
