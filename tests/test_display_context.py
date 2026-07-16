"""Tests for admin display labels and jargon-free auto notices (v0.4.2)."""

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


def test_format_auto_result_no_jargon_keys():
    text = format_auto_result_notice(
        request_id="REQ-f110eda247dc",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=True,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        group_label="南京大学 2026 本科新生 1 群（826811581）",
        user_label="张三（2152823507）",
    )
    _assert_no_jargon_keys(text)
    assert "已自动通过" in text
    assert "申请人：张三 / 26115002" in text
    assert "QQ：张三（2152823507）" in text
    assert "群：南京大学 2026 本科新生 1 群（826811581）" in text
    assert "验证：张三 26115002" in text
    assert "判断：姓名+学号强匹配" in text
    assert "处理：已同意入群" in text
    assert "/audit view REQ-f110eda247dc" in text
    assert "flag" not in text.lower()
    assert "token" not in text.lower()
    assert "raw_event" not in text.lower()


def test_format_auto_result_failure_and_fallbacks():
    text = format_auto_result_notice(
        request_id="REQ-fail",
        group_id=GROUP_ID,
        user_id=USER_ID,
        ok=False,
        reason="姓名+学号强匹配",
        summary="张三 / 26115002",
        comment="张三 26115002",
        action_message="backend timeout",
    )
    _assert_no_jargon_keys(text)
    assert "自动通过失败" in text
    assert f"群：群 {GROUP_ID}" in text
    assert f"QQ：{USER_ID}" in text
    assert "错误：backend timeout" in text
    assert "/audit list" in text


def test_format_auto_result_summary_fallback_to_user_id():
    text = format_auto_result_notice(
        request_id="REQ-x",
        group_id="1",
        user_id="999",
        ok=True,
        reason="ok",
        summary=None,
    )
    assert "申请人：999" in text


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
    display = DisplayContext(actions, cache)
    label = await display.get_group_label(GROUP_ID)
    assert label == f"南京大学 2026 本科新生 1 群（{GROUP_ID}）"
    assert cache.get_name(GROUP_ID) == "南京大学 2026 本科新生 1 群"


@pytest.mark.asyncio
async def test_display_context_group_label_fallback_on_failure(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    actions = MagicMock()
    actions.get_group_list = AsyncMock(
        return_value=ActionResult(ok=False, message="unavailable")
    )
    display = DisplayContext(actions, cache)
    label = await display.get_group_label(GROUP_ID)
    assert label == f"群 {GROUP_ID}"


@pytest.mark.asyncio
async def test_display_context_user_label_from_parsed(tmp_path: Path):
    cache = GroupDisplayCache(tmp_path / "group_display_cache.json")
    display = DisplayContext(MagicMock(), cache)
    label = await display.get_user_label(GROUP_ID, USER_ID, {"name": "张三"})
    assert label == f"张三（{USER_ID}）"
    fallback = await display.get_user_label(GROUP_ID, USER_ID, {})
    assert fallback == USER_ID


@pytest.mark.asyncio
async def test_notify_auto_result_survives_group_label_failure(tmp_path: Path):
    settings = load_settings(
        DummyConfig({"admin_qq_ids": ADMIN_ID, "admin_notify": True, "onebot_http_url": ""})
    )
    store = AdminSessionStore(tmp_path / "admin_sessions.json")
    actions = MagicMock()
    actions.send_private_msg_safe = AsyncMock(return_value=MagicMock(ok=True, message="ok"))
    actions.get_group_list = AsyncMock(side_effect=RuntimeError("boom"))
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
    assert "已自动通过" in message
    assert f"群：群 {GROUP_ID}" in message
    assert f"QQ：张三（{USER_ID}）" in message
    _assert_no_jargon_keys(message)
