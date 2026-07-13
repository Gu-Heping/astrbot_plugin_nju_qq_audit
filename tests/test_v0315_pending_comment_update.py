"""v0.3.15 pending comment update on same flag."""

import sys
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.ux_formatter import format_list, format_view
from config import load_settings
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


def _pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="796836121",
        user_id="2492835361",
        comment="test",
        flag="flag-1",
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="未找到匹配记录",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _event(**kwargs) -> GroupJoinRequest:
    defaults = dict(
        group_id="796836121",
        user_id="2492835361",
        comment="张三 261122001 计算机类",
        flag="flag-1",
        sub_type="add",
    )
    defaults.update(kwargs)
    return GroupJoinRequest(**defaults)


@pytest.mark.asyncio
async def test_external_invite_notifies_all_admins_even_if_applicant_is_admin(tmp_path):
    admin_qq = "2492835361"
    pipe, requests, _, notifier = _pipeline(
        tmp_path,
        admin_notify=True,
        admin_qq_ids=admin_qq,
    )
    req = _pending(user_id=admin_qq)
    await requests.upsert(req)

    await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
        operator_id="1179350197",
        notifier=notifier,
    )

    assert (await requests.get_by_id(req.id)).status == "external"
    notifier.notify_external_handled.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_same_flag_after_reconcile_reapplies_on_new_attempt(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path)
    req = _pending(
        id="REQ-ext",
        status="external",
        processed_at="2020-01-01T00:00:00+00:00",
        action_result=ActionResult(ok=True, message="external"),
    )
    await requests.upsert(req)

    await pipe.handle_group_request(_event(comment="new answer after external"))

    latest = await requests.get_by_flag("flag-1")
    assert latest.id != req.id
    assert latest.status == "pending"
    assert latest.reapply_of == req.id
    assert (await requests.get_by_id(req.id)).status == "external"
    assert any(r.get("type") == "reapplication_created" for r in audit.read_all())


def _pipeline(tmp_path, *, admin_notify=False, admin_qq_ids="111"):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "796836121",
                "admin_notify": admin_notify,
                "admin_qq_ids": admin_qq_ids,
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
    notifier.notify_pending_comment_updated = AsyncMock()
    notifier.notify_manual_review = AsyncMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, notifier


@pytest.mark.asyncio
async def test_pending_comment_changed_updates_same_request(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path)
    req = _pending(id="REQ-update", comment="test", retry_count=2)
    await requests.upsert(req)

    await pipe.handle_group_request(_event())

    updated = await requests.get_by_id("REQ-update")
    assert updated.id == "REQ-update"
    assert updated.flag == "flag-1"
    assert updated.comment == "张三 261122001 计算机类"
    assert updated.status == "pending"
    assert updated.processed_at is None
    assert updated.created_at == "2026-07-09T00:00:00+00:00"
    assert updated.updated_at
    assert updated.comment_revision == 1
    assert updated.previous_comments == ["test"]
    assert updated.retry_count == 2
    assert updated.parsed.get("name") == "张三"
    assert updated.parsed.get("student_id") == "261122001"
    assert any(r.get("type") == "duplicate_pending_comment_updated" for r in audit.read_all())
    assert not any(r.get("type") == "duplicate_pending_comment_changed" for r in audit.read_all())


@pytest.mark.asyncio
async def test_pending_same_comment_noop(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending(comment="same")
    await requests.upsert(req)
    before = audit.read_all()

    await pipe.handle_group_request(_event(comment="same"))

    assert (await requests.get_by_id(req.id)).comment == "same"
    assert len(audit.read_all()) == len(before)
    notifier.notify_pending_comment_updated.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stale", "ignored"])
async def test_terminal_same_flag_changed_comment_ignored(tmp_path, status):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending(
        id=f"REQ-{status}",
        flag=f"flag-{status}",
        status=status,
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(ok=True, message="done"),
    )
    await requests.upsert(req)

    await pipe.handle_group_request(_event(flag=f"flag-{status}", comment="new answer"))

    updated = await requests.get_by_id(req.id)
    assert updated.status == status
    assert updated.comment == "test"
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())
    notifier.notify_pending_comment_updated.assert_not_called()


@pytest.mark.asyncio
async def test_list_and_view_show_updated_content(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending(id="REQ-ui", comment="test")
    await requests.upsert(req)

    await pipe.handle_group_request(_event())

    updated = await requests.get_by_id("REQ-ui")
    items = await requests.list_pending(limit=10)
    assert len(items) == 1
    assert items[0].comment == "张三 261122001 计算机类"

    list_text = format_list(items, {1: updated.id})
    assert "test" not in list_text
    assert "张三" in list_text

    view_text = format_view(updated, index=1)
    assert "张三 261122001 计算机类" in view_text
    assert "历史填写：1 次" in view_text
    assert "261122001" in view_text
    assert "flag" not in view_text


@pytest.mark.asyncio
async def test_notify_on_comment_update(tmp_path):
    pipe, requests, _, notifier = _pipeline(tmp_path, admin_notify=True)
    req = _pending(comment="test")
    await requests.upsert(req)

    await pipe.handle_group_request(_event())

    notifier.notify_pending_comment_updated.assert_awaited_once()
    kwargs = notifier.notify_pending_comment_updated.await_args.kwargs
    assert kwargs["request_id"] == req.id
    assert "张三" in kwargs["comment"]
    assert "flag" not in str(kwargs)


@pytest.mark.asyncio
async def test_new_flag_still_supersedes_old_pending(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    old = _pending(id="REQ-old", flag="flag-old", comment="old pending")
    await requests.upsert(old)

    await pipe.handle_group_request(_event(flag="flag-new", comment="张三 261122001"))

    assert (await requests.get_by_flag("flag-old")).status == "ignored"
    assert (await requests.get_by_flag("flag-new")).status == "pending"
