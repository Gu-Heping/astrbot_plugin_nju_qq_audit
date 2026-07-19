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
        user_id="261820094",
        comment="陈姝然 261820094",
        flag="legacy-flag",
        sub_type="add",
        parsed={"name": "陈姝然", "student_id": "261820094"},
        match={"strength": "strong"},
        decision="approve",
        confidence=0.95,
        reason="strong",
        mode="record-only",
        status="pending",
        created_at="2026-07-19T00:00:00+00:00",
        match_strength="strong",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


@pytest.mark.asyncio
async def test_stale_not_found_member_present_marks_external(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "796836121",
                "admin_notify": True,
                "admin_qq_ids": "111",
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(
            ok=False,
            retcode=100,
            message="matching group request not found",
        )
    )
    actions.get_group_member_info = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok", data={"user_id": "261820094"})
    )
    notifier = MagicMock()
    notifier.notify_external_handled = AsyncMock()
    notifier.notify_stale_request = AsyncMock()
    notifier.settings = settings
    pipe = AuditPipeline(settings, requests, audit, runtime, cache, actions, notifier)

    req = _pending()
    await requests.upsert(req)

    result = await pipe.admin_approve(req, "111")

    assert result.ok is False
    updated = await requests.get_by_id(req.id)
    assert updated.status == "external"
    assert updated.action_result.ok is True
    assert "已在群内" in (updated.action_result.message or "")
    notifier.notify_external_handled.assert_awaited_once()
    notifier.notify_stale_request.assert_not_awaited()
