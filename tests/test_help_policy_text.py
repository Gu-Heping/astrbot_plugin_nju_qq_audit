"""Help text for undergraduate routing / exclusive / overflow policies."""

from __future__ import annotations

from admin.formatter import format_help
from admin.release import format_release_help
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
        "undergrad_exclusive_reject_reason": "不可加入多个群",
        "undergrad_overflow_enabled": True,
        "undergrad_overflow_source_group_id": SOURCE_GROUP_ID,
        "undergrad_overflow_redirect_group_id": REDIRECT_GROUP_ID,
        "undergrad_overflow_threshold": 1950,
    }
    base.update(overrides)
    return load_settings(DummyConfig(base))


def test_advanced_help_mentions_routing_policy_keywords():
    text = format_help(topic="advanced")
    for keyword in (
        "undergrad_exclusive_action",
        "manual_review",
        "auto_reject",
        "release/catchup",
        "不会在批量流程里自动拒绝",
    ):
        assert keyword in text


def test_policy_topic_help_content():
    text = format_help(topic="policy", settings=_settings())
    assert "本科路由/策略帮助" in text
    assert "undergrad_overflow_threshold" in text
    assert SOURCE_GROUP_ID in text
    assert REDIRECT_GROUP_ID in text
    assert "release/catchup 只移出放行队列" in text


def test_release_help_auto_reject_clarifies_realtime_only():
    text = format_release_help(3, _settings())
    assert "auto_reject" in text
    assert "实时申请" in text
    assert "release/catchup 不自动拒绝" in text
    assert "不会在批量流程里自动拒绝" in text


def test_release_help_overflow_shows_threshold_and_redirect():
    text = format_release_help(3, _settings())
    assert "阈值" in text or "1950" in text
    assert "备用群" in text or REDIRECT_GROUP_ID in text
    assert "release/catchup 不自动拒绝" in text


def test_help_policy_text_has_no_sensitive_samples():
    texts = [
        format_help(topic="policy", settings=_settings()),
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
