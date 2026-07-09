import asyncio

import aiohttp
from aiohttp import web

from config import load_settings
from onebot.http_actions import HttpActionClient, OneBotHttpActions, build_action_url


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


async def _run_server():
    async def handler(request):
        body = await request.json()
        if request.headers.get("Authorization") != "Bearer test-token":
            return web.json_response({"status": "failed", "retcode": 401}, status=401)
        return web.json_response({"status": "ok", "retcode": 0, "data": body})

    app = web.Application()
    app.router.add_post("/send_private_msg", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def test_build_action_url():
    assert build_action_url("http://127.0.0.1:3000/", "send_private_msg") == (
        "http://127.0.0.1:3000/send_private_msg"
    )


def test_call_action_success():
    async def _test():
        runner, base = await _run_server()
        try:
            settings = load_settings(
                DummyConfig(
                    {
                        "onebot_http_url": base,
                        "onebot_access_token": "test-token",
                        "http_retries": 0,
                    }
                )
            )
            actions = HttpActionClient(settings)
            await actions.start()
            result = await actions.send_private_msg("123456", "hello")
            assert result.ok
        finally:
            await runner.cleanup()

    asyncio.run(_test())


def test_http_client_alias():
    assert OneBotHttpActions is HttpActionClient


def test_empty_http_url_returns_error():
    async def _test():
        settings = load_settings(
            DummyConfig({"onebot_action_backend": "http", "onebot_http_url": ""})
        )
        actions = HttpActionClient(settings)
        result = await actions.set_group_add_request("f", "add", True)
        assert not result.ok
        assert "empty" in (result.message or "").lower()

    asyncio.run(_test())
