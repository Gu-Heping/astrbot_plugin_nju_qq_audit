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
