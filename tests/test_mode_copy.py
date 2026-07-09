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
    assert "/audit unknown" in text
    assert "/audit report" in text
    list_pos = text.index("/audit list")
    pending_pos = text.index("/audit pending")
    assert list_pos < pending_pos
