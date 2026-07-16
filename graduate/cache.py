from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from data_source.student_cache import SyncState
from graduate.models import GraduateStudent


class GraduateStudentCache:
    """Separated graduate cache files (never write undergrad students.cache.json)."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache_path = data_dir / "grad_students.cache.json"
        self.sync_state_path = data_dir / "grad_sync_state.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_students(self) -> list[GraduateStudent]:
        if not self.cache_path.exists() or self.cache_path.stat().st_size == 0:
            return []
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        students: list[GraduateStudent] = []
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                students.append(GraduateStudent.from_dict(item))
        return students

    def save_students(self, students: list[GraduateStudent]) -> None:
        payload = [s.to_dict() for s in students]
        self._atomic_write(
            self.cache_path, json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def load_sync_state(self) -> SyncState:
        if not self.sync_state_path.exists() or self.sync_state_path.stat().st_size == 0:
            return SyncState(source="grad_nju_table")
        try:
            raw = json.loads(self.sync_state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return SyncState(
                    last_sync_at=raw.get("last_sync_at"),
                    last_sync_result=raw.get("last_sync_result"),
                    row_count=int(raw.get("row_count", 0)),
                    filtered_count=int(raw.get("filtered_count", 0)),
                    raw_row_count=int(raw.get("raw_row_count", 0)),
                    mapped_count=int(raw.get("mapped_count", 0)),
                    source=str(raw.get("source", "grad_nju_table")),
                    next_sync_at=raw.get("next_sync_at"),
                    last_sync_source=raw.get("last_sync_source"),
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return SyncState(source="grad_nju_table")

    def save_sync_state(self, state: SyncState) -> None:
        self._atomic_write(
            self.sync_state_path,
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
