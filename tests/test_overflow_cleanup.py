"""Overflow batch cleanup preview/confirm."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.overflow_cleanup import (
    OverflowCleanupService,
    is_overflow_cleanup_candidate,
    list_overflow_cleanup_candidates,
)
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP_SOURCE = "2601"
GROUP_REDIRECT = "2602"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**overrides):
    base = {
        "target_group_ids": f"{GROUP_SOURCE},{GROUP_REDIRECT}",
        "undergrad_overflow_enabled": True,
        "undergrad_overflow_source_group_id": GROUP_SOURCE,
        "undergrad_overflow_redirect_group_id": GROUP_REDIRECT,
        "undergrad_overflow_threshold": 1950,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _req(
    req_id: str,
    *,
    status: str = "pending",
    group_id: str = GROUP_SOURCE,
    sub_type: str = "add",
    flag: str = "flag-1",
    profile: str = "undergraduate",
) -> PendingRequest:
    return PendingRequest(
        id=req_id,
        group_id=group_id,
        user_id=f"user-{req_id}",
        comment="test comment",
        flag=flag,
        sub_type=sub_type,
        parsed={"name": "测试"},
        match={},
        decision="manual_review",
        confidence=0,
        reason="",
        mode="manual",
        status=status,
        created_at="2026-07-28T08:00:00+00:00",
        profile=profile,
    )


@pytest.fixture
def cleanup_env(tmp_path):
    settings = _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path / "cache")
    actions = MagicMock()
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
    )
    service = OverflowCleanupService()
    return {
        "settings": settings,
        "requests": requests,
        "audit": audit,
        "pipeline": pipe,
        "service": service,
    }


@pytest.mark.asyncio
async def test_preview_only_selects_pending_eligible_requests(cleanup_env):
    requests = cleanup_env["requests"]
    settings = cleanup_env["settings"]
    service = cleanup_env["service"]

    await requests.insert_attempt(_req("pending-ok"))
    await requests.insert_attempt(_req("processed", status="processed"))
    await requests.insert_attempt(_req("graduate", profile="graduate"))
    await requests.insert_attempt(_req("wrong-group", group_id=GROUP_REDIRECT))
    await requests.insert_attempt(_req("invite", sub_type="invite"))
    await requests.insert_attempt(_req("no-flag", flag=""))

    preview = await service.preview(requests, settings)
    assert preview.group_id == GROUP_SOURCE
    assert preview.pending_count == 4
    assert preview.eligible_count == 1
    assert preview.reason
    assert len(preview.samples) == 1
    assert preview.samples[0].request_id == "pending-ok"


@pytest.mark.asyncio
async def test_list_overflow_cleanup_candidates_filters_pending_only(cleanup_env):
    requests = cleanup_env["requests"]
    settings = cleanup_env["settings"]

    await requests.insert_attempt(_req("a"))
    await requests.insert_attempt(_req("b", status="processed"))

    items = await list_overflow_cleanup_candidates(requests, settings)
    assert [item.id for item in items] == ["a"]


def test_is_overflow_cleanup_candidate_requires_undergraduate_add_flag():
    settings = _settings()
    assert is_overflow_cleanup_candidate(_req("ok"), settings)
    assert not is_overflow_cleanup_candidate(_req("grad", profile="graduate"), settings)
    assert not is_overflow_cleanup_candidate(_req("invite", sub_type="invite"), settings)
    assert not is_overflow_cleanup_candidate(_req("empty", flag=""), settings)


@pytest.mark.asyncio
async def test_confirm_calls_execute_qq_reject_for_each_request(cleanup_env):
    requests = cleanup_env["requests"]
    settings = cleanup_env["settings"]
    service = cleanup_env["service"]
    pipe = cleanup_env["pipeline"]

    await requests.insert_attempt(_req("one"))
    await requests.insert_attempt(_req("two"))

    with patch(
        "core.pipeline.execute_qq_reject",
        new_callable=AsyncMock,
        return_value=ActionResult(ok=True, retcode=0, status="ok", message="ok"),
    ) as execute_mock:
        result = await service.confirm(
            requests_store=requests,
            pipeline=pipe,
            settings=settings,
            admin_user_id="admin1",
        )

    assert execute_mock.await_count == 2
    assert result.requested == 2
    assert result.success == 2
    assert result.failed == 0
    sources = {call.kwargs["source"] for call in execute_mock.await_args_list}
    assert sources == {"overflow_cleanup"}
    reasons = [call.kwargs["reason"] for call in execute_mock.await_args_list]
    assert all(isinstance(text, str) and text for text in reasons)


@pytest.mark.asyncio
async def test_confirm_continues_when_single_item_fails(cleanup_env):
    requests = cleanup_env["requests"]
    settings = cleanup_env["settings"]
    service = cleanup_env["service"]
    pipe = cleanup_env["pipeline"]

    await requests.insert_attempt(_req("one"))
    await requests.insert_attempt(_req("two"))
    await requests.insert_attempt(_req("three"))

    outcomes = [
        ActionResult(ok=True, retcode=0, status="ok", message="ok"),
        ActionResult(ok=False, retcode=1, status="failed", message="boom"),
        ActionResult(ok=True, retcode=0, status="ok", message="ok"),
    ]

    with patch(
        "core.pipeline.execute_qq_reject",
        new_callable=AsyncMock,
        side_effect=outcomes,
    ) as execute_mock:
        result = await service.confirm(
            requests_store=requests,
            pipeline=pipe,
            settings=settings,
            admin_user_id="admin1",
        )

    assert execute_mock.await_count == 3
    assert result.requested == 3
    assert result.success == 2
    assert result.failed == 1
    assert len(result.lines) == 3
    assert result.lines[1].ok is False


@pytest.mark.asyncio
async def test_confirm_cleanup_lock_blocks_duplicate_run(cleanup_env):
    service = cleanup_env["service"]
    service._running = True
    result = await service.confirm(
        requests_store=cleanup_env["requests"],
        pipeline=cleanup_env["pipeline"],
        settings=cleanup_env["settings"],
        admin_user_id="admin1",
    )
    assert result.busy is True
    assert result.requested == 0
