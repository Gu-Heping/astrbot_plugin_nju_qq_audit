"""v0.3.17 reject reapply with event fingerprint."""

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings
from core.event_fingerprint import compute_event_fingerprint, extract_event_time_iso
from core.pipeline import AuditPipeline
from data_source.mock_provider import generate_mock_students
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


PROCESSED_AT = "2026-07-13T04:00:00+00:00"


def _unix_after(iso: str, seconds: int) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp()) + seconds


def _pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="796836121",
        user_id="2492835361",
        comment="张三",
        flag="flag-1",
        sub_type="add",
        parsed={"name": "张三"},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="仅姓名，信息不足",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _event(**kwargs) -> GroupJoinRequest:
    raw = kwargs.pop("raw_event", None)
    if raw is None and "time" in kwargs:
        raw = {"time": kwargs.pop("time")}
    defaults = dict(
        group_id="796836121",
        user_id="2492835361",
        comment="张三",
        flag="flag-1",
        sub_type="add",
    )
    defaults.update(kwargs)
    return GroupJoinRequest(
        group_id=defaults["group_id"],
        user_id=defaults["user_id"],
        comment=defaults["comment"],
        flag=defaults["flag"],
        sub_type=defaults["sub_type"],
        raw_event=raw,
    )


def _pipeline(tmp_path, *, admin_notify=False, debounce_seconds=120):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "796836121",
                "admin_notify": admin_notify,
                "admin_qq_ids": "1179350197",
                "reapply_debounce_seconds": debounce_seconds,
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(generate_mock_students())
    actions = MagicMock()
    notifier = MagicMock()
    notifier.notify_manual_review = AsyncMock()
    notifier.notify_pending_comment_updated = AsyncMock()
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, notifier


def _fingerprint(event: GroupJoinRequest) -> str:
    event_time = extract_event_time_iso(event.raw_event)
    return compute_event_fingerprint(
        group_id=event.group_id,
        user_id=event.user_id,
        flag=event.flag,
        event_time=event_time,
        comment=event.comment or "",
        sub_type=event.sub_type,
    )


@pytest.mark.asyncio
async def test_reject_same_flag_new_event_time_creates_pending_and_notifies(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path, admin_notify=True)
    old = _pending(
        id="REQ-old",
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    event = _event(
        comment="入群申请测试",
        time=_unix_after(PROCESSED_AT, 3600),
    )
    await pipe.handle_group_request(event)

    latest = await requests.get_by_flag("flag-1")
    assert latest is not None
    assert latest.id != old.id
    assert latest.status == "pending"
    assert latest.reapply_of == old.id
    assert latest.attempt_no == 2
    assert latest.received_event_time
    assert latest.event_fingerprint == _fingerprint(event)

    rejected = await requests.get_by_id(old.id)
    assert rejected.status == "processed"
    assert rejected.decision == "reject"

    assert any(r.get("type") == "reapplication_created" for r in audit.read_all())
    notifier.notify_manual_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_same_flag_same_fingerprint_replayed(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path)
    old = _pending(
        id="REQ-old",
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    event = _event(time=_unix_after(PROCESSED_AT, 3600))
    await pipe.handle_group_request(event)
    first_count = len(await requests.list_pending(limit=10))

    await pipe.handle_group_request(event)
    assert len(await requests.list_pending(limit=10)) == first_count
    replays = [r for r in audit.read_all() if r.get("type") == "duplicate_event_replayed"]
    assert len(replays) >= 1


@pytest.mark.asyncio
async def test_reject_same_flag_comment_changed_new_pending(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    old = _pending(
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    event = _event(comment="李四 261122001", raw_event=None)
    await pipe.handle_group_request(event)

    latest = await requests.get_by_flag("flag-1")
    assert latest.status == "pending"
    assert latest.comment == "李四 261122001"
    assert latest.reapply_of == old.id


@pytest.mark.asyncio
async def test_reject_same_flag_same_comment_debounce_without_event_time(tmp_path):
    pipe, requests, audit, notifier = _pipeline(
        tmp_path, admin_notify=True, debounce_seconds=3600
    )
    old = _pending(
        status="processed",
        decision="reject",
        processed_at=utc_now_iso(),
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    event = _event(comment="张三", raw_event=None)
    await pipe.handle_group_request(event)

    assert len(await requests.list_pending(limit=10)) == 0
    assert any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "reapply_debounce_same_comment"
        for r in audit.read_all()
    )
    notifier.notify_manual_review.assert_not_called()


@pytest.mark.asyncio
async def test_approve_same_flag_new_event_still_ignored(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path, admin_notify=True)
    old = _pending(
        status="processed",
        decision="approve",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="ok"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(
        _event(comment="新内容", time=_unix_after(PROCESSED_AT, 7200))
    )

    assert len(await requests.list_pending(limit=10)) == 0
    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())
    notifier.notify_manual_review.assert_not_called()


@pytest.mark.parametrize("status", ["external", "stale", "ignored"])
@pytest.mark.asyncio
async def test_other_terminal_same_flag_still_ignored(tmp_path, status):
    pipe, requests, audit, _ = _pipeline(tmp_path)
    old = _pending(
        status=status,
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message=status),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(
        _event(time=_unix_after(PROCESSED_AT, 7200))
    )

    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_reapply_preserves_old_reject_record_and_updates_by_flag(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    old = _pending(
        id="REQ-reject",
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event(time=_unix_after(PROCESSED_AT, 60)))

    assert await requests.get_by_id("REQ-reject") is not None
    latest = await requests.get_by_flag("flag-1")
    assert latest.id != "REQ-reject"
    assert latest.reapply_of == "REQ-reject"


@pytest.mark.asyncio
async def test_reject_event_time_before_processed_is_replayed(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path, admin_notify=True)
    old = _pending(
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(
        _event(
            comment="入群申请测试",
            time=_unix_after(PROCESSED_AT, -3600),
        )
    )

    assert len(await requests.list_pending(limit=10)) == 0
    assert any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "event_time_before_processed_at"
        for r in audit.read_all()
    )
    notifier.notify_manual_review.assert_not_called()


@pytest.mark.asyncio
async def test_reapply_fallback_audit_notes_no_event_time(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path, debounce_seconds=0)
    old = _pending(
        status="processed",
        decision="reject",
        processed_at="2020-01-01T00:00:00+00:00",
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event(comment="新答案", raw_event=None))

    assert any(
        r.get("type") == "reapplication_created" and r.get("fallback") == "no_event_time"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_new_flag_after_reject_keeps_existing_behavior(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path, admin_notify=True)
    old = _pending(
        flag="flag-old",
        status="processed",
        decision="reject",
        processed_at=PROCESSED_AT,
        action_result=ActionResult(ok=True, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(
        _event(flag="flag-new", comment="入群申请测试", time=_unix_after(PROCESSED_AT, 60))
    )

    new = await requests.get_by_flag("flag-new")
    assert new is not None
    assert new.status == "pending"
    assert new.reapply_of is None
    assert (await requests.get_by_id(old.id)).status == "processed"
    notifier.notify_manual_review.assert_awaited_once()


def utc_now_iso() -> str:
    from storage.audit_log import utc_now_iso as _now

    return _now()
