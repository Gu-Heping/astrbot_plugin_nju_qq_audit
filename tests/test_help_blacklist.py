from admin.blacklist import format_blacklist_help
from admin.formatter import format_help


def test_default_help_exposes_blacklist_entry():
    text = format_help()
    assert "/audit help blacklist" in text
    assert "黑名单" in text


def test_help_blacklist_topic_content():
    text = format_help(topic="blacklist")
    assert "/audit blacklist list" in text
    assert "/audit blacklist add 3 confirm 家长申请" in text
    assert "/audit blacklist remove BL-xxxx confirm" in text
    assert "黑名单优先级高于 strong" in text
    assert "黑名单只按 QQ 号拦截，不按学号/考生号拦截" in text
    assert text == format_blacklist_help()
    for banned in (
        "/audit blacklist add student",
        "/audit blacklist add exam",
        "/audit blacklist add notice",
        "/audit blacklist add grad",
        "graduate_key",
        "通知书",
        "研究生匹配键",
    ):
        assert banned not in text


def test_help_blacklist_chinese_alias():
    assert format_help(topic="黑名单") == format_help(topic="blacklist")


def test_advanced_help_keeps_blacklist_section_qq_only():
    text = format_help(topic="advanced")
    assert "黑名单：" in text
    assert "/audit help blacklist" in text
    assert "/audit blacklist list" in text
    assert "黑名单优先级高于 strong" in text
    assert "按 QQ 号阻止" in text
    assert "按 QQ/学号" not in text
    assert "研究生匹配键" not in text
