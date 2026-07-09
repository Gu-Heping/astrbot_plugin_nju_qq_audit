from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> dict[str, Any]:
        return {"version": 1}

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

    def get_mode_override(self) -> str | None:
        data = self.load()
        mode = data.get("mode")
        if isinstance(mode, str) and mode:
            return mode
        return None

    async def set_mode(self, mode: str, updated_by: str) -> None:
        async with self._lock:
            data = self.load()
            data.update(
                {
                    "version": 1,
                    "mode": mode,
                    "updated_at": utc_now_iso(),
                    "updated_by": updated_by,
                }
            )
            self._write(data)

    async def clear_mode(self) -> None:
        async with self._lock:
            data = self._empty()
            if self.path.exists():
                self.path.unlink(missing_ok=True)
            else:
                self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
