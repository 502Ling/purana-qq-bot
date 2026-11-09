"""入站附件：从 QQ 事件里把图片抠出来。

官方协议里图片不在 ``content``（content 只有文字），而在 ``attachments[]``：
每个附件带 ``content_type``（如 ``image/png``）、``url``（QQ 的 CDN 链接）、
``filename``。引用消息里的图则藏在 ``msg_elements[].attachments[]``。

处理链路：
1. ``extract_images`` —— 纯同步，从事件里找出所有图片附件（不联网）
2. ``download_images`` —— 下载成 data URL，给视觉模型用

⚠️ 两个坑：
- 附件里的 ``url`` 带签名和有效期，**不能缓存复用**（过期就 403）。
- 官方 CDN 可能对 Referer 敏感，实测不带 Referer 能下（带反而被拒）。
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("purana.attachments")

# 下载单张图的上限（视觉模型按 base64 塞进 prompt，太大既慢又烧钱）
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 一次最多处理几张图
MAX_IMAGES = 3
# 下载超时
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)

# 允许的图片来源域名（QQ 官方 CDN）。白名单是安全底线：
# 用户可以随便发消息，不能让机器人去下载任意地址（SSRF）。
ALLOWED_HOSTS = (
    "qq.com",
    "qpic.cn",
    "qlogo.cn",
    "gtimg.cn",
    "sgroup.qq.com",
    "gchat.qpic.cn",
)


@dataclass
class InboundImage:
    """一张入站图片。"""

    url: str
    content_type: str = "image/png"
    filename: str = ""

    @property
    def is_image(self) -> bool:
        return self.content_type.lower().startswith("image/")


def _host_ok(url: str) -> bool:
    """域名白名单检查（防 SSRF）。"""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)


def _collect(data: dict, out: list[InboundImage]) -> None:
    """从事件数据里递归收集图片附件。"""
    # 顶层 attachments
    for att in data.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        ctype = str(att.get("content_type") or "").lower()
        url = str(att.get("url") or "")
        if not url:
            continue
        if ctype.startswith("image/"):
            out.append(InboundImage(url, ctype, str(att.get("filename") or "")))

    # 引用消息里的图（msg_elements[].attachments[]）
    for el in data.get("msg_elements") or []:
        if isinstance(el, dict):
            _collect(el, out)


def extract_images(data: dict) -> list[InboundImage]:
    """从事件里提取图片附件（纯同步，不发网络请求）。

    调用方拿它做「要不要走视觉流程」的判断 —— 必须同步，否则会卡住收消息的循环。
    """
    out: list[InboundImage] = []
    _collect(data, out)

    # 去重（同一张图可能在 attachments 和 msg_elements 里各出现一次）
    seen: set[str] = set()
    uniq: list[InboundImage] = []
    for img in out:
        if img.url in seen:
            continue
        seen.add(img.url)
        uniq.append(img)

    return uniq[:MAX_IMAGES]


async def download_to_data_url(
    session: aiohttp.ClientSession, img: InboundImage
) -> str | None:
    """下载一张图，转成 data URL（data:image/png;base64,...）。

    下载失败返回 None（不抛异常 —— 一张图下不到 shouldn't让整条消息失败）。
    """
    if not _host_ok(img.url):
        log.warning("图片来源域名不在白名单，跳过：%s", img.url[:80])
        return None

    try:
        async with session.get(
            img.url,
            timeout=DOWNLOAD_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                log.warning("图片下载 HTTP %s：%s", resp.status, img.url[:80])
                return None
            raw = await resp.read()
    except asyncio.TimeoutError:
        log.warning("图片下载超时：%s", img.url[:80])
        return None
    except aiohttp.ClientError as exc:
        log.warning("图片下载失败：%s（%s）", exc, img.url[:80])
        return None

    if not raw:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        log.warning("图片太大（%.1fMB），跳过：%s", len(raw) / 1024 / 1024, img.url[:60])
        return None

    ctype = img.content_type or "image/png"
    if not ctype.startswith("image/"):
        ctype = "image/png"
    return f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"


async def download_images(
    session: aiohttp.ClientSession, images: list[InboundImage]
) -> list[str]:
    """批量下载成 data URL；失败的跳过。"""
    if not images:
        return []
    results = await asyncio.gather(
        *(download_to_data_url(session, img) for img in images),
        return_exceptions=True,
    )
    out: list[str] = []
    for r in results:
        if isinstance(r, str) and r:
            out.append(r)
        elif isinstance(r, Exception):
            log.warning("图片下载异常：%s", r)
    return out
