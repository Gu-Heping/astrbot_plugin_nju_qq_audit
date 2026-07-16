"""v0.4.10 short help grad copy."""

from __future__ import annotations

from admin.formatter import format_help


def test_help_grad_short_copy():
    text = format_help(topic="grad")
    assert "NJU QQ Audit v0.4.10 · 研究生审核" in text
    assert len(text) < 600
    assert "填写格式：" in text
    assert "规则：" in text
    assert "张三 马克思主义哲学 硕" in text
    assert "硕士/博士" not in text
    assert "填写顺序不限" not in text
    assert "问题模板占位" not in text
