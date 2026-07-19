from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_source.students import ActionResult, PendingRequest

REQUESTS_VERSION = 3


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_request_id() -> str:
    return f"REQ-{uuid.uuid4().hex[:12]}"


class RequestsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._write(self._empty())

    def _empty(self) -> dict[str, Any]:
        return {
            "version": REQUESTS_VERSION,
            "by_id": {},
            "by_flag": {},
            "seen_fingerprints": {},
            "membership_by_user_group": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return self._empty()
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if isinstance(parsed, dict) and parsed.get("version") == REQUESTS_VERSION:
            parsed.setdefault("by_id", {})
            parsed.setdefault("by_flag", {})
            parsed.setdefault("seen_fingerprints", {})
            parsed.setdefault("membership_by_user_group", {})
            return parsed
        if isinstance(parsed, dict) and parsed.get("version") == 2:
            parsed["version"] = REQUESTS_VERSION
            parsed.setdefault("seen_fingerprints", {})
            return parsed
        if isinstance(parsed, dict):
            return self._migrate_v1(parsed)
        return self._empty()

    def _migrate_v1(self, v1: dict[str, Any]) -> dict[str, Any]:
        store = self._empty()
        for flag, req in v1.items():
            if not isinstance(req, dict):
                continue
            req_id = str(req.get("id") or new_request_id())
            pending = self._dict_to_request(req_id, req, flag)
            store["by_id"][req_id] = self._request_to_dict(pending)
            store["by_flag"][flag] = req_id
        return store

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _dict_to_request(req_id: str, data: dict[str, Any], flag: str) -> PendingRequest:
        action_result = data.get("action_result")
        ar = None
        if isinstance(action_result, dict):
            ar = ActionResult(
                ok=bool(action_result.get("ok")),
                retcode=action_result.get("retcode"),
                message=action_result.get("message"),
            )
        last_action_result = data.get("last_action_result")
        lar = None
        if isinstance(last_action_result, dict):
            lar = ActionResult(
                ok=bool(last_action_result.get("ok")),
                retcode=last_action_result.get("retcode"),
                message=last_action_result.get("message"),
            )
        return PendingRequest(
            id=req_id,
            group_id=str(data.get("group_id", "")),
            user_id=str(data.get("user_id", "")),
            comment=str(data.get("comment", "")),
            flag=str(data.get("flag", flag)),
            sub_type=str(data.get("sub_type", "add")),
            parsed=data.get("parsed") or {},
            match=data.get("match") or {},
            decision=data.get("decision", "manual_review"),
            confidence=float(data.get("confidence", 0)),
            reason=str(data.get("reason", "")),
            mode=str(data.get("mode", "record-only")),
            status=data.get("status", "pending"),
            created_at=str(data.get("created_at", utc_now_iso())),
            processed_at=data.get("processed_at"),
            action_result=ar,
            last_action_result=lar,
            last_action_at=data.get("last_action_at"),
            retry_count=int(data.get("retry_count") or 0),
            admin_override=bool(data.get("admin_override", False)),
            admin_user_id=str(data.get("admin_user_id")) if data.get("admin_user_id") else None,
            admin_command=data.get("admin_command"),
            match_strength=data.get("match_strength", "none"),
            matched_student_key=data.get("matched_student_key"),
            updated_at=data.get("updated_at"),
            comment_revision=int(data.get("comment_revision") or 0),
            previous_comments=[
                str(c) for c in (data.get("previous_comments") or []) if c
            ][-5:],
            reapply_of=str(data["reapply_of"]) if data.get("reapply_of") else None,
            attempt_no=int(data.get("attempt_no") or 1),
            received_event_time=data.get("received_event_time"),
            event_fingerprint=data.get("event_fingerprint"),
            dismissed_at=data.get("dismissed_at"),
            dismissed_by=str(data["dismissed_by"]) if data.get("dismissed_by") else None,
            dismiss_reason=str(data["dismiss_reason"]) if data.get("dismiss_reason") else None,
            profile=str(data.get("profile") or "undergraduate"),
        )

    @staticmethod
    def _request_to_dict(req: PendingRequest) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": req.id,
            "group_id": req.group_id,
            "user_id": req.user_id,
            "comment": req.comment,
            "flag": req.flag,
            "sub_type": req.sub_type,
            "parsed": req.parsed,
            "match": req.match,
            "decision": req.decision,
            "confidence": req.confidence,
            "reason": req.reason,
            "mode": req.mode,
            "status": req.status,
            "created_at": req.created_at,
            "processed_at": req.processed_at,
            "retry_count": req.retry_count,
            "last_action_at": req.last_action_at,
            "admin_override": req.admin_override,
            "admin_user_id": req.admin_user_id,
            "admin_command": req.admin_command,
            "match_strength": req.match_strength,
            "matched_student_key": req.matched_student_key,
            "updated_at": req.updated_at,
            "comment_revision": req.comment_revision,
            "previous_comments": list(req.previous_comments)[-5:],
            "reapply_of": req.reapply_of,
            "attempt_no": req.attempt_no,
            "received_event_time": req.received_event_time,
            "event_fingerprint": req.event_fingerprint,
            "dismissed_at": req.dismissed_at,
            "dismissed_by": req.dismissed_by,
            "dismiss_reason": req.dismiss_reason,
            "profile": getattr(req, "profile", None) or "undergraduate",
        }
        if req.action_result:
            data["action_result"] = {
                "ok": req.action_result.ok,
                "retcode": req.action_result.retcode,
                "message": req.action_result.message,
            }
        if req.last_action_result:
            data["last_action_result"] = {
                "ok": req.last_action_result.ok,
                "retcode": req.last_action_result.retcode,
                "message": req.last_action_result.message,
            }
        return data

    def _to_request(self, data: dict[str, Any]) -> PendingRequest:
        return self._dict_to_request(str(data["id"]), data, str(data.get("flag", "")))

    @staticmethod
    def membership_key(group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    async def get_membership_state(self, group_id: str, user_id: str) -> dict[str, Any]:
        async with self._lock:
            store = self._read_unlocked()
            key = self.membership_key(group_id, user_id)
            data = store.get("membership_by_user_group", {}).get(key)
            return dict(data) if isinstance(data, dict) else {}

    async def update_membership_state(
        self, group_id: str, user_id: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._lock:
            store = self._read_unlocked()
            membership = store.setdefault("membership_by_user_group", {})
            key = self.membership_key(group_id, user_id)
            current = dict(membership.get(key, {}))
            current.update(update)
            membership[key] = current
            self._write(store)
            return current

    async def get_by_id(self, req_id: str) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            data = store["by_id"].get(req_id)
            return self._to_request(data) if data else None

    async def get_by_flag(self, flag: str) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            req_id = store["by_flag"].get(flag)
            if not req_id:
                return None
            data = store["by_id"].get(req_id)
            return self._to_request(data) if data else None

    async def release_flag(self, flag: str) -> str | None:
        """解除 flag 索引绑定，便于同 flag 退群后重新申请。"""
        async with self._lock:
            store = self._read_unlocked()
            req_id = store["by_flag"].pop(flag, None)
            self._write(store)
            return str(req_id) if req_id else None

    async def find_active_pending_by_user_group(
        self, group_id: str, user_id: str
    ) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            for data in store["by_id"].values():
                if (
                    str(data.get("group_id")) == group_id
                    and str(data.get("user_id")) == user_id
                    and data.get("status") == "pending"
                    and not data.get("processed_at")
                ):
                    return self._to_request(data)
            return None

    async def ensure_retryable(self, req_id: str) -> PendingRequest | None:
        """将旧版 status=failed 迁移为可重试的 pending。"""
        req = await self.get_by_id(req_id)
        if req is None:
            return None
        if req.status != "failed":
            return req
        return await self.update_by_id(
            req_id,
            {
                "status": "pending",
                "processed_at": None,
            },
        )

    async def list_retryable_failures(self, limit: int = 20) -> list[PendingRequest]:
        items: list[PendingRequest] = []
        for req in await self.list_all():
            if req.status == "failed":
                items.append(req)
                continue
            if req.status == "pending" and req.last_action_result and not req.last_action_result.ok:
                items.append(req)
        items.sort(key=lambda r: r.last_action_at or r.created_at, reverse=True)
        return items[:limit]

    async def get_fingerprint_request_id(self, fingerprint: str) -> str | None:
        async with self._lock:
            store = self._read_unlocked()
            req_id = store.get("seen_fingerprints", {}).get(fingerprint)
            return str(req_id) if req_id else None

    async def has_fingerprint(self, fingerprint: str) -> bool:
        async with self._lock:
            store = self._read_unlocked()
            return fingerprint in store.get("seen_fingerprints", {})

    async def register_fingerprint(self, fingerprint: str, req_id: str) -> None:
        async with self._lock:
            store = self._read_unlocked()
            store.setdefault("seen_fingerprints", {})[fingerprint] = req_id
            self._write(store)

    async def insert_attempt(self, req: PendingRequest) -> None:
        """写入新 attempt：保留旧 by_id 记录，by_flag 指向最新。"""
        async with self._lock:
            store = self._read_unlocked()
            store["by_id"][req.id] = self._request_to_dict(req)
            store["by_flag"][req.flag] = req.id
            if req.event_fingerprint:
                store.setdefault("seen_fingerprints", {})[req.event_fingerprint] = req.id
            self._write(store)

    async def upsert(self, req: PendingRequest) -> None:
        await self.insert_attempt(req)

    async def update_by_id(self, req_id: str, update: dict[str, Any]) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            data = store["by_id"].get(req_id)
            if not data:
                return None
            data.update(update)
            store["by_id"][req_id] = data
            self._write(store)
            return self._to_request(data)

    async def refresh_flag_by_id(self, req_id: str, new_flag: str) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            data = store["by_id"].get(req_id)
            if not data:
                return None
            old_flag = str(data.get("flag") or "")
            if old_flag and store["by_flag"].get(old_flag) == req_id:
                store["by_flag"].pop(old_flag, None)
            data["flag"] = new_flag
            store["by_id"][req_id] = data
            store["by_flag"][new_flag] = req_id
            self._write(store)
            return self._to_request(data)

    async def update_by_flag(self, flag: str, update: dict[str, Any]) -> PendingRequest | None:
        async with self._lock:
            store = self._read_unlocked()
            req_id = store["by_flag"].get(flag)
            if not req_id:
                return None
            data = store["by_id"].get(req_id)
            if not data:
                return None
            data.update(update)
            store["by_id"][req_id] = data
            self._write(store)
            return self._to_request(data)

    async def supersede_pending(self, flag: str, superseded_by_flag: str) -> None:
        await self.update_by_flag(
            flag,
            {
                "status": "ignored",
                "processed_at": utc_now_iso(),
                "action_result": {
                    "ok": False,
                    "message": f"superseded by new application ({superseded_by_flag})",
                },
            },
        )

    async def list_stale(self, limit: int = 20) -> list[PendingRequest]:
        async with self._lock:
            store = self._read_unlocked()
            items = [
                self._to_request(data)
                for data in store["by_id"].values()
                if data.get("status") == "stale"
            ]
        items.sort(key=lambda r: r.last_action_at or r.created_at, reverse=True)
        return items[:limit]

    async def list_pending(self, limit: int = 10) -> list[PendingRequest]:
        async with self._lock:
            store = self._read_unlocked()
            items = [
                self._to_request(data)
                for data in store["by_id"].values()
                if data.get("status") == "pending"
            ]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[:limit]

    async def list_all(self) -> list[PendingRequest]:
        async with self._lock:
            store = self._read_unlocked()
            return [self._to_request(data) for data in store["by_id"].values()]

    @staticmethod
    def _parse_created_at(iso_text: str) -> datetime | None:
        try:
            return datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        except ValueError:
            return None

    async def list_since(self, days: int = 7) -> list[PendingRequest]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        records = await self.list_all()
        result: list[PendingRequest] = []
        for req in records:
            created = self._parse_created_at(req.created_at)
            if created is None:
                continue
            if created >= cutoff:
                result.append(req)
        result.sort(key=lambda r: r.created_at, reverse=True)
        return result

    async def list_unknown_samples(self, days: int = 7, limit: int = 5) -> list[PendingRequest]:
        limit = max(1, min(int(limit), 30))
        records = await self.list_since(days)
        samples = [
            r
            for r in records
            if r.decision == "manual_review" or (r.parsed and not any(r.parsed.values()))
        ]
        return samples[:limit]

    async def count_today(self) -> int:
        today = datetime.now(timezone.utc).date()
        count = 0
        for req in await self.list_all():
            created = self._parse_created_at(req.created_at)
            if created and created.date() == today:
                count += 1
        return count

    async def count_processed(self) -> int:
        return sum(1 for r in await self.list_all() if r.status == "processed")

    async def clear_all(self) -> None:
        async with self._lock:
            self._write(self._empty())

    async def resolve_by_id_or_prefix(self, req_id: str) -> PendingRequest | None:
        exact = await self.get_by_id(req_id)
        if exact:
            return exact
        matches = [r for r in await self.list_all() if r.id.startswith(req_id)]
        if len(matches) == 1:
            return matches[0]
        return None

    async def get_stats(self) -> dict[str, int]:
        records = await self.list_all()
        stats = {
            "total": len(records),
            "pending": 0,
            "approve": 0,
            "manual_review": 0,
            "reject": 0,
            "ignored": 0,
            "auto_approved": 0,
            "admin_approved": 0,
            "failed": 0,
            "external": 0,
            "stale": 0,
            "dismissed": 0,
        }
        for req in records:
            if req.status == "pending":
                stats["pending"] += 1
            stats[req.decision] = stats.get(req.decision, 0) + 1
            if req.action_result and req.action_result.ok and req.decision == "approve":
                if req.admin_override:
                    stats["admin_approved"] += 1
                elif req.mode == "auto":
                    stats["auto_approved"] += 1
            if req.status == "failed":
                stats["failed"] += 1
            if req.status == "external":
                stats["external"] += 1
            if req.status == "stale":
                stats["stale"] += 1
            if req.status == "dismissed":
                stats["dismissed"] += 1
        return stats
