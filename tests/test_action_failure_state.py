import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.command_resolver import map_action_error, processed_request_message, resolve_request_ref
from admin.ux_formatter import format_list, format_view
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
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
        user_id="111",
        comment="test",
        flag="flag-1",
        sub_type="add",
        parsed={"name": "张三"},
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


def _pipeline(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121", "admin_notify": False}))
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="flag expired")
    )
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    return AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    ), requests, actions, notifier


@pytest.mark.asyncio
async def test_admin_reject_failure_keeps_pending(tmp_path):
    pipeline, requests, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipeline.admin_reject(req, "admin1", "test reason")
    assert result.ok is False

    updated = await requests.get_by_id(req.id)
    assert updated.status == "pending"
    assert updated.processed_at is None
    assert updated.last_action_result is not None
    assert updated.last_action_result.ok is False
    assert updated.retry_count == 1


@pytest.mark.asyncio
async def test_admin_reject_success_marks_processed(tmp_path):
    pipeline, requests, actions, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    actions.set_group_add_request = AsyncMock(return_value=ActionResult(ok=True, retcode=0, message="ok"))

    result = await pipeline.admin_reject(req, "admin1")
    assert result.ok is True
    updated = await requests.get_by_id(req.id)
    assert updated.status == "processed"
    assert updated.processed_at is not None


@pytest.mark.asyncio
async def test_failed_legacy_can_retry(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(
        _pending(
            id=req_id,
            status="failed",
            processed_at="2026-07-09T01:00:00+00:00",
        )
    )
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111", [req_id])

    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert result.ok
    assert result.request.status == "pending"


@pytest.mark.asyncio
async def test_external_blocks_ok_no(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(
        _pending(
            id=req_id,
            status="external",
            processed_at="2026-07-09T01:00:00+00:00",
        )
    )
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111", [req_id])

    result = await resolve_request_ref("111", "1", list_cache=cache, requests=requests)
    assert not result.ok
    assert "QQ 客户端" in result.message


@pytest.mark.asyncio
async def test_external_view_allowed(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    req = _pending(id=req_id, status="external", processed_at="2026-07-09T01:00:00+00:00")
    await requests.upsert(req)
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111", [req_id])

    result = await resolve_request_ref(
        "111", "1", list_cache=cache, requests=requests, for_view=True
    )
    assert result.ok
    text = format_view(result.request, result.index)
    assert "external" in text
    assert "flag" not in text


@pytest.mark.asyncio
async def test_reconcile_external_and_notify(tmp_path):
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "admin_notify": True, "admin_qq_ids": "111"})
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    pipeline = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending(user_id="222", parsed={"name": "刘骐铭"})
    await requests.upsert(req)
    await list_cache.refresh("111", [req.id])

    ok = await pipeline.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        list_cache=list_cache,
    )
    assert ok is True
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert list_cache.resolve("111", 1) is None
    notifier.notify_external_handled.assert_awaited_once()
    call_kwargs = notifier.notify_external_handled.await_args.kwargs
    assert "flag" not in str(call_kwargs)


@pytest.mark.asyncio
async def test_mark_external(tmp_path):
    pipeline, requests, _, notifier = _pipeline(tmp_path)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh("111", [req.id])

    await pipeline.mark_external(req, "admin1", list_cache=list_cache)
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert list_cache.resolve("111", 1) is None


def test_map_action_error_mentions_mark_external():
    msg = map_action_error("flag expired")
    assert "mark-external" in msg
    assert "flag" not in msg


def test_view_shows_last_failure():
    req = _pending(
        last_action_result=ActionResult(ok=False, message="flag expired secret"),
        last_action_at="2026-07-09T01:00:00+00:00",
        retry_count=1,
    )
    text = format_view(req, index=1)
    assert "上次审批结果：失败" in text
    assert "flag" not in text
    assert "mark-external" in text


def test_list_shows_retry_hint():
    req = _pending(
        last_action_result=ActionResult(ok=False, message="expired"),
        last_action_at="2026-07-09T01:00:00+00:00",
    )
    text = format_list([req], {1: req.id})
    assert "上次操作失败" in text


def test_processed_request_message_external():
    req = _pending(status="external", processed_at="t")
    assert "QQ 客户端" in processed_request_message(req)
