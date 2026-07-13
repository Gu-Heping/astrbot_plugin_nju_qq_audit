"""v0.3.9 re-application after leave group tests."""

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
        status="external",
        processed_at="2026-07-09T01:00:00+00:00",
        action_result=ActionResult(ok=True, message="external"),
        created_at="2026-07-09T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def _event(**kwargs) -> GroupJoinRequest:
    defaults = dict(
        group_id="796836121",
        user_id="2492835361",
        comment="张三20260002",
        flag="flag-1",
        sub_type="add",
    )
    defaults.update(kwargs)
    return GroupJoinRequest(**defaults)


def _pipeline(tmp_path, *, in_group: bool | None = False):
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "admin_notify": False})
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    if in_group is False:
        member = ActionResult(ok=False, retcode=1, message="not in group")
    elif in_group is True:
        member = ActionResult(
            ok=True, retcode=0, message="ok", data={"user_id": "2492835361"}
        )
    else:
        member = ActionResult(ok=False, retcode=1, message="api error")
    actions.get_group_member_info = AsyncMock(return_value=member)
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, MagicMock()
    )
    return pipe, requests, audit, actions


@pytest.mark.asyncio
async def test_external_same_flag_always_reapplies_on_group_request(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path, in_group=True)
    old = _pending(id="REQ-old")
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(old.id)).status == "external"
    current = await requests.get_by_flag("flag-1")
    assert current is not None
    assert current.id != old.id
    assert current.status == "pending"
    assert any(r.get("type") == "reapplication_after_terminal" for r in audit.read_all())


@pytest.mark.asyncio
async def test_external_same_flag_user_left_creates_new_pending(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path, in_group=False)
    old = _pending(id="REQ-old")
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(old.id)).status == "external"
    current = await requests.get_by_flag("flag-1")
    assert current is not None
    assert current.id != old.id
    assert current.status == "pending"
    assert any(r.get("type") == "reapplication_after_terminal" for r in audit.read_all())


@pytest.mark.asyncio
async def test_external_same_flag_user_still_in_group_still_reapplies(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path, in_group=True)
    old = _pending(id="REQ-old")
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    current = await requests.get_by_flag("flag-1")
    assert current is not None
    assert current.id != old.id
    assert current.status == "pending"
    assert any(r.get("type") == "reapplication_after_terminal" for r in audit.read_all())


@pytest.mark.asyncio
async def test_processed_same_flag_still_ignored_after_leave(tmp_path):
    pipe, requests, audit, _ = _pipeline(tmp_path, in_group=False)
    old = _pending(
        id="REQ-old",
        status="processed",
        action_result=ActionResult(ok=False, message="reject"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(old.id)).status == "processed"
    assert await requests.get_by_flag("flag-1") is not None
    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())
