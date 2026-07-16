"""Tests for readable auto-approve admin notifications (v0.4.1)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.labels import applicant_summary
from admin.notify import AdminNotifier
from admin.ux_formatter import (
    format_auto_result_notice,
    format_no_result,
    format_ok_result,
)
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.mock_provider import generate_mock_students
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import GroupJoinRequest
from storage.admin_session_store import AdminSessionStore
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


GROUP_ID = "826811581"
USER_ID = "2152823507"
ADMIN_ID = "111"


def test_format_auto_result_success_includes_summary():
    text = format_auto_result_notice(
        request_id="REQ-f110eda247dc",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        match_strength="strong",
    )
    assert "自动通过成功" in text
    assert "申请人：张三 / 26115002" in text
    assert f"群：{GROUP_ID}" in text
    assert f"QQ：{USER_ID}" in text
    assert "验证：张三 26115002" in text
    assert "强匹配，已自动同意" in text
    assert "原因：姓名+学号强匹配" in text
    assert "/audit view REQ-f110eda247dc" in text
    assert "flag" not in text.lower()
    assert "token" not in text.lower()
    assert "raw_event" not in text.lower()


def test_format_auto_result_failure_includes_error_and_list():
    text = format_auto_result_notice(
        request_id="REQ-fail",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=False,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        match_strength="strong",
        action_message="retcode=1200 network error",
    )
    assert "自动通过失败" in text
    assert "申请人：张三 / 26115002" in text
    assert "错误：retcode=1200 network error" in text
    assert "/audit list" in text
    assert "flag" not in text.lower()


def test_format_auto_result_summary_fallback_to_user_id():
    text = format_auto_result_notice(
        request_id="REQ-x",
        group_id="1",
        user_id="999",
        ok=True,
        reason="ok",
        summary=None,
    )
    assert "申请人：999" in text


def test_format_ok_no_include_applicant_summary():
    req = PendingRequest(
        id=new_request_id(),
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="张三 26115002",
        flag="secret-flag",
        sub_type="add",
        decision="approve",
        confidence=0.9,
        reason="ok",
        mode="auto",
        status="pending",
        created_at="2026-07-16T00:00:00+00:00",
        parsed={"name": "张三", "student_id": "26115002"},
        match={"strength": "strong"},
        match_strength="strong",
    )
    ok_text = format_ok_result(req, 1)
    assert "申请人：张三 / 26115002" in ok_text
    assert f"QQ：{USER_ID}" in ok_text
    assert f"群：{GROUP_ID}" in ok_text
    assert "flag" not in ok_text
    no_text = format_no_result(req, 1, "信息不完整")
    assert "申请人：张三 / 26115002" in no_text
    assert f"群：{GROUP_ID}" in no_text


@pytest.mark.asyncio
async def test_notify_auto_result_legacy_kwargs_still_work(tmp_path):
    settings = load_settings(
        DummyConfig({"admin_qq_ids": ADMIN_ID, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    notifier = AdminNotifier(settings, actions, MagicMock(), store, lambda: None)
    await notifier.notify_auto_result(
        request_id="REQ-legacy",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "自动通过成功" in message
    assert f"申请人：{USER_ID}" in message
    assert "REQ-legacy" in message


@pytest.mark.asyncio
async def test_notify_auto_result_rich_payload(tmp_path):
    settings = load_settings(
        DummyConfig({"admin_qq_ids": ADMIN_ID, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    notifier = AdminNotifier(settings, actions, MagicMock(), store, lambda: None)
    await notifier.notify_auto_result(
        request_id="REQ-rich",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=False,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002 flag=should-not-leak",
        match_strength="strong",
        action_message="backend timeout",
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "自动通过失败" in message
    assert "张三 / 26115002" in message
    assert "backend timeout" in message
    assert "/audit list" in message
    # comment may contain the word flag as applicant text; ensure we don't dump secrets fields
    assert "token" not in message.lower()
    assert "raw_event" not in message.lower()


@pytest.mark.asyncio
async def test_pipeline_passes_summary_to_auto_notify(tmp_path: Path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_qq_ids": ADMIN_ID,
                "admin_notify": True,
                "student_source": "mock",
                "mode": "auto",
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
        return_value=ActionResult(ok=True, message="approved")
    )
    notifier = MagicMock()
    notifier.notify_auto_result = AsyncMock()
    notifier.notify_manual_review = AsyncMock()
    pipe = AuditPipeline(settings, requests, audit, runtime, cache, actions, notifier)
    await runtime.set_mode("auto", ADMIN_ID)

    # Use a known mock student: 张三 261122001 from mock provider
    students = generate_mock_students()
    student = next(s for s in students if s.student_id.startswith("261"))
    comment = f"{student.name} {student.student_id}"
    event = GroupJoinRequest(
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment=comment,
        flag="flag-auto-ux",
        sub_type="add",
        raw_event={"time": 1000},
    )
    await pipe.handle_group_request(event)

    notifier.notify_auto_result.assert_awaited()
    kwargs = notifier.notify_auto_result.await_args.kwargs
    assert kwargs["ok"] is True
    assert kwargs["summary"]
    assert student.name in kwargs["summary"] or student.student_id in kwargs["summary"]
    assert kwargs["comment"] == comment
    assert kwargs["match_strength"] == "strong"
    assert kwargs["action_message"] == "approved"
    assert "flag" not in (kwargs["summary"] or "").lower()
