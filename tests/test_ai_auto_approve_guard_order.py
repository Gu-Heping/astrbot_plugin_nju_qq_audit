"""AI auto-approve guard must run after apply_*_auto_approve_flag (v0.4.17)."""

from __future__ import annotations

from core.ai_parser.service import apply_ai_auto_approve_guard
from core.decision import apply_auto_approve_flag, make_decision
from core.matcher import match_student
from core.parser import ParsedApplication
from data_source.students import Student
from graduate.decision import apply_graduate_auto_approve_flag, make_graduate_decision
from graduate.matcher import match_graduate
from graduate.models import GraduateParsedApplication, GraduateStudent


def test_undergrad_ai_merged_strong_blocks_auto_approve():
    parsed = ParsedApplication(
        raw="何聿璿+261880009+技术科学试验班",
        name="何聿璿",
        student_id="261880009",
        major="技术科学试验班",
        parse_errors=["ai_parse_merged"],
    )
    students = [
        Student(
            name="何聿璿",
            updated_at="t",
            student_id="261880009",
            major="技术科学试验班",
        )
    ]
    match = match_student(parsed, students)
    assert match.strength == "strong"
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_auto_approve_flag(decision, "auto", match)
    assert decision.should_auto_approve is True
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert decision.decision == "manual_review"
    assert decision.should_auto_approve is False


def test_grad_ai_merged_strong_blocks_auto_approve():
    parsed = GraduateParsedApplication(
        raw="陈俊毅生物学博",
        name="陈俊毅",
        major_text="生物学",
        admission_type="博士",
        parse_errors=["ai_parse_merged"],
    )
    students = [
        GraduateStudent(
            source_id="1",
            admission_type="博士",
            college="生科院",
            major_code="071000",
            major_name="生物学",
            name="陈俊毅",
            key="k1",
        )
    ]
    match = match_graduate(parsed, students)
    decision = make_graduate_decision(parsed, match, is_target_group=True)
    decision = apply_graduate_auto_approve_flag(decision, "auto", match)
    if decision.decision == "approve":
        assert decision.should_auto_approve is True
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert decision.decision == "manual_review"
    assert decision.should_auto_approve is False


def test_deterministic_strong_still_auto_in_auto_mode():
    parsed = ParsedApplication(
        raw="何聿璿 261880009",
        name="何聿璿",
        student_id="261880009",
        parse_errors=[],
    )
    students = [
        Student(
            name="何聿璿",
            updated_at="t",
            student_id="261880009",
            major="技术科学试验班",
        )
    ]
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_auto_approve_flag(decision, "auto", match)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert decision.decision == "approve"
    assert decision.should_auto_approve is True


def test_ai_allow_auto_approve_true_keeps_approve():
    parsed = ParsedApplication(
        raw="何聿璿+261880009",
        name="何聿璿",
        student_id="261880009",
        parse_errors=["ai_parse_merged"],
    )
    students = [
        Student(
            name="何聿璿",
            updated_at="t",
            student_id="261880009",
            major="技术科学试验班",
        )
    ]
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_auto_approve_flag(decision, "auto", match)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=True
    )
    assert decision.decision == "approve"
    assert decision.should_auto_approve is True
