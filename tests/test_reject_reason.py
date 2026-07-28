import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from config import load_settings
from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient
from onebot.http_actions import HttpActionClient
from onebot.reject_reason import normalize_qq_reject_reason, resolve_qq_reject_reason
from admin.labels import DEFAULT_REJECT_REASON


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("你好", "你好"),
        ('"你好"', "你好"),
        ("\"你好\"", "你好"),
        ("  你好  ", "你好"),
        ('""', ""),
        ("", ""),
    ],
)
def test_normalize_qq_reject_reason_unwraps_quotes(raw, expected):
    assert normalize_qq_reject_reason(raw) == expected


def test_load_settings_normalizes_quoted_blacklist_reason():
    settings = load_settings(
        DummyConfig({"blacklist_reject_reason": '"你好"'})
    )
    assert settings.blacklist_reject_reason == "你好"


@pytest.mark.asyncio
async def test_adapter_set_group_add_request_sends_unquoted_reason():
    context = MagicMock()
    settings = load_settings(DummyConfig())
    client = AstrBotAdapterActionClient(context, settings)
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot
    await client.set_group_add_request("flag123", "add", False, '"你好"')
    bot.api.call_action.assert_awaited_once_with(
        "set_group_add_request",
        flag="flag123",
        sub_type="add",
        approve=False,
        reason="你好",
    )


def test_http_set_group_add_request_sends_unquoted_reason():
    async def _run():
        async def handler(request):
            body = await request.json()
            return web.json_response({"status": "ok", "retcode": 0, "data": body})

        app = web.Application()
        app.router.add_post("/set_group_add_request", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            settings = load_settings(
                DummyConfig(
                    {
                        "onebot_http_url": f"http://127.0.0.1:{port}",
                        "http_retries": 0,
                    }
                )
            )
            actions = HttpActionClient(settings)
            await actions.start()
            result = await actions.set_group_add_request(
                "flag123", "add", False, '"你好"'
            )
            assert result.ok
            assert result.data["reason"] == "你好"
        finally:
            await runner.cleanup()

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_blacklist_auto_reject_sends_unquoted_config_reason(tmp_path):
    import sys
    from unittest.mock import MagicMock

    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api"].logger = MagicMock()

    from config import load_settings
    from core.pipeline import AuditPipeline
    from data_source.student_cache import StudentCache
    from data_source.students import ActionResult, Student
    from onebot.event_extract import GroupJoinRequest
    from storage.audit_log import AuditLog
    from storage.blacklist_store import BlacklistStore
    from storage.requests_store import RequestsStore
    from storage.runtime_store import RuntimeStore

    group = "796836121"
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": group,
                "mode": "record-only",
                "admin_notify": False,
                "student_source": "mock",
                "blacklist_enabled": True,
                "blacklist_auto_reject": True,
                "blacklist_reject_reason": '"你好"',
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261880001",
                name="测试",
                student_id="261880001",
                notice_no="20260001",
                major="计算机类",
                status="已确认",
                updated_at="2026-07-22T00:00:00+00:00",
            )
        ]
    )
    blacklist = BlacklistStore(tmp_path / "blacklist.json")
    await blacklist.add(
        kind="user_id",
        value="12345",
        reason="测试",
        created_by="admin",
    )
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
        blacklist_store=blacklist,
    )
    event = GroupJoinRequest(
        group_id=group,
        user_id="12345",
        comment="测试 261880001",
        flag="flag-bl-quoted",
        sub_type="add",
    )
    await pipe._audit_and_act(event)
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert call.args[3] == "你好"
    assert call.args[3] != '"你好"'


def test_resolve_qq_reject_reason_falls_back_when_empty():
    assert resolve_qq_reject_reason("") == DEFAULT_REJECT_REASON
    assert resolve_qq_reject_reason('""') == DEFAULT_REJECT_REASON
    assert resolve_qq_reject_reason("测试") == "测试"


