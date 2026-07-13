"""v0.3.8 version / debug output tests."""

from admin.formatter import format_status
from config import load_settings
from core.version import (
    DUPLICATE_POLICY_VERSION,
    PENDING_UPDATE_POLICY_VERSION,
    PLUGIN_VERSION,
    RECONCILE_LOGIC_VERSION,
)


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_debug_shows_logic_versions():
    settings = load_settings(DummyConfig({}))
    text = format_status(
        settings,
        effective_mode="record-only",
        mode_source="plugin_config",
        student_count=0,
        pending_count=0,
        sync_state=type("S", (), {"last_sync_at": None, "last_sync_result": None})(),
        probe_count=0,
        data_dir="/tmp",
        plugin_version=PLUGIN_VERSION,
        reconcile_logic_version=RECONCILE_LOGIC_VERSION,
        duplicate_policy_version=DUPLICATE_POLICY_VERSION,
        pending_update_policy_version=PENDING_UPDATE_POLICY_VERSION,
        git_commit="abc1234",
    )
    assert "plugin_version: v0.3.16" in text
    assert f"reconcile_logic_version: {RECONCILE_LOGIC_VERSION}" in text
    assert f"duplicate_policy_version: {DUPLICATE_POLICY_VERSION}" in text
    assert f"pending_update_policy_version: {PENDING_UPDATE_POLICY_VERSION}" in text
    assert "git_commit: abc1234" in text


def test_reconcile_logic_version_constant():
    assert RECONCILE_LOGIC_VERSION == "v2-invite-matches-pending"
    assert DUPLICATE_POLICY_VERSION == "v5-terminal-never-reapply"
    assert PENDING_UPDATE_POLICY_VERSION == "v1-update-pending-on-comment-change"
