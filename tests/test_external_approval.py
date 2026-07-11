"""Automated coverage for external-admin approval scenarios (plan A/B/D/F)."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# pipeline imports astrbot.api.logger
sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.command_resolver import map_action_error, resolve_request_ref
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import GroupJoinRequest, extract_group_increase
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
        comment="张三 261220001",
        flag="flag-old",
        sub_type="add",
        parsed={"name": "张三", "student_id": "261220001"},
        match={"strength": "strong"},
        decision="manual_review",
        confidence=0.5,
        reason="待人工",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _make_pipeline(tmp_path, **config_overrides):
    config = DummyConfig(
        {
            "target_group_ids": "796836121",
            "student_source": "mock",
            "admin_notify": False,
            **config_overrides,
        }
    )
    settings = load_settings(config)
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="flag expired")
    )
    notifier = MagicMock()
    notifier.notify_manual_review = AsyncMock()
    pipeline = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, notifier
    )
    return pipeline, requests, audit, actions


@pytest.mark.asyncio
async def test_scenario_a_external_approve_reconciles_pending(tmp_path):
    """QQ 侧已同意且 bot 未操作 → group_increase 对账后不再 pending。"""
    pipeline, requests, audit, _ = _make_pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    reconciled = await pipeline.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="approve",
        operator_id="10001",
    )
    assert reconciled is True

    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert updated.processed_at
    assert updated.action_result.ok
    assert "非 bot 审批" in updated.action_result.message

    pending = await requests.list_pending(limit=100)
    assert pending == []

    records = audit.read_all()
    assert any(r.get("type") == "external_handled" for r in records)


@pytest.mark.asyncio
async def test_scenario_b_admin_ok_after_external_fails_gracefully(tmp_path):
    """QQ 侧已同意后 bot 再 /audit ok → API 失败、变 failed、友好提示。"""
    pipeline, requests, _, actions = _make_pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    cache = AdminListCacheStore(tmp_path / "list_cache.json")
    await cache.refresh("111", [req.id])

    result = await pipeline.admin_approve(req, "111")
    assert result.ok is False

    updated = await requests.get_by_id(req.id)
    assert updated.status == "failed"
    assert updated.processed_at

    msg = map_action_error(result.message)
    assert "其他管理员" in msg
    assert "adapter" not in msg

    resolved = await resolve_request_ref(
        "111", "1", list_cache=cache, requests=requests
    )
    assert not resolved.ok
    assert resolved.error == "already_processed"

    actions.set_group_add_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_scenario_d_reapply_supersedes_old_pending(tmp_path):
    """QQ 侧拒绝后用户重新申请 → 旧 pending 被 supersede。"""
    pipeline, requests, _, _ = _make_pipeline(tmp_path)
    old = _pending(flag="flag-old")
    await requests.upsert(old)

    event = GroupJoinRequest(
        group_id=old.group_id,
        user_id=old.user_id,
        comment=old.comment,
        flag="flag-new",
        sub_type="add",
    )
    await pipeline.handle_group_request(event)

    superseded = await requests.get_by_flag("flag-old")
    assert superseded.status == "ignored"

    active = await requests.get_by_flag("flag-new")
    assert active is not None
    assert active.status == "pending"


@pytest.mark.asyncio
async def test_scenario_f_auto_race_leaves_single_outcome(tmp_path):
    """auto 与人工竞态 → 仅一条 API 调用，失败时 status=failed。"""
    pipeline, requests, _, actions = _make_pipeline(tmp_path, mode="auto")
    from data_source.mock_provider import generate_mock_students

    pipeline.cache.save_students(generate_mock_students())

    event = GroupJoinRequest(
        group_id="796836121",
        user_id="100001",
        comment="张三 261122001",
        flag="flag-auto",
        sub_type="add",
    )
    await pipeline.handle_group_request(event)

    stored = await requests.get_by_flag("flag-auto")
    assert stored is not None
    assert stored.status == "failed"
    actions.set_group_add_request.assert_awaited_once()


def test_extract_group_increase_approve():
    raw = {
        "post_type": "notice",
        "notice_type": "group_increase",
        "group_id": 796836121,
        "user_id": 2492835361,
        "sub_type": "approve",
        "operator_id": 10001,
    }
    increase = extract_group_increase(raw)
    assert increase is not None
    assert increase.group_id == "796836121"
    assert increase.sub_type == "approve"
    assert increase.operator_id == "10001"


@pytest.mark.asyncio
async def test_reconcile_skips_invite_notice(tmp_path):
    pipeline, requests, _, _ = _make_pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)

    reconciled = await pipeline.reconcile_external_join(
        req.group_id,
        req.user_id,
        notice_sub_type="invite",
    )
    assert reconciled is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "pending"


@pytest.mark.asyncio
async def test_reconcile_skips_non_target_group(tmp_path):
    pipeline, requests, _, _ = _make_pipeline(tmp_path)
    req = _pending(group_id="999999")
    await requests.upsert(req)

    reconciled = await pipeline.reconcile_external_join(
        "999999",
        req.user_id,
        notice_sub_type="approve",
    )
    assert reconciled is False
