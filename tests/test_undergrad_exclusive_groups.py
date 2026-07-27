"""Undergraduate exclusive group membership guard."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.release import (
    ReleaseService,
    list_releasable,
    rematch_and_list_releasable,
)
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.blacklist_store import BlacklistStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_A = "1001"
GROUP_B = "1002"
USER_ID = "12345"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _exclusive_settings(**overrides):
    base = {
        "target_group_ids": f"{GROUP_A},{GROUP_B}",
        "mode": "record-only",
        "admin_notify": False,
        "student_source": "mock",
        "undergrad_exclusive_groups_enabled": True,
        "undergrad_exclusive_reject_reason": "不可加入多个群",
        "batch_approve_interval_ms": 0,
        "batch_approve_max_count": 10,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _member_map(membership: dict[tuple[str, str], bool]):
    async def get_group_member_info(group_id, user_id, *, no_cache=True):
        if membership.get((group_id, user_id)):
            return ActionResult(
                ok=True,
                data={"user_id": user_id, "nickname": f"u{user_id}", "role": "member", "join_time": 123456},
            )
        return ActionResult(ok=False, message="not found")

    return get_group_member_info


def _pipeline(tmp_path, settings, actions=None, *, blacklist=None):
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261880001",
                name="周七七",
                student_id="261880001",
                notice_no="20260001",
                major="计算机类",
                status="已确认",
                updated_at="2026-07-22T00:00:00+00:00",
            )
        ]
    )
    if actions is None:
        actions = MagicMock()
        actions.get_group_member_info = AsyncMock(side_effect=_member_map({}))
        actions.set_group_add_request = AsyncMock(
            return_value=ActionResult(ok=True, retcode=0, message="ok")
        )
    notifier = MagicMock()
    notifier.notify_manual_review = AsyncMock()
    notifier.notify_auto_result = AsyncMock()
    notifier.notify_policy_reject_result = AsyncMock()
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        notifier,
        blacklist_store=blacklist,
    )
    return pipe, requests, audit, actions, notifier


def _strong_pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id="REQ-excl-1",
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-excl-1",
        sub_type="add",
        profile="undergraduate",
        parsed={"name": "周七七", "student_id": "261880001"},
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
async def test_hit_other_undergrad_group_downgrades_to_manual_review(tmp_path):
    settings = _exclusive_settings()
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-new-1",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "manual_review"
    assert pending.match_strength == "strong"
    assert "已在本科新生群" in pending.reason
    assert pending.parsed.get("_undergrad_exclusive_hit") is True
    actions.set_group_add_request.assert_not_awaited()

    releasable = await list_releasable(requests, settings)
    assert all(r.id != req_id for r in releasable)
    assert any(
        r.get("type") == "undergrad_exclusive_manual_review" for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_not_in_other_groups_keeps_strong_approve(tmp_path):
    settings = _exclusive_settings(mode="auto")
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=_member_map({}))
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, _, actions, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-new-2",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "approve"
    assert pending.match_strength == "strong"
    actions.set_group_add_request.assert_awaited()


@pytest.mark.asyncio
async def test_exclusive_not_found_is_not_partial_failure(tmp_path):
    settings = _exclusive_settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A and user_id == USER_ID:
            return ActionResult(ok=False, message="not found")
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-not-found",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert not pending.parsed.get("_undergrad_exclusive_hit")
    assert pending.decision == "approve"
    assert pending.match_strength == "strong"
    assert not any(
        r.get("type") == "undergrad_exclusive_check_partial_failed"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_api_failure_fail_open(tmp_path):
    settings = _exclusive_settings()

    async def failing_info(group_id, user_id, *, no_cache=True):
        raise RuntimeError("network down")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=failing_info)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, _, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-new-3",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "approve"
    assert pending.match_strength == "strong"
    assert any(
        r.get("type") == "undergrad_exclusive_check_partial_failed"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_blacklist_priority_over_exclusive_guard(tmp_path):
    settings = _exclusive_settings(
        blacklist_enabled=True,
        blacklist_auto_reject=True,
    )
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    blacklist = BlacklistStore(tmp_path / "blacklist.json")
    await blacklist.add(kind="user_id", value=USER_ID, reason="测试拦截")
    pipe, requests, _, actions, _ = _pipeline(
        tmp_path, settings, actions, blacklist=blacklist
    )

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-bl-excl",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "reject"
    assert pending.reason.startswith("命中黑名单：")
    assert not pending.parsed.get("_undergrad_exclusive_hit")


@pytest.mark.asyncio
async def test_graduate_profile_not_affected(tmp_path):
    from graduate.cache import GraduateStudentCache
    from graduate.models import GraduateStudent

    settings = _exclusive_settings(
        grad_enabled=True,
        grad_target_group_ids="2001",
    )
    grad_cache = GraduateStudentCache(tmp_path / "grad_cache")
    grad_cache.save_students(
        [
            GraduateStudent(
                source_id="1",
                admission_type="博士",
                college="生命科学学院",
                major_code="071001",
                major_name="生物学",
                name="王五",
                key="王五:博士:071001",
            )
        ]
    )
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, "99999"): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
        grad_cache=grad_cache,
    )

    event = GroupJoinRequest(
        group_id="2001",
        user_id="99999",
        comment="王五 生物学 博士",
        flag="flag-grad-1",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event, profile="graduate")
    pending = await requests.get_by_id(req_id)

    assert pending.profile == "graduate"
    assert not pending.parsed.get("_undergrad_exclusive_hit")


@pytest.mark.asyncio
async def test_rematch_blocks_release_when_user_joined_other_group(tmp_path):
    settings = _exclusive_settings()
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    pipe, requests, _, _, _ = _pipeline(tmp_path, settings, actions)

    await requests.upsert(_strong_pending())
    assert await list_releasable(requests, settings)

    await pipe.rematch_active_pending(source="test_exclusive")
    pending = await requests.get_by_id("REQ-excl-1")

    assert pending.decision == "manual_review"
    assert "已在本科新生群" in pending.reason
    assert await list_releasable(requests, settings) == []


@pytest.mark.asyncio
async def test_release_preflight_blocks_undergrad_exclusive(tmp_path):
    settings = _exclusive_settings()
    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(
        return_value=ActionResult(ok=True, data=[])
    )
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions, _ = _pipeline(tmp_path, settings, actions)
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
    assert result.undergrad_exclusive_blocked == 1
    actions.set_group_add_request.assert_not_awaited()
    pending = await requests.get_by_id("REQ-excl-1")
    assert pending.decision == "manual_review"
    assert any(
        r.get("type") == "batch_preflight_undergrad_exclusive_blocked"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_current_group_membership_not_counted_as_hit(tmp_path):
    settings = _exclusive_settings()
    call_groups: list[str] = []

    async def track_info(group_id, user_id, *, no_cache=True):
        call_groups.append(group_id)
        if group_id == GROUP_A and user_id == USER_ID:
            return ActionResult(ok=True, data={"user_id": user_id, "role": "member", "join_time": 123456})
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=track_info)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, _, _, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_A,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-current-group",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert GROUP_A not in call_groups
    assert not pending.parsed.get("_undergrad_exclusive_hit")
    assert pending.decision == "approve"


@pytest.mark.asyncio
async def test_exclusive_auto_reject_calls_qq_reject(tmp_path):
    settings = _exclusive_settings(
        undergrad_exclusive_action="auto_reject",
        mode="record-only",
    )
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions, _ = _pipeline(tmp_path, settings, actions)

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-auto-reject",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "reject"
    assert pending.parsed.get("_undergrad_exclusive_action") == "auto_reject"
    actions.set_group_add_request.assert_awaited()
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert "不可加入多个群" in call.args[3]
    assert await list_releasable(requests, settings) == []
    assert any(
        r.get("type") == "undergrad_exclusive_reject_rejected"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_rematch_and_list_releasable_excludes_exclusive_hit(tmp_path):
    settings = _exclusive_settings()
    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    pipe, requests, _, _, _ = _pipeline(tmp_path, settings, actions)
    await requests.upsert(_strong_pending())

    _, items = await rematch_and_list_releasable(
        pipe, requests, settings, source="test_rematch_list"
    )
    assert items == []
