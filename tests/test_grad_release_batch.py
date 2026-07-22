"""Tests for graduate release batch / preflight behavior."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.grad_release import GradReleaseService, format_grad_release_result
from admin.release import ReleaseService, is_releasable
from config import load_settings
from core.pipeline import RematchSummary
from data_source.students import ActionResult, PendingRequest
from storage.requests_store import RequestsStore, new_request_id

GRAD_GROUP = "200"
UNDER_GROUP = "796836121"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**kwargs):
    base = {
        "target_group_ids": UNDER_GROUP,
        "grad_enabled": True,
        "grad_target_group_ids": GRAD_GROUP,
        "batch_approve_max_count": 2,
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


def _under_strong(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id=UNDER_GROUP,
        user_id="222",
        comment="李四 261220001",
        flag="flag-under-1",
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
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _mock_pipeline():
    pipeline = MagicMock()
    pipeline.admin_approve = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipeline.rematch_active_pending = AsyncMock(
        return_value=RematchSummary(scanned=0, changed=0, upgraded_to_strong=0)
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
async def test_grad_release_preview_rematch_graduate_only(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-g1"))
    pipeline = _mock_pipeline()

    preview = await GradReleaseService().preview(
        store, settings, pipeline=pipeline, rematch_source="grad_release_preview"
    )
    pipeline.rematch_active_pending.assert_awaited_once()
    kwargs = pipeline.rematch_active_pending.await_args.kwargs
    assert kwargs["profiles"] == frozenset({"graduate"})
    assert kwargs["source"] == "grad_release_preview"
    assert preview.total_releasable == 1


@pytest.mark.asyncio
async def test_grad_release_confirm_approves_grad_not_undergrad(tmp_path):
    settings = _settings(batch_approve_max_count=10)
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-g1"))
    under = _under_strong(id="REQ-u1")
    await store.upsert(under)
    assert is_releasable(under, settings)

    pipeline = _mock_pipeline()
    result = await GradReleaseService().run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=10,
        audit_log=None,
        skip_rematch=True,
    )
    assert result is not None
    assert result.success == 1
    assert pipeline.admin_approve.await_count == 1
    approved = pipeline.admin_approve.await_args.args[0]
    assert approved.id == "REQ-g1"
    assert approved.profile == "graduate"


@pytest.mark.asyncio
async def test_grad_release_preflight_stale_counts_stale_not_failed(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-stale", flag="stale-legacy"))

    pipeline = _mock_pipeline()

    async def preflight(pipe, requests_store, settings, batch):
        req = await requests_store.get_by_id(batch[0].id)
        req.status = "stale"
        await requests_store.upsert(req)
        return []

    import admin.grad_release as grad_mod

    original = grad_mod.preflight_releasable_with_live_snapshot
    grad_mod.preflight_releasable_with_live_snapshot = preflight
    try:
        result = await GradReleaseService().run_batch(
            requests_store=store,
            pipeline=pipeline,
            settings=settings,
            admin_user_id="admin",
            count=1,
            audit_log=None,
            skip_rematch=True,
        )
    finally:
        grad_mod.preflight_releasable_with_live_snapshot = original

    assert result is not None
    assert result.stale_count == 1
    assert result.failed == 0
    assert result.success == 0
    pipeline.admin_approve.assert_not_awaited()
    text = format_grad_release_result(result, settings)
    assert "已失效：1" in text
    assert "研究生强匹配批量放行" in text


@pytest.mark.asyncio
async def test_grad_release_preflight_external_counts_external(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-ext", flag="ext-legacy"))

    pipeline = _mock_pipeline()

    async def preflight(pipe, requests_store, settings, batch):
        req = await requests_store.get_by_id(batch[0].id)
        req.status = "external"
        await requests_store.upsert(req)
        return []

    import admin.grad_release as grad_mod

    original = grad_mod.preflight_releasable_with_live_snapshot
    grad_mod.preflight_releasable_with_live_snapshot = preflight
    try:
        result = await GradReleaseService().run_batch(
            requests_store=store,
            pipeline=pipeline,
            settings=settings,
            admin_user_id="admin",
            count=1,
            audit_log=None,
            skip_rematch=True,
        )
    finally:
        grad_mod.preflight_releasable_with_live_snapshot = original

    assert result is not None
    assert result.external_count == 1
    assert result.failed == 0
    pipeline.admin_approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_grad_release_action_failed_counts_failed(tmp_path):
    settings = _settings()
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_grad_strong(id="REQ-fail"))
    pipeline = _mock_pipeline()
    pipeline.admin_approve = AsyncMock(
        return_value=ActionResult(ok=False, retcode=1, message="network error")
    )
    result = await GradReleaseService().run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=1,
        audit_log=None,
        skip_rematch=True,
    )
    assert result is not None
    assert result.failed == 1
    assert result.stale_count == 0
    assert result.external_count == 0


@pytest.mark.asyncio
async def test_grad_release_count_limit(tmp_path):
    settings = _settings(batch_approve_max_count=10)
    store = RequestsStore(tmp_path / "requests.json")
    for i in range(3):
        await store.upsert(
            _grad_strong(
                id=f"REQ-{i}",
                user_id=str(100 + i),
                flag=f"flag-{i}",
                created_at=f"2026-07-20T00:00:0{i}+00:00",
            )
        )
    pipeline = _mock_pipeline()
    result = await GradReleaseService().run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=2,
        audit_log=None,
        skip_rematch=True,
    )
    assert result is not None
    assert result.requested == 2
    assert pipeline.admin_approve.await_count == 2


@pytest.mark.asyncio
async def test_grad_release_all_respects_batch_max(tmp_path):
    settings = _settings(batch_approve_max_count=2)
    store = RequestsStore(tmp_path / "requests.json")
    for i in range(4):
        await store.upsert(
            _grad_strong(
                id=f"REQ-{i}",
                user_id=str(200 + i),
                flag=f"flag-all-{i}",
                created_at=f"2026-07-20T01:00:0{i}+00:00",
            )
        )
    pipeline = _mock_pipeline()
    result = await GradReleaseService().run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=None,
        audit_log=None,
        skip_rematch=True,
    )
    assert result is not None
    assert result.requested == 2
    assert pipeline.admin_approve.await_count == 2


@pytest.mark.asyncio
async def test_undergrad_release_unchanged(tmp_path):
    settings = _settings(batch_approve_max_count=10)
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_under_strong(id="REQ-u1"))
    await store.upsert(_grad_strong(id="REQ-g1"))
    pipeline = _mock_pipeline()
    result = await ReleaseService().run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=10,
        audit_log=None,
        skip_rematch=True,
    )
    assert result is not None
    assert result.success == 1
    assert pipeline.admin_approve.await_count == 1
    assert pipeline.admin_approve.await_args.args[0].id == "REQ-u1"
    kwargs = pipeline.rematch_active_pending.await_args
    # skip_rematch=True → rematch not called
    pipeline.rematch_active_pending.assert_not_awaited()
    del kwargs
