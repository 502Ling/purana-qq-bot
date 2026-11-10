"""集中配置：从 .env 读取，不散落在各处。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（本文件的上一级）
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}，请检查 .env（可照抄 .env.example）")
    return value


def _resolve_dir(value: str) -> Path:
    """把目录配置转成绝对路径（相对路径按项目根算，不按当前工作目录）。"""
    p = Path(value)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def parse_pool_aliases(raw: str) -> dict[str, str]:
    """解析图片池别名：``猫=表情包,表情=表情包``。

    放在 config 里而不是 pool.py，是为了让 .env 的解析只有一处。
    真正的别名解析（含链式追别名）在 bot/pool.py 的 PoolManager 里。
    """
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        left, right = item.split("=", 1)
        left, right = left.strip(), right.strip()
        if left and right:
            out[left.lower()] = right
    return out


@dataclass(frozen=True)
class Settings:
    # QQ 开放平台
    app_id: str
    app_secret: str
    # OpenClaw 网关
    openclaw_base: str
    openclaw_token: str
    openclaw_model: str
    # ECUST 网关
    ecust_base_url: str
    ecust_api_key: str
    ecust_model: str
    ecust_system_prompt: str
    # 其他
    admin_openids: tuple[str, ...]
    data_dir: Path
    # 群复读
    repeat_min_count: int
    repeat_delay: float
    # B站识别
    bili_enabled: bool
    bili_mode: str
    bili_off_groups: tuple[str, ...]
    bili_only_groups: tuple[str, ...]
    bili_card: bool
    bili_link: bool
    # 群里不 @ 机器人也能用的命令
    group_commands: tuple[str, ...]
    # 总闸：群里不 @ 机器人能不能发斜杠命令
    group_command_enable: bool
    # 视觉模型（看图用）；空 = 不支持看图
    vision_model: str
    # 出站图片：允许的图片来源域名（防 SSRF）
    image_domains: tuple[str, ...]
    # 本地固定图片目录（/image 命令用）
    image_dir: Path
    # 总闸：要不要开富媒体（发图）
    media_enabled: bool
    # /asimg 渲染图片时的默认标题（可留空）
    asimg_title: str
    # 渲染图片的配色主题（dark / light）
    render_theme: str
    # 渲染图片的缓存目录（按内容哈希命名，重复内容不重复渲染）
    render_dir: Path
    # Phigros 资源根目录（曲库 + 曲绘）；空 = 不开 Phigros 功能
    pgr_data_root: str
    # 图片池根目录（子目录 = 池子）；空 = 不开随机图片池
    pool_dir: Path
    # 图片池别名（别名=池子名，逗号分隔）
    pool_aliases: dict[str, str]
    # 默认池子（/img 不带参数时用这个）
    pool_default: str

    @property
    def admin_openid(self) -> str:
        """兼容旧字段：第一个管理员 openid（没设就是空串）。"""
        return self.admin_openids[0] if self.admin_openids else ""

    @property
    def token_cache(self) -> Path:
        return self.data_dir / "token_cache.json"

    def session_key(self, openid: str) -> str:
        """每个 QQ 用户一个独立会话（同一个 user 值 = 同一个会话）。"""
        return f"qq:{openid}"


def load_settings() -> Settings:
    # ⚠️ 路径一律转成绝对路径：.env 里写的是相对路径（./data），
    # 而进程的工作目录会变（dev.py / 计划任务 / 从别的目录启动），
    # 相对路径会指向不同地方 → 找不到图片、缓存写错地方。
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_id=_required("QQ_APPID"),
        app_secret=_required("QQ_SECRET"),

        openclaw_base=os.getenv("OPENCLAW_BASE", "http://localhost:18789").rstrip("/"),
        openclaw_token=os.getenv("OPENCLAW_TOKEN", "").strip(),
        openclaw_model=os.getenv("OPENCLAW_MODEL", "openclaw/default").strip(),

        ecust_base_url=os.getenv("ECUST_BASE_URL", "").rstrip("/"),
        ecust_api_key=os.getenv("ECUST_API_KEY", "").strip(),
        ecust_model=os.getenv("ECUST_MODEL", "").strip(),
        ecust_system_prompt=os.getenv("ECUST_SYSTEM_PROMPT", "").strip(),

        admin_openids=tuple(
            a.strip() for a in os.getenv("ADMIN_OPENID", "").split(",") if a.strip()
        ),
        data_dir=data_dir,

        repeat_min_count=max(2, int(os.getenv("REPEAT_MIN_COUNT", "3"))),
        repeat_delay=max(0.0, float(os.getenv("REPEAT_DELAY", "30"))),

        bili_enabled=os.getenv("BILI_ENABLE", "1").strip().lower()
        not in ("0", "false", "off", "no"),
        bili_mode=os.getenv("BILI_MODE", "black").strip() or "black",
        bili_off_groups=tuple(
            g.strip() for g in os.getenv("BILI_OFF_GROUPS", "").split(",") if g.strip()
        ),
        bili_only_groups=tuple(
            g.strip() for g in os.getenv("BILI_ONLY_GROUPS", "").split(",") if g.strip()
        ),
        # 默认发纯文本（BV号单独一行，方便复制）；置 1 才发图文卡片
        bili_card=os.getenv("BILI_CARD", "0").strip().lower()
        in ("1", "true", "on", "yes"),
        # 是否在 B 站回复里附带完整链接（默认不带：QQ 会把链接渲染成小程序预览）
        bili_link=os.getenv("BILI_LINK", "0").strip().lower()
        in ("1", "true", "on", "yes"),
        # 群里不 @ 机器人也能用的命令（总闸关了整条都无效）
        group_command_enable=os.getenv("GROUP_COMMAND_ENABLE", "1").strip().lower()
        not in ("0", "false", "off", "no"),
        group_commands=tuple(
            c.strip().lower()
            for c in os.getenv(
                "GROUP_COMMANDS", "help,time,choose,echo,whoami,pgr"
            ).split(",")
            if c.strip()
        ),

        # 看图用的视觉模型（实测 ECUST 上 glm-4.7 / qwen3.5 能看图，
        # deepseek-v4.1-flash 不行）。留空 = 不开看图功能。
        vision_model=os.getenv("VISION_MODEL", "").strip(),
        # 出站图片来源域名白名单（逗号分隔）—— 防止机器人去下任意地址
        image_domains=tuple(
            d.strip().lower()
            for d in os.getenv("IMAGE_DOMAINS", "qq.com,qpic.cn,gtimg.cn,bilibili.com,hdslb.com").split(",")
            if d.strip()
        ),
        # 固定图片目录（/image 命令从这里找图）
        image_dir=_resolve_dir(os.getenv("IMAGE_DIR", str(data_dir / "images"))),
        # 富媒体总闸：0 = 关掉发图能力
        media_enabled=os.getenv("MEDIA_ENABLE", "1").strip().lower()
        not in ("0", "false", "off", "no"),
        # /asimg 渲染出来的图片标题（留空 = 不显示标题栏）
        asimg_title=os.getenv("ASIMG_TITLE", "").strip(),
        # 渲染配色主题
        render_theme=os.getenv("RENDER_THEME", "dark").strip().lower() or "dark",
        # 渲染缓存目录（同一份内容只渲染一次）
        render_dir=_resolve_dir(os.getenv("RENDER_DIR", str(data_dir / "render_cache"))),
        # Phigros 资源根目录（曲库+曲绘，共享一份，不复制）
        # ⚠️ 代码里**不写死**机器路径 —— 真实路径放 .env，提交后别人 clone 也不带本地路径。
        pgr_data_root=os.getenv("PGR_DATA_ROOT", "").strip(),
        # 图片池根目录（子目录 = 池子，/img <池子名> 随机抽一张）
        pool_dir=_resolve_dir(os.getenv("POOL_DIR", str(data_dir / "pool"))),
        # 池子别名：猫=表情包,表情=表情包
        pool_aliases=parse_pool_aliases(os.getenv("POOL_ALIASES", "")),
        # /img 不带参数时用哪个池子（留空 = 必须带池子名）
        pool_default=os.getenv("POOL_DEFAULT", "").strip(),
    )
