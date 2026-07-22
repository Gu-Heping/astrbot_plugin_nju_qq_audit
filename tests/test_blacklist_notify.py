"""Blacklist auto-reject admin notification wording."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.ux_formatter import format_blacklist_reject_notice
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.blacklist_store import BlacklistStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP = "796836121"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_format_blacklist_reject_notice_ok():
    text = format_blacklist_reject_notice(
        request_id="REQ-1",
        group_id=GROUP,
        user_id="2492835361",
        ok=True,
        reason="命中黑名单：家长申请",
        reject_reason="请使用本人账号并按要求填写验证信息",
        summary="张三 / 261220001",
        comment="张三 261220001",
    )
    assert "已自动拒绝" in text
    assert "已向 QQ 发送拒绝" in text
    assert "请使用本人账号并按要求填写验证信息" in text
    assert "命中黑名单：家长申请" in text
    assert "/audit view REQ-1" in text
    assert "已自动通过" not in text
    assert "已同意入群" not in text
    assert "失败" not in text


def test_format_blacklist_reject_notice_dismissed():
    text = format_blacklist_reject_notice(
        request_id="REQ-d",
        group_id=GROUP,
        user_id="2492835361",
        ok=False,
        reason="命中黑名单：家长申请",
        reject_reason="请使用本人账号并按要求填写验证信息",
        final_status="dismissed",
        action_message="already refuse msg",
    )
    assert "QQ 侧已拒绝" in text
    assert "已移出队列" in text
    assert "无需重复拒绝" in text
    assert "失败" not in text
    assert "已自动通过" not in text


def test_format_blacklist_reject_notice_processed():
    text = format_blacklist_reject_notice(
        request_id="REQ-p",
        group_id=GROUP,
        user_id="2492835361",
        ok=False,
        reason="命中黑名单：家长申请",
        reject_reason="请使用本人账号并按要求填写验证信息",
        final_status="processed",
        action_message="already agree msg by self",
    )
    assert "QQ 侧已处理" in text
    assert "已移出队列" in text
    assert "无需重复审批" in text
    assert "失败" not in text


def test_format_blacklist_reject_notice_pending_failure():
    text = format_blacklist_reject_notice(
        request_id="REQ-2",
        group_id=GROUP,
        user_id="2492835361",
        ok=False,
        reason="命中黑名单：广告号",
        reject_reason="请使用本人账号并按要求填写验证信息",
        final_status="pending",
        action_message="adapter unavailable",
    )
    assert "黑名单自动拒绝失败" in text
    assert "adapter unavailable" in text
    assert "/audit list" in text
    assert "/audit view REQ-2" in text
    assert "已保留记录" in text


def _pipeline(tmp_path, *, admin_notify: bool, action_result: ActionResult | None = None):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP,
                "mode": "record-only",
                "admin_notify": admin_notify,
                "student_source": "mock",
                "blacklist_enabled": True,
                "blacklist_auto_reject": True,
                "blacklist_reject_reason": "请使用本人账号并按要求填写验证信息",
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261220001",
                name="张三",
                student_id="261220001",
                notice_no="20260001",
                major="计算机类",
                status="已确认",
                updated_at="2026-07-22T00:00:00+00:00",
            )
        ]
    )
    blacklist = BlacklistStore(tmp_path / "blacklist.json")
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=action_result
        or ActionResult(ok=True, retcode=0, message="ok")
    )
    notifier = MagicMock()
    notifier.notify_auto_result = AsyncMock()
    notifier.notify_blacklist_reject_result = AsyncMock()
    notifier.notify_manual_review = AsyncMock()
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
    return pipe, actions, settings, blacklist, notifier, requests


@pytest.mark.asyncio
async def test_pipeline_blacklist_reject_uses_dedicated_notify(tmp_path):
    pipe, actions, settings, blacklist, notifier, _requests = _pipeline(
        tmp_path, admin_notify=True
    )
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-notify",
        sub_type="add",
    )
    await pipe._audit_and_act(event)
    notifier.notify_blacklist_reject_result.assert_awaited()
    notifier.notify_auto_result.assert_not_awaited()
    kwargs = notifier.notify_blacklist_reject_result.await_args.kwargs
    assert kwargs["ok"] is True
    assert kwargs["final_status"] == "processed"
    assert kwargs["reject_reason"] == settings.blacklist_reject_reason
    assert "黑名单" not in kwargs["reject_reason"]
    assert kwargs["reason"].startswith("命中黑名单：")
    call = actions.set_group_add_request.await_args
    assert call.args[3] == settings.blacklist_reject_reason
    assert "黑名单" not in call.args[3]


@pytest.mark.asyncio
async def test_pipeline_blacklist_already_refuse_notifies_dismissed(tmp_path):
    pipe, _actions, settings, blacklist, notifier, requests = _pipeline(
        tmp_path,
        admin_notify=True,
        action_result=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162003 on 0x10c8_1: already refuse msg",
        ),
    )
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-already-refuse",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    latest = await requests.get_by_id(req_id)
    assert latest.status == "dismissed"
    notifier.notify_blacklist_reject_result.assert_awaited()
    notifier.notify_auto_result.assert_not_awaited()
    kwargs = notifier.notify_blacklist_reject_result.await_args.kwargs
    assert kwargs["ok"] is False
    assert kwargs["final_status"] == "dismissed"
    assert kwargs["reject_reason"] == settings.blacklist_reject_reason
    assert "黑名单" not in kwargs["reject_reason"]


@pytest.mark.asyncio
async def test_pipeline_blacklist_reject_skips_notify_when_disabled(tmp_path):
    pipe, _actions, _settings, blacklist, notifier, _requests = _pipeline(
        tmp_path, admin_notify=False
    )
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-notify-off",
        sub_type="add",
    )
    await pipe._audit_and_act(event)
    notifier.notify_blacklist_reject_result.assert_not_awaited()
    notifier.notify_auto_result.assert_not_awaited()
