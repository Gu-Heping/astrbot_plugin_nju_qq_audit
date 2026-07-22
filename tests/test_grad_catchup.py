"""Tests for graduate catchup sync + rematch + release."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.formatter import format_help
from admin.grad_release import (
    GradReleaseService,
    format_grad_catchup_preview,
    format_grad_catchup_result,
)
from admin.release import ReleaseService, format_catchup_result
from config import load_settings
from core.pipeline import RematchSummary
from data_source.student_cache import SyncState
from data_source.students import ActionResult, PendingRequest
from graduate.cache import GraduateStudentCache
from storage.requests_store import RequestsStore, new_request_id

GRAD_GROUP = "200"
UNDER_GROUP = "796836121"
ADMIN_ID = "111"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**kwargs):
    base = {
        "target_group_ids": UNDER_GROUP,
        "grad_enabled": True,
        "grad_target_group_ids": GRAD_GROUP,
        "batch_approve_max_count": 20,
        "batch_approve_interval_ms": 0,
        "ai_parse_allow_auto_approve": False,
    }
    base.update(kwargs)
    return load_settings(DummyConfig(base))


def _grad_strong(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id=GRAD_GROUP,
        user_id="111",
        comment="张三 生物学 博士",
        flag="flag-grad-1",
        sub_type="add",
        profile="graduate",
        parsed={
            "name": "张三",
            "admission_type": "博士",
            "major_text": "生物学",
        },
        match={
            "strength": "strong",
            "candidate_count": 1,
            "matched_student_key": "张三:生物学:博士",
        },
        decision="approve",
        confidence=0.95,
        reason="研究生强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-20T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _mock_pipeline():
    pipeline = MagicMock()
    pipeline.admin_approve = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipeline.rematch_active_pending = AsyncMock(
        return_value=RematchSummary(scanned=1, changed=1, upgraded_to_strong=1)
    )
    pipeline.actions = MagicMock()
    pipeline.actions.get_group_system_msg = AsyncMock(
        side_effect=Exception("skip preflight snapshot")
    )
    pipeline.audit = MagicMock()
    pipeline.audit.append = AsyncMock()
    pipeline.runtime = MagicMock()
    pipeline.runtime.get_qq_snapshot_index = MagicMock(return_value=None)
    pipeline.runtime.get_qq_snapshot_meta = MagicMock(return_value=None)
    return pipeline


@pytest.mark.asyncio
async def test_catchup_grad_preview_calls_grad_sync(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-g1"))
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_sync_state(
        SyncState(
            last_sync_result="success",
            filtered_count=3,
            row_count=3,
            source="njutable",
        )
    )
    sync_calls: list[str] = []

    async def fake_sync(*, source: str = "manual") -> str:
        sync_calls.append(source)
        return "研究生名单同步成功\n缓存人数：3 人"

    pipeline = _mock_pipeline()
    preview = await GradReleaseService().catchup_preview(
        run_sync=fake_sync,
        pipeline=pipeline,
        requests_store=store,
        settings=settings,
        grad_cache=grad_cache,
    )
    assert sync_calls == ["grad_catchup"]
    assert preview.sync_ok is True
    pipeline.rematch_active_pending.assert_awaited()
    assert pipeline.rematch_active_pending.await_args.kwargs["profiles"] == frozenset(
        {"graduate"}
    )
    text = format_grad_catchup_preview(preview, settings)
    assert "研究生名单同步：成功" in text
    assert "/audit catchup grad confirm" in text


@pytest.mark.asyncio
async def test_catchup_grad_preview_sync_failure_blocks(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-g1"))
    grad_cache = GraduateStudentCache(tmp_path)
    pipeline = _mock_pipeline()

    async def fail_sync(*, source: str = "manual") -> str:
        return "研究生名单同步失败：RuntimeError\n已保留旧缓存：0 人"

    preview = await GradReleaseService().catchup_preview(
        run_sync=fail_sync,
        pipeline=pipeline,
        requests_store=store,
        settings=settings,
        grad_cache=grad_cache,
    )
    assert preview.sync_ok is False
    pipeline.rematch_active_pending.assert_not_awaited()
    text = format_grad_catchup_preview(preview, settings)
    assert "未对待处理重算或放行" in text


@pytest.mark.asyncio
async def test_catchup_grad_confirm_sync_rematch_and_approve(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-g1"))
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_sync_state(
        SyncState(last_sync_result="success", filtered_count=1, row_count=1)
    )
    sync_calls: list[str] = []

    async def fake_sync(*, source: str = "manual") -> str:
        sync_calls.append(source)
        return "研究生名单同步成功\n缓存人数：1 人"

    pipeline = _mock_pipeline()
    result = await GradReleaseService().catchup_batch(
        run_sync=fake_sync,
        pipeline=pipeline,
        requests_store=store,
        settings=settings,
        grad_cache=grad_cache,
        admin_user_id=ADMIN_ID,
        count=None,
        audit_log=None,
    )
    assert result.sync_ok is True
    assert result.busy is False
    assert sync_calls == ["grad_catchup"]
    assert pipeline.rematch_active_pending.await_args.kwargs["profiles"] == frozenset(
        {"graduate"}
    )
    assert result.release is not None
    assert result.release.success == 1
    assert pipeline.admin_approve.await_count == 1
    text = format_grad_catchup_result(result, settings)
    assert "成功：1" in text


@pytest.mark.asyncio
async def test_undergrad_catchup_still_undergrad_only(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(
        PendingRequest(
            id="REQ-u1",
            group_id=UNDER_GROUP,
            user_id="222",
            comment="李四 261220001",
            flag="flag-u1",
            sub_type="add",
            profile="undergraduate",
            parsed={"name": "李四", "student_id": "261220001"},
            match={"strength": "strong"},
            decision="approve",
            confidence=0.95,
            reason="强匹配",
            mode="record-only",
            status="pending",
            created_at="2026-07-20T00:00:00+00:00",
            match_strength="strong",
        )
    )
    await store.upsert(_grad_strong(id="REQ-g1"))

    from data_source.student_cache import StudentCache

    cache = StudentCache(tmp_path)
    cache.save_sync_state(
        SyncState(last_sync_result="success", filtered_count=1, row_count=1, source="mock")
    )
    sync_calls: list[str] = []

    async def fake_sync(*, source: str = "manual") -> str:
        sync_calls.append(source)
        return "同步成功: source=mock, raw=1, mapped=1, filtered=1"

    pipeline = _mock_pipeline()
    result = await ReleaseService().catchup_batch(
        run_sync=fake_sync,
        pipeline=pipeline,
        requests_store=store,
        settings=settings,
        cache=cache,
        admin_user_id=ADMIN_ID,
        count=None,
        audit_log=None,
    )
    assert sync_calls == ["catchup"]
    assert pipeline.rematch_active_pending.await_args.kwargs["profiles"] == frozenset(
        {"undergraduate"}
    )
    assert result.release is not None
    assert result.release.success == 1
    assert pipeline.admin_approve.await_args.args[0].id == "REQ-u1"
    text = format_catchup_result(result, settings)
    assert "成功：1" in text


def test_help_grad_mentions_release_catchup_grad():
    text = format_help(topic="grad")
    assert "/audit release grad preview" in text
    assert "/audit release grad 10 confirm" in text
    assert "/audit catchup grad preview" in text
    assert "/audit catchup grad confirm" in text
