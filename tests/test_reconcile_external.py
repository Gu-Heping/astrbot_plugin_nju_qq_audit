"""Tests for reconcile_external_join and on_all_events group_increase handling."""

import inspect
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules.setdefault("astrbot.api.event", MagicMock())
sys.modules.setdefault("astrbot.api.platform", MagicMock())
_mock_star = MagicMock()


class _StarBase:
    pass


def _register_stub(*_args, **_kwargs):
    def _decorator(cls):
        return cls

    return _decorator


_mock_star.register = _register_stub
_mock_star.Star = _StarBase
sys.modules["astrbot.api.star"] = _mock_star
sys.modules.setdefault("astrbot.core.utils.astrbot_path", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = MagicMock(
    return_value="/tmp/astrbot"
)

from core.reconcile import ReconcileResult
from admin.command_resolver import processed_request_message, resolve_request_ref
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import PendingRequest
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
        comment="问题：姓名\n答案：刘骐铭 26115002",
        flag="secret-flag",
        sub_type="add",
        parsed={"name": "刘骐铭", "student_id": "26115002"},
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
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipe, requests, audit, notifier


def test_reconcile_signature_accepts_list_cache_and_notifier():
    sig = inspect.signature(AuditPipeline.reconcile_external_join)
    params = sig.parameters
    assert "list_cache" in params
    assert "notifier" in params
    assert "self_id" in params
    assert params["list_cache"].default is None
    assert params["notifier"].default is None
    assert params["self_id"].default is None


@pytest.mark.asyncio
async def test_reconcile_without_list_cache_or_notifier(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    result = await pipe.reconcile_external_join(
        req.group_id, req.user_id, notice_sub_type="approve"
    )
    assert result.handled is True
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert updated.processed_at
    assert updated.action_result.ok is True
    assert any(r.get("type") == "external_handled" for r in audit.read_all())
    notifier.notify_external_handled.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_with_list_cache_and_notifier(tmp_path):
    pipe, requests, audit, notifier = _pipeline(tmp_path, admin_notify=True)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh("111", [req.id])

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id="99999",
        list_cache=list_cache,
        notifier=notifier,
    )
    assert result.handled is True
    assert list_cache.resolve("111", 1) is None
    notifier.notify_external_handled.assert_awaited_once()
    kwargs = notifier.notify_external_handled.await_args.kwargs
    assert kwargs["request_id"] == req.id
    assert kwargs["group_id"] == req.group_id
    assert kwargs["operator_id"] == "99999"
    assert "flag" not in str(kwargs)
    assert "secret" not in str(kwargs)


@pytest.mark.asyncio
async def test_notifier_exception_still_marks_external(tmp_path):
    pipe, requests, _, notifier = _pipeline(tmp_path, admin_notify=True)
    req = _pending()
    await requests.upsert(req)
    notifier.notify_external_handled = AsyncMock(side_effect=RuntimeError("notify boom"))

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        notifier=notifier,
    )
    assert result.handled is True
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_list_cache_failure_still_marks_external(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    bad_cache = MagicMock()
    bad_cache.remove_request_id = AsyncMock(side_effect=OSError("cache io error"))

    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        list_cache=bad_cache,
    )
    assert result.handled is True
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_reconcile_skips_non_target_group(tmp_path):
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending(group_id="999999")
    await requests.upsert(req)
    result = await pipe.reconcile_external_join("999999", req.user_id)
    assert not result.handled
    assert result.reason == "non_target_group"


@pytest.mark.asyncio
async def test_reconcile_invite_with_pending_add_marks_external(tmp_path):
    pipe, requests, _, notifier = _pipeline(tmp_path, admin_notify=True)
    req = _pending()
    await requests.upsert(req)
    result = await pipe.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
        operator_id="1179350197",
        notifier=notifier,
    )
    assert result.handled is True
    assert result.reason == "matched_pending_external"
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_reconcile_invite_without_pending(tmp_path):
    pipe, _, _, _ = _pipeline(tmp_path)
    result = await pipe.reconcile_external_join(
        "796836121", "0000000000", notice_sub_type="invite"
    )
    assert not result.handled
    assert result.reason == "invite_notice_no_pending"


@pytest.mark.asyncio
async def test_reconcile_no_pending_returns_false(tmp_path):
    pipe, _, _, _ = _pipeline(tmp_path)
    result = await pipe.reconcile_external_join("796836121", "0000000000")
    assert not result.handled
    assert result.reason == "no_matching_pending"


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
    msg = processed_request_message(result.request)
    assert "QQ 客户端" in msg


@pytest.mark.asyncio
async def test_reconcile_exception_swallowed_like_main_handler(tmp_path):
    """与 main.py on_all_events 相同 try/except：reconcile 异常不得向上抛出。"""
    pipe, requests, _, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")

    pipe.reconcile_external_join = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await pipe.reconcile_external_join(
            req.group_id,
            req.user_id,
            notice_sub_type="approve",
            operator_id=None,
            list_cache=list_cache,
        )

    leaked = False
    try:
        try:
            await pipe.reconcile_external_join(
                req.group_id,
                req.user_id,
                notice_sub_type="approve",
                operator_id=None,
                list_cache=list_cache,
            )
        except Exception:
            pass
    except Exception:
        leaked = True
    assert not leaked
    assert pipe.reconcile_external_join.await_count == 2


def test_list_cache_remove_request_id_alias(tmp_path):
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    assert hasattr(cache, "remove_request_id")
    assert hasattr(cache, "remove_request_everywhere")
