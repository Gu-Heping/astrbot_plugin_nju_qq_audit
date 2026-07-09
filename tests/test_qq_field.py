from config import load_settings
from core.matcher import match_student
from core.normalize import parse_qq_field, student_qq_matches
from core.parser import parse_application_comment
from data_source.njutable_provider import map_row_to_student
from data_source.students import Student


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_parse_qq_field():
    assert parse_qq_field("123456789, 987654321") == "123456789"
    assert parse_qq_field("abc") is None


def test_njutable_maps_qq():
    settings = load_settings(DummyConfig({"njutable_col_qq": "QQ"}))
    student = map_row_to_student({"姓名": "张三", "QQ": "123456789"}, settings)
    assert student is not None
    assert student.qq == "123456789"


def test_qq_auxiliary_match():
    students = [Student(name="张三", updated_at="t", qq="123456789", student_id="261220001")]
    students[0].key = "k1"
    parsed = parse_application_comment("张三")
    match = match_student(parsed, students, applicant_user_id="123456789")
    assert match.strength == "auxiliary"
    assert match.qq_match is True


def test_qq_alone_not_approve():
    students = [Student(name="张三", updated_at="t", qq="123456789", student_id="261220001")]
    students[0].key = "k1"
    parsed = parse_application_comment("")
    match = match_student(parsed, students, applicant_user_id="123456789")
    assert match.strength != "strong"


def test_student_qq_matches():
    assert student_qq_matches("123456789", "123456789")
    assert student_qq_matches("123456789", "123456789,999")
