import sys
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from admin.notify import AdminNotifier
from config import load_settings
from storage.admin_session_store import AdminSessionStore
from storage.list_cache import AdminListCacheStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _install_mock_astrbot():
    mock_event = MagicMock()
    chain = MagicMock()
    mock_event.MessageChain.return_value = chain
    chain.message.return_value = chain
    sys.modules.setdefault("astrbot", MagicMock())
    sys.modules.setdefault("astrbot.api", MagicMock())
    sys.modules["astrbot.api.event"] = mock_event
    return chain


@pytest.mark.asyncio
async def test_notify_uses_context_send_message_without_http(tmp_path):
    _install_mock_astrbot()
    settings = load_settings(DummyConfig({"admin_qq_ids": "111", "onebot_http_url": ""}))
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    await store.record("111", "aiocqhttp:FriendMessage:111")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock()
    notifier = AdminNotifier(settings, actions, context, store, lambda: None)
    await notifier.notify_auto_result(
        request_id="r1",
        group_id="g1",
        user_id="u1",
        ok=True,
        reason="test",
    )
    context.send_message.assert_awaited_once()
    actions.send_private_msg_safe.assert_not_called()


@pytest.mark.asyncio
async def test_manual_review_notifies_when_admin_is_applicant(tmp_path):
    _install_mock_astrbot()
    user_id = "2492835361"
    settings = load_settings(
        DummyConfig({"admin_qq_ids": user_id, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    await store.record(user_id, "aiocqhttp:FriendMessage:2492835361")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    notifier = AdminNotifier(settings, MagicMock(), context, store, lambda: None)
    await notifier.notify_manual_review(
        request_id="REQ-test",
        group_id="796836121",
        user_id=user_id,
        comment="test",
        parsed={},
        reason="manual",
    )
    context.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_review_notice_includes_short_commands(tmp_path):
    settings = load_settings(DummyConfig({"admin_qq_ids": "111", "onebot_http_url": ""}))
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    list_cache = AdminListCacheStore(tmp_path / "list_cache.json")
    http_client = MagicMock()
    http_client.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True))
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    notifier = AdminNotifier(
        settings, MagicMock(), context, store, lambda: http_client, list_cache
    )
    await notifier.notify_manual_review(
        request_id="REQ-test123",
        group_id="796836121",
        user_id="2492835361",
        comment="李四 计算机类",
        parsed={"name": "李四"},
        reason="姓名+专业弱匹配",
    )
    message = http_client.send_private_msg_safe.await_args.args[1]
    assert "/audit view 1" in message
    assert "/audit ok 1" in message
    assert "/audit no 1" in message
    assert "flag" not in message
    assert "REQ-test123" not in message
    assert list_cache.resolve("111", 1) == "REQ-test123"


@pytest.mark.asyncio
async def test_notify_falls_back_to_http_when_no_umo(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "admin_qq_ids": "111",
                "onebot_http_url": "http://127.0.0.1:3000",
            }
        )
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    http_client = MagicMock()
    http_client.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True))
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=False, message="fail"))
    notifier = AdminNotifier(settings, actions, context, store, lambda: http_client)
    await notifier.notify_manual_review(
        request_id="r1",
        group_id="g1",
        user_id="u1",
        comment="hello",
        parsed={},
        reason="manual",
    )
    actions.send_private_msg_safe.assert_awaited_once_with("111", ANY)
    http_client.send_private_msg_safe.assert_awaited_once_with("111", ANY)
    context.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notify_falls_back_to_adapter_when_no_umo(tmp_path):
    settings = load_settings(DummyConfig({"admin_qq_ids": "111", "onebot_http_url": ""}))
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    notifier = AdminNotifier(settings, actions, context, store, lambda: None)
    await notifier.notify_manual_review(
        request_id="r1",
        group_id="g1",
        user_id="u1",
        comment="hello",
        parsed={},
        reason="manual",
    )
    actions.send_private_msg_safe.assert_awaited_once_with("111", ANY)
    context.send_message.assert_not_called()
