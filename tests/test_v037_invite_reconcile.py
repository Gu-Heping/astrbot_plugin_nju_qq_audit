"""v0.3.7 invite group_increase reconcile with matching pending add."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.command_resolver import resolve_request_ref
from admin.ux_formatter import format_view
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


def _pipeline(tmp_path, *, admin_notify=False):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "796836121",
                "admin_notify": admin_notify,
                "admin_qq_ids": "111",
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, notifier


@pytest.mark.asyncio
async def test_invite_with_matching_pending_add_marks_external(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
        operator_id="1179350197",
    )
    assert result.handled is True
    assert result.reason == "matched_pending_external"
    assert result.request_id == req.id
    assert "notice_sub_type=invite" in result.message
    assert "1179350197" in result.message

    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"


@pytest.mark.asyncio
async def test_invite_without_pending_not_handled(tmp_path):
    pipe, _, _, _ = _pipeline(tmp_path)
    result = await pipe.reconcile_external_join(
        "796836121", "0000000000", notice_sub_type="invite"
    )
    assert result.handled is False
    assert result.reason == "invite_notice_no_pending"


@pytest.mark.asyncio
async def test_invite_with_pending_sub_type_invite_not_external(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending(sub_type="invite")
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
        operator_id="1179350197",
    )
    assert result.handled is False
    assert result.reason == "pending_sub_type_not_add"
    assert (await requests.get_by_id(req.id)).status == "pending"


@pytest.mark.asyncio
async def test_approve_with_matching_pending_add_marks_external(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id="10001",
    )
    assert result.handled is True
    assert result.reason == "matched_pending_external"
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_external_removes_list_cache_and_notifies(tmp_path):
    pipe, requests, _, notifier = _pipeline(tmp_path, admin_notify=True)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh("111", [req.id])

    await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
        operator_id="1179350197",
        list_cache=list_cache,
        notifier=notifier,
    )
    assert list_cache.resolve("111", 1) is None
    notifier.notify_external_handled.assert_awaited_once()
    kwargs = notifier.notify_external_handled.await_args.kwargs
    assert kwargs["notice_sub_type"] == "invite"
    assert kwargs["operator_id"] == "1179350197"
    assert "flag" not in str(kwargs)
    assert "secret" not in str(kwargs)


@pytest.mark.asyncio
async def test_external_hidden_from_pending_list(tmp_path):
    requests = RequestsStore(tmp_path / "requests.json")
    await requests.upsert(
        _pending(status="external", processed_at="2026-07-09T01:00:00+00:00")
    )
    await requests.upsert(_pending(user_id="222"))
    pending = await requests.list_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].user_id == "222"


@pytest.mark.asyncio
async def test_view_by_id_shows_external_after_list_cache_cleared(tmp_path):
    req_id = new_request_id()
    requests = RequestsStore(tmp_path / "requests.json")
    req = _pending(
        id=req_id,
        status="external",
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(
            ok=True,
            message="QQ 侧已入群（非 bot 审批，notice_sub_type=invite）",
        ),
    )
    await requests.upsert(req)
    cache = AdminListCacheStore(tmp_path / "list_cache.json")

    expired = await resolve_request_ref(
        "111", "1", list_cache=cache, requests=requests
    )
    assert not expired.ok

    by_id = await resolve_request_ref(
        "111", req_id, list_cache=cache, requests=requests, for_view=True
    )
    assert by_id.ok
    text = format_view(by_id.request, by_id.index)
    assert "QQ 侧已处理" in text
    assert "flag" not in text
