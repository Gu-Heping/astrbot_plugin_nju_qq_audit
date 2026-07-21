"""Undergraduate exam_no parser coverage (fictional credentials only)."""

from __future__ import annotations

from core.parser import parse_application_comment

FICTIONAL_EXAM = "26123456000001"


def test_space_separated_name_exam_major():
    parsed = parse_application_comment(f"张三 {FICTIONAL_EXAM} 计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.major == "计算机科学与技术"
    assert parsed.student_id is None


def test_plus_separated_name_exam_major():
    parsed = parse_application_comment(f"张三+{FICTIONAL_EXAM}+计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.major == "计算机科学与技术"
    assert parsed.student_id is None


def test_glued_name_exam_major():
    parsed = parse_application_comment(f"张三{FICTIONAL_EXAM}计算机科学与技术")
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.major == "计算机科学与技术"
    assert parsed.student_id is None


def test_exam_no_label():
    parsed = parse_application_comment(
        f"姓名：张三 考生号：{FICTIONAL_EXAM} 专业：计算机科学与技术"
    )
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.major == "计算机科学与技术"
    assert parsed.student_id is None


def test_exam_no_only():
    parsed = parse_application_comment(FICTIONAL_EXAM)
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.name is None
    assert parsed.student_id is None
    assert not parsed.notice_no_candidates


def test_exam_no_not_in_student_id_or_notice_candidates():
    parsed = parse_application_comment(f"张三 {FICTIONAL_EXAM}")
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.student_id is None
    assert FICTIONAL_EXAM not in (parsed.notice_no_candidates or [])
    assert parsed.notice_no != FICTIONAL_EXAM


def test_legacy_student_id_still_parses():
    parsed = parse_application_comment("张三 261220001 计算机类")
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.exam_no is None


def test_legacy_notice_no_still_parses():
    parsed = parse_application_comment("张三 通知书编号：20260002")
    assert parsed.name == "张三"
    assert parsed.notice_no == "20260002"
    assert parsed.exam_no is None


def test_exam_no_in_student_id_label_not_parsed_as_student_id():
    parsed = parse_application_comment(
        f"姓名：张三 学号：{FICTIONAL_EXAM} 专业：计算机科学与技术"
    )
    assert parsed.name == "张三"
    assert parsed.exam_no == FICTIONAL_EXAM
    assert parsed.student_id is None
    assert parsed.major == "计算机科学与技术"


def test_student_id_label_still_parses_normal_sid():
    parsed = parse_application_comment(
        "姓名：张三 学号：261220001 专业：计算机科学与技术"
    )
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.exam_no is None
    assert parsed.major == "计算机科学与技术"
