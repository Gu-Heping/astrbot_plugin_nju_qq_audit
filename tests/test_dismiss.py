"""Tests for /audit dismiss local close."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.command_resolver import parse_dismiss_command, resolve_request_ref
from admin.ux_formatter import format_list, format_view
from config import load_settings
from core.pending_reconcile import PendingReconcileSummary
from core.pipeline import AuditPipeline
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore


GROUP_ID = "796836121"
USER_ID = "2492835361"
ADMIN_ID = "111"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _pending(**kwargs) -> PendingRequest:
    base = dict(
        id="REQ-dismiss-1",
        group_id=GROUP_ID,
        user_id=USER_ID,
        comment="测试 241220000",
        flag="flag-dismiss-1",
        sub_type="add",
        decision="manual_review",
        confidence=0.4,
        reason="需人工",
        mode="record-only",
        status="pending",
        created_at="2026-07-13T01:00:00+00:00",
        match_strength="weak",
        parsed={"name": "测试", "student_id": "241220000"},
        match={},
    )
    base.update(kwargs)
    return PendingRequest(**base)


def _pipeline(tmp_path: Path, actions=None):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_qq_ids": ADMIN_ID,
                "admin_notify": False,
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    if actions is None:
        actions = MagicMock()
        actions.set_group_add_request = AsyncMock(
            return_value=ActionResult(ok=True, message="should not be called")
        )
        actions.get_group_system_msg = AsyncMock(
            return_value=ActionResult(ok=True, data=[])
        )
        actions.get_group_member_info = AsyncMock(
            return_value=ActionResult(ok=False, message="not found")
        )
    pipe = AuditPipeline(
        settings, requests, audit, runtime, MagicMock(), actions, None
    )
    return pipe, requests, audit, list_cache, actions


@pytest.mark.asyncio
async def test_dismiss_pending_success_removes_from_list(tmp_path):
    pipe, requests, audit, list_cache, actions = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    await list_cache.refresh(ADMIN_ID, [req.id])

    result = await pipe.dismiss_pending(
        req, ADMIN_ID, "测试数据", list_cache=list_cache
    )
    assert result["ok"] is True
    assert result.get("idempotent") is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "dismissed"
    assert updated.dismiss_reason == "测试数据"
    assert updated.dismissed_by == ADMIN_ID
    assert updated.dismissed_at
    assert updated.admin_command == "dismiss"
    assert updated.processed_at
    assert (await requests.list_pending(limit=10)) == []
    actions.set_group_add_request.assert_not_awaited()

    pending = await resolve_request_ref(
        ADMIN_ID, "1", list_cache=list_cache, requests=requests
    )
    assert not pending.ok


@pytest.mark.asyncio
async def test_dismiss_does_not_call_qq_action(tmp_path):
    pipe, requests, _, list_cache, actions = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    await pipe.dismiss_pending(req, ADMIN_ID, "过期申请", list_cache=list_cache)
    actions.set_group_add_request.assert_not_awaited()
    actions.get_group_system_msg.assert_not_awaited()


def test_dismiss_requires_confirm_literal():
    has_confirm, reason = parse_dismiss_command("/audit dismiss 1 测试", "1")
    assert has_confirm is False
    assert reason == ""


def test_dismiss_requires_non_empty_reason():
    has_confirm, reason = parse_dismiss_command("/audit dismiss 1 confirm", "1")
    assert has_confirm is True
    assert reason == ""
    has_confirm, reason = parse_dismiss_command(
        "/audit dismiss 1 confirm 重复申请", "1"
    )
    assert has_confirm is True
    assert reason == "重复申请"


@pytest.mark.asyncio
async def test_dismiss_terminal_is_idempotent(tmp_path):
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    first = await pipe.dismiss_pending(req, ADMIN_ID, "错误导入", list_cache=list_cache)
    assert first["ok"] is True
    second = await pipe.dismiss_pending(
        first["request"], ADMIN_ID, "再次关闭", list_cache=list_cache
    )
    assert second["ok"] is True
    assert second.get("idempotent") is True
    latest = await requests.get_by_id(req.id)
    assert latest.dismiss_reason == "错误导入"
    assert latest.dismissed_by == ADMIN_ID
    dismiss_audits = [
        r
        for r in audit.read_all()
        if r.get("type") == "admin_command" and r.get("command") == "dismiss"
    ]
    assert len(dismiss_audits) == 1


@pytest.mark.asyncio
async def test_dismiss_other_terminal_unchanged(tmp_path):
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path)
    req = _pending(status="external", processed_at="2026-07-13T02:00:00+00:00")
    await requests.upsert(req)
    result = await pipe.dismiss_pending(req, ADMIN_ID, "误操作", list_cache=list_cache)
    assert result.get("already_terminal") is True
    assert (await requests.get_by_id(req.id)).status == "external"


@pytest.mark.asyncio
async def test_dismiss_audit_saves_admin_and_reason(tmp_path):
    pipe, requests, audit, list_cache, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    await pipe.dismiss_pending(req, ADMIN_ID, "过期", list_cache=list_cache)
    records = [
        r
        for r in audit.read_all()
        if r.get("type") == "admin_command" and r.get("command") == "dismiss"
    ]
    assert len(records) == 1
    assert records[0]["result"] == "ok"
    assert records[0]["reason"] == "过期"
    assert records[0]["admin_user_id"] == ADMIN_ID
    assert records[0]["affected_request_id"] == req.id


@pytest.mark.asyncio
async def test_view_shows_dismiss_info(tmp_path):
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    await pipe.dismiss_pending(req, ADMIN_ID, "测试数据清理", list_cache=list_cache)
    updated = await requests.get_by_id(req.id)
    text = format_view(updated, None)
    assert "dismissed" in text
    assert ADMIN_ID in text
    assert "测试数据清理" in text
    assert "未向 QQ 发起拒绝" in text


def test_saturated_summary_mentions_manual_paths():
    summary = PendingReconcileSummary(snapshot_saturated=True)
    text = "\n".join(summary.to_display_lines())
    assert "mark-external" in text
    assert "dismiss" in text
    assert "20 条上限" in text


def test_format_list_includes_dismiss_and_mark_external_hints():
    item = _pending()
    text = format_list([item], {1: item.id})
    assert "/audit mark-external 1 confirm" in text
    assert "/audit dismiss 1 confirm <原因>" in text
    assert "若已被其他管理员在 QQ 侧处理" in text
    assert "若申请已过期、重复或为测试数据" in text


@pytest.mark.asyncio
async def test_list_cache_index_invalid_after_dismiss(tmp_path):
    pipe, requests, _, list_cache, _ = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    index_map = await list_cache.refresh(ADMIN_ID, [req.id])
    assert index_map[1] == req.id
    await pipe.dismiss_pending(req, ADMIN_ID, "重复", list_cache=list_cache)
    assert list_cache.find_index(ADMIN_ID, req.id) is None
    resolved = await resolve_request_ref(
        ADMIN_ID, "1", list_cache=list_cache, requests=requests
    )
    assert not resolved.ok
