"""Undergraduate name+exam_no matcher / grade-26 guard (fictional data)."""

from __future__ import annotations

from core.decision import make_decision
from core.matcher import match_student
from core.parser import ParsedApplication
from data_source.students import Student

FICTIONAL_EXAM = "26123456000001"
NON26_EXAM = "25123456000001"


def _student(**kwargs) -> Student:
    base = dict(
        name="张三",
        updated_at="t",
        student_id="261880001",
        exam_no=FICTIONAL_EXAM,
        major="计算机科学与技术",
        key="k1",
    )
    base.update(kwargs)
    return Student(**base)


def test_name_exam_no_strong():
    parsed = ParsedApplication(
        raw="张三 考生号",
        name="张三",
        exam_no=FICTIONAL_EXAM,
    )
    match = match_student(parsed, [_student()])
    assert match.strength == "strong"
    assert match.reason == "姓名+考生号强匹配"
    assert "name_examNo" in match.matched_by
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "approve"


def test_exam_no_only_manual():
    parsed = ParsedApplication(raw=FICTIONAL_EXAM, exam_no=FICTIONAL_EXAM)
    match = match_student(parsed, [_student()])
    assert match.strength == "none"
    assert "仅考生号匹配，缺少姓名" in match.reason
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"


def test_name_wrong_exam_no_not_strong():
    parsed = ParsedApplication(
        raw="x",
        name="张三",
        exam_no="26123456000099",
    )
    match = match_student(parsed, [_student()])
    assert match.strength != "strong"


def test_name_exam_no_multiple_candidates():
    students = [
        _student(key="k1"),
        _student(key="k2", student_id="261880002"),
    ]
    parsed = ParsedApplication(raw="x", name="张三", exam_no=FICTIONAL_EXAM)
    match = match_student(parsed, students)
    assert match.strength != "strong"
    assert "人工复核" in match.reason


def test_student_id_and_exam_no_point_to_different_students():
    students = [
        _student(key="k1", student_id="261880001", exam_no="26123456000011"),
        _student(
            key="k2",
            name="李四",
            student_id="261880002",
            exam_no=FICTIONAL_EXAM,
        ),
    ]
    parsed = ParsedApplication(
        raw="x",
        name="张三",
        student_id="261880001",
        exam_no=FICTIONAL_EXAM,
    )
    match = match_student(parsed, students)
    assert match.strength != "strong"
    assert "冲突" in match.reason
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"


def test_non_grade26_exam_no_manual_review():
    parsed = ParsedApplication(
        raw="x",
        name="张三",
        exam_no=NON26_EXAM,
    )
    students = [_student(exam_no=NON26_EXAM)]
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"
    assert "考生号非26级" in decision.reason
