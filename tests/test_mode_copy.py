from admin.formatter import format_help
from admin.ux_formatter import format_mode_changed, format_off_warning


def test_off_warning_suggests_record():
    text = format_off_warning()
    assert "/audit record" in text
    assert "完全跳过" in text


def test_record_mode_changed_mentions_release():
    text = format_mode_changed("record-only")
    assert "/audit release" in text


def test_help_includes_release_and_report():
    text = format_help()
    assert "/audit release preview" in text
    assert "/audit catchup preview" in text
    assert "/audit catchup confirm" in text
    assert "catchup：先同步校对表" in text or "先同步校对表" in text
    assert "/audit unknown" in text
    assert "/audit report" in text
    assert "推荐流程" in text
    assert "batch strong" in text
    assert "NJU QQ Audit v0.3." in text
    list_pos = text.index("/audit list")
    pending_pos = text.index("/audit pending")
    catchup_pos = text.index("/audit catchup preview")
    assert list_pos < pending_pos
    assert catchup_pos < pending_pos


def test_help_shows_context_when_provided():
    text = format_help(effective_mode="record-only", pending_count=3, releasable_count=2)
    assert "当前：" in text
    assert "record-only" in text
    assert "待处理 3" in text
    assert "可分批 2" in text
