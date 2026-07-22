from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLACKLIST_VERSION = 1
SUPPORTED_KINDS = frozenset(
    {"user_id", "student_id", "exam_no", "notice_no", "graduate_key"}
)
KIND_ALIASES = {
    "qq": "user_id",
    "user": "user_id",
    "userid": "user_id",
    "user_id": "user_id",
    "student": "student_id",
    "sid": "student_id",
    "student_id": "student_id",
    "学号": "student_id",
    "exam": "exam_no",
    "exam_no": "exam_no",
    "考生号": "exam_no",
    "notice": "notice_no",
    "notice_no": "notice_no",
    "通知书": "notice_no",
    "通知书编号": "notice_no",
    "grad": "graduate_key",
    "graduate": "graduate_key",
    "graduate_key": "graduate_key",
    "研究生": "graduate_key",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_blacklist_id() -> str:
    return f"BL-{uuid.uuid4().hex[:8]}"


def normalize_kind(kind: str) -> str | None:
    key = (kind or "").strip().lower()
    return KIND_ALIASES.get(key)


def normalize_value(kind: str, value: str) -> str:
    text = (value or "").strip()
    if kind in {"user_id", "student_id", "exam_no", "notice_no"}:
        text = re.sub(r"\s+", "", text)
    return text


@dataclass
class BlacklistEntry:
    id: str
    kind: str
    value: str
    reason: str
    group_id: str | None = None
    profile: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    created_by: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlacklistEntry:
        return cls(
            id=str(data.get("id") or new_blacklist_id()),
            kind=str(data.get("kind") or ""),
            value=str(data.get("value") or ""),
            reason=str(data.get("reason") or ""),
            group_id=(str(data["group_id"]) if data.get("group_id") else None),
            profile=(str(data["profile"]) if data.get("profile") else None),
            created_at=str(data.get("created_at") or utc_now_iso()),
            created_by=(str(data["created_by"]) if data.get("created_by") else None),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(frozen=True)
class BlacklistHit:
    entry_id: str
    kind: str
    value: str
    reason: str
    group_id: str | None = None
    profile: str | None = None


class BlacklistStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._write(self._empty())

    def _empty(self) -> dict[str, Any]:
        return {"version": BLACKLIST_VERSION, "entries": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return self._empty()
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(parsed, dict):
            return self._empty()
        parsed.setdefault("version", BLACKLIST_VERSION)
        parsed.setdefault("entries", {})
        if not isinstance(parsed["entries"], dict):
            parsed["entries"] = {}
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _entries(self, data: dict[str, Any]) -> dict[str, BlacklistEntry]:
        out: dict[str, BlacklistEntry] = {}
        for key, raw in (data.get("entries") or {}).items():
            if not isinstance(raw, dict):
                continue
            entry = BlacklistEntry.from_dict(raw)
            out[str(key)] = entry
        return out

    async def add(
        self,
        *,
        kind: str,
        value: str,
        reason: str,
        created_by: str | None = None,
        group_id: str | None = None,
        profile: str | None = None,
    ) -> BlacklistEntry:
        kind_norm = normalize_kind(kind)
        if kind_norm is None or kind_norm not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported blacklist kind: {kind}")
        value_norm = normalize_value(kind_norm, value)
        if not value_norm:
            raise ValueError("blacklist value is empty")
        reason_text = (reason or "").strip()
        if not reason_text:
            raise ValueError("blacklist reason is empty")

        entry = BlacklistEntry(
            id=new_blacklist_id(),
            kind=kind_norm,
            value=value_norm,
            reason=reason_text,
            group_id=(str(group_id).strip() or None) if group_id else None,
            profile=(str(profile).strip() or None) if profile else None,
            created_by=created_by,
            enabled=True,
        )
        async with self._lock:
            data = self._read_unlocked()
            data["entries"][entry.id] = entry.to_dict()
            self._write(data)
        return entry

    async def remove(self, entry_id: str) -> BlacklistEntry | None:
        async with self._lock:
            data = self._read_unlocked()
            entries = data.get("entries") or {}
            raw = entries.pop(str(entry_id), None)
            if raw is None:
                return None
            self._write(data)
            return BlacklistEntry.from_dict(raw)

    async def disable(self, entry_id: str) -> BlacklistEntry | None:
        async with self._lock:
            data = self._read_unlocked()
            raw = (data.get("entries") or {}).get(str(entry_id))
            if not isinstance(raw, dict):
                return None
            raw["enabled"] = False
            data["entries"][str(entry_id)] = raw
            self._write(data)
            return BlacklistEntry.from_dict(raw)

    async def get(self, entry_id: str) -> BlacklistEntry | None:
        async with self._lock:
            data = self._read_unlocked()
            raw = (data.get("entries") or {}).get(str(entry_id))
            if not isinstance(raw, dict):
                return None
            return BlacklistEntry.from_dict(raw)

    async def list(self, *, enabled_only: bool = True) -> list[BlacklistEntry]:
        async with self._lock:
            data = self._read_unlocked()
            entries = list(self._entries(data).values())
        if enabled_only:
            entries = [e for e in entries if e.enabled]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    def match_request(
        self,
        *,
        group_id: str,
        user_id: str,
        profile: str | None,
        parsed: dict[str, Any] | None,
        match: dict[str, Any] | None,
        enabled_only: bool = True,
    ) -> BlacklistHit | None:
        data = self._read_unlocked()
        entries = list(self._entries(data).values())
        if enabled_only:
            entries = [e for e in entries if e.enabled]
        parsed = parsed or {}
        match = match or {}
        candidates: dict[str, list[str]] = {
            "user_id": [str(user_id)] if user_id else [],
            "student_id": [],
            "exam_no": [],
            "notice_no": [],
            "graduate_key": [],
        }
        if parsed.get("student_id"):
            candidates["student_id"].append(str(parsed["student_id"]))
        if parsed.get("exam_no"):
            candidates["exam_no"].append(str(parsed["exam_no"]))
        if parsed.get("notice_no"):
            candidates["notice_no"].append(str(parsed["notice_no"]))
        for item in parsed.get("notice_no_candidates") or []:
            if item:
                candidates["notice_no"].append(str(item))
        if match.get("matched_student_key"):
            candidates["graduate_key"].append(str(match["matched_student_key"]))
        if parsed.get("matched_student_key"):
            candidates["graduate_key"].append(str(parsed["matched_student_key"]))

        profile_norm = (profile or "").strip() or None
        for entry in entries:
            if entry.group_id and str(entry.group_id) != str(group_id):
                continue
            if entry.profile and profile_norm and entry.profile != profile_norm:
                continue
            if entry.profile and not profile_norm:
                continue
            values = candidates.get(entry.kind) or []
            target = normalize_value(entry.kind, entry.value)
            for raw in values:
                if normalize_value(entry.kind, str(raw)) == target:
                    return BlacklistHit(
                        entry_id=entry.id,
                        kind=entry.kind,
                        value=entry.value,
                        reason=entry.reason,
                        group_id=entry.group_id,
                        profile=entry.profile,
                    )
        return None

    def match_user_id(
        self, user_id: str, *, group_id: str | None = None, enabled_only: bool = True
    ) -> BlacklistHit | None:
        return self.match_request(
            group_id=group_id or "",
            user_id=user_id,
            profile=None,
            parsed={},
            match={},
            enabled_only=enabled_only,
        )


class NullBlacklistStore:
    """No-op store for legacy tests that construct AuditPipeline without blacklist."""

    def match_request(self, **kwargs) -> BlacklistHit | None:
        del kwargs
        return None

    def match_user_id(self, user_id: str, **kwargs) -> BlacklistHit | None:
        del user_id, kwargs
        return None

    async def list(self, *, enabled_only: bool = True) -> list[BlacklistEntry]:
        del enabled_only
        return []


def safe_match_request(store, **kwargs) -> BlacklistHit | None:
    """Call store.match_request, ignoring unittest.mock stand-ins."""
    if store is None:
        return None
    module = type(store).__module__ or ""
    if module.startswith("unittest.mock"):
        return None
    matcher = getattr(store, "match_request", None)
    if not callable(matcher):
        return None
    hit = matcher(**kwargs)
    if hit is None:
        return None
    if isinstance(hit, BlacklistHit):
        return hit
    hit_module = type(hit).__module__ or ""
    if hit_module.startswith("unittest.mock"):
        return None
    return hit
