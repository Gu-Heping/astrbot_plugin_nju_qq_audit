from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import redact_tokens_in_string
from config import PluginSettings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    def __init__(self, path: Path, settings: PluginSettings) -> None:
        self.path = path
        self.settings = settings
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def append(self, record: dict[str, Any]) -> None:
        safe = self._sanitize_record(record)
        line = json.dumps(safe, ensure_ascii=False) + "\n"
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(line)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
            except json.JSONDecodeError:
                continue
        return records

    def _sanitize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        safe = dict(record)
        safe["time"] = safe.get("time") or utc_now_iso()
        if "flag" in safe:
            safe.pop("flag", None)
            safe["flag_present"] = bool(record.get("flag"))
        for key in list(safe.keys()):
            if key in {"raw_event", "sanitized_raw"}:
                safe.pop(key, None)
        if "message" in safe and isinstance(safe["message"], str):
            safe["message"] = redact_tokens_in_string(safe["message"], self.settings)
        if "error" in safe and isinstance(safe["error"], str):
            safe["error"] = redact_tokens_in_string(safe["error"], self.settings)
        return safe
