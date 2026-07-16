"""v0.4.11 advanced help: global mode scope for undergrad + graduate."""

from __future__ import annotations

from admin.formatter import format_help


def test_help_advanced_mode_is_global():
    text = format_help(topic="advanced")
    assert "模式（全局，对本科和研究生都生效）" in text
    assert "自动通过强匹配 26 级" not in text
    assert "自动通过强匹配申请（本科/研究生都会生效）" in text
    assert "本科：强匹配且通过 26 级检查" in text
    assert "研究生：姓名 + 专业/代码 + 硕或博 唯一匹配" in text
    assert "release/catchup 不是 mode，只处理本科补放" in text
    assert "/audit mode                 查看当前全局模式" in text
    assert "/audit mode reset confirm   恢复插件配置中的 mode" in text


def test_help_advanced_still_lists_release_as_batch():
    text = format_help(topic="advanced")
    assert "分批放人（仅本科强匹配 26 级待处理，不改变 mode）" in text
