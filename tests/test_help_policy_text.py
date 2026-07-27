"""Help text for undergraduate routing policy commands."""

from __future__ import annotations

from admin.formatter import format_help
from admin.release import format_catchup_help, format_release_help
from config import load_settings

SOURCE_GROUP_ID = "<SOURCE_GROUP_ID>"
REDIRECT_GROUP_ID = "<REDIRECT_GROUP_ID>"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**overrides):
    base = {
        "undergrad_exclusive_groups_enabled": True,
        "undergrad_exclusive_action": "auto_reject",
        "undergrad_overflow_enabled": True,
        "undergrad_overflow_source_group_id": SOURCE_GROUP_ID,
        "undergrad_overflow_redirect_group_id": REDIRECT_GROUP_ID,
        "undergrad_overflow_threshold": 1950,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def test_advanced_help_mentions_policy_command():
    text = format_help(topic="advanced")
    assert "/audit policy" in text
    assert "release/catchup" in text.lower() or "不会在批量流程里自动拒绝" in text
    assert "undergrad_exclusive_groups_enabled" not in text


def test_batch_help_points_to_policy_command():
    text = format_help(topic="batch")
    assert "/audit help policy" in text
    assert "/audit policy" in text
    assert "不会在批量流程里自动拒绝" in text


def test_release_and_catchup_help_no_config_dump():
    settings = _settings()
    release = format_release_help(0, settings)
    catchup = format_catchup_help(settings)
    for text in (release, catchup):
        assert "/audit policy" in text
        assert "不会在批量流程里自动拒绝" in text
        assert "undergrad_exclusive_action" not in text
        assert "undergrad_overflow_source_group_id" not in text


def test_help_policy_text_has_no_sensitive_samples():
    texts = [
        format_help(topic="policy"),
        format_release_help(0, _settings()),
        format_help(topic="batch"),
    ]
    banned_substrings = (
        "raw_event",
        "flag=",
        "_undergrad_exclusive_hit",
        "_undergrad_overflow_hit",
    )
    for text in texts:
        for banned in banned_substrings:
            assert banned not in text
        assert "123456789" not in text
        assert "1093442531" not in text
