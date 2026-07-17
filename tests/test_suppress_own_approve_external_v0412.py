"""v0.4.12: suppress external notify when bot itself approved the join."""

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
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import extract_group_increase
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


BOT_SELF_ID = "1179350197"
OTHER_ADMIN_ID = "2152823507"


def _pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="796836121",
        user_id="2492835361",
        comment="刘骐铭 26115002",
        flag="secret-flag",
        sub_type="add",
        parsed={"name": "刘骐铭", "student_id": "26115002"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.9,
        reason="test",
        mode="auto",
        status="pending",
        created_at="2026-07-17T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _pipeline(tmp_path, *, admin_notify=True):
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
    notifier.notify_auto_result = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(settings, requests, audit, runtime, cache, actions, notifier)
    return pipe, requests, audit, notifier


def test_extract_group_increase_reads_self_id():
    increase = extract_group_increase(
        {
            "post_type": "notice",
            "notice_type": "group_increase",
            "group_id": 796836121,
            "user_id": 2492835361,
            "sub_type": "approve",
            "operator_id": BOT_SELF_ID,
            "self_id": BOT_SELF_ID,
        }
    )
    assert increase is not None
    assert increase.self_id == BOT_SELF_ID
    assert increase.operator_id == BOT_SELF_ID


def test_extract_group_increase_without_self_id():
    increase = extract_group_increase(
        {
            "post_type": "notice",
            "notice_type": "group_increase",
            "group_id": 796836121,
            "user_id": 2492835361,
            "sub_type": "approve",
            "operator_id": OTHER_ADMIN_ID,
        }
    )
    assert increase is not None
    assert increase.self_id is None


@pytest.mark.asyncio
async def test_bot_own_approve_group_increase_suppresses_external(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await list_cache.refresh("111", [req.id])

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id=BOT_SELF_ID,
        self_id=BOT_SELF_ID,
        list_cache=list_cache,
        notifier=notifier,
    )

    assert result.handled is True
    updated = await requests.get_by_id(req.id)
    assert updated.status == "pending"
    assert updated.status != "external"
    notifier.notify_external_handled.assert_not_awaited()
    assert any(r.get("type") == "own_approve_join_notice_suppressed" for r in audit.read_all())
    assert list_cache.resolve("111", 1) is None


@pytest.mark.asyncio
async def test_admin_ok_then_bot_group_increase_keeps_processed(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending(decision="manual_review", mode="manual")
    await requests.upsert(req)
    pipe.actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, message="ok")
    )
    await pipe.admin_approve(req, "111")
    processed = await requests.get_by_id(req.id)
    assert processed.status == "processed"

    # Race: find still sees the in-memory pending snapshot, store already processed.
    pipe.requests.find_active_pending_by_user_group = AsyncMock(return_value=req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id=BOT_SELF_ID,
        self_id=BOT_SELF_ID,
        notifier=notifier,
    )

    assert result.handled is True
    latest = await requests.get_by_id(req.id)
    assert latest.status == "processed"
    assert latest.status != "external"
    notifier.notify_external_handled.assert_not_awaited()
    assert any(r.get("type") == "own_approve_join_notice_suppressed" for r in audit.read_all())


@pytest.mark.asyncio
async def test_other_admin_group_increase_still_external(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id=OTHER_ADMIN_ID,
        self_id=BOT_SELF_ID,
        notifier=notifier,
    )

    assert result.handled is True
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    notifier.notify_external_handled.assert_awaited_once()
    kwargs = notifier.notify_external_handled.await_args.kwargs
    assert kwargs["operator_id"] == OTHER_ADMIN_ID
    assert "flag" not in str(kwargs)
    assert "secret" not in str(kwargs)
    assert any(r.get("type") == "external_handled" for r in audit.read_all())


@pytest.mark.asyncio
async def test_processed_race_suppresses_external_without_self_id(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    await requests.update_by_id(
        req.id,
        {
            "status": "processed",
            "processed_at": "2026-07-17T01:00:00+00:00",
            "action_result": {"ok": True, "message": "auto ok"},
            "decision": "approve",
        },
    )
    pipe.requests.find_active_pending_by_user_group = AsyncMock(return_value=req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id=OTHER_ADMIN_ID,
        self_id=None,
        notifier=notifier,
    )

    assert result.handled is True
    latest = await requests.get_by_id(req.id)
    assert latest.status == "processed"
    assert latest.status != "external"
    notifier.notify_external_handled.assert_not_awaited()
    assert any(
        r.get("type") == "join_notice_after_processed_approve_suppressed"
        for r in audit.read_all()
    )


@pytest.mark.asyncio
async def test_missing_self_id_still_allows_true_external(tmp_path):
    pipe, requests, _, notifier = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id=OTHER_ADMIN_ID,
        self_id=None,
        notifier=notifier,
    )

    assert result.handled is True
    assert (await requests.get_by_id(req.id)).status == "external"
    notifier.notify_external_handled.assert_awaited_once()
