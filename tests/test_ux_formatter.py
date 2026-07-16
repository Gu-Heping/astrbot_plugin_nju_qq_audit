from admin.formatter import format_help
from admin.ux_formatter import format_home, format_list, format_view
from config import load_settings
from data_source.student_cache import SyncState
from data_source.students import PendingRequest


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _sample_request() -> PendingRequest:
    return PendingRequest(
        id="REQ-c78e78844b32",
        group_id="796836121",
        user_id="2492835361",
        comment="李四 计算机类",
        flag="secret-flag",
        sub_type="add",
        parsed={"name": "李四", "major": "计算机类"},
        match={"strength": "weak"},
        decision="manual_review",
        confidence=0.4,
        reason="姓名+专业弱匹配",
        mode="record-only",
        status="pending",
        created_at="2026-07-09T03:46:00+00:00",
        match_strength="weak",
    )


def test_format_home_no_secrets():
    settings = load_settings(
        DummyConfig({"target_group_ids": "796836121", "admin_qq_ids": "111"})
    )
    text = format_home(
        settings,
        effective_mode="record-only",
        student_count=17,
        pending_count=1,
        sync_state=SyncState(),
        adapter_probe={"adapter_action_available": "yes"},
    )
    assert "NJU QQ Audit" in text
    assert "本科：" in text
    assert "研究生：" in text
    assert "/audit list" in text
    assert "flag" not in text
    assert "secret" not in text


def test_format_home_shows_warnings_when_unconfigured():
    settings = load_settings(DummyConfig({"target_group_ids": "", "admin_qq_ids": ""}))
    text = format_home(
        settings,
        effective_mode="record-only",
        student_count=0,
        pending_count=0,
        sync_state=SyncState(),
        adapter_probe={"adapter_action_available": "no"},
    )
    assert "未配置目标群" in text
    assert "未配置管理员" in text
    assert "审批接口不可用" in text


def test_format_list_hides_sensitive_fields():
    item = _sample_request()
    text = format_list([item], {1: item.id})
    assert "[1]" in text
    assert "李四" in text
    assert "secret-flag" not in text
    assert "REQ-c78e78844b32" not in text


def test_format_view_shows_actions():
    item = _sample_request()
    text = format_view(item, 3)
    assert "申请详情 [3]" in text
    assert "/audit ok 3" in text
    assert "/audit no 3" in text
    assert "flag" not in text


def test_format_help_common_commands_first():
    default = format_help()
    advanced = format_help(topic="advanced")
    assert "/audit list" in default
    assert len(default) < len(advanced)
    list_pos = advanced.index("/audit list")
    pending_pos = advanced.index("/audit pending")
    assert list_pos < pending_pos
