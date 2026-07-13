"""Minimal vertical slice: approve → join → leave → immediate reapply."""

import sys
from datetime import datetime
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
from data_source.students import ActionResult
from onebot.event_extract import GroupJoinRequest, GroupMemberDecrease, GroupMemberIncrease
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_ID = "796836121"
USER_ID = "2492835361"
FLAG = "flag-reapply-cycle"
COMMENT = "张三20260002"
EVENT_UNIX = 1720848000  # fixed event.time for NapCat reuse simulation


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _event(**kwargs) -> GroupJoinRequest:
    raw = kwargs.pop("raw_event", None)
    if raw is None:
        time_val = kwargs.pop("time", EVENT_UNIX)
        raw = {"time": time_val}
    defaults = dict(
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment=COMMENT,
        flag=FLAG,
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


def _increase(**kwargs) -> GroupMemberIncrease:
    defaults = dict(
        group_id=GROUP_ID,
        user_id=USER_ID,
        sub_type="approve",
        operator_id="1179350197",
    )
    defaults.update(kwargs)
    return GroupMemberIncrease(**defaults)


def _decrease(**kwargs) -> GroupMemberDecrease:
    defaults = dict(
        group_id=GROUP_ID,
        user_id=USER_ID,
        sub_type="leave",
        operator_id=USER_ID,
    )
    defaults.update(kwargs)
    return GroupMemberDecrease(**defaults)


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


def _pipeline(tmp_path, *, debounce_seconds=120):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_notify": True,
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
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    notifier = MagicMock()
    notifier.notify_manual_review = AsyncMock()
    notifier.notify_pending_comment_updated = AsyncMock()
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, notifier, actions


def _attempts_for_user(store_data: dict) -> list[str]:
    return [
        rid
        for rid, data in store_data["by_id"].items()
        if str(data.get("user_id")) == USER_ID
    ]


async def _read_store(requests: RequestsStore) -> dict:
    async with requests._lock:
        return requests._read_unlocked()


@pytest.mark.asyncio
async def test_approve_join_leave_immediate_reapply_bypasses_debounce(tmp_path):
    """Full pipeline: request → approve → increase → decrease → <15s same-field reapply."""
    pipe, requests, audit, notifier, _ = _pipeline(tmp_path, debounce_seconds=120)
    first_event = _event()

    await pipe.handle_group_request(first_event)
    first = await requests.get_by_flag(FLAG)
    assert first is not None
    assert first.status == "pending"
    assert first.attempt_no == 1
    assert notifier.notify_manual_review.await_count == 1

    await pipe.admin_approve(first, "1179350197")
    first = await requests.get_by_id(first.id)
    assert first.status == "processed"
    processed_at = first.processed_at
    assert processed_at

    await pipe.handle_group_increase(_increase())
    membership = await requests.get_membership_state(GROUP_ID, USER_ID)
    assert membership.get("membership") == "joined"

    await pipe.handle_group_decrease(_decrease())
    membership = await requests.get_membership_state(GROUP_ID, USER_ID)
    assert membership.get("membership") == "left"
    assert membership.get("reapply_eligible") is True

    event_time_iso = extract_event_time_iso(first_event.raw_event)
    assert event_time_iso
    processed_dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
    event_dt = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
    assert event_dt <= processed_dt

    reapply_event = _event()
    assert _fingerprint(reapply_event) == _fingerprint(first_event)

    await pipe.handle_group_request(reapply_event)

    second = await requests.get_by_flag(FLAG)
    assert second is not None
    assert second.id != first.id
    assert second.status == "pending"
    assert second.reapply_of == first.id
    assert second.attempt_no == 2
    assert notifier.notify_manual_review.await_count == 2

    store = await _read_store(requests)
    assert len(_attempts_for_user(store)) == 2

    records = audit.read_all()
    assert not any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "reapply_burst_after_terminal"
        for r in records
    )
    assert not any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "reapply_burst_recycled_event_time"
        for r in records
    )
    assert any(r.get("type") == "reapplication_created" for r in records)
    assert any(
        r.get("type") == "reapplication_created"
        and r.get("fallback") == "strong_signal_group_decrease"
        for r in records
    )

    consumed = await requests.get_membership_state(GROUP_ID, USER_ID)
    assert consumed.get("reapply_eligible") is False


@pytest.mark.asyncio
async def test_reapply_event_replay_does_not_create_third_attempt(tmp_path):
    """Re-delivering the same reapply event must not create attempt 3 or notify again."""
    pipe, requests, audit, notifier, _ = _pipeline(tmp_path)
    first_event = _event()

    await pipe.handle_group_request(first_event)
    first = await requests.get_by_flag(FLAG)
    await pipe.admin_approve(first, "1179350197")
    await pipe.handle_group_increase(_increase())
    await pipe.handle_group_decrease(_decrease())

    reapply_event = _event()
    await pipe.handle_group_request(reapply_event)
    assert notifier.notify_manual_review.await_count == 2

    await pipe.handle_group_request(reapply_event)

    store = await _read_store(requests)
    assert len(_attempts_for_user(store)) == 2
    assert notifier.notify_manual_review.await_count == 2
    assert any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "seen_fingerprint"
        for r in audit.read_all()
    )
