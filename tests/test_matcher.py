from core.matcher import match_student
from core.parser import parse_application_comment
from data_source.mock_provider import generate_mock_students


def test_strong_name_student_id():
    students = generate_mock_students()
    zhang = next(s for s in students if s.name == "张三")
    parsed = parse_application_comment(f"姓名：张三 学号：{zhang.student_id}")
    match = match_student(parsed, students)
    assert match.strength == "strong"


def test_weak_name_major():
    students = generate_mock_students()
    zhang = next(s for s in students if s.name == "张三")
    parsed = parse_application_comment(f"张三 {zhang.major}")
    match = match_student(parsed, students)
    assert match.strength == "weak"


def test_non_grade26():
    students = generate_mock_students()
    liu = next(s for s in students if s.name == "刘学长")
    parsed = parse_application_comment(f"刘学长 {liu.student_id}")
    match = match_student(parsed, students)
    from core.matcher import is_non_grade26

    assert is_non_grade26(match, parsed)


def test_short_student_id_prefix_match():
    from data_source.students import Student

    students = [
        Student(
            name="刘骐铭",
            updated_at="t",
            student_id="261150020",
            major="地质学类",
        )
    ]
    students[0].key = "刘骐铭"
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业 答案：刘骐铭 26115002 地质学类"
    )
    match = match_student(parsed, students)
    assert match.strength == "strong"
    assert "前缀" in match.reason
