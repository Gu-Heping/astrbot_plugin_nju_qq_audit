"""v0.4.5 graduate admin UX: help, home, list grad empty, sync grad copy."""

from __future__ import annotations

from admin.formatter import format_help
from admin.ux_formatter import format_grad_sync_result, format_home, format_list
from config import load_settings
from data_source.student_cache import SyncState


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_default_help_includes_graduate_entries():
    text = format_help()
    assert "/audit list grad" in text
    assert "/audit sync grad" in text
    assert "/audit help grad" in text


def test_help_grad_shows_graduate_rules():
    text = format_help(topic="grad")
    assert "研究生审核" in text
    assert "/audit sync grad" in text
    assert "/audit list grad" in text
    assert "填写格式：" in text
    assert "姓名 专业 硕/博" in text
    assert "张三 马克思主义哲学 硕" in text
    assert "李四 010101 博" in text
    assert "硕或博 唯一匹配" in text
    assert "不会自动拒绝" in text
    assert "release/catchup 只处理本科" in text
    assert "不要照抄" not in text
    assert "填写顺序不限" not in text
    assert "硕士/博士" not in text
    assert "独立名单" not in text


def test_format_home_shows_undergrad_and_grad_sections():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "826811581",
                "admin_qq_ids": "111",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
            }
        )
    )
    text = format_home(
        settings,
        effective_mode="record-only",
        student_count=100,
        pending_count=3,
        sync_state=SyncState(last_sync_at="2026-07-16T06:00:00+00:00", last_sync_result="success"),
        grad_enabled=True,
        grad_target_group_ids=["200"],
        grad_student_count=42,
        grad_pending_count=2,
        grad_sync_state=SyncState(
            last_sync_at="2026-07-16T07:00:00+00:00", last_sync_result="success"
        ),
        adapter_probe={"adapter_action_available": "yes"},
    )
    assert "本科：" in text
    assert "研究生：" in text
    assert "启用：是" in text
    assert "名单人数：100 人" in text
    assert "名单人数：42 人" in text
    assert "待处理数：3 条" in text
    assert "待处理数：2 条" in text
    assert "200" in text


def test_format_home_grad_disabled():
    settings = load_settings(
        DummyConfig({"target_group_ids": "826811581", "admin_qq_ids": "111", "grad_enabled": False})
    )
    text = format_home(
        settings,
        effective_mode="record-only",
        student_count=10,
        pending_count=1,
        sync_state=SyncState(),
        grad_enabled=False,
        grad_student_count=0,
        grad_pending_count=0,
    )
    assert "启用：否" in text


def test_list_grad_empty_shows_dedicated_hint():
    text = format_list([], {}, list_profile="graduate")
    assert "目前没有研究生待处理申请" in text
    assert "/audit sync grad" in text
    assert "/audit list" in text


def test_format_grad_sync_success():
    state = SyncState(
        last_sync_at="2026-07-16T08:00:00+00:00",
        last_sync_result="success",
        filtered_count=88,
        source="njutable",
    )
    text = format_grad_sync_result(ok=True, sync_state=state, cached_count=88)
    assert "研究生名单同步成功" in text
    assert "缓存人数：88 人" in text
    assert "最近同步：" in text
    assert "来源：njutable" in text
    assert "raw=" not in text


def test_format_grad_sync_failure_with_suggestions():
    text = format_grad_sync_result(ok=False, cached_count=12, error_name="ConnectionError")
    assert "研究生名单同步失败" in text
    assert "ConnectionError" in text
    assert "已保留旧缓存：12 人" in text
    assert "/audit debug" in text
    assert "/audit sync grad" in text
