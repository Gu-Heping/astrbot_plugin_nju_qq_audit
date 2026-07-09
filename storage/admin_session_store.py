from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdminSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "sessions": {}}

    def load(self) -> dict[str, Any]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty()

    def get_umo(self, admin_qq: str) -> str | None:
        sessions = self.load().get("sessions", {})
        entry = sessions.get(admin_qq)
        if isinstance(entry, dict):
            umo = entry.get("umo")
            if isinstance(umo, str) and umo:
                return umo
        return None

    async def record(self, admin_qq: str, umo: str) -> None:
        if not admin_qq or not umo:
            return
        async with self._lock:
            data = self.load()
            sessions = data.setdefault("sessions", {})
            sessions[admin_qq] = {"umo": umo, "updated_at": utc_now_iso()}
            self._write(data)

    def stats(self, admin_qq_ids: frozenset[str]) -> dict[str, int]:
        sessions = self.load().get("sessions", {})
        cached = sum(1 for admin_id in admin_qq_ids if admin_id in sessions)
        return {"cached": cached, "total": len(admin_qq_ids)}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
