from core.decision import apply_auto_approve_flag, make_decision
from core.matcher import match_student
from core.parser import parse_application_comment
from data_source.students import Student


def _student(name, major, **kwargs):
    s = Student(
        name=name,
        major=major,
        updated_at="2026-01-01T00:00:00+00:00",
        student_id=kwargs.get("student_id", "261220001"),
        **{k: v for k, v in kwargs.items() if k != "student_id"},
    )
    s.key = f"{name}-{major}"
    return s


def test_electronic_weak_match():
    students = [_student("张三", "电子信息类")]
    parsed = parse_application_comment("张三 电子")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_telecom_weak_match():
    students = [_student("李四", "电子信息类")]
    parsed = parse_application_comment("李四 电信")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_computer_alias():
    students = [_student("王五", "计算机类")]
    parsed = parse_application_comment("王五 计科")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_software_alias():
    students = [_student("赵六", "软件工程")]
    parsed = parse_application_comment("赵六 软工")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_ai_alias():
    students = [_student("钱七", "人工智能")]
    parsed = parse_application_comment("钱七 AI")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_major_weak_not_auto_approve():
    students = [_student("孙八", "电子信息类")]
    parsed = parse_application_comment("孙八 电子")
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    applied = apply_auto_approve_flag(decision, "auto", match)
    assert not applied.should_auto_approve


def test_multiple_major_candidates():
    students = [
        _student("周九", "计算机类", student_id="261220009"),
        _student("周九", "软件工程", student_id="261220010"),
    ]
    parsed = parse_application_comment("周九 计算机")
    match = match_student(parsed, students)
    assert match.strength == "weak"
    assert match.confidence == 0.4


def test_grade_prefix_name_with_marxism_major():
    students = [_student("刘津娴", "马克思主义理论", student_id="261010001")]
    parsed = parse_application_comment("26刘津娴 马理论专业")
    match = match_student(parsed, students)
    assert match.strength == "weak"
    assert "专业" in match.reason
