"""v0.4.4 admin UX: human-readable list/view/receipts/help."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

import pytest

from admin.formatter import format_help
from admin.labels import status_label
from admin.receipts import (
    format_dismiss_result,
    format_mark_external_result,
    format_restore_result,
    resolve_display_labels,
)
from admin.release import format_catchup_help, format_release_help
from admin.sweep import format_sweep_help, format_sweep_preview, SweepPreview
from admin.ux_formatter import (
    format_list,
    format_no_result,
    format_ok_result,
    format_view,
)
from config import load_settings
from data_source.students import PendingRequest


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _undergrad(**kwargs) -> PendingRequest:
    base = dict(
        id="REQ-ux044",
        group_id="826811581",
        user_id="2152823507",
        comment="张三 26115002",
        flag="secret-flag-token",
        sub_type="add",
        parsed={"name": "张三", "student_id": "26115002"},
        match={"strength": "strong", "qq_match": True},
        decision="approve",
        confidence=0.95,
        reason="姓名+学号匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-16T00:00:00+00:00",
        match_strength="strong",
        profile="undergraduate",
    )
    base.update(kwargs)
    return PendingRequest(**base)


def test_status_label_human():
    assert status_label("pending") == "等待处理"
    assert status_label("processed") == "已处理"
    assert status_label("external") == "QQ 侧已处理"
    assert status_label("stale") == "QQ 侧已找不到申请"
    assert status_label("dismissed") == "本地已关闭"
    assert status_label("ignored") == "已忽略/已被新申请取代"


def test_format_list_readable_no_raw_field_names():
    item = _undergrad()
    text = format_list(
        [item],
        {1: item.id},
        group_labels={"826811581": "南京大学 2026 本科新生 1 群（826811581）"},
        user_labels={"826811581:2152823507": "小蓝鲸（2152823507）"},
    )
    assert "request_id:" not in text
    assert "group_id:" not in text
    assert "user_id:" not in text
    assert "match_strength:" not in text
    assert "申请人" in text or "张三 / 26115002" in text
    assert "QQ：小蓝鲸（2152823507）" in text
    assert "群：南京大学 2026 本科新生 1 群（826811581）" in text
    assert "验证：" in text
    assert "判断：" in text
    assert "操作：" in text
    assert "/audit ok 1" in text
    assert "/audit view 1" in text
    assert "secret-flag" not in text
    assert "编号来自本次列表，30 分钟内有效" in text


def test_format_list_graduate_summary():
    item = _undergrad(
        id="REQ-g",
        comment="刘尚明 马克思主义哲学 硕",
        parsed={"name": "刘尚明", "admission_type": "硕士", "major_text": "马克思主义哲学"},
        profile="graduate",
        match_strength="weak",
        decision="manual_review",
        reason="需人工",
        match={"strength": "weak"},
    )
    text = format_list([item], {1: item.id})
    assert "研究生｜刘尚明 / 硕士 / 马克思主义哲学" in text


def test_format_list_fallback_without_labels():
    item = _undergrad()
    text = format_list([item], {1: item.id})
    assert "QQ：2152823507" in text
    assert "群 826811581" in text


def test_format_view_status_human_and_fields():
    for status, label in [
        ("pending", "等待处理"),
        ("external", "QQ 侧已处理"),
        ("stale", "QQ 侧已找不到申请"),
        ("dismissed", "本地已关闭"),
        ("processed", "已处理"),
    ]:
        item = _undergrad(status=status, processed_at="t" if status != "pending" else None)
        text = format_view(
            item,
            1,
            group_label="测试群（826811581）",
            user_label="小蓝鲸（2152823507）",
        )
        assert f"状态：{label}" in text
        assert "申请人：" in text
        assert "QQ：小蓝鲸（2152823507）" in text
        assert "群：测试群（826811581）" in text
        assert "用户：" not in text.split("申请人：")[0]  # no leading 用户 field
        assert "记录编号：REQ-ux044" in text
        # request id not as primary title when index present
        assert text.startswith("申请详情 [1]")
        assert "QQ 辅助匹配：QQ号与名单一致" in text
        assert "secret-flag" not in text
        assert "raw_event" not in text


def test_format_ok_no_receipts():
    item = _undergrad()
    ok = format_ok_result(
        item,
        1,
        group_label="南京大学 2026 本科新生 1 群（826811581）",
        user_label="小蓝鲸（2152823507）",
    )
    assert "已同意申请 [1]" in ok
    assert "申请人：张三 / 26115002" in ok
    assert "QQ：小蓝鲸（2152823507）" in ok
    assert "群：南京大学 2026 本科新生 1 群（826811581）" in ok
    assert "处理：管理员手动通过" in ok
    assert "processed" not in ok
    assert "flag" not in ok

    no = format_no_result(
        item,
        1,
        "信息不完整",
        group_label="南京大学 2026 本科新生 1 群（826811581）",
        user_label="小蓝鲸（2152823507）",
    )
    assert "已拒绝申请 [1]" in no
    assert "理由：信息不完整" in no
    assert "处理：已向 QQ 发送拒绝" in no
    assert "processed" not in no


def test_mark_external_dismiss_restore_receipts_human():
    item = _undergrad()
    ext = format_mark_external_result(item, 1)
    assert "已标记为「QQ侧已处理」" in ext
    assert "不会调用 QQ 审批接口" in ext
    assert "标记为 external" not in ext

    dismiss = format_dismiss_result(item, 1, "测试数据")
    assert "已本地关闭申请" in dismiss
    assert "不会向 QQ 发送拒绝" in dismiss
    assert "原因：测试数据" in dismiss
    assert "dismissed" not in dismiss

    restore = format_restore_result(item, 1)
    assert "待处理" in restore
    assert "QQ 侧可能已经没有" in restore
    assert "pending" not in restore


def test_sweep_release_copy_no_english_jargon():
    sweep = format_sweep_help()
    assert "non-strong" not in sweep
    assert "pending" not in sweep.lower() or "待处理" in sweep
    assert "非强匹配" in sweep
    assert "不会向 QQ 发送拒绝" in sweep

    preview = format_sweep_preview(
        SweepPreview(candidates=[_undergrad(match_strength="none")], kept_strong=[])
    )
    assert "non-strong" not in preview
    assert "非强匹配" in preview

    settings = load_settings(DummyConfig({"batch_approve_max_count": 20}))
    release = format_release_help(3, settings)
    assert "strong match" not in release
    assert "本科申请" in release
    assert "系统强匹配" in release
    assert "学号判断为 26 级" in release
    assert "仍在待处理队列中" in release

    catchup = format_catchup_help(settings)
    assert "strong match" not in catchup
    assert "本科申请" in catchup


def test_help_default_short_with_topics():
    default = format_help()
    advanced = format_help(topic="advanced")
    grad = format_help(topic="grad")
    assert len(default) < len(advanced) // 2
    assert "/audit list" in default
    assert "/audit list grad" in default
    assert "/audit sync grad" in default
    assert "/audit help batch" in default
    assert "/audit help grad" in default
    assert "/audit help debug" in default
    assert "/audit help advanced" in default
    assert "/audit pending" not in default
    assert "/audit pending" in advanced
    assert "硕/博" in grad
    assert "/audit release preview" in format_help(topic="batch")
    assert "/audit probe api" in format_help(topic="debug")


@pytest.mark.asyncio
async def test_resolve_display_labels_safe_fallback():
    display = MagicMock()
    display.get_group_label = AsyncMock(side_effect=RuntimeError("boom"))
    display.get_user_label = AsyncMock(side_effect=RuntimeError("boom"))
    item = _undergrad()
    groups, users = await resolve_display_labels(display, [item])
    assert groups["826811581"] == "群 826811581"
    assert users["826811581:2152823507"] == "2152823507"

    groups2, users2 = await resolve_display_labels(None, [item])
    assert groups2["826811581"] == "群 826811581"
    assert users2["826811581:2152823507"] == "2152823507"
