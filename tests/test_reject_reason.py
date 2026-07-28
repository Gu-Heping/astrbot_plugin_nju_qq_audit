import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from config import load_settings
from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient
from onebot.http_actions import HttpActionClient
from onebot.reject_reason import normalize_qq_reject_reason


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
