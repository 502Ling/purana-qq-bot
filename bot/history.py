"""按用户保存的对话历史 —— 给裸模型（普拉娜）用，让她能记住上文。

存成 data/ecust_history.json：{ "openid": [ {role, content}, ... ] }
只保留最近 MAX_MESSAGES 条，超了就从头丢。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("purana.history")

MAX_MESSAGES = 20  # 每个用户最多保留的条数（约 10 轮对话）


class History:
    def __init__(self, path: Path, max_messages: int = MAX_MESSAGES) -> None:
        self._path = path
        self._max = max_messages
        self._data: dict[str, list[dict[str, str]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("读取历史失败，从空开始：%s", exc)
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.warning("写历史失败：%s", exc)

    def get(self, key: str) -> list[dict[str, str]]:
        """取该用户的完整历史（副本，别让调用方改到内部）。"""
        return list(self._data.get(key, []))

    def append(self, key: str, role: str, content: str) -> None:
        msgs = self._data.setdefault(key, [])
        msgs.append({"role": role, "content": content})
        if len(msgs) > self._max:
            del msgs[: len(msgs) - self._max]
        self._save()

    def clear(self, key: str) -> None:
        self._data.pop(key, None)
        self._save()
