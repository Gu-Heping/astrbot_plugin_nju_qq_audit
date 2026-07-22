"""Blacklist integration with pipeline auto-reject."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

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


def _pipeline(tmp_path, *, settings=None, actions=None):
    settings = settings or load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP,
                "mode": "record-only",
                "admin_notify": False,
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
    if actions is None:
        actions = MagicMock()
        actions.set_group_add_request = AsyncMock(
            return_value=ActionResult(ok=True, retcode=0, message="ok")
        )
    notifier = MagicMock()
    notifier.notify_auto_result = AsyncMock()
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
    return pipe, requests, audit, actions, settings, blacklist


@pytest.mark.asyncio
async def test_blacklist_user_auto_reject_uses_neutral_reason(tmp_path):
    pipe, requests, audit, actions, settings, blacklist = _pipeline(tmp_path)
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")

    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-1",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    actions.set_group_add_request.assert_awaited()
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert call.args[3] == settings.blacklist_reject_reason
    assert "黑名单" not in call.args[3]
    assert "家长" not in call.args[3]

    pending = await requests.get_by_id(req_id)
    assert pending.decision == "reject"
    assert pending.status == "processed"
    assert pending.reason.startswith("命中黑名单：")
    assert any(r.get("type") == "blacklist_rejected" for r in audit.read_all())


@pytest.mark.asyncio
async def test_blacklist_already_refuse_becomes_dismissed(tmp_path):
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162003 on 0x10c8_1: already refuse msg",
        )
    )
    pipe, requests, audit, actions, settings, blacklist = _pipeline(
        tmp_path, actions=actions
    )
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-2",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)
    assert pending.status == "dismissed"
    assert any(r.get("type") == "action_already_refused" for r in audit.read_all())


@pytest.mark.asyncio
async def test_blacklist_auto_reject_disabled_keeps_pending(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP,
                "mode": "record-only",
                "admin_notify": False,
                "student_source": "mock",
                "blacklist_enabled": True,
                "blacklist_auto_reject": False,
            }
        )
    )
    pipe, requests, audit, actions, settings, blacklist = _pipeline(
        tmp_path, settings=settings
    )
    await blacklist.add(kind="user_id", value="2492835361", reason="家长申请")
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="2492835361",
        comment="张三 261220001",
        flag="flag-bl-3",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    actions.set_group_add_request.assert_not_awaited()
    pending = await requests.get_by_id(req_id)
    assert pending.decision == "reject"
    assert pending.status == "pending"


@pytest.mark.asyncio
async def test_non_blacklist_strong_unaffected(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP,
                "mode": "auto",
                "admin_notify": False,
                "student_source": "mock",
                "blacklist_enabled": True,
                "blacklist_auto_reject": True,
            }
        )
    )
    pipe, requests, audit, actions, settings, blacklist = _pipeline(
        tmp_path, settings=settings
    )
    event = GroupJoinRequest(
        group_id=GROUP,
        user_id="111",
        comment="张三 261220001",
        flag="flag-ok",
        sub_type="add",
    )
    req_id = await pipe._audit_and_act(event)
    pending = await requests.get_by_id(req_id)
    assert pending.decision == "approve"
    assert pending.status == "processed"
    call = actions.set_group_add_request.await_args
    assert call.args[2] is True
