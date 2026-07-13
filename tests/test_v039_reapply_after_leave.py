"""v0.3.14+ terminal reapply policy tests."""

import sys
from datetime import datetime
from unittest.mock import MagicMock

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


def _unix_after(iso: str, seconds: int) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp()) + seconds


def _event(**kwargs) -> GroupJoinRequest:
    raw = kwargs.pop("raw_event", None)
    if raw is None and "time" in kwargs:
        raw = {"time": kwargs.pop("time")}
    defaults = dict(
        group_id="796836121",
        user_id="2492835361",
        comment="张三20260002",
        flag="flag-1",
        sub_type="add",
    )
    defaults.update(kwargs)
    return GroupJoinRequest(
        group_id=defaults["group_id"],
        user_id=defaults["user_id"],
        comment=defaults["comment"],
        flag=defaults["flag"],
        sub_type=defaults["sub_type"],
        raw_event=raw,
    )


def _pipeline(tmp_path):
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "admin_notify": False})
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, MagicMock()
    )
    return pipe, requests, audit


@pytest.mark.asyncio
async def test_external_same_flag_reapplies_with_new_event(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    old = _pending(id="REQ-old")
    await requests.upsert(old)
    await requests.update_by_id(
        old.id,
        {
            "status": "external",
            "processed_at": "2026-07-09T01:00:00+00:00",
            "action_result": {"ok": True, "message": "external"},
        },
    )

    await pipe.handle_group_request(
        _event(
            comment="入群申请测试",
            time=_unix_after("2026-07-09T01:00:00+00:00", 3600),
        )
    )

    latest = await requests.get_by_flag("flag-1")
    assert latest.id != old.id
    assert latest.status == "pending"
    assert latest.reapply_of == old.id
    assert (await requests.get_by_id(old.id)).status == "external"
    assert any(r.get("type") == "reapplication_created" for r in audit.read_all())


@pytest.mark.asyncio
async def test_ignored_same_flag_never_reapplies(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    old = _pending(id="REQ-old", status="ignored", flag="flag-1")
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(old.id)).status == "ignored"
    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_stale_same_flag_never_reapplies(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    old = _pending(
        id="REQ-old",
        status="stale",
        action_result=ActionResult(ok=False, message="flag expired"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event())

    assert (await requests.get_by_id(old.id)).status == "stale"
    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(r.get("type") == "duplicate_request_ignored" for r in audit.read_all())


@pytest.mark.asyncio
async def test_processed_same_flag_debounce_blocks_immediate_replay(tmp_path):
    pipe, requests, audit = _pipeline(tmp_path)
    from storage.audit_log import utc_now_iso

    old = _pending(
        id="REQ-old",
        status="processed",
        decision="approve",
        comment="张三20260002",
        processed_at=utc_now_iso(),
        action_result=ActionResult(ok=True, message="ok"),
    )
    await requests.upsert(old)

    await pipe.handle_group_request(_event(comment="张三20260002"))

    assert (await requests.get_by_id(old.id)).status == "processed"
    assert (await requests.get_by_flag("flag-1")).id == old.id
    assert any(
        r.get("type") == "duplicate_event_replayed"
        and r.get("reason") == "reapply_burst_after_terminal"
        for r in audit.read_all()
    )
