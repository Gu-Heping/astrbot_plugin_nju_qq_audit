from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_TTL_HOURS = 24


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class GroupDisplayCache:
    """Persistent cache of QQ group_id → group_name for admin display."""

    def __init__(self, path: Path, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.path = path
        self.ttl_hours = max(1, int(ttl_hours))
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "updated_at": None, "expires_at": None, "groups": {}}

    def load(self) -> dict[str, Any]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("groups", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty()

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_expired(self) -> bool:
        data = self.load()
        expires_at = data.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return True
        try:
            return datetime.fromisoformat(expires_at) < utc_now()
        except ValueError:
            return True

    def get_name(self, group_id: str) -> str | None:
        gid = str(group_id or "").strip()
        if not gid:
            return None
        entry = self.load().get("groups", {}).get(gid)
        if isinstance(entry, dict):
            name = entry.get("group_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        if isinstance(entry, str) and entry.strip():
            return entry.strip()
        return None

    async def replace_groups(self, groups: dict[str, str]) -> None:
        async with self._lock:
            now = utc_now()
            payload = {
                "version": 1,
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=self.ttl_hours)).isoformat(),
                "groups": {
                    str(gid): {"group_name": str(name), "updated_at": now.isoformat()}
                    for gid, name in groups.items()
                    if str(gid).strip() and str(name).strip()
                },
            }
            self._write(payload)
