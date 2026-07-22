"""Terminal QQ already-agree / already-refuse action handling."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.action_error import classify_action_failure
from admin.grad_release import GradReleaseService, format_grad_release_result
from admin.release import ReleaseService, format_release_result
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from storage.audit_log import AuditLog
from storage.blacklist_store import BlacklistStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP = "796836121"
GRAD_GROUP = "200"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_classify_already_agree_by_self():
    msg = "OIDB error 120162004 on 0x10c8_1: already agree msg by self"
    assert classify_action_failure(msg, 100).kind == "ALREADY_APPROVED"


def test_classify_already_refuse():
    msg = "OIDB error 120162003 on 0x10c8_1: already refuse msg"
    assert classify_action_failure(msg, 100).kind == "ALREADY_REFUSED"


def _pipeline(tmp_path, *, actions=None, settings=None):
    settings = settings or load_settings(
        DummyConfig(
            {
                "target_group_ids": GROUP,
                "grad_enabled": True,
                "grad_target_group_ids": GRAD_GROUP,
                "admin_notify": False,
                "batch_approve_interval_ms": 0,
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    blacklist = BlacklistStore(tmp_path / "blacklist.json")
    if actions is None:
        actions = MagicMock()
        actions.set_group_add_request = AsyncMock()
        actions.get_group_member_info = AsyncMock(
            return_value=ActionResult(ok=False, message="not found")
        )
        actions.get_group_system_msg = AsyncMock(side_effect=Exception("skip"))
    notifier = MagicMock()
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        notifier,
        blacklist_store=blacklist,
    )
    return pipe, requests, audit, actions, settings


def _strong_under(**kwargs) -> PendingRequest:
    defaults = dict(
        id="REQ-u1",
        group_id=GROUP,
        user_id="111",
        comment="张三 261220001",
        flag="flag-u1",
        sub_type="add",
        profile="undergraduate",
        parsed={"name": "张三", "student_id": "261220001"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.9,
        reason="强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _strong_grad(**kwargs) -> PendingRequest:
    defaults = dict(
        id="REQ-g1",
        group_id=GRAD_GROUP,
        user_id="222",
        comment="李四 生物学 博士",
        flag="flag-g1",
        sub_type="add",
        profile="graduate",
        parsed={
            "name": "李四",
            "admission_type": "博士",
            "major_text": "生物学",
        },
        match={"strength": "strong", "candidate_count": 1},
        decision="approve",
        confidence=0.9,
        reason="研究生强匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


@pytest.mark.asyncio
async def test_admin_approve_already_agree_by_self_processed(tmp_path):
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162004 on 0x10c8_1: already agree msg by self",
        )
    )
    actions.get_group_system_msg = AsyncMock(side_effect=Exception("skip"))
    pipe, requests, audit, _, settings = _pipeline(tmp_path, actions=actions)
    req = _strong_under()
    await requests.upsert(req)

    result = await pipe.admin_approve(req, "admin")
    assert result.ok is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "processed"
    assert updated.action_result.ok is True
    assert any(r.get("type") == "action_already_approved" for r in audit.read_all())

    service = ReleaseService()
    batch = await service.run_batch(
        requests_store=requests,
        pipeline=pipe,
        settings=settings,
        admin_user_id="admin",
        count=1,
        audit_log=audit,
        skip_rematch=True,
    )
    # already processed: nothing releasable left / no failed retry
    assert batch is not None
    assert batch.failed == 0


@pytest.mark.asyncio
async def test_admin_approve_already_refuse_dismissed(tmp_path):
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162003 on 0x10c8_1: already refuse msg",
        )
    )
    actions.get_group_system_msg = AsyncMock(side_effect=Exception("skip"))
    pipe, requests, audit, _, settings = _pipeline(tmp_path, actions=actions)
    req = _strong_under()
    await requests.upsert(req)

    result = await pipe.admin_approve(req, "admin")
    assert result.ok is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "dismissed"
    assert "QQ 侧显示该申请已被拒绝" in (updated.dismiss_reason or "")
    assert updated.action_result.ok is True
    assert any(r.get("type") == "action_already_refused" for r in audit.read_all())


@pytest.mark.asyncio
async def test_release_counts_already_refuse_as_dismissed_not_failed(tmp_path):
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162003 on 0x10c8_1: already refuse msg",
        )
    )
    actions.get_group_system_msg = AsyncMock(side_effect=Exception("skip"))
    pipe, requests, audit, _, settings = _pipeline(tmp_path, actions=actions)
    await requests.upsert(_strong_under())

    result = await ReleaseService().run_batch(
        requests_store=requests,
        pipeline=pipe,
        settings=settings,
        admin_user_id="admin",
        count=1,
        audit_log=audit,
        skip_rematch=True,
    )
    assert result is not None
    assert result.dismissed_count == 1
    assert result.failed == 0
    text = format_release_result(result, settings)
    assert "QQ 侧已拒绝，已移出队列" in text
    assert "已拒绝/已关闭：1" in text


@pytest.mark.asyncio
async def test_grad_release_handles_already_approved(tmp_path):
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="OIDB error 120162004 on 0x10c8_1: already agree msg by self",
        )
    )
    actions.get_group_system_msg = AsyncMock(side_effect=Exception("skip"))
    pipe, requests, audit, _, settings = _pipeline(tmp_path, actions=actions)
    await requests.upsert(_strong_grad())

    result = await GradReleaseService().run_batch(
        requests_store=requests,
        pipeline=pipe,
        settings=settings,
        admin_user_id="admin",
        count=1,
        audit_log=audit,
        skip_rematch=True,
    )
    assert result is not None
    assert result.failed == 0
    assert result.already_approved_count == 1 or result.success == 0
    text = format_grad_release_result(result, settings)
    assert "已同意" in text or "QQ 侧已同意" in text
