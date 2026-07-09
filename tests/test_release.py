import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from admin.release import (
    ReleaseService,
    format_release_preview,
    format_release_result,
    is_releasable,
    list_releasable,
)
from config import load_settings
from data_source.students import ActionResult, PendingRequest
from storage.requests_store import RequestsStore, new_request_id


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _strong_req(**kwargs) -> PendingRequest:
    defaults = dict(
        id=new_request_id(),
        group_id="796836121",
        user_id="111",
        comment="张三 261220001",
        flag="flag-1",
        sub_type="add",
        parsed={"name": "张三", "student_id": "261220001"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.95,
        reason="强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


@pytest.mark.asyncio
async def test_is_releasable_strong_pending(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req()
    assert is_releasable(req, settings)


@pytest.mark.asyncio
async def test_is_releasable_rejects_manual_review(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req(decision="manual_review", match_strength="weak")
    assert not is_releasable(req, settings)


@pytest.mark.asyncio
async def test_is_releasable_rejects_non26(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req(parsed={"name": "张三", "student_id": "251220001"})
    assert not is_releasable(req, settings)


@pytest.mark.asyncio
async def test_is_releasable_rejects_notice_only_without_grade26_id(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req(
        comment="张三 通知书编号 20260001",
        parsed={"name": "张三", "notice_no": "20260001"},
        match={"strength": "strong"},
    )
    assert not is_releasable(req, settings)


@pytest.mark.asyncio
async def test_is_releasable_accepts_matched_student_id(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req(
        comment="张三 通知书编号 20260001",
        parsed={"name": "张三", "notice_no": "20260001"},
        match={"strength": "strong", "matched_student_id": "261220001"},
    )
    assert is_releasable(req, settings)


@pytest.mark.asyncio
async def test_is_releasable_rejects_invite(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    req = _strong_req(sub_type="invite")
    assert not is_releasable(req, settings)


@pytest.mark.asyncio
async def test_release_preview_no_flag_in_output(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_strong_req(flag="secret-flag"))
    preview = await ReleaseService().preview(store, settings)
    text = format_release_preview(preview, settings)
    assert "secret-flag" not in text
    assert "261220001" in text or "张三" in text


@pytest.mark.asyncio
async def test_release_batch_respects_max_count(tmp_path):
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "batch_approve_max_count": 2})
    )
    store = RequestsStore(tmp_path / "requests.json")
    for i in range(3):
        await store.upsert(_strong_req(id=new_request_id(), parsed={"name": f"用户{i}", "student_id": f"2612200{i:02d}"}))

    pipeline = MagicMock()
    pipeline.admin_approve = AsyncMock(return_value=ActionResult(ok=True, retcode=0, message="ok"))
    service = ReleaseService()
    result = await service.run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=10,
        audit_log=None,
    )
    assert result is not None
    assert result.processed <= 2
    assert pipeline.admin_approve.await_count <= 2


@pytest.mark.asyncio
async def test_release_fail_continue(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121", "batch_approve_interval_ms": 0}))
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_strong_req(id="REQ-1"))
    await store.upsert(_strong_req(id="REQ-2", parsed={"name": "李四", "student_id": "261220002"}))

    pipeline = MagicMock()
    pipeline.admin_approve = AsyncMock(
        side_effect=[ActionResult(ok=False, retcode=1, message="expired"), ActionResult(ok=True, retcode=0, message="ok")]
    )
    service = ReleaseService()
    result = await service.run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=2,
        audit_log=None,
    )
    assert result.failed == 1
    assert result.success == 1


@pytest.mark.asyncio
async def test_release_concurrent_rejected(tmp_path):
    settings = load_settings(DummyConfig({"target_group_ids": "796836121", "batch_approve_interval_ms": 50}))
    store = RequestsStore(tmp_path / "requests.json")
    await store.upsert(_strong_req(id="REQ-1"))
    await store.upsert(_strong_req(id="REQ-2", parsed={"name": "李四", "student_id": "261220002"}))

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_approve(req, admin_user_id):
        started.set()
        await release.wait()
        return ActionResult(ok=True, retcode=0, message="ok")

    pipeline = MagicMock()
    pipeline.admin_approve = AsyncMock(side_effect=slow_approve)
    service = ReleaseService()

    first = asyncio.create_task(
        service.run_batch(
            requests_store=store,
            pipeline=pipeline,
            settings=settings,
            admin_user_id="admin",
            count=2,
            audit_log=None,
        )
    )
    await started.wait()
    second = await service.run_batch(
        requests_store=store,
        pipeline=pipeline,
        settings=settings,
        admin_user_id="admin",
        count=1,
        audit_log=None,
    )
    assert second is None
    release.set()
    result = await first
    assert result is not None


def test_format_release_result_no_flag():
    from admin.release import ReleaseLineResult, ReleaseResult
    from config import load_settings

    settings = load_settings(DummyConfig())
    text = format_release_result(
        ReleaseResult(
            requested=1,
            processed=1,
            success=1,
            failed=0,
            remaining=0,
            lines=[
                ReleaseLineResult(
                    index=1,
                    request_id="REQ-abc",
                    summary="张三",
                    ok=True,
                    message="",
                )
            ],
        ),
        settings,
    )
    assert "flag" not in text
