"""B站分享解析（QQ 官方开放平台版）。

⚠️ 和 Nico_21599 版不是一回事，别照抄：
OneBot 有 raw_message + CQ 码（``[CQ:json,data=...]``），官方协议**完全没有**这俩。
官方事件里链接可能出现在：
- ``content`` 纯文本（最常见，直接甩个 b23.tv 短链）
- ``ark_data.fields.jump_url``（图文 H5 / 小程序卡片）
- ``msg_elements[].content`` / ``msg_elements[].ark_data``（引用消息里的嵌套内容）
- ``attachments[].url``（少见）

所以这里的思路是：**在事件数据里到处找 bilibili / b23 链接**，找到就解析成视频信息。
找不到就返回 None —— 不是 B 站内容不报错，绝不影响正常消息处理。

短链（b23.tv）需要跟一次跳转才能拿到 BV 号：只读 302 的 Location，不下载视频页正文；
结果按短链缓存，失败不缓存（下次还能重试）。
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

import aiohttp

log = logging.getLogger("purana.bili")

# BV 号：BV + 10 位 base58 字符
_BV_RE = re.compile(r"BV[1-9A-HJ-NP-Za-km-z]{10}")
_URL_RE = re.compile(r"https?://[^\s\"'<>\\)\]】]+")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=8)
_REDIRECT_LIMIT = 5
_VIEW_API = "https://api.bilibili.com/x/web-interface/view"

# 短链 → BV 号；只缓存成功结果，失败留着下次重试
_BV_CACHE: dict[str, str] = {}


# ---------------------------------------------------------------- 提取


def _walk_strings(obj):
    """递归遍历，产出所有字符串。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def _pick_bili_links(strings) -> list[str]:
    """从一串字符串里挑出 B 站相关链接（去重、保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for s in strings:
        for m in _URL_RE.finditer(s or ""):
            u = m.group(0).rstrip(".,，。！!?)）")
            if "bilibili.com" not in u and "b23.tv" not in u:
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
        if out:
            break   # 找到就够了，别把整个事件里的字符串都翻一遍
    return out


def has_link(data: dict) -> bool:
    """事件里有没有 B 站链接。

    **纯同步判断，不发网络请求** —— 调用方用它做「要不要走 B 站流程」的快速判断，
    避免在收消息的循环里等网络。
    """
    return bool(extract_links(data))


def extract_links(data: dict) -> list[str]:
    """从群/私聊事件数据里找出 B 站链接。

    只看几个已知位置，不做全量深搜 —— 事件里有 auth_token 之类的长字符串，
    全量搜纯属浪费。
    """
    candidates: list[str] = []

    content = data.get("content")
    if isinstance(content, str):
        candidates.append(content)

    ark = data.get("ark_data")
    if isinstance(ark, dict):
        candidates.extend(_walk_strings(ark))

    elements = data.get("msg_elements")
    if isinstance(elements, list):
        for el in elements:
            if isinstance(el, dict):
                candidates.extend(_walk_strings(el))

    for att in data.get("attachments") or []:
        if isinstance(att, dict):
            url = att.get("url")
            if isinstance(url, str):
                candidates.append(url)

    return _pick_bili_links(candidates)


# ---------------------------------------------------------------- 解析


async def _next_location(session: aiohttp.ClientSession, url: str) -> str | None:
    """取 302 的 Location；HEAD 不行再退回 GET（仍不读正文）。"""
    for method in ("head", "get"):
        try:
            async with getattr(session, method)(
                url,
                allow_redirects=False,
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            ) as resp:
                if method == "head" and resp.status in (403, 405):
                    continue
                loc = resp.headers.get("Location")
                return urljoin(url, loc) if loc else None
        except Exception as exc:
            log.debug("短链跳转失败（%s %s）：%s", method, url, exc)
            return None
    return None


async def resolve_bvid(session: aiohttp.ClientSession, url: str) -> str | None:
    """跟随短链跳转取 BV 号（最多 5 跳，只读 Location）。"""
    if url in _BV_CACHE:
        return _BV_CACHE[url]

    current = url
    try:
        for _ in range(_REDIRECT_LIMIT):
            m = _BV_RE.search(current)
            if m:
                _BV_CACHE[url] = m.group(0)
                return m.group(0)
            nxt = await _next_location(session, current)
            if not nxt:
                break
            current = nxt
    except Exception as exc:
        log.warning("短链解析失败（%s）：%s", url, exc)
    return None


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def get_video(
    session: aiohttp.ClientSession, bvid: str
) -> dict | None:
    """查 B 站接口拿标题 / UP / 封面 / 时长。失败返回 None。"""
    try:
        async with session.get(
            _VIEW_API,
            params={"bvid": bvid},
            headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/"},
            timeout=_TIMEOUT,
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception:
        log.warning("B站视频信息获取失败：%s", bvid, exc_info=True)
        return None

    if not isinstance(data, dict) or data.get("code") != 0:
        log.info("B站接口返回异常（%s）：code=%s", bvid, (data or {}).get("code"))
        return None

    d = data.get("data") or {}
    pic = (d.get("pic") or "").strip()
    if pic.startswith("http://"):
        pic = "https://" + pic[len("http://"):]
    return {
        "bvid": bvid,
        "title": (d.get("title") or "").strip(),
        "owner": ((d.get("owner") or {}).get("name") or "").strip(),
        "pic": pic,
        "duration": _fmt_duration(d.get("duration") or 0),
        "url": f"https://www.bilibili.com/video/{bvid}",
    }


async def parse_event(
    data: dict, session: aiohttp.ClientSession | None = None
) -> dict | None:
    """事件数据 → 视频信息字典；不是 B 站内容返回 None。"""
    links = extract_links(data)
    if not links:
        return None

    bvid = None
    for u in links:
        m = _BV_RE.search(u)
        if m:
            bvid = m.group(0)
            break
    if not bvid and session is not None:
        for u in links:
            bvid = await resolve_bvid(session, u)
            if bvid:
                break
    if not bvid:
        return None

    info = await get_video(session, bvid) if session is not None else None
    if info is None:
        # 接口挂了也照样认：至少把 BV 号和链接报出来
        info = {
            "bvid": bvid,
            "title": "",
            "owner": "",
            "pic": "",
            "duration": "",
            "url": f"https://www.bilibili.com/video/{bvid}",
        }
    return info


def format_video(info: dict) -> str:
    """视频信息 → 文本（当卡片发不出去时的兜底）。"""
    lines = []
    if info.get("title"):
        lines.append(info["title"])
    meta = []
    if info.get("owner"):
        meta.append(f"UP：{info['owner']}")
    if info.get("duration"):
        meta.append(f"时长：{info['duration']}")
    if meta:
        lines.append("　".join(meta))
    lines.append(info.get("url") or f"https://www.bilibili.com/video/{info.get('bvid','')}")
    return "\n".join(lines)


def build_card(info: dict) -> dict | None:
    """视频信息 → QQ 图文卡片（msg_type=8, type=tuwen）。

    官方要求：title 1-50 字、description 1-200 字、url/pic_url 必须是 https
    且长度 ≤1024。缺封面就发不了卡片，返回 None 走文本兜底。
    """
    title = (info.get("title") or info.get("bvid") or "").strip()
    if not title:
        return None
    pic = (info.get("pic") or "").strip()
    url = (info.get("url") or "").strip()
    if not url.startswith("https://") or not pic.startswith("https://"):
        return None
    desc_bits = []
    if info.get("owner"):
        desc_bits.append(f"UP：{info['owner']}")
    if info.get("duration"):
        desc_bits.append(f"时长：{info['duration']}")
    desc = "　".join(desc_bits) or " bilibili 视频"
    return {
        "type": "tuwen",
        "content": {
            "title": title[:50],
            "description": desc[:200],
            "url": url[:1024],
            "pic_url": pic[:1024],
        },
    }


# ---------------------------------------------------------------- 开关


class BiliSwitch:
    """B站识别的开关：全局总闸 + 黑白名单（模式由 .env 的 BILI_MODE 决定）。

    - 总闸来自 .env 的 BILI_ENABLE（0/false/off = 全关）
    - BILI_MODE=black（默认）：默认开，名单里的群关；初值来自 BILI_OFF_GROUPS
    - BILI_MODE=white：默认关，名单里的群开；初值来自 BILI_ONLY_GROUPS
    - 名单持久化到 data/bili_off.json
    - 私聊不受按群开关影响（只看总闸）
    """

    def __init__(
        self,
        path,
        enabled: bool = True,
        mode: str = "black",
        off_groups=(),
        on_groups=(),
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.mode = "white" if str(mode).strip().lower().startswith("w") else "black"
        self.off_groups: set[str] = {str(g).strip() for g in off_groups if str(g).strip()}
        self.on_groups: set[str] = {str(g).strip() for g in on_groups if str(g).strip()}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for key in ("off_groups", "on_groups"):
            groups = data.get(key)
            if isinstance(groups, list):
                setattr(
                    self,
                    key,
                    {str(g).strip() for g in groups if str(g).strip()},
                )

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "off_groups": sorted(self.off_groups),
                "on_groups": sorted(self.on_groups),
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            log.warning("B站开关状态写入失败：%s", self.path, exc_info=True)

    @property
    def mode_label(self) -> str:
        return "白名单（默认关）" if self.mode == "white" else "黑名单（默认开）"

    def allowed(self, conv: str, target: str) -> bool:
        """这条会话能不能走 B 站识别。"""
        if not self.enabled:
            return False
        if conv != "group":
            return True
        gid = str(target).strip()
        if self.mode == "white":
            return gid in self.on_groups
        return gid not in self.off_groups

    def set_group(self, gid: str, on: bool) -> None:
        gid = str(gid).strip()
        if not gid:
            return
        if self.mode == "white":
            if on:
                self.on_groups.add(gid)
            else:
                self.on_groups.discard(gid)
        else:
            if on:
                self.off_groups.discard(gid)
            else:
                self.off_groups.add(gid)
        self._save()
