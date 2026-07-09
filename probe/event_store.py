"""探针事件 JSONL 持久化。"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProbeEventStore:
    def __init__(self, data_dir: Path, max_recent_events: int = 20) -> None:
        self.data_dir = data_dir
        self.max_recent_events = max(1, max_recent_events)
        self.events_path = data_dir / "probe_events.jsonl"
        self.state_path = data_dir / "probe_state.json"
        self._lock = asyncio.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_recent_events)
        self._state: dict[str, Any] = {
            "last_request_group_at": None,
            "total_recorded": 0,
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()
        self._load_recent_events()

    def _load_state(self) -> None:
        if not self.state_path.exists() or self.state_path.stat().st_size == 0:
            self._save_state_unlocked()
            return
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._state.update(
                    {
                        "last_request_group_at": loaded.get("last_request_group_at"),
                        "total_recorded": int(loaded.get("total_recorded", 0)),
                    }
                )
                return
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        self._state = {
            "last_request_group_at": None,
            "total_recorded": 0,
        }
        self._save_state_unlocked()

    def _save_state_unlocked(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_recent_events(self) -> None:
        if not self.events_path.exists() or self.events_path.stat().st_size == 0:
            return
        loaded: list[dict[str, Any]] = []
        try:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    loaded.append(item)
        except OSError:
            return
        for item in loaded[-self.max_recent_events :]:
            self._events.append(item)

    async def append(self, record: dict[str, Any]) -> None:
        async with self._lock:
            self._events.append(record)
            with self.events_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._state["total_recorded"] = int(self._state.get("total_recorded", 0)) + 1
            if (
                record.get("post_type") == "request"
                and record.get("request_type") == "group"
            ):
                self._state["last_request_group_at"] = record.get("received_at")
            self._save_state_unlocked()

    async def clear(self) -> None:
        async with self._lock:
            self._events.clear()
            self.events_path.write_text("", encoding="utf-8")
            self._state = {
                "last_request_group_at": None,
                "total_recorded": 0,
            }
            self._save_state_unlocked()

    def get_last(self) -> dict[str, Any] | None:
        if not self._events:
            return None
        return self._events[-1]

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        return list(self._events)[-n:]

    def get_state(self) -> dict[str, Any]:
        return dict(self._state)

    def count(self) -> int:
        return len(self._events)

    def update_max_recent(self, max_recent_events: int) -> None:
        self.max_recent_events = max(1, max_recent_events)
        items = list(self._events)[-self.max_recent_events :]
        self._events = deque(items, maxlen=self.max_recent_events)
