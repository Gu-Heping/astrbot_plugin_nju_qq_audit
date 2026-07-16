import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()
sys.modules.setdefault("astrbot.api.event", MagicMock())


def _reset_messagechain_stub():
    """Reset MessageChain mock for stable message assertions in full-suite."""
    event_mod = sys.modules["astrbot.api.event"]
    chain = MagicMock()
    event_mod.MessageChain.return_value = chain
    chain.message.side_effect = lambda msg: msg

from admin.notify import AdminNotifier
from config import load_settings
from storage.admin_session_store import AdminSessionStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)
@pytest.mark.asyncio
async def test_external_notice_human_labels_and_no_tech_fields(tmp_path):
    _reset_messagechain_stub()
    admin_id = "111"
    group_id = "826811581"
    user_id = "2875324318"
    operator_id = "3905536442"
    request_id = "REQ-063f1781"

    # Mock DisplayContext to provide labels.
    display = MagicMock()
    display.get_group_label = AsyncMock(
        return_value=f"南京大学 2026 本科新生群（{group_id}）"
    )

    async def _get_user_label(_group_id: str, uid: str, _parsed=None):
        if uid == user_id:
            return f"昵称（{uid}）"
        if uid == operator_id:
            return f"昵称（{uid}）"
        return uid

    display.get_user_label = AsyncMock(side_effect=_get_user_label)

    settings = load_settings(
        DummyConfig(
            {"admin_qq_ids": admin_id, "admin_notify": True, "onebot_http_url": ""}
        )
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    await store.record(admin_id, f"aiocqhttp:FriendMessage:{admin_id}")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    actions = MagicMock()

    notifier = AdminNotifier(
        settings,
        actions,
        context,
        store,
        lambda: None,
        display=display,
    )
    await notifier.notify_external_handled(
        request_id=request_id,
        group_id=group_id,
        user_id=user_id,
        summary="张轩玮",
        comment="张轩玮 261108002 德语",
        operator_id=operator_id,
        notice_sub_type="invite",
    )

    context.send_message.assert_awaited_once()
    msg = context.send_message.await_args.args[1]
    lower = str(msg).lower()

    assert "[入群审核] QQ 侧已通过 ✅" in msg
    assert "申请人：" in msg
    assert "张轩玮 / 261108002 / 德语" in msg
    assert f"QQ：昵称（{user_id}）" in msg
    assert f"群：南京大学 2026 本科新生群（{group_id}）" in msg
    assert "验证：张轩玮，261108002，德语" in msg
    assert "处理：已从待处理列表移除，无需重复审批" in msg
    assert f"QQ侧处理人：昵称（{operator_id}）" in msg
    assert f"/audit view {request_id}" in msg

    assert "external" not in lower
    assert "request_id:" not in lower
    assert "group_id:" not in lower
    assert "user_id:" not in lower
    assert "sub_type" not in lower

    assert "flag" not in lower
    assert "token" not in lower
    assert "raw_event" not in lower


@pytest.mark.asyncio
async def test_external_notice_degrades_when_display_fails(tmp_path):
    _reset_messagechain_stub()
    admin_id = "111"
    group_id = "826811581"
    user_id = "2875324318"
    operator_id = "3905536442"
    request_id = "REQ-063f1781"

    display = MagicMock()
    display.get_group_label = AsyncMock(side_effect=RuntimeError("boom"))
    display.get_user_label = AsyncMock(side_effect=RuntimeError("boom"))

    settings = load_settings(
        DummyConfig(
            {"admin_qq_ids": admin_id, "admin_notify": True, "onebot_http_url": ""}
        )
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    await store.record(admin_id, f"aiocqhttp:FriendMessage:{admin_id}")
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    actions = MagicMock()

    notifier = AdminNotifier(
        settings,
        actions,
        context,
        store,
        lambda: None,
        display=display,
    )
    await notifier.notify_external_handled(
        request_id=request_id,
        group_id=group_id,
        user_id=user_id,
        summary="张轩玮",
        comment="张轩玮 261108002 德语",
        operator_id=operator_id,
        notice_sub_type="invite",
    )

    msg = context.send_message.await_args.args[1]
    lower = str(msg).lower()
    assert f"QQ：{user_id}" in msg
    assert f"QQ侧处理人：{operator_id}" in msg
    assert f"群：群 {group_id}" in msg
    assert "[入群审核] QQ 侧已通过 ✅" in msg
    assert "external" not in lower
    assert "sub_type" not in lower

