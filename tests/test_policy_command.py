"""Runtime /audit policy command and undergraduate exclusive action override."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.formatter import format_help
from admin.policy import (
    format_policy_status,
    handle_policy_command,
    parse_policy_command,
)
from admin.release import ReleaseService, format_release_help
from config import get_effective_undergrad_exclusive_action, load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_A = "1001"
GROUP_B = "1002"
USER_ID = "12345"
SOURCE_GROUP_ID = "<SOURCE_GROUP_ID>"
REDIRECT_GROUP_ID = "<REDIRECT_GROUP_ID>"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**overrides):
    base = {
        "target_group_ids": f"{GROUP_A},{GROUP_B}",
        "mode": "record-only",
        "admin_notify": False,
        "student_source": "mock",
        "undergrad_exclusive_groups_enabled": True,
        "undergrad_exclusive_action": "manual_review",
        "undergrad_exclusive_reject_reason": "不可加入多个群",
        "undergrad_overflow_enabled": True,
        "undergrad_overflow_source_group_id": SOURCE_GROUP_ID,
        "undergrad_overflow_redirect_group_id": REDIRECT_GROUP_ID,
        "undergrad_overflow_threshold": 1950,
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
                data={"user_id": user_id, "nickname": f"u{user_id}"},
            )
        return ActionResult(ok=False, message="not found")

    return get_group_member_info


def _pipeline(tmp_path, settings, actions=None, *, runtime=None):
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = runtime or RuntimeStore(tmp_path / "runtime.json")
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
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
    )
    return pipe, requests, audit, actions, runtime


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


def test_policy_help_mentions_commands_not_config_doc():
    text = format_help(topic="policy")
    for required in (
        "/audit policy",
        "/audit policy exclusive manual confirm",
        "/audit policy exclusive auto-reject confirm",
        "release/catchup 不会批量拒绝",
    ):
        assert required in text
    for banned in (
        "undergrad_exclusive_groups_enabled",
        "undergrad_overflow_source_group_id",
        "undergrad_exclusive_action=",
        "示例配置",
    ):
        assert banned not in text


def test_policy_status_shows_effective_action(tmp_path):
    settings = _settings(undergrad_exclusive_action="manual_review")
    runtime = RuntimeStore(tmp_path / "runtime.json")

    text = format_policy_status(settings, runtime)
    assert "当前处理方式：转人工" in text
    assert "处理方式来源：插件配置" in text

    runtime.load()
    runtime.path.write_text(
        '{"version":1,"undergrad_exclusive_action_override":"auto_reject"}',
        encoding="utf-8",
    )
    text2 = format_policy_status(settings, runtime)
    assert "当前处理方式：自动拒绝" in text2
    assert "处理方式来源：运行时指令" in text2


@pytest.mark.asyncio
async def test_policy_switch_requires_confirm(tmp_path):
    settings = _settings()
    runtime = RuntimeStore(tmp_path / "runtime.json")
    command = parse_policy_command("/audit policy exclusive auto-reject")
    assert command.kind == "switch_auto_reject"
    assert command.confirmed is False

    result = await handle_policy_command(
        settings=settings,
        runtime=runtime,
        command=command,
        updated_by="admin1",
    )
    assert "确认切换为自动拒绝" in result
    assert runtime.get_undergrad_exclusive_action_override() is None


@pytest.mark.asyncio
async def test_policy_switch_to_auto_reject(tmp_path):
    settings = _settings()
    runtime = RuntimeStore(tmp_path / "runtime.json")
    command = parse_policy_command("/audit policy exclusive auto-reject confirm")

    result = await handle_policy_command(
        settings=settings,
        runtime=runtime,
        command=command,
        updated_by="admin1",
    )
    assert runtime.get_undergrad_exclusive_action_override() == "auto_reject"
    assert "自动拒绝" in result
    assert "实时申请" in result
    assert "release/catchup 不会批量拒绝" in result


@pytest.mark.asyncio
async def test_policy_switch_to_manual(tmp_path):
    settings = _settings(undergrad_exclusive_action="auto_reject")
    runtime = RuntimeStore(tmp_path / "runtime.json")
    command = parse_policy_command("/audit policy exclusive manual confirm")

    result = await handle_policy_command(
        settings=settings,
        runtime=runtime,
        command=command,
        updated_by="admin1",
    )
    assert runtime.get_undergrad_exclusive_action_override() == "manual_review"
    assert "转人工" in result
    assert "不会自动拒绝" in result


@pytest.mark.asyncio
async def test_runtime_override_affects_exclusive_realtime_path(tmp_path):
    settings = _settings(undergrad_exclusive_action="manual_review")
    runtime = RuntimeStore(tmp_path / "runtime.json")
    await runtime.set_undergrad_exclusive_action_override("auto_reject", "admin1")

    action, source = get_effective_undergrad_exclusive_action(
        settings, runtime.get_undergrad_exclusive_action_override()
    )
    assert action == "auto_reject"
    assert source == "runtime"

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, _, actions, _ = _pipeline(
        tmp_path, settings, actions, runtime=runtime
    )

    event = GroupJoinRequest(
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-runtime-auto",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)

    assert pending.decision == "reject"
    actions.set_group_add_request.assert_awaited()
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert "不可加入多个群" in call.args[3]


@pytest.mark.asyncio
async def test_release_preflight_ignores_runtime_auto_reject(tmp_path):
    settings = _settings(undergrad_exclusive_action="manual_review")
    runtime = RuntimeStore(tmp_path / "runtime.json")
    await runtime.set_undergrad_exclusive_action_override("auto_reject", "admin1")

    actions = MagicMock()
    actions.get_group_system_msg = AsyncMock(return_value=ActionResult(ok=True, data=[]))
    actions.get_group_member_info = AsyncMock(
        side_effect=_member_map({(GROUP_A, USER_ID): True})
    )
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe, requests, audit, actions, _ = _pipeline(
        tmp_path, settings, actions, runtime=runtime
    )
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


def test_release_help_points_to_policy_command():
    text = format_release_help(0, _settings())
    assert "/audit policy" in text
    assert "不会在批量流程里自动拒绝" in text
    assert "undergrad_exclusive_action" not in text
