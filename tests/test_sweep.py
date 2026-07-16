"""Tests for /audit sweep bulk local dismiss of non-strong pending."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.formatter import format_help
from admin.sweep import (
    collect_sweep_preview,
    format_sweep_preview,
    is_sweep_candidate,
    parse_sweep_command,
    run_sweep,
)
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.mock_provider import generate_mock_students
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.list_cache import AdminListCacheStore
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore


GROUP_ID = "796836121"
ADMIN_ID = "111"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _pending(**kwargs) -> PendingRequest:
    base = dict(
        id=new_request_id(),
        group_id=GROUP_ID,
        user_id="20001",
        comment="杂讯",
        flag=f"flag-{new_request_id()}",
        sub_type="add",
        decision="manual_review",
        confidence=0.2,
        reason="需人工",
        mode="auto",
        status="pending",
        created_at="2026-07-14T01:00:00+00:00",
        match_strength="none",
        parsed={},
        match={"strength": "none"},
    )
    base.update(kwargs)
    return PendingRequest(**base)


def _pipeline(tmp_path: Path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_qq_ids": ADMIN_ID,
                "admin_notify": False,
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
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, message="should not be called")
    )
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, None
    )
    return pipe, requests, audit, list_cache, actions


def test_parse_sweep_command():
    assert parse_sweep_command("/audit sweep", "", "")[0] == "help"
    assert parse_sweep_command("/audit sweep preview", "preview", "")[0] == "preview"
    assert parse_sweep_command("/audit sweep confirm", "confirm", "")[0] == "need_reason"
    action, reason = parse_sweep_command(
        "/audit sweep confirm QQ侧已拒无效申请", "confirm", "QQ侧已拒无效申请"
    )
    assert action == "confirm"
    assert reason == "QQ侧已拒无效申请"
    action, reason = parse_sweep_command(
        "/audit sweep confirm 原因里 有空格", "confirm", "原因里"
    )
    assert action == "confirm"
    assert reason == "原因里 有空格"
    assert parse_sweep_command("/audit sweep blah", "blah", "")[0] == "bad_usage"


def test_is_sweep_candidate():
    assert is_sweep_candidate(_pending(match_strength="none"))
    assert is_sweep_candidate(_pending(match_strength="weak"))
    assert not is_sweep_candidate(_pending(match_strength="strong"))
    assert not is_sweep_candidate(
        _pending(status="dismissed", match_strength="none", processed_at="t")
    )


def test_help_mentions_sweep():
    text = format_help(topic="advanced")
    assert "/audit sweep preview" in text
    assert "/audit sweep confirm" in text


@pytest.mark.asyncio
async def test_preview_keeps_strong_lists_non_strong(tmp_path):
    pipe, requests, _, _, actions = _pipeline(tmp_path)
    weak = _pending(
        id="REQ-weak",
        comment="张三",
        match_strength="none",
        decision="manual_review",
    )
    strong = _pending(
        id="REQ-strong",
        comment="张三 261122001",
        user_id="20002",
        match_strength="strong",
        decision="approve",
        parsed={"name": "张三", "student_id": "261122001"},
        match={"strength": "strong"},
    )
    await requests.upsert(weak)
    await requests.upsert(strong)

    preview = await collect_sweep_preview(pipe)
    candidate_ids = {r.id for r in preview.candidates}
    kept_ids = {r.id for r in preview.kept_strong}
    assert "REQ-weak" in candidate_ids
    assert "REQ-strong" not in candidate_ids
    assert "REQ-strong" in kept_ids
    text = format_sweep_preview(preview)
    assert "将本地关闭" in text
    assert "将保留（强匹配）" in text
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_rematch_upgrades_to_strong_then_not_swept(tmp_path):
    """Stale none fields that rematch to strong must be kept."""
    pipe, requests, _, list_cache, actions = _pipeline(tmp_path)
    upgradable = _pending(
        id="REQ-upgrade",
        comment="张三 261122001",
        match_strength="none",
        decision="manual_review",
        parsed={},
        match={"strength": "none"},
    )
    await requests.upsert(upgradable)

    preview = await collect_sweep_preview(pipe)
    assert all(r.id != "REQ-upgrade" for r in preview.candidates)
    assert any(r.id == "REQ-upgrade" for r in preview.kept_strong)
    updated = await requests.get_by_id("REQ-upgrade")
    assert updated.match_strength == "strong"

    result = await run_sweep(
        pipeline=pipe,
        admin_user_id=ADMIN_ID,
        reason="清扫测试",
        list_cache=list_cache,
    )
    assert result.dismissed == 0
    assert (await requests.get_by_id("REQ-upgrade")).status == "pending"
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sweep_dismisses_non_strong_keeps_strong(tmp_path):
    pipe, requests, audit, list_cache, actions = _pipeline(tmp_path)
    weak = _pending(id="REQ-w", comment="乱七八糟", match_strength="weak")
    none = _pending(
        id="REQ-n",
        comment="仅姓名张三",
        user_id="20003",
        match_strength="none",
    )
    strong = _pending(
        id="REQ-s",
        comment="张三 261122001",
        user_id="20004",
        match_strength="strong",
        decision="approve",
        parsed={"name": "张三", "student_id": "261122001"},
        match={"strength": "strong"},
    )
    for req in (weak, none, strong):
        await requests.upsert(req)

    result = await run_sweep(
        pipeline=pipe,
        admin_user_id=ADMIN_ID,
        reason="QQ侧管理员已拒或长期无效",
        list_cache=list_cache,
        audit_log=audit,
    )
    assert result.dismissed == 2
    assert result.failed == 0
    assert (await requests.get_by_id("REQ-w")).status == "dismissed"
    assert (await requests.get_by_id("REQ-n")).status == "dismissed"
    assert (await requests.get_by_id("REQ-w")).dismiss_reason == "QQ侧管理员已拒或长期无效"
    assert (await requests.get_by_id("REQ-s")).status == "pending"
    assert result.skipped_strong >= 1
    actions.set_group_add_request.assert_not_awaited()
    assert any(r.get("type") == "bulk_dismiss_non_strong" for r in audit.read_all())


@pytest.mark.asyncio
async def test_run_sweep_empty_reason_does_not_dismiss(tmp_path):
    pipe, requests, _, list_cache, actions = _pipeline(tmp_path)
    await requests.upsert(_pending(id="REQ-keep", match_strength="none"))
    result = await run_sweep(
        pipeline=pipe,
        admin_user_id=ADMIN_ID,
        reason="   ",
        list_cache=list_cache,
    )
    assert result.dismissed == 0
    assert (await requests.get_by_id("REQ-keep")).status == "pending"
    actions.set_group_add_request.assert_not_awaited()
