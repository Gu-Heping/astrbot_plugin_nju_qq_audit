import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import load_settings
from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _make_client(platform=None):
    context = MagicMock()
    context.get_platform.return_value = platform
    settings = load_settings(DummyConfig())
    return AstrBotAdapterActionClient(context, settings)


@pytest.mark.asyncio
async def test_set_group_add_request_calls_adapter_api():
    client = _make_client(MagicMock())
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot
    result = await client.set_group_add_request("flag123", "add", True, "ok")
    assert result.ok
    bot.api.call_action.assert_awaited_once_with(
        "set_group_add_request",
        flag="flag123",
        sub_type="add",
        approve=True,
        reason="ok",
    )


@pytest.mark.asyncio
async def test_probe_api_uses_get_login_info():
    client = _make_client(MagicMock())
    bot = MagicMock()
    bot.api.call_action = AsyncMock(
        return_value={
            "status": "ok",
            "retcode": 0,
            "data": {"user_id": 12345, "nickname": "bot"},
        }
    )

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot
    probe = await client.probe_api()
    assert probe["adapter_action_available"] == "yes"
    assert probe["test_action"] == "get_login_info"
    assert probe["user_id"] == 12345
    assert probe["nickname"] == "bot"


@pytest.mark.asyncio
async def test_probe_api_no_adapter():
    client = _make_client(None)

    async def fake_get_bot(event=None):
        return None

    client._get_bot_client = fake_get_bot
    probe = await client.probe_api()
    assert probe["adapter_found"] == "no"
    assert probe["adapter_action_available"] == "no"


@pytest.mark.asyncio
async def test_remember_event_bot_is_used():
    client = _make_client(None)
    bot = MagicMock()
    bot.api.call_action = AsyncMock(
        return_value={"status": "ok", "retcode": 0, "data": {"user_id": 1}}
    )
    event = MagicMock()
    event.bot = bot
    event.get_platform_id.return_value = "aiocqhttp_1"
    client.remember_event(event)
    result = await client.get_login_info()
    assert result.ok
    bot.api.call_action.assert_awaited_once_with("get_login_info")


@pytest.mark.asyncio
async def test_send_private_msg_safe_calls_adapter_api():
    client = _make_client(MagicMock())
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})

    async def fake_get_bot(event=None):
        return bot

    client._get_bot_client = fake_get_bot
    result = await client.send_private_msg_safe("123456", "hello")
    assert result.ok
    bot.api.call_action.assert_awaited_once_with(
        "send_private_msg",
        user_id=123456,
        message="hello",
    )


@pytest.mark.asyncio
async def test_get_platform_inst_fallback():
    client = _make_client(None)
    bot = MagicMock()
    bot.api.call_action = AsyncMock(return_value={"status": "ok", "retcode": 0, "data": {}})
    platform = MagicMock()
    platform.meta.return_value = MagicMock(name="aiocqhttp", id="aiocqhttp_1")
    platform.get_client.return_value = bot
    client.astrbot_context.get_platform_inst = MagicMock(return_value=platform)
    client._platform_id = "aiocqhttp_1"
    result = await client.set_group_add_request("flag123", "add", True, "ok")
    assert result.ok
    client.astrbot_context.get_platform_inst.assert_called_once_with("aiocqhttp_1")
