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
    admin_openid: str
    data_dir: Path
    # 群复读
    repeat_min_count: int
    repeat_delay: float
    # B站识别
    bili_enabled: bool
    bili_mode: str
    bili_off_groups: tuple[str, ...]
    bili_only_groups: tuple[str, ...]

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

        admin_openid=os.getenv("ADMIN_OPENID", "").strip(),
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
    )
