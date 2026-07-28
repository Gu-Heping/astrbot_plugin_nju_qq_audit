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
