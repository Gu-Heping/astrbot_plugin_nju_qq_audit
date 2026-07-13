from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_ITEMS = 50
TTL_MINUTES = 30


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(minutes=TTL_MINUTES)).isoformat()


class AdminListCacheStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "admins": {}}

    def load(self) -> dict[str, Any]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("admins", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty()

    def _get_admin_entry(self, admin_id: str) -> dict[str, Any] | None:
        entry = self.load()["admins"].get(admin_id)
        if not isinstance(entry, dict):
            return None
        expires_at = entry.get("expires_at")
        if isinstance(expires_at, str):
            try:
                if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                    return None
            except ValueError:
                return None
        items = entry.get("items")
        if not isinstance(items, dict):
            return None
        return entry

    def is_expired(self, admin_id: str) -> bool:
        return self._get_admin_entry(admin_id) is None

    async def refresh(self, admin_id: str, request_ids: list[str]) -> dict[int, str]:
        async with self._lock:
            data = self.load()
            admins = data.setdefault("admins", {})
            items: dict[str, str] = {}
            for idx, req_id in enumerate(request_ids[:MAX_ITEMS], start=1):
                items[str(idx)] = req_id
            now = utc_now_iso()
            admins[admin_id] = {
                "updated_at": now,
                "expires_at": _expires_at_iso(),
                "items": items,
            }
            self._write(data)
            return {int(k): v for k, v in items.items()}

    async def append(self, admin_id: str, request_id: str) -> int | None:
        if not request_id:
            return None
        async with self._lock:
            data = self.load()
            admins = data.setdefault("admins", {})
            entry = admins.get(admin_id)
            now_dt = datetime.now(timezone.utc)
            expired = True
            items: dict[str, str] = {}
            if isinstance(entry, dict):
                expires_at = entry.get("expires_at")
                if isinstance(expires_at, str):
                    try:
                        expired = datetime.fromisoformat(expires_at) < now_dt
                    except ValueError:
                        expired = True
                raw_items = entry.get("items")
                if not expired and isinstance(raw_items, dict):
                    items = {str(k): str(v) for k, v in raw_items.items()}

            if request_id in items.values():
                for key, value in items.items():
                    if value == request_id:
                        return int(key)

            next_index = max((int(k) for k in items.keys()), default=0) + 1
            if next_index > MAX_ITEMS:
                sorted_keys = sorted(int(k) for k in items.keys())
                while len(items) >= MAX_ITEMS and sorted_keys:
                    del items[str(sorted_keys.pop(0))]
                next_index = max((int(k) for k in items.keys()), default=0) + 1

            items[str(next_index)] = request_id
            admins[admin_id] = {
                "updated_at": utc_now_iso(),
                "expires_at": _expires_at_iso(now_dt),
                "items": items,
            }
            self._write(data)
            return next_index

    def resolve(self, admin_id: str, index: int) -> str | None:
        entry = self._get_admin_entry(admin_id)
        if not entry:
            return None
        items = entry.get("items", {})
        value = items.get(str(index))
        return str(value) if value else None

    def find_index(self, admin_id: str, request_id: str) -> int | None:
        entry = self._get_admin_entry(admin_id)
        if not entry:
            return None
        for key, value in entry.get("items", {}).items():
            if value == request_id:
                return int(key)
        return None

    async def remove_request_everywhere(self, request_id: str) -> None:
        if not request_id:
            return
        async with self._lock:
            data = self.load()
            admins = data.setdefault("admins", {})
            changed = False
            for admin_id, entry in list(admins.items()):
                if not isinstance(entry, dict):
                    continue
                items = entry.get("items")
                if not isinstance(items, dict):
                    continue
                new_items = {k: v for k, v in items.items() if v != request_id}
                if new_items != items:
                    entry["items"] = new_items
                    admins[admin_id] = entry
                    changed = True
            if changed:
                self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
