"""v0.3.8 duplicate flag / terminal never reapply tests."""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
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
        comment="张三",
        flag="flag-1",
        sub_type="add",
        parsed={"name": "张三"},
        match={},
        decision="manual_review",
        confidence=0.5,
        reason="仅姓名，信息不足",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _event(**kwargs) -> GroupJoinRequest:
    defaults = dict(
        group_id="796836121",
        user_id="2492835361",
        comment="张三",
        flag="flag-1",
        sub_type="add",
    )
    defaults.update(kwargs)
    return GroupJoinRequest(**defaults)


def _pipeline(tmp_path, *, in_group: bool | None = None):
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "admin_notify": False})
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    if in_group is not None:
        if in_group:
            member = ActionResult(
                ok=True, retcode=0, message="ok", data={"user_id": "2492835361"}
            )
        else:
            member = ActionResult(ok=False, retcode=1, message="not in group")
        actions.get_group_member_info = AsyncMock(return_value=member)
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, MagicMock()
    )
    return pipe, requests, audit


@pytest.mark.asyncio
async def test_processed_same_flag_always_ignored(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    req = _pending(
        status="processed",
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(ok=True, message="ok"),
    )
    await requests.upsert(req)

    await pipe.handle_group_request(_event())
    await pipe.handle_group_request(_event(comment="不同验证内容"))

    updated = await requests.get_by_id(req.id)
    assert updated.status == "processed"
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_stale_same_flag_user_still_in_group_ignored(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path, in_group=True)
    req = _pending(
        id="REQ-stale",
        status="stale",
        action_result=ActionResult(ok=False, message="flag expired"),
    )
    await requests.upsert(req)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(req.id)).status == "stale"
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_processed_same_flag_different_comment_ignored(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    req = _pending(
        status="processed",
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(ok=False, message="信息不完整"),
    )
    await requests.upsert(req)

    await pipe.handle_group_request(_event(comment="张三20260002"))

    assert (await requests.get_by_id(req.id)).status == "processed"
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_pending_same_flag_same_comment_noop(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    before = audit.read_all()

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(req.id)).status == "pending"
    assert len(audit.read_all()) == len(before)


@pytest.mark.asyncio
async def test_pending_same_flag_changed_comment_logged_not_updated(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    req = _pending(comment="张三", retry_count=2)
    await requests.upsert(req)

    await pipe.handle_group_request(_event(comment="张三20260002"))

    updated = await requests.get_by_id(req.id)
    assert updated.status == "pending"
    assert updated.comment == "张三"
    assert updated.retry_count == 2
    assert any(r.get("type") == "duplicate_pending_comment_changed" for r in audit.read_all())


@pytest.mark.asyncio
async def test_new_flag_after_processed_creates_new_request(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    old = _pending(
        id="REQ-old",
        flag="flag-old",
        status="processed",
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(ok=False, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event(flag="flag-new", comment="张三20260002"))

    new = await requests.get_by_flag("flag-new")
    assert new is not None
    assert new.id != old.id
    assert new.status == "pending"
    assert (await requests.get_by_id(old.id)).status == "processed"


@pytest.mark.asyncio
async def test_new_flag_supersedes_old_pending(tmp_path):
    pipe, requests, _ = _pipeline(tmp_path)
    old = _pending(id="REQ-old", flag="flag-old")
    await requests.upsert(old)

    await pipe.handle_group_request(_event(flag="flag-new", comment="张三20260002"))

    superseded = await requests.get_by_flag("flag-old")
    assert superseded.status == "ignored"
    active = await requests.get_by_flag("flag-new")
    assert active is not None
    assert active.status == "pending"