@pytest.mark.asyncio
async def test_blacklist_auto_reject_uses_dispatch_fallback_when_config_reason_empty(
    tmp_path,
):
    import sys
    from unittest.mock import MagicMock

    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api"].logger = MagicMock()

    from core.pipeline import AuditPipeline
    from data_source.student_cache import StudentCache
    from data_source.students import ActionResult, Student
    from onebot.event_extract import GroupJoinRequest
    from storage.audit_log import AuditLog
    from storage.blacklist_store import BlacklistStore
    from storage.requests_store import RequestsStore
    from storage.runtime_store import RuntimeStore

    group = "796836121"
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": group,
                "mode": "auto",
                "admin_notify": False,
                "student_source": "mock",
                "blacklist_enabled": True,
                "blacklist_auto_reject": True,
            }
        )
    )
    settings.blacklist_reject_reason = ""
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261880001",
                name="测试",
                student_id="261880001",
                notice_no="20260001",
                major="计算机类",
                status="已确认",
                updated_at="2026-07-22T00:00:00+00:00",
            )
        ]
    )
    blacklist = BlacklistStore(tmp_path / "blacklist.json")
    await blacklist.add(
        kind="user_id",
        value="12345",
        reason="测试",
        created_by="admin",
    )
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
        blacklist_store=blacklist,
    )
    event = GroupJoinRequest(
        group_id=group,
        user_id="12345",
        comment="测试 261880001",
        flag="flag-bl-empty-reason",
        sub_type="add",
    )
    await pipe._audit_and_act(event)
    call = actions.set_group_add_request.await_args
    assert call.args[2] is False
    assert call.args[3] == DEFAULT_REJECT_REASON
    assert call.args[3] != ""


@pytest.mark.asyncio
async def test_admin_reject_and_auto_reject_share_reason_resolution(tmp_path):
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
    requests = RequestsStore(tmp_path / "requests2.json")
    audit = AuditLog(tmp_path / "audit2.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime2.json")
    cache = StudentCache(tmp_path / "cache2")
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
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
        id="req-1",
        group_id="796836121",
        user_id="12345",
        comment="test",
        flag="flag-manual",
        sub_type="add",
        parsed={},
        match={},
        decision="manual_review",
        confidence=0,
        reason="",
        mode="manual",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )
    await requests.insert_attempt(req)
    await pipe.admin_reject(req, "admin", "")
    manual_call = actions.set_group_add_request.await_args
    assert manual_call.args[3] == DEFAULT_REJECT_REASON

    req2 = PendingRequest(
        id="req-2",
        group_id="796836121",
        user_id="67890",
        comment="test",
        flag="flag-auto",
        sub_type="add",
        parsed={},
        match={},
        decision="reject",
        confidence=0,
        reason="",
        mode="auto",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )
    await pipe._dispatch_reject(req2, reason="", source="blacklist", decision="reject")
    auto_call = actions.set_group_add_request.await_args
    assert auto_call.args[3] == DEFAULT_REJECT_REASON


@pytest.mark.asyncio
async def test_auto_reject_delay_applied_only_for_auto_dispatch(tmp_path):
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api"].logger = MagicMock()

    from core.pipeline import AuditPipeline
    from data_source.students import ActionResult, PendingRequest
    from storage.audit_log import AuditLog
    from storage.requests_store import RequestsStore
    from storage.runtime_store import RuntimeStore
    from data_source.student_cache import StudentCache

    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "auto_reject_delay_sec": 2})
    )
    requests = RequestsStore(tmp_path / "delay.json")
    audit = AuditLog(tmp_path / "delay.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "delay_runtime.json")
    cache = StudentCache(tmp_path / "delay_cache")
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
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
        id="req-delay",
        group_id="796836121",
        user_id="12345",
        comment="test",
        flag="flag-delay",
        sub_type="add",
        parsed={},
        match={},
        decision="reject",
        confidence=0,
        reason="",
        mode="auto",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )
    with patch("core.pipeline.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await pipe._dispatch_reject(
            req, reason="自动拒绝测试", source="blacklist", decision="reject"
        )
        sleep_mock.assert_awaited_once_with(2.0)

    await pipe.admin_reject(req, "admin", "手动拒绝")
    assert actions.set_group_add_request.await_count == 2


@pytest.mark.asyncio
async def test_auto_reject_zero_delay_skips_sleep(tmp_path):
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

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
    assert settings.auto_reject_delay_sec == 0
    requests = RequestsStore(tmp_path / "delay0.json")
    audit = AuditLog(tmp_path / "delay0.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "delay0_runtime.json")
    cache = StudentCache(tmp_path / "delay0_cache")
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
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
        id="req-delay0",
        group_id="796836121",
        user_id="12345",
        comment="test",
        flag="flag-delay0",
        sub_type="add",
        parsed={},
        match={},
        decision="reject",
        confidence=0,
        reason="",
        mode="auto",
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )
    with patch("core.pipeline.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await pipe._dispatch_reject(
            req, reason="测试", source="undergrad_exclusive_reject", decision="reject"
        )
        sleep_mock.assert_not_awaited()
