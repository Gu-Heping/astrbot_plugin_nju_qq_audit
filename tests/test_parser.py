from core.parser import parse_application_comment


def test_name_student_id_strong_parse():
    parsed = parse_application_comment("姓名：张三 学号：261220001 专业：计算机类")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.major == "计算机类"


def test_compact_format():
    parsed = parse_application_comment("张三261220001")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"


def test_notice_no():
    parsed = parse_application_comment("张三 通知书编号：20260002")
    assert parsed.notice_no == "20260002"


def test_empty_comment():
    parsed = parse_application_comment("")
    assert parsed.parse_errors
