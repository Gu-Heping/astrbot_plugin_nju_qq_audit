"""Tests for admin display labels with real QQ nickname (v0.4.3)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.display_context import DisplayContext
from admin.notify import AdminNotifier
from admin.ux_formatter import format_auto_result_notice
from config import load_settings
from data_source.students import ActionResult
from storage.admin_session_store import AdminSessionStore
from storage.group_display_cache import GroupDisplayCache


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


GROUP_ID = "826811581"
USER_ID = "2152823507"
ADMIN_ID = "111"


def _assert_no_jargon_keys(text: str) -> None:
    for key in ("request_id:", "group_id:", "user_id:", "reason:"):
        assert key not in text


def test_format_auto_result_keeps_applicant_and_qq_separate():
    text = format_auto_result_notice(
        request_id="REQ-f110eda247dc",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        group_label="南京大学 2026 本科新生 1 群（826811581）",
        user_label="小蓝鲸（2152823507）",
    )
    _assert_no_jargon_keys(text)
    assert "申请人：张三 / 26115002" in text
    assert "QQ：小蓝鲸（2152823507）" in text
    assert "QQ：张三（" not in text
    assert "flag" not in text.lower()
    assert "token" not in text.lower()
    assert "raw_event" not in text.lower()


def test_format_auto_result_nickname_failure_keeps_qq_id():
    text = format_auto_result_notice(
        request_id="REQ-fail",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        user_label=None,
    )
    assert "申请人：张三 / 26115002" in text
    assert f"QQ：{USER_ID}" in text
    assert "QQ：张三" not in text


@pytest.mark.asyncio
async def test_display_context_group_label_from_api(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_group_list = AsyncMock(
        return_value=ActionResult(
            ok=True,
            data=[
                {"group_id": int(GROUP_ID), "group_name": "南京大学 2026 本科新生 1 群"},
            ],
        )
    )
    actions.get_group_info = AsyncMock()
    display = DisplayContext(actions, cache)
    label = await display.get_group_label(GROUP_ID)
    assert label == f"南京大学 2026 本科新生 1 群（{GROUP_ID}）"
    actions.get_group_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_display_context_group_info_fallback(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_group_list = AsyncMock(
        return_value=ActionResult(ok=True, data=[])
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(
            ok=True,
            data={"group_id": int(GROUP_ID), "group_name": "单群名"},
        )
    )
    display = DisplayContext(actions, cache)
    label = await display.get_group_label(GROUP_ID)
    assert label == f"单群名（{GROUP_ID}）"
    actions.get_group_info.assert_awaited()


@pytest.mark.asyncio
async def test_display_context_group_label_fallback_on_failure(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_group_list = AsyncMock(
        return_value=ActionResult(ok=False, message="unavailable")
    )
    actions.get_group_info = AsyncMock(
        return_value=ActionResult(ok=False, message="unavailable")
    )
    display = DisplayContext(actions, cache)
    label = await display.get_group_label(GROUP_ID)
    assert label == f"群 {GROUP_ID}"


@pytest.mark.asyncio
async def test_get_user_label_uses_stranger_nickname(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_stranger_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"user_id": int(USER_ID), "nickname": "小蓝鲸"})
    )
    display = DisplayContext(actions, cache)
    label = await display.get_user_label(GROUP_ID, USER_ID, {"name": "张三"})
    assert label == f"小蓝鲸（{USER_ID}）"
    assert "张三" not in label


@pytest.mark.asyncio
async def test_get_user_label_failure_returns_qq_id(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_stranger_info = AsyncMock(
        return_value=ActionResult(ok=False, message="rate limited")
    )
    display = DisplayContext(actions, cache)
    label = await display.get_user_label(GROUP_ID, USER_ID, {"name": "张三"})
    assert label == USER_ID


@pytest.mark.asyncio
async def test_get_user_label_ignores_parsed_name_when_no_nickname(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_stranger_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"user_id": int(USER_ID)})
    )
    display = DisplayContext(actions, cache)
    label = await display.get_user_label(GROUP_ID, USER_ID, {"name": "张三"})
    assert label == USER_ID
    assert "张三" not in label


@pytest.mark.asyncio
async def test_notify_auto_result_with_real_nickname(tmp_path: Path):
    settings = load_settings(
        DummyConfig({"admin_qq_ids": ADMIN_ID, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    actions.get_group_list = AsyncMock(
        return_value=ActionResult(
            ok=True,
            data=[{"group_id": int(GROUP_ID), "group_name": "南大新生群"}],
        )
    )
    actions.get_stranger_info = AsyncMock(
        return_value=ActionResult(ok=True, data={"nickname": "小蓝鲸"})
    )
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    display = DisplayContext(actions, cache)
    notifier = AdminNotifier(
        settings, actions, MagicMock(), store, lambda: None, display=display
    )
    await notifier.notify_auto_result(
        request_id="REQ-nick",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        parsed={"name": "张三"},
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "申请人：张三 / 26115002" in message
    assert f"QQ：小蓝鲸（{USER_ID}）" in message
    assert f"群：南大新生群（{GROUP_ID}）" in message
    assert "QQ：张三" not in message
    _assert_no_jargon_keys(message)


@pytest.mark.asyncio
async def test_notify_auto_result_nickname_failure_keeps_qq_id(tmp_path: Path):
    settings = load_settings(
        DummyConfig({"admin_qq_ids": ADMIN_ID, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    actions.get_group_list = AsyncMock(side_effect=RuntimeError("boom"))
    actions.get_group_info = AsyncMock(side_effect=RuntimeError("boom"))
    actions.get_stranger_info = AsyncMock(side_effect=RuntimeError("boom"))
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    display = DisplayContext(actions, cache)
    notifier = AdminNotifier(
        settings, actions, MagicMock(), store, lambda: None, display=display
    )
    await notifier.notify_auto_result(
        request_id="REQ-safe",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        parsed={"name": "张三"},
    )
    message = actions.send_private_msg_safe.await_args.args[1]
    assert "申请人：张三 / 26115002" in message
    assert f"QQ：{USER_ID}" in message
    assert "QQ：张三" not in message
    assert f"群：群 {GROUP_ID}" in message
    _assert_no_jargon_keys(message)
