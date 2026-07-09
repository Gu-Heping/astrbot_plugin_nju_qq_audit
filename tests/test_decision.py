from core.decision import make_decision, should_auto_approve
from core.matcher import MatchResult, match_student
from core.parser import parse_application_comment
from data_source.mock_provider import generate_mock_students


def test_strong_decision_approve():
    students = generate_mock_students()
    zhang = next(s for s in students if s.name == "张三")
    parsed = parse_application_comment(f"张三 {zhang.student_id}")
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "approve"
    assert should_auto_approve(decision.decision, "auto", match)


def test_keyword_manual_review():
    parsed = parse_application_comment("学长想进群")
    match = MatchResult(strength="none", confidence=0, reason="x")
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"


def test_only_major_manual():
    parsed = parse_application_comment("计算机")
    match = MatchResult(strength="none", confidence=0, reason="x")
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"


def test_weak_not_auto():
    students = generate_mock_students()
    zhang = next(s for s in students if s.name == "张三")
    parsed = parse_application_comment(f"张三 {zhang.major}")
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"
    assert not should_auto_approve(decision.decision, "auto", match)
