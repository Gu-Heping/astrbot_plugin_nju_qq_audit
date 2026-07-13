"""v0.3.6 stale/external reconciliation and notification tests."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.action_error import classify_action_failure, format_action_outcome_message
from admin.command_resolver import resolve_request_ref
from admin.notify import AdminNotifier
from admin.ux_formatter import format_stale_list, format_view
from config import load_settings
from core.pipeline import AuditPipeline
from core.reconcile import ReconcileResult
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from storage.admin_session_store import AdminSessionStore
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
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
        comment="张三20260002",
        flag="flag-secret",
        sub_type="add",
        parsed={"name": "张三", "student_id": "20260002"},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="test",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


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
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="flag expired")
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="not found")
    )
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.notify_stale_request = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, actions, notifier


def test_classify_stale_markers():
    for msg in ("flag expired", "request not found", "请求已处理", "已过期"):
        assert classify_action_failure(msg).kind == "STALE"


def test_classify_permission_keeps_pending_kind():
    assert classify_action_failure("no permission").kind == "PERMISSION"
    assert classify_action_failure("adapter not available").kind == "ADAPTER"
    assert classify_action_failure("connection timeout").kind == "TRANSIENT"


@pytest.mark.asyncio
async def test_stale_failure_marks_stale(tmp_path):
    pipe, requests, audit, _, notifier = _pipeline(tmp_path, admin_notify=True)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh("111", [req.id])

    result = await pipe.admin_approve(req, "111", list_cache=list_cache)
    assert result.ok is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "stale"
    assert list_cache.resolve("111", 1) is None
    assert any(r.get("type") == "request_stale" for r in audit.read_all())
    notifier.notify_stale_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_failure_member_in_group_marks_external(tmp_path):
    pipe, requests, audit, actions, notifier = _pipeline(tmp_path, admin_notify=True)
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok", data={"user_id": "2492835361"})
    )
    req = _pending()
    await requests.upsert(req)

    await pipe.admin_approve(req, "111")
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    notifier.notify_external_handled.assert_awaited_once()
    assert not any(r.get("type") == "request_stale" for r in audit.read_all())


@pytest.mark.asyncio
async def test_permission_failure_keeps_pending(tmp_path):
    pipe, requests, _, actions, _ = _pipeline(tmp_path)
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="no permission")
    )
    req = _pending()
    await requests.upsert(req)

    await pipe.admin_approve(req, "111")
    updated = await requests.get_by_id(req.id)
    assert updated.status == "pending"
    assert updated.retry_count == 1


@pytest.mark.asyncio
async def test_reconcile_returns_result(tmp_path):
    pipe, requests, _, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(req.group_id, req.user_id)
    assert isinstance(result, ReconcileResult)
    assert result.handled is True
    assert result.request_id == req.id


@pytest.mark.asyncio
async def test_reconcile_not_handled_reason(tmp_path):
    pipe, _, _, _, _ = _pipeline(tmp_path)
    result = await pipe.reconcile_external_join("796836121", "000")
    assert result.handled is False
    assert result.reason == "no_matching_pending"


@pytest.mark.asyncio
async def test_external_hidden_from_pending_list(tmp_path):
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(_pending(status="external", processed_at="t"))
    await requests.upsert(_pending(user_id="222"))
    pending = await requests.list_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].user_id == "222"


@pytest.mark.asyncio
async def test_stale_list_and_restore(tmp_path):
    pipe, requests, _, _, _ = _pipeline(tmp_path)
    req = _pending(status="stale", processed_at="t")
    await requests.upsert(req)
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111:stale", [req.id])

    stale_items = await requests.list_stale(limit=10)
    assert len(stale_items) == 1
    text = format_stale_list(stale_items, {1: req.id})
    assert "stale" in text
    assert "flag" not in text

    resolved = await resolve_request_ref(
        "111", "1", list_cache=cache, requests=requests, for_restore=True
    )
    assert resolved.ok
    await pipe.restore_stale(resolved.request, "111")
    assert (await requests.get_by_id(req.id)).status == "pending"


@pytest.mark.asyncio
async def test_mark_external_from_stale(tmp_path):
    pipe, requests, _, _, _ = _pipeline(tmp_path)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending(status="stale", processed_at="t")
    await requests.upsert(req)
    await list_cache.refresh("111:stale", [req.id])

    resolved = await resolve_request_ref(
        "111", "1", list_cache=list_cache, requests=requests, allow_stale=True
    )
    assert resolved.ok
    await pipe.mark_external(resolved.request, "111", list_cache=list_cache)
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_notify_external_when_admin_is_applicant(tmp_path):
    sys.modules.setdefault("astrbot.api.event", MagicMock())
    chain = MagicMock()
    sys.modules["astrbot.api.event"].MessageChain.return_value = chain
    chain.message.return_value = chain

    user_id = "2492835361"
    settings = load_settings(
        DummyConfig({"admin_qq_ids": user_id, "admin_notify": True})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    await store.record(user_id, "aiocqhttp:FriendMessage:2492835361")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    notifier = AdminNotifier(settings, MagicMock(), context, store, lambda: None)
    await notifier.notify_external_handled(
        request_id="req123456789",
        group_id="796836121",
        user_id=user_id,
        summary="张三",
        comment="张三20260002",
    )
    context.send_message.assert_awaited_once()
    msg = context.send_message.await_args.args[1]
    assert "flag" not in str(msg)
    assert "secret" not in str(msg)


@pytest.mark.asyncio
async def test_stale_view_shows_status(tmp_path):
    req = _pending(
        status="stale",
        processed_at="t",
        last_action_result=ActionResult(ok=False, message="flag expired token"),
    )
    text = format_view(req, index=1)
    assert "stale" in text
    assert "flag" not in text
    assert "restore" in text


def test_action_outcome_messages():
    assert "stale" in format_action_outcome_message("flag expired", 1, final_status="stale")
    assert "external" in format_action_outcome_message(
        "flag expired", 1, final_status="external"
    )
    assert "pending" in format_action_outcome_message(
        "no permission", 1, final_status="pending"
    )
