"""Strict get_group_member_info checks for undergraduate exclusive guard."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.ux_formatter import format_manual_review_notice, format_policy_reject_notice, format_view
from admin.release import ReleaseService
from config import load_settings
from core.parsed_store import strip_internal_parsed_keys
from core.pipeline import AuditPipeline
from core.undergrad_exclusive import check_undergrad_exclusive_membership
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest, Student
from onebot.member_info import format_member_probe_report, inspect_user_in_group
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_A = "1001"
GROUP_B = "1002"
USER_ID = "12345"


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
        "batch_approve_interval_ms": 0,
        "batch_approve_max_count": 10,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _result(**kwargs) -> ActionResult:
    defaults = dict(ok=True, retcode=0, message="ok", data={})
    defaults.update(kwargs)
    return ActionResult(**defaults)


def test_member_present_when_user_id_matches_with_evidence():
    result = _result(data={"user_id": USER_ID, "join_time": 123456})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True
    assert check.result_status == "present"


def test_member_ambiguous_when_echo_only_ids():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


@pytest.mark.parametrize(
    "extra",
    [
        {"card_changeable": False},
        {"unfriendly": False},
        {"shut_up_timestamp": 0},
        {"title_expire_time": 0},
    ],
)
def test_member_ambiguous_when_only_default_fields(extra):
    data = {"user_id": USER_ID, "group_id": GROUP_A, **extra}
    check = inspect_user_in_group(
        _result(data=data),
        expected_group_id=GROUP_A,
        expected_user_id=USER_ID,
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


def test_member_ambiguous_when_role_member_only():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "role": "member"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


def test_member_ambiguous_when_default_role_and_times():
    result = _result(
        data={
            "user_id": USER_ID,
            "group_id": GROUP_A,
            "role": "member",
            "join_time": 0,
            "last_sent_time": 0,
            "level": 0,
        }
    )
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


def test_member_ambiguous_when_level_zero_only():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "level": 0})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


def test_member_ambiguous_when_level_string_only():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "level": "1"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_member_present_when_privileged_role(role):
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "role": role})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_present_when_join_time_positive():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "join_time": 123})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_present_when_last_sent_time_positive():
    result = _result(
        data={"user_id": USER_ID, "group_id": GROUP_A, "last_sent_time": 456}
    )
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_present_when_title_present():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "title": "活跃"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_present_when_card_present():
    result = _result(
        data={"user_id": USER_ID, "group_id": GROUP_A, "card": "25 电子 <NAME>"}
    )
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_probe_report_default_role_fields_not_counted_as_evidence():
    result = _result(
        data={
            "user_id": USER_ID,
            "group_id": GROUP_A,
            "role": "member",
            "join_time": 0,
            "last_sent_time": 0,
            "level": 0,
            "flag": "secret",
        }
    )
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    text = format_member_probe_report(
        group_id=GROUP_A,
        user_id=USER_ID,
        check=check,
        data=result.data,
    )
    assert "结果：无法确认" in text
    assert "成员证据：无" in text
    assert "role=member" in text
    assert "join_time=0" in text
    assert "last_sent_time=0" in text
    assert "level=0" in text
    assert "备注：missing_member_evidence" in text
    assert "flag" not in text
    assert "raw_event" not in text
    assert "token" not in text


@pytest.mark.asyncio
async def test_default_shell_member_check_does_not_hit_exclusive(tmp_path):
    settings = _settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A:
            return _result(
                data={
                    "user_id": user_id,
                    "group_id": group_id,
                    "role": "member",
                    "join_time": 0,
                    "last_sent_time": 0,
                    "level": 0,
                }
            )
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    hit = await check_undergrad_exclusive_membership(
        actions,
        settings,
        current_group_id=GROUP_B,
        user_id=USER_ID,
        audit_log=audit,
        audit_context={"source": "test"},
    )
    assert hit.hit is False
    assert GROUP_A in hit.failed_group_ids
    assert any(
        row.get("type") == "undergrad_exclusive_member_check"
        and row.get("result_status") == "ambiguous"
        and row.get("ambiguity_reason") == "missing_member_evidence"
        for row in audit.read_all()
    )


@pytest.mark.asyncio
async def test_default_fields_member_check_does_not_hit_exclusive(tmp_path):
    settings = _settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A:
            return _result(
                data={
                    "user_id": user_id,
                    "group_id": group_id,
                    "shut_up_timestamp": 0,
                }
            )
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    hit = await check_undergrad_exclusive_membership(
        actions,
        settings,
        current_group_id=GROUP_B,
        user_id=USER_ID,
        audit_log=audit,
        audit_context={"source": "test"},
    )
    assert hit.hit is False
    assert GROUP_A in hit.failed_group_ids


def test_member_present_when_join_time_evidence():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "join_time": 123456})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is True


def test_member_ambiguous_when_nickname_only_with_ids():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "nickname": "测试"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "missing_member_evidence"


def test_member_probe_report_echo_only():
    result = _result(data={"user_id": USER_ID, "group_id": GROUP_A, "flag": "secret"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    text = format_member_probe_report(
        group_id=GROUP_A,
        user_id=USER_ID,
        check=check,
        data=result.data,
    )
    assert "结果：无法确认" in text
    assert "备注：missing_member_evidence" in text
    assert "返回字段：" in text
    assert f"user_id={USER_ID}" in text
    assert f"group_id={GROUP_A}" in text
    assert "成员证据：无" in text
    assert "flag" not in text
    assert "raw_event" not in text
    assert "token" not in text


@pytest.mark.asyncio
async def test_echo_only_member_check_does_not_hit_exclusive(tmp_path):
    settings = _settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A:
            return _result(data={"user_id": user_id, "group_id": group_id})
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    hit = await check_undergrad_exclusive_membership(
        actions,
        settings,
        current_group_id=GROUP_B,
        user_id=USER_ID,
        audit_log=audit,
        audit_context={"source": "test"},
    )
    assert hit.hit is False
    assert GROUP_A in hit.failed_group_ids
    assert any(
        row.get("type") == "undergrad_exclusive_member_check"
        and row.get("result_status") == "ambiguous"
        and row.get("ambiguity_reason") == "missing_member_evidence"
        for row in audit.read_all()
    )


def test_member_ambiguous_when_user_id_mismatch():
    result = _result(data={"user_id": "99999", "nickname": "测试"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "identity_mismatch"


def test_member_not_found_when_only_nickname():
    result = _result(data={"nickname": "测试"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is False
    assert check.result_status == "not_found"


def test_member_ambiguous_when_group_id_mismatch():
    result = _result(data={"user_id": USER_ID, "group_id": "9999"})
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is None
    assert check.ambiguity_reason == "group_mismatch"


def test_member_not_found_from_message():
    result = ActionResult(ok=False, retcode=1, message="member not found")
    check = inspect_user_in_group(
        result, expected_group_id=GROUP_A, expected_user_id=USER_ID
    )
    assert check.present is False
    assert check.result_status == "not_found"


@pytest.mark.asyncio
async def test_ambiguous_member_check_does_not_hit_exclusive(tmp_path):
    settings = _settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A:
            return _result(data={"card": "仅群名片"})
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    hit = await check_undergrad_exclusive_membership(
        actions,
        settings,
        current_group_id=GROUP_B,
        user_id=USER_ID,
        audit_log=audit,
        audit_context={"source": "test"},
    )
    assert hit.hit is False
    assert GROUP_A in hit.failed_group_ids
    assert any(
        row.get("type") == "undergrad_exclusive_member_check"
        and row.get("result_status") == "ambiguous"
        for row in audit.read_all()
    )


def test_view_shows_exclusive_hit_groups():
    item = PendingRequest(
        id="REQ-view-1",
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        flag="flag-view",
        sub_type="add",
        parsed={
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        },
        match={"strength": "strong"},
        decision="manual_review",
        confidence=0.5,
        reason="申请人 QQ 已在本科新生群之一",
        mode="record-only",
        status="pending",
        created_at="2026-07-27T00:00:00+00:00",
        match_strength="strong",
    )
    text = format_view(
        item,
        1,
        group_labels={GROUP_A: f"测试群（{GROUP_A}）", GROUP_B: f"目标群（{GROUP_B}）"},
    )
    assert "多群互斥：命中" in text
    assert GROUP_A in text
    assert "_undergrad_exclusive_group_ids" not in text


def test_manual_review_notice_shows_exclusive_hit_groups():
    text = format_manual_review_notice(
        index=1,
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        judgement="申请人 QQ 已在本科新生群之一",
        parsed={"name": "周七七"},
        group_labels={GROUP_A: f"测试群（{GROUP_A}）"},
    )
    assert "命中本科群" not in text

    text2 = format_manual_review_notice(
        index=1,
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        judgement="申请人 QQ 已在本科新生群之一",
        parsed={
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        },
        group_labels={GROUP_A: f"测试群（{GROUP_A}）"},
    )
    assert "命中本科群" in text2
    assert GROUP_A in text2

    stripped = strip_internal_parsed_keys(
        {
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        }
    )
    text3 = format_manual_review_notice(
        index=1,
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        judgement="申请人 QQ 已在本科新生群之一",
        parsed=stripped,
        group_labels={GROUP_A: f"测试群（{GROUP_A}）"},
        exclusive_hit_group_ids=[GROUP_A],
    )
    assert "命中本科群" in text3
    assert GROUP_A in text3
    assert "_undergrad_exclusive_group_ids" not in text3


def test_policy_reject_notice_shows_exclusive_hit_groups():
    stripped = strip_internal_parsed_keys(
        {
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        }
    )
    text = format_policy_reject_notice(
        title="本科多群互斥",
        request_id="REQ-policy-1",
        group_label=f"目标群（{GROUP_B}）",
        user_label=USER_ID,
        ok=True,
        reason="申请人 QQ 已在本科新生群之一",
        reject_reason="请勿重复申请",
        summary="周七七",
        comment="周七七 261880001",
        action_message=None,
        final_status="rejected",
        parsed=stripped,
        group_labels={GROUP_A: f"测试群（{GROUP_A}）"},
        exclusive_hit_group_ids=[GROUP_A],
    )
    assert "命中本科群" in text
    assert GROUP_A in text
    assert "_undergrad_exclusive_group_ids" not in text


@pytest.mark.asyncio
async def test_notify_manual_review_shows_exclusive_hit_groups(tmp_path):
    from admin.notify import AdminNotifier
    from storage.admin_session_store import AdminSessionStore

    settings = load_settings(
        {
            "admin_qq_ids": "111",
            "admin_notify": True,
            "onebot_http_url": "",
        }
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    notifier = AdminNotifier(settings, actions, context, store, lambda: None)
    stripped = strip_internal_parsed_keys(
        {
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        }
    )
    await notifier.notify_manual_review(
        request_id="REQ-notify-1",
        group_id=GROUP_B,
        user_id=USER_ID,
        comment="周七七 261880001",
        parsed=stripped,
        reason="申请人 QQ 已在本科新生群之一",
        exclusive_hit_group_ids=[GROUP_A],
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "命中本科群" in message
    assert GROUP_A in message
    assert "_undergrad_exclusive_group_ids" not in message


@pytest.mark.asyncio
async def test_notify_policy_reject_shows_exclusive_hit_groups(tmp_path):
    from admin.notify import AdminNotifier
    from storage.admin_session_store import AdminSessionStore

    settings = load_settings(
        {
            "admin_qq_ids": "111",
            "admin_notify": True,
            "onebot_http_url": "",
        }
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    notifier = AdminNotifier(settings, actions, context, store, lambda: None)
    stripped = strip_internal_parsed_keys(
        {
            "name": "周七七",
            "_undergrad_exclusive_hit": True,
            "_undergrad_exclusive_group_ids": [GROUP_A],
        }
    )
    await notifier.notify_policy_reject_result(
        title="本科多群互斥",
        request_id="REQ-notify-2",
        group_id=GROUP_B,
        user_id=USER_ID,
        ok=True,
        reason="申请人 QQ 已在本科新生群之一",
        reject_reason="请勿重复申请",
        summary="周七七",
        comment="周七七 261880001",
        parsed=stripped,
        final_status="rejected",
        exclusive_hit_group_ids=[GROUP_A],
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "命中本科群" in message
    assert GROUP_A in message
    assert "_undergrad_exclusive_group_ids" not in message


@pytest.mark.asyncio
async def test_release_preflight_fail_open_on_ambiguous_member_check(tmp_path):
    settings = _settings()

    async def member_info(group_id, user_id, *, no_cache=True):
        if group_id == GROUP_A:
            return _result(data={"user_id": "99999"})
        return ActionResult(ok=False, message="not found")

    actions = MagicMock()
    actions.get_group_member_info = AsyncMock(side_effect=member_info)
    actions.get_group_system_msg = AsyncMock(return_value=ActionResult(ok=True, data=[]))
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, MagicMock()
    )
    await requests.upsert(
        PendingRequest(
            id="REQ-rel-1",
            group_id=GROUP_B,
            user_id=USER_ID,
            comment="周七七 261880001",
            flag="flag-rel",
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
    )

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
    assert result.undergrad_exclusive_blocked == 0
    actions.set_group_add_request.assert_awaited()
