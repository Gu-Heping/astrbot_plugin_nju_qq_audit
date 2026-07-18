"""Undergraduate parser: plus / slash field separators (v0.4.14)."""

from __future__ import annotations

from data_source.students import Student
from core.matcher import match_student
from core.parser import parse_application_comment


def test_plus_separator_with_question_template():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：何聿璿+261880009+技术科学试验班"
    )
    assert parsed.name == "何聿璿"
    assert parsed.student_id == "261880009"
    assert parsed.major == "技术科学试验班"
    assert parsed.name != "问题：姓名"
    assert parsed.name != "答案"


def test_fullwidth_plus_separator():
    parsed = parse_application_comment("何聿璿＋261880009＋技术科学试验班")
    assert parsed.name == "何聿璿"
    assert parsed.student_id == "261880009"
    assert parsed.major == "技术科学试验班"


def test_slash_separator():
    parsed = parse_application_comment("何聿璿/261880009/技术科学试验班")
    assert parsed.name == "何聿璿"
    assert parsed.student_id == "261880009"
    assert parsed.major == "技术科学试验班"


def test_space_and_comma_still_work():
    spaced = parse_application_comment("何聿璿 261880009 技术科学试验班")
    assert spaced.name == "何聿璿"
    assert spaced.student_id == "261880009"
    assert spaced.major == "技术科学试验班"

    comma = parse_application_comment("何聿璿，261880009，技术科学试验班")
    assert comma.name == "何聿璿"
    assert comma.student_id == "261880009"
    assert comma.major == "技术科学试验班"


def test_name_plus_student_id_only():
    parsed = parse_application_comment("何聿璿+261880009")
    assert parsed.name == "何聿璿"
    assert parsed.student_id == "261880009"
    assert parsed.major is None


def test_name_plus_notice_no_plus_major():
    parsed = parse_application_comment("张三+20260001+计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.notice_no == "20260001"
    assert parsed.student_id is None
    assert parsed.major == "计算机科学与技术"


def test_template_tokens_not_used_as_name():
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：何聿璿+261880009+技术科学试验班"
    )
    assert parsed.name == "何聿璿"
    assert "问题" not in (parsed.name or "")
    assert parsed.major == "技术科学试验班"


def test_match_student_strong_after_plus_parse():
    students = [
        Student(
            name="何聿璿",
            updated_at="t",
            student_id="261880009",
            major="技术科学试验班",
            academy="测试书院",
        )
    ]
    parsed = parse_application_comment(
        "问题：姓名 学号/录取号 专业\n答案：何聿璿+261880009+技术科学试验班"
    )
    match = match_student(parsed, students)
    assert match.strength == "strong"
    assert "姓名+学号强匹配" in (match.reason or "")
