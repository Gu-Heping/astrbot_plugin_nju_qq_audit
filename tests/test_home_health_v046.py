"""v0.4.6 home health considers undergrad and graduate target groups."""

from __future__ import annotations

from admin.ux_formatter import format_home
from config import load_settings
from data_source.student_cache import SyncState


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _home(**kwargs) -> str:
    settings = kwargs.pop("settings")
    defaults = dict(
        effective_mode="record-only",
        student_count=0,
        pending_count=0,
        sync_state=SyncState(),
        grad_student_count=0,
        grad_pending_count=0,
        grad_sync_state=SyncState(),
        adapter_probe={"adapter_action_available": "yes"},
    )
    defaults.update(kwargs)
    return format_home(settings, **defaults)


def test_home_undergrad_only_is_normal():
    settings = load_settings(
        DummyConfig({"target_group_ids": "826811581", "admin_qq_ids": "111"})
    )
    text = _home(settings=settings, student_count=10)
    assert "状态：正常" in text
    assert "未配置任何目标群" not in text
    assert "当前不会处理任何入群申请" not in text


def test_home_graduate_only_is_normal():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "",
                "admin_qq_ids": "111",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
            }
        )
    )
    text = _home(
        settings=settings,
        grad_enabled=True,
        grad_target_group_ids=["200"],
        grad_student_count=5,
    )
    assert "状态：正常" in text
    assert "未配置任何目标群" not in text
    assert "当前不会处理任何入群申请" not in text
    assert "未配置目标群" not in text


def test_home_both_missing_needs_config():
    settings = load_settings(
        DummyConfig({"target_group_ids": "", "admin_qq_ids": "111", "grad_enabled": False})
    )
    text = _home(settings=settings)
    assert "状态：需要配置" in text
    assert "未配置任何目标群" in text


def test_home_grad_enabled_without_grad_groups_warns():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "826811581",
                "admin_qq_ids": "111",
                "grad_enabled": True,
                "grad_target_group_ids": "",
            }
        )
    )
    text = _home(settings=settings, grad_enabled=True, grad_target_group_ids=[])
    assert "状态：正常" in text
    assert "研究生已启用但未配置研究生目标群" in text
    assert "当前不会处理任何入群申请" not in text


def test_home_grad_enabled_no_groups_and_no_undergrad():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "",
                "admin_qq_ids": "111",
                "grad_enabled": True,
                "grad_target_group_ids": "",
            }
        )
    )
    text = _home(settings=settings, grad_enabled=True, grad_target_group_ids=[])
    assert "状态：需要配置" in text
    assert "未配置任何目标群" in text
    assert "研究生已启用但未配置研究生目标群" in text
