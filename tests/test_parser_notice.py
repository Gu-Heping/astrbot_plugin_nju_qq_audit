from core.matcher import match_student
from core.normalize import normalize_notice_no, notice_nos_match
from core.parser import parse_application_comment
from data_source.students import Student


def test_notice_label_admission():
    parsed = parse_application_comment("张三 录取通知书编号：20260001")
    assert parsed.notice_no == "20260001"


def test_notice_label_short():
    parsed = parse_application_comment("张三 编号 20260002")
    assert parsed.notice_no == "20260002"


def test_notice_normalize():
    assert normalize_notice_no("2026-0001") == "20260001"
    assert notice_nos_match("2026-0001", "20260001")


def test_loose_candidate_parsed():
    parsed = parse_application_comment("张三 NJ2026ABC")
    assert parsed.notice_no == "NJ2026ABC" or "NJ2026ABC" in parsed.notice_no_candidates


def test_name_notice_strong_match():
    students = [
        Student(
            name="张三",
            updated_at="t",
            notice_no="20260001",
            student_id="261220001",
            major="计算机类",
        )
    ]
    students[0].key = "k1"
    parsed = parse_application_comment("张三 通知书编号 20260001")
    match = match_student(parsed, students)
    assert match.strength == "strong"


def test_notice_only_no_strong_without_name():
    students = [Student(name="张三", updated_at="t", notice_no="20260001", student_id="261220001")]
    students[0].key = "k1"
    parsed = parse_application_comment("20260001")
    match = match_student(parsed, students)
    assert match.strength != "strong"
