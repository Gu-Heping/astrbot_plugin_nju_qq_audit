"""Undergraduate overflow routing policy."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.release import ReleaseService, list_releasable
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_2601 = "2601"
GROUP_2602 = "2602"
USER_ID = "54321"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _overflow_settings(**overrides):
    base = {
        "target_group_ids": f"{GROUP_2601},{GROUP_2602}",
        "mode": "record-only",
        "admin_notify": False,
        "student_source": "mock",
        "undergrad_overflow_enabled": True,
        "undergrad_overflow_source_group_id": GROUP_2601,
        "undergrad_overflow_redirect_group_id": GROUP_2602,
        "undergrad_overflow_threshold": 1950,
        "batch_approve_interval_ms": 0,
        "batch_approve_max_count": 10,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _pipeline(tmp_path, settings, actions=None):
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261880002",
                name="吴九九",
                student_id="261880002",
                notice_no="20260002",
                major="计算机类",
                status="已确认",
                updated_at="2026-07-22T00:00:00+00:00",
            )
        ]
    )
    if actions is None:
        actions = MagicMock()
        actions.get_group_member_info = AsyncMock(
            return_value=ActionResult(ok=False, message="not found")
        )
        actions.get_group_info = AsyncMock(
            return_value=ActionResult(ok=True, data={"member_count": 1000})
        )
        actions.set_group_add_request = AsyncMock(
            return_value=ActionResult(ok=True, retcode=0, message="ok")
        )
    notifier = MagicMock()
    notifier.notify_manual_review = AsyncMock()
    notifier.notify_policy_reject_result = AsyncMock()
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        notifier,
    )
    return pipe, requests, audit, actions


def _strong_pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id="REQ-overflow-1",
        group_id=GROUP_2601,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-1",
        sub_type="add",
        profile="undergraduate",
        parsed={"name": "吴九九", "student_id": "261880002"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.95,
        reason="strong",
        mode="record-only",
        status="pending",
        created_at="2026-07-27T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


@pytest.mark.asyncio
async def test_overflow_skips_admin_user(tmp_path):
    settings = _overflow_settings(admin_qq_ids=USER_ID, mode="record-only")
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"member_count": 2000})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_2601,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-admin",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert not pending.parsed.get("_undergrad_overflow_hit")
    assert pending.decision == "approve"
    actions.set_group_add_request.assert_not_awaited()
    assert not any(
        r.get("type") == "undergrad_overflow_policy_hit" for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_overflow_at_threshold_auto_rejects_with_redirect(tmp_path):
    settings = _overflow_settings()
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(
            ok=True,
            data={"member_count": 1950, "max_member_count": 2000},
        )
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_2601,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-new",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "reject"
    assert GROUP_2602 in pending.reason
    actions.set_group_add_request.assert_awaited()
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert GROUP_2602 in call.args[3]
    assert await list_releasable(requests, settings) == []
    assert any(
        r.get("type") == "undergrad_overflow_reject_rejected"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_overflow_below_threshold_keeps_strong_approve(tmp_path):
    settings = _overflow_settings(mode="auto")
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"member_count": 1949})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, _, actions = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_2601,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-below",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "approve"
    assert pending.match_strength == "strong"
    actions.set_group_add_request.assert_awaited()


@pytest.mark.asyncio
async def test_overflow_query_failure_fail_open(tmp_path):
    settings = _overflow_settings()

    async def failing_info(group_id, *, no_cache=True):
        raise RuntimeError("network down")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(side_effect=failing_info)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_2601,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-fail",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "approve"
    assert pending.match_strength == "strong"
    actions.set_group_add_request.assert_not_awaited()
    assert any(
        r.get("type") == "undergrad_overflow_check_failed" for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_overflow_only_applies_to_source_group(tmp_path):
    settings = _overflow_settings()
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"member_count": 2000})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, _, actions = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_2602,
        user_id=USER_ID,
        comment="吴九九 261880002",
        flag="flag-overflow-2602",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert not pending.parsed.get("_undergrad_overflow_hit")
    assert pending.decision == "approve"
    actions.get_group_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_preflight_overflow_block(tmp_path):
    settings = _overflow_settings()
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        return_value=ActionResult(ok=True, data=[])
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, message="not found")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"member_count": 1950})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions = _pipeline(tmp_path, settings, actions)
    await requests.upsert(_strong_pending())

    service = ReleaseService()
    result = await service.run_batch(
        requests_store=requests,
        pipeline=pipe,
        settings=settings,
        admin_user_id="admin1",
        count=1,
        audit_log=audit,
        skip_rematch=True,
    )

    assert result is not None
    assert result.undergrad_overflow_blocked == 1
    actions.set_group_add_request.assert_not_awaited()
    pending = await requests.get_by_id("REQ-overflow-1")
    assert pending.decision == "manual_review"
    assert GROUP_2602 in pending.reason
    assert any(
        r.get("type") == "batch_preflight_undergrad_overflow_blocked"
        for r in audit.read_all()
    )
