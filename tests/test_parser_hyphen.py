from core.parser import parse_application_comment


def test_hyphen_separated_name_sid_major():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：高煜韬-261200028-环境与健康实验班"
    )
    assert parsed.name == "高煜韬"
    assert parsed.student_id == "261200028"
    assert parsed.major == "环境与健康实验班"
    assert parsed.exam_no is None


def test_hyphen_variants_fullwidth_and_dash():
    for sep in ("-", "－", "—", "–"):
        parsed = parse_application_comment(f"高煜韬{sep}261200028{sep}环境与健康实验班")
        assert parsed.name == "高煜韬", sep
        assert parsed.student_id == "261200028", sep
        assert parsed.major == "环境与健康实验班", sep


def test_hyphen_does_not_put_exam_no_into_student_id():
    parsed = parse_application_comment("张三-26110100123456-计算机类")
    assert parsed.name == "张三"
    assert parsed.exam_no == "26110100123456"
    assert parsed.student_id is None
