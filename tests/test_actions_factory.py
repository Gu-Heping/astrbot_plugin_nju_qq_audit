import asyncio
from unittest.mock import MagicMock

from config import load_settings
from onebot.actions import create_action_client, create_http_notify_client
from onebot.astrbot_adapter_actions import AstrBotAdapterActionClient
from onebot.http_actions import HttpActionClient


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_factory_uses_adapter_by_default():
    settings = load_settings(DummyConfig())
    client = create_action_client(MagicMock(), settings)
    assert isinstance(client, AstrBotAdapterActionClient)
    assert client.backend_name() == "astrbot_adapter"


def test_factory_uses_http_when_configured():
    settings = load_settings(
        DummyConfig(
            {
                "onebot_action_backend": "http",
                "onebot_http_url": "http://127.0.0.1:3000",
            }
        )
    )
    client = create_action_client(MagicMock(), settings)
    assert isinstance(client, HttpActionClient)
    assert client.backend_name() == "http"


def test_http_notify_client_none_without_url():
    settings = load_settings(DummyConfig())
    assert create_http_notify_client(settings) is None


def test_http_notify_client_created_with_url():
    settings = load_settings(DummyConfig({"onebot_http_url": "http://127.0.0.1:3000"}))
    client = create_http_notify_client(settings)
    assert isinstance(client, HttpActionClient)


def test_adapter_client_does_not_open_http_session():
    async def _test():
        settings = load_settings(DummyConfig())
        client = create_action_client(MagicMock(), settings)
        await client.start()
        assert not hasattr(client, "_session") or getattr(client, "_session", None) is None
        await client.close()

    asyncio.run(_test())
