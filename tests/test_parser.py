from core.parser import extract_answer_segment, parse_application_comment


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


def test_grade_prefix_on_name():
    parsed = parse_application_comment("26刘津娴 马理论专业")
    assert parsed.name == "刘津娴"
    assert parsed.major == "马理论专业"


def test_grade_prefix_with_level_suffix():
    parsed = parse_application_comment("26级刘津娴 计算机类")
    assert parsed.name == "刘津娴"


def test_qq_questionnaire_multiline():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：刘骐铭 26115002 地质学类"
    )
    assert parsed.name == "刘骐铭"
    assert parsed.student_id == "26115002"
    assert parsed.major == "地质学类"


def test_qq_questionnaire_inline():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业 答案：刘骐铭 26115002 地质学类"
    )
    assert parsed.name == "刘骐铭"
    assert parsed.student_id == "26115002"
    assert parsed.major == "地质学类"


def test_qq_questionnaire_crlf():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\r\n答案：叶一正251010015 汉语言文学"
    )
    assert parsed.name == "叶一正"
    assert parsed.student_id == "251010015"
    assert parsed.major == "汉语言文学"


def test_glued_name_sid_major():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：钱至元251830027地科"
    )
    assert parsed.name == "钱至元"
    assert parsed.student_id == "251830027"
    assert parsed.major == "地科"


def test_answer_only_marxism():
    parsed = parse_application_comment("答案：刘雯婷 26级马理论新生")
    assert parsed.name == "刘雯婷"
    assert "马理论" in (parsed.major or "")


def test_huida_prefix():
    parsed = parse_application_comment("回答：徐曦霖 拟录取理科实验班")
    assert parsed.name == "徐曦霖"
    assert "理科实验班" in (parsed.major or "")


def test_answer_marker_a():
    parsed = parse_application_comment("A：张三 261220001 计算机类")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"


def test_answer_marker_answer_en():
    parsed = parse_application_comment("answer: 李四 261220002 软件工程")
    assert parsed.name == "李四"


def test_multiple_answer_markers_take_last():
    parsed = parse_application_comment("答案：错误 答案：刘骐铭 26115002 地质学类")
    assert parsed.name == "刘骐铭"
    assert parsed.student_id == "26115002"


def test_fallback_without_answer_marker_not_template_tokens():
    parsed = parse_application_comment("问题：姓名 学号/录取号 专业")
    assert parsed.name != "问题：姓名"
    assert parsed.major != "答案"


def test_verify_prefix_multiline():
    parsed = parse_application_comment(
        "验证：问题：姓名 学号/录取号 专业\n答案：刘骐铭 26115002 地质学类"
    )
    assert parsed.name == "刘骐铭"
    assert parsed.student_id == "26115002"
