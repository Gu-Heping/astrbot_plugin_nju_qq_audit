"""Tests for /audit catchup sync + rematch + release."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.release import (
    ReleaseService,
    format_catchup_preview,
    format_catchup_result,
    is_releasable,
)
from config import load_settings
from core.pipeline import AuditPipeline, RematchSummary
from data_source.student_cache import StudentCache, SyncState
from data_source.students import ActionResult, PendingRequest, Student
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_ID = "796836121"
ADMIN_ID = "111"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _student(**kwargs) -> Student:
    base = dict(
        key="261220099",
        name="王补录",
        student_id="261220099",
        notice_no="20260999",
        major="计算机类",
        status="已确认",
        updated_at="2026-07-14T00:00:00+00:00",
    )
    base.update(kwargs)
    return Student(**base)


def _pending_none(**kwargs) -> PendingRequest:
    base = dict(
        id="REQ-catchup-1",
        group_id=GROUP_ID,
        user_id="2492835361",
        comment="王补录 261220099",
        flag="flag-catchup-1",
        sub_type="add",
        decision="manual_review",
        confidence=0,
        reason="未找到匹配记录",
        mode="record-only",
        status="pending",
        created_at="2026-07-13T01:00:00+00:00",
        match_strength="none",
        parsed={"name": "王补录", "student_id": "261220099"},
        match={"strength": "none", "reason": "未找到匹配记录"},
    )
    base.update(kwargs)
    return PendingRequest(**base)


def _pipeline(tmp_path: Path, *, students: list[Student] | None = None, actions=None):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP_ID,
                "admin_qq_ids": ADMIN_ID,
                "admin_notify": False,
                "student_source": "mock",
                "batch_approve_interval_ms": 0,
                "batch_approve_max_count": 20,
            }
        )
    )
    cache = StudentCache(tmp_path)
    if students is not None:
        cache.save_students(students)
        cache.save_sync_state(
            SyncState(
                last_sync_at="2026-07-14T00:00:00+00:00",
                last_sync_result="success",
                row_count=len(students),
                filtered_count=len(students),
                source="mock",
            )
        )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    if actions is None:
        actions = MagicMock()
        actions.set_group_add_request = AsyncMock(
            return_value=ActionResult(ok=True, message="ok")
        )
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, None
    )
    return pipe, requests, audit, cache, settings, actions


@pytest.mark.asyncio
async def test_rematch_upgrades_none_to_strong_when_student_added(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[]
    )
    req = _pending_none()
    await requests.upsert(req)

    first = await pipe.rematch_active_pending(source="test")
    assert first.upgraded_to_strong == 0
    assert (await requests.get_by_id(req.id)).match_strength == "none"

    cache.save_students([_student()])
    summary = await pipe.rematch_active_pending(source="release_preview")
    assert summary.upgraded_to_strong == 1
    updated = await requests.get_by_id(req.id)
    assert updated.match_strength == "strong"
    assert updated.decision == "approve"
    assert is_releasable(updated, settings)
    assert actions.set_group_add_request.await_count == 0
    assert any(r.get("type") == "pending_rematched" for r in audit.read_all())


@pytest.mark.asyncio
async def test_release_preview_shows_newly_strong_after_rematch(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[_student()]
    )
    await requests.upsert(_pending_none())

    from admin.release import format_release_preview

    preview = await ReleaseService().preview(
        requests, settings, pipeline=pipe, rematch_source="release_preview"
    )
    text = format_release_preview(preview, settings)
    assert preview.rematch is not None
    assert preview.rematch.upgraded_to_strong == 1
    assert preview.total_releasable == 1
    assert "新升为强匹配：1" in text
    assert "王补录" in text
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_catchup_preview_sync_then_rematch(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[]
    )
    await requests.upsert(_pending_none())

    async def fake_sync(*, source: str = "manual") -> str:
        cache.save_students([_student()])
        cache.save_sync_state(
            SyncState(
                last_sync_at="2026-07-14T01:00:00+00:00",
                last_sync_result="success",
                row_count=1,
                filtered_count=1,
                source="mock",
                last_sync_source=source,
            )
        )
        return "同步成功: source=mock, raw=1, mapped=1, filtered=1"

    service = ReleaseService()
    preview = await service.catchup_preview(
        run_sync=fake_sync,
        pipeline=pipe,
        requests_store=requests,
        settings=settings,
        cache=cache,
    )
    assert preview.sync_ok is True
    assert preview.rematch is not None
    assert preview.rematch.upgraded_to_strong == 1
    assert preview.release_preview is not None
    assert preview.release_preview.total_releasable == 1
    text = format_catchup_preview(preview, settings)
    assert "名单同步：成功" in text
    assert "新升为强匹配：1" in text
    assert "/audit catchup confirm" in text
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_catchup_confirm_approves_without_rematch_qq(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[]
    )
    await requests.upsert(_pending_none())

    async def fake_sync(*, source: str = "manual") -> str:
        cache.save_students([_student()])
        cache.save_sync_state(
            SyncState(
                last_sync_result="success",
                filtered_count=1,
                row_count=1,
                source="mock",
            )
        )
        return "同步成功: source=mock, raw=1, mapped=1, filtered=1"

    service = ReleaseService()
    result = await service.catchup_batch(
        run_sync=fake_sync,
        pipeline=pipe,
        requests_store=requests,
        settings=settings,
        cache=cache,
        admin_user_id=ADMIN_ID,
        count=None,
        audit_log=audit,
    )
    assert result.sync_ok is True
    assert result.busy is False
    assert result.release is not None
    assert result.release.success == 1
    assert actions.set_group_add_request.await_count == 1
    updated = await requests.get_by_id("REQ-catchup-1")
    assert updated.status == "processed"
    text = format_catchup_result(result, settings)
    assert "成功：1" in text


@pytest.mark.asyncio
async def test_catchup_sync_failure_blocks_rematch_and_approve(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[]
    )
    await requests.upsert(_pending_none())

    async def fail_sync(*, source: str = "manual") -> str:
        return "同步失败: RuntimeError。已保留旧缓存 0 条。"

    service = ReleaseService()
    preview = await service.catchup_preview(
        run_sync=fail_sync,
        pipeline=pipe,
        requests_store=requests,
        settings=settings,
        cache=cache,
    )
    assert preview.sync_ok is False
    assert (await requests.get_by_id("REQ-catchup-1")).match_strength == "none"
    text = format_catchup_preview(preview, settings)
    assert "未对待处理重算或放行" in text

    result = await service.catchup_batch(
        run_sync=fail_sync,
        pipeline=pipe,
        requests_store=requests,
        settings=settings,
        cache=cache,
        admin_user_id=ADMIN_ID,
        count=None,
    )
    assert result.sync_ok is False
    assert result.release is None
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_rematch_skips_terminal_statuses(tmp_path):
    pipe, requests, _, cache, settings, _ = _pipeline(
        tmp_path, students=[_student()]
    )
    await requests.upsert(
        _pending_none(
            id="REQ-dismissed",
            status="dismissed",
            processed_at="2026-07-13T02:00:00+00:00",
            dismiss_reason="test",
        )
    )
    await requests.upsert(
        _pending_none(
            id="REQ-external",
            status="external",
            processed_at="2026-07-13T02:00:00+00:00",
            flag="flag-ext",
        )
    )
    summary = await pipe.rematch_active_pending(source="test")
    assert summary.scanned == 0
    assert (await requests.get_by_id("REQ-dismissed")).status == "dismissed"
    assert (await requests.get_by_id("REQ-external")).status == "external"


@pytest.mark.asyncio
async def test_catchup_batch_mutex_with_release(tmp_path):
    pipe, requests, audit, cache, settings, actions = _pipeline(
        tmp_path, students=[_student()]
    )
    await requests.upsert(_pending_none())
    await pipe.rematch_active_pending(source="setup")

    service = ReleaseService()
    assert await service._try_begin()

    async def fake_sync(*, source: str = "manual") -> str:
        return "同步成功: source=mock, raw=1, mapped=1, filtered=1"

    result = await service.catchup_batch(
        run_sync=fake_sync,
        pipeline=pipe,
        requests_store=requests,
        settings=settings,
        cache=cache,
        admin_user_id=ADMIN_ID,
        count=1,
    )
    assert result.busy is True
    assert format_catchup_result(result, settings) == "已有分批任务进行中，请稍后再试。"
    await service._finish()
