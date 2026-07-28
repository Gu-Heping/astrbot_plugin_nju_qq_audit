import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import load_settings
from core.reject_executor import execute_qq_reject
from data_source.students import ActionResult, PendingRequest


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _pending(**overrides) -> PendingRequest:
    base = {
        "id": "req-1",
        "group_id": "796836121",
        "user_id": "12345",
        "comment": "test",
        "flag": "flag-1",
        "sub_type": "add",
        "parsed": {},
        "match": {},
        "decision": "reject",
        "confidence": 0,
        "reason": "",
        "mode": "auto",
        "status": "pending",
        "created_at": "2026-07-28T08:00:00+00:00",
    }
    base.update(overrides)
    return PendingRequest(**base)


@pytest.mark.asyncio
async def test_execute_qq_reject_is_single_api_entry():
    settings = load_settings(DummyConfig())
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, status="ok", message="ok")
    )
    req = _pending()
    result = await execute_qq_reject(
        actions,
        settings,
        req,
        reason="测试",
        source="blacklist",
    )
    assert result.ok
    actions.set_group_add_request.assert_awaited_once_with(
        "flag-1",
        "add",
        False,
        "测试",
        request_id="req-1",
        reject_source="blacklist",
        request_time="2026-07-28T08:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_execute_qq_reject_manual_skips_delay():
    settings = load_settings(DummyConfig({"auto_reject_delay_sec": 5}))
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, status="ok", message="ok")
    )
    req = _pending()
    with patch("core.reject_executor.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await execute_qq_reject(
            actions,
            settings,
            req,
            reason="手动",
            source="manual",
        )
        sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_reject_routes_through_execute_qq_reject(tmp_path):
    import sys
    from unittest.mock import MagicMock

    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api"].logger = MagicMock()

    from core.pipeline import AuditPipeline
    from data_source.students import ActionResult, PendingRequest
    from storage.audit_log import AuditLog
    from storage.requests_store import RequestsStore
    from storage.runtime_store import RuntimeStore
    from data_source.student_cache import StudentCache

    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    requests = RequestsStore(tmp_path / "manual_exec.json")
    audit = AuditLog(tmp_path / "manual_exec.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "manual_exec_runtime.json")
    cache = StudentCache(tmp_path / "manual_exec_cache")
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
    req = PendingRequest(
        id="req-manual-exec",
        group_id="796836121",
        user_id="12345",
        comment="test",
        flag="flag-manual-exec",
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0,
        reason="",
        mode="manual",
        status="pending",
        created_at="2026-07-28T08:00:00+00:00",
    )
    with patch(
        "core.pipeline.execute_qq_reject",
        new_callable=AsyncMock,
        return_value=ActionResult(ok=True, retcode=0, status="ok", message="ok"),
    ) as execute_mock:
        await pipe.admin_reject(req, "admin1", "手动理由")
    execute_mock.assert_awaited_once()
    assert execute_mock.await_args.kwargs["source"] == "manual"
    assert execute_mock.await_args.kwargs["reason"] == "手动理由"


@pytest.mark.asyncio
async def test_reject_for_policy_routes_through_execute_qq_reject(tmp_path):
    import sys
    from unittest.mock import MagicMock

    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api"].logger = MagicMock()

    from core.pipeline import AuditPipeline
    from data_source.students import ActionResult, PendingRequest
    from onebot.event_extract import GroupJoinRequest
    from storage.audit_log import AuditLog
    from storage.requests_store import RequestsStore
    from storage.runtime_store import RuntimeStore
    from data_source.student_cache import StudentCache

    settings = load_settings(DummyConfig({"target_group_ids": "796836121"}))
    requests = RequestsStore(tmp_path / "auto_exec.json")
    audit = AuditLog(tmp_path / "auto_exec.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "auto_exec_runtime.json")
    cache = StudentCache(tmp_path / "auto_exec_cache")
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
    pending = PendingRequest(
        id="req-auto-exec",
        group_id="796836121",
        user_id="67890",
        comment="test",
        flag="flag-auto-exec",
        sub_type="add",
        parsed={},
        match={},
        decision="reject",
        confidence=0,
        reason="overflow",
        mode="auto",
        status="pending",
        created_at="2026-07-28T08:00:00+00:00",
    )
    event = GroupJoinRequest(
        group_id="796836121",
        user_id="67890",
        comment="test",
        flag="flag-auto-exec",
        sub_type="add",
    )
    decision = MagicMock(decision="reject", reason="overflow")

    with patch(
        "core.pipeline.execute_qq_reject",
        new_callable=AsyncMock,
        return_value=ActionResult(ok=True, retcode=0, status="ok", message="ok"),
    ) as execute_mock:
        await pipe._reject_for_policy(
            pending,
            event,
            decision,
            policy_name="undergrad_overflow_reject",
            reject_reason="自动溢出拒绝",
            notify_title="overflow",
        )
    execute_mock.assert_awaited_once()
    assert execute_mock.await_args.kwargs["source"] == "undergrad_overflow_reject"
    assert execute_mock.await_args.kwargs["reason"] == "自动溢出拒绝"
