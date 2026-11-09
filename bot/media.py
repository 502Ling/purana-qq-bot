"""QQ 官方富媒体：先上传拿 file_info，再发 msg_type=7 消息。

协议要点（官方文档 + 反复踩过的坑）：

1. **上传和发送是两个独立的 HTTP 请求**：
   - 上传：``POST /v2/{users|groups}/{id}/files`` → 返回 ``file_info``
   - 发送：``POST /v2/{users|groups}/{id}/messages``，带 ``msg_type=7`` + ``media.file_info``
   ``file_info`` 是个不透明的二进制串，**直接透传，不要解析**。

2. **单聊和群聊的上传接口互相隔离**：
   - 单聊 ``/v2/users/{user_openid}/files`` → 只能发到单聊
   - 群聊 ``/v2/groups/{group_openid}/files`` → 只能发到群聊
   用错了会直接报错。

3. **file_info 有 ttl**：响应里 ``ttl: 300`` 表示 5 分钟后失效（0 = 长期有效）。
   所以**不能缓存复用**，每次发图都要现传现发。

4. **三种上传方式**：
   - URL 上传：传 ``url``，由 QQ 服务端去下载转存。要求 URL 公网可访问。
   - 二进制直传：传 ``file_data``（base64），**上限 5MB**。
   - 分片上传：> 5MB 走这条（预上传 → 逐片 PUT → 确认分片 → 合并）。

5. **格式限制**：file_type=1（图片）只认 png/jpg；gif/webp/bmp 会走 file_type=4（文件）。
   语音只认 silk/mp3/wav；视频只认 mp4。

6. **file_type 超过软限制会降级**：图片软限 20MB、硬限 200MB；超软限降级成文件类型，
   超硬限报错（850031）。

7. ``srv_send_msg=true`` 可以在上传时直接发出去，但会**占用主动消息频次**。
   这里统一用 false（只拿 file_info），发送走独立的消息接口 —— 这样能带 msg_id
   走被动回复（额度更宽），也能复用同一个 file_info 发多条。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("purana.media")

API_BASE = "https://api.sgroup.qq.com"

# file_type（官方定义）
FILE_TYPE_IMAGE = 1
FILE_TYPE_VIDEO = 2
FILE_TYPE_VOICE = 3
FILE_TYPE_FILE = 4

# 场景标识（和 QQ 官方 SDK 的 ChatScope 保持一致）
SCOPE_C2C = "c2c"
SCOPE_GROUP = "group"

# 二进制直传上限（官方文档：5M）；超过就走分片
MAX_BINARY_UPLOAD = 5 * 1024 * 1024
# 分片大小（官方默认 5MB，服务端会在 upload_prepare 里下发 block_size）
DEFAULT_BLOCK_SIZE = 5 * 1024 * 1024
# md5_10m 的取样长度（官方文档：前 10002432 字节 ≈ 9.54MB）
MD5_10M_SIZE = 10_002_432
# 上传接口官方建议超时 ≥ 5 秒，这里给足
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=120)
# 分片 PUT 到 COS 的超时
PART_TIMEOUT = aiohttp.ClientTimeout(total=300)

# 图片扩展名 → file_type。注意 gif/webp/bmp 官方图片接口不收，
# 只能降级成 file_type=4（文件）发出去。
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


class MediaError(RuntimeError):
    """富媒体操作失败（上传/发送）。"""


@dataclass
class MediaRequest:
    """一条待发的富媒体请求。

    source 可以是：
    - http(s) URL（QQ 服务端自己去下载）
    - 本地文件路径（我们自己上传）
    """

    source: str
    kind: str = "image"          # image / video / voice / file
    file_name: str = ""          # 本地文件或 file_type=4 时用
    caption: str = ""            # 随图带的一句话（可选）
    scope: str = ""              # 留空 = 跟随当前会话场景
    target: str = ""             # 留空 = 跟随当前会话目标


@dataclass
class UploadResult:
    """上传结果。"""

    file_info: str
    file_uuid: str = ""
    ttl: int = 0
    raw_url: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- 工具


def _files_url(scope: str, target: str) -> str:
    if scope == SCOPE_C2C:
        return f"{API_BASE}/v2/users/{target}/files"
    return f"{API_BASE}/v2/groups/{target}/files"


def _upload_prepare_url(scope: str, target: str) -> str:
    if scope == SCOPE_C2C:
        return f"{API_BASE}/v2/users/{target}/upload_prepare"
    return f"{API_BASE}/v2/groups/{target}/upload_prepare"


def _upload_part_finish_url(scope: str, target: str) -> str:
    if scope == SCOPE_C2C:
        return f"{API_BASE}/v2/users/{target}/upload_part_finish"
    return f"{API_BASE}/v2/groups/{target}/upload_part_finish"


def _messages_url(scope: str, target: str) -> str:
    if scope == SCOPE_C2C:
        return f"{API_BASE}/v2/users/{target}/messages"
    return f"{API_BASE}/v2/groups/{target}/messages"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"QQBot {token}"}


def file_type_for(kind: str, name: str = "") -> int:
    """按媒体种类 + 文件名推断 file_type。

    图片只认 png/jpg —— 官方图片接口对 gif/webp/bmp 不收（会 850019），
    这类降级成 file_type=4（文件）发出去，至少能送达。
    """
    kind = (kind or "image").strip().lower()
    if kind == "video":
        return FILE_TYPE_VIDEO
    if kind == "voice":
        return FILE_TYPE_VOICE
    if kind == "file":
        return FILE_TYPE_FILE
    # 图片：按扩展名判断能不能走真·图片类型
    ext = Path(name or "").suffix.lower()
    if ext and ext not in IMAGE_EXTS:
        return FILE_TYPE_FILE
    return FILE_TYPE_IMAGE


async def _post_json(
    session: aiohttp.ClientSession,
    token: str,
    url: str,
    payload: dict,
    timeout: aiohttp.ClientTimeout = UPLOAD_TIMEOUT,
) -> dict:
    """POST JSON，返回解析后的 dict；失败抛 MediaError（带状态码和错误码）。"""
    try:
        async with session.post(
            url, json=payload, headers=_auth(token), timeout=timeout
        ) as resp:
            status = resp.status
            body = await resp.text()
    except asyncio.TimeoutError as exc:
        raise MediaError(f"上传超时（{url}）") from exc
    except aiohttp.ClientError as exc:
        raise MediaError(f"上传请求失败（{url}）：{exc}") from exc

    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {}

    if status >= 400:
        code = ""
        if isinstance(parsed, dict):
            code = parsed.get("code") or parsed.get("err_code") or ""
            msg = parsed.get("message") or parsed.get("msg") or body[:200]
        else:
            msg = body[:200]
        raise MediaError(f"上传失败 HTTP {status}（错误码 {code}）：{msg}")

    if not isinstance(parsed, dict):
        raise MediaError(f"上传返回格式异常：{str(parsed)[:200]}")
    return parsed


# ---------------------------------------------------------------- 上传


async def upload_by_url(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    url: str,
    file_type: int = FILE_TYPE_IMAGE,
    file_name: str = "",
) -> UploadResult:
    """URL 上传：QQ 服务端自己去下载转存。要求 URL 公网可访问。"""
    payload: dict = {"file_type": file_type, "url": url, "srv_send_msg": False}
    if file_name:
        payload["file_name"] = file_name
    data = await _post_json(session, token, _files_url(scope, target), payload)
    info = data.get("file_info")
    if not info:
        raise MediaError(f"上传返回里没有 file_info：{str(data)[:200]}")
    return UploadResult(
        file_info=info,
        file_uuid=data.get("file_uuid") or "",
        ttl=int(data.get("ttl") or 0),
        raw_url=data.get("raw_url") or "",
        extra=data,
    )


async def upload_by_bytes(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    data: bytes,
    file_type: int = FILE_TYPE_IMAGE,
    file_name: str = "",
) -> UploadResult:
    """二进制直传（base64）。**上限 5MB**，超过请走分片。"""
    if len(data) > MAX_BINARY_UPLOAD:
        raise MediaError(
            f"文件 {len(data) / 1024 / 1024:.1f}MB 超过直传上限 "
            f"{MAX_BINARY_UPLOAD / 1024 / 1024:.0f}MB，请用分片上传"
        )
    payload: dict = {
        "file_type": file_type,
        "file_data": base64.b64encode(data).decode("ascii"),
        "srv_send_msg": False,
    }
    if file_name:
        payload["file_name"] = file_name
    result = await _post_json(session, token, _files_url(scope, target), payload)
    info = result.get("file_info")
    if not info:
        raise MediaError(f"上传返回里没有 file_info：{str(result)[:200]}")
    return UploadResult(
        file_info=info,
        file_uuid=result.get("file_uuid") or "",
        ttl=int(result.get("ttl") or 0),
        raw_url=result.get("raw_url") or "",
        extra=result,
    )


def _hashes(data: bytes) -> dict[str, str]:
    """算 md5 / sha1 / md5_10m（分片上传用）。"""
    md5 = hashlib.md5(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest()
    if len(data) > MD5_10M_SIZE:
        md5_10m = hashlib.md5(data[:MD5_10M_SIZE]).hexdigest()
    else:
        md5_10m = md5
    return {"md5": md5, "sha1": sha1, "md5_10m": md5_10m}


async def upload_chunked(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    path: Path,
    file_type: int = FILE_TYPE_IMAGE,
    file_name: str = "",
) -> UploadResult:
    """分片上传（> 5MB 的文件）。

    四步：预上传 → 逐片 PUT 到预签名 URL → 每片确认 → 合并。
    """
    data = path.read_bytes()
    size = len(data)
    name = file_name or path.name
    hashes = _hashes(data)

    log.info("分片上传准备：%s（%.1fMB，type=%s）", name, size / 1024 / 1024, file_type)

    prepare = await _post_json(
        session,
        token,
        _upload_prepare_url(scope, target),
        {
            "file_type": file_type,
            "file_name": name,
            "file_size": size,
            **hashes,
        },
    )
    upload_id = prepare.get("upload_id")
    parts = prepare.get("parts") or []
    if not upload_id or not parts:
        raise MediaError(f"预上传返回异常：{str(prepare)[:200]}")

    block_size = int(prepare.get("block_size") or DEFAULT_BLOCK_SIZE)
    concurrency = max(1, min(int(prepare.get("concurrency") or 1), 10))
    log.info(
        "预上传成功：upload_id=%s，%s 片，每片 %s，并发 %s",
        str(upload_id)[:16], len(parts), block_size, concurrency,
    )

    sem = asyncio.Semaphore(concurrency)

    async def do_part(part: dict) -> None:
        index = int(part.get("index") or 0)
        presigned = part.get("presigned_url") or ""
        offset = (index - 1) * block_size
        chunk = data[offset: offset + block_size]
        md5 = hashlib.md5(chunk).hexdigest()
        async with sem:
            # 1) PUT 到 COS 预签名 URL
            for attempt in range(3):
                try:
                    async with session.put(
                        presigned, data=chunk, timeout=PART_TIMEOUT
                    ) as resp:
                        if resp.status >= 300:
                            raise MediaError(
                                f"分片 {index} PUT 失败 HTTP {resp.status}"
                            )
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise MediaError(f"分片 {index} 上传失败：{exc}") from exc
                    await asyncio.sleep(1.5 * (attempt + 1))

            # 2) 通知服务端这一片完成了
            await _post_json(
                session,
                token,
                _upload_part_finish_url(scope, target),
                {
                    "upload_id": upload_id,
                    "part_index": index,
                    "block_size": block_size,
                    "md5": md5,
                },
            )

    await asyncio.gather(*(do_part(p) for p in parts))

    # 3) 合并
    merged = await _post_json(
        session,
        token,
        _files_url(scope, target),
        {"upload_id": upload_id, "file_type": file_type, "srv_send_msg": False},
    )
    info = merged.get("file_info")
    if not info:
        raise MediaError(f"合并后没有 file_info：{str(merged)[:200]}")
    log.info("分片上传完成：%s（ttl=%s）", name, merged.get("ttl"))
    return UploadResult(
        file_info=info,
        file_uuid=merged.get("file_uuid") or "",
        ttl=int(merged.get("ttl") or 0),
        raw_url=merged.get("raw_url") or "",
        extra=merged,
    )


async def upload(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    source: str,
    kind: str = "image",
    file_name: str = "",
) -> UploadResult:
    """统一上传入口：按来源和大小自动选路子。

    - http(s) URL → URL 上传
    - 本地文件 → ≤5MB 二进制直传；>5MB 分片上传
    """
    if source.startswith(("http://", "https://")):
        return await upload_by_url(
            session, token, scope, target, source,
            file_type_for(kind, file_name or source),
            file_name,
        )

    p = Path(source)
    if not p.is_file():
        raise MediaError(f"文件不存在：{source}")
    size = p.stat().st_size
    ftype = file_type_for(kind, file_name or p.name)
    if size <= MAX_BINARY_UPLOAD:
        return await upload_by_bytes(
            session, token, scope, target, p.read_bytes(), ftype, file_name or p.name
        )
    return await upload_chunked(
        session, token, scope, target, p, ftype, file_name or p.name
    )


# ---------------------------------------------------------------- 自动发图

# 整行就是一张图的 URL（严格一点，避免把正文里的链接误当成图）
_IMAGE_URL_LINE = re.compile(
    r"^\s*(https?://[^\s<>\"']+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<>\"']*)?)\s*$",
    re.IGNORECASE,
)


def host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    """域名白名单检查（防 SSRF）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def extract_image_urls(text: str, domains: tuple[str, ...]) -> list[str]:
    """从一段文本里找出「整行就是图片 URL」的行。

    为什么要求**整行**：模型回复里经常提到网址（比如「详见 https://...」），
    那些不能当成图发出去 —— 会变成一堆垃圾图。只认「单独一行、就是图片地址」的情况。

    还要求域名在白名单里：机器人不能去下任意地址（SSRF）。
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _IMAGE_URL_LINE.match(line)
        if not m:
            continue
        url = m.group(1)
        if not host_allowed(url, domains):
            continue
        if url not in out:
            out.append(url)
    return out


def strip_image_lines(text: str, domains: tuple[str, ...]) -> str:
    """把「整行就是图片 URL」的行从文本里删掉。

    为什么要删：图已经作为富媒体发出去了，文本里再留一行 URL，
    QQ 会把它渲染成链接预览/小程序卡片，变成重复内容。
    """
    if not text:
        return text
    keep: list[str] = []
    for line in text.splitlines():
        m = _IMAGE_URL_LINE.match(line)
        if m and host_allowed(m.group(1), domains):
            continue
        keep.append(line)
    out = "\n".join(keep).strip()
    return out or text


# ---------------------------------------------------------------- 发送


async def send_media(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    file_info: str,
    msg_id: str = "",
    msg_seq: int = 1,
    content: str = "",
    msg_ref_id: str = "",
) -> int:
    """发一条富媒体消息（msg_type=7）。返回 HTTP 状态码。

    带 msg_id 是被动回复（额度更宽，5 分钟内有效）；不带是主动消息（频控更严）。
    """
    payload: dict = {
        "msg_type": 7,
        "media": {"file_info": file_info},
        "msg_seq": msg_seq,
    }
    if msg_id:
        payload["msg_id"] = msg_id
    if content:
        payload["content"] = content
    if msg_ref_id:
        payload["message_reference"] = {"message_id": msg_ref_id}

    try:
        async with session.post(
            _messages_url(scope, target),
            json=payload,
            headers=_auth(token),
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            status = resp.status
            body = await resp.text()
    except aiohttp.ClientError as exc:
        raise MediaError(f"发送富媒体失败：{exc}") from exc

    if status >= 400:
        raise MediaError(f"发送富媒体失败 HTTP {status}：{body[:200]}")
    log.info("已发送富媒体（HTTP %s，%s）", status, scope)
    return status


async def send_media_file(
    session: aiohttp.ClientSession,
    token: str,
    scope: str,
    target: str,
    source: str,
    kind: str = "image",
    file_name: str = "",
    msg_id: str = "",
    msg_seq: int = 1,
    content: str = "",
    msg_ref_id: str = "",
) -> int:
    """一步到位：上传 + 发送。返回 HTTP 状态码。

    注意 file_info 有 ttl，所以**每次都要现传现发**，不能缓存复用。
    """
    result = await upload(session, token, scope, target, source, kind, file_name)
    return await send_media(
        session, token, scope, target, result.file_info,
        msg_id=msg_id, msg_seq=msg_seq, content=content, msg_ref_id=msg_ref_id,
    )
