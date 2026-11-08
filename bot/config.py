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
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
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
        # 群里不 @ 机器人也能用的命令（公开命令）
        group_commands=tuple(
            c.strip().lower()
            for c in os.getenv("GROUP_COMMANDS", "help,time,choose,echo,whoami").split(",")
            if c.strip()
        ),
    )
