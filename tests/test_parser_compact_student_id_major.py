"""Compact student_id + glued major / sid-first token parsing."""

from __future__ import annotations

from core.ai_parser.models import AiParsedFields
from core.ai_parser.service import merge_ai_fields_into_undergrad_parsed
from core.parser import ParsedApplication, parse_application_comment

NAME_A = "周七七"
SID_A = "261880001"
MAJOR_A = "计算机类"

NAME_B = "吴九九"
SID_B = "261880002"
MAJOR_B = "地质学类"

NAME_C = "郑十十"
EXAM_NO_14 = "26188001234567"
MAJOR_C = "法学类"

NAME_D = "钱十一"
SID_D = "261880003"
MAJOR_D = "汉语言文学"

NAME_E = "孙十二"
SID_9 = "261880001"
EXTRA_DIGIT = "1"
MAJOR_E = "计算机类"

QUESTION = "问题：姓名 学号/录取号 专业"


def test_name_space_sid_glued_major():
    raw = f"{QUESTION}\n答案：{NAME_A} {SID_A}{MAJOR_A}"
    parsed = parse_application_comment(raw)
    assert parsed.name == NAME_A
    assert parsed.student_id == SID_A
    assert parsed.major == MAJOR_A


def test_sid_name_major_glued():
    raw = f"{QUESTION}\n答案：{SID_B}{NAME_B}{MAJOR_B}"
    parsed = parse_application_comment(raw)
    assert parsed.student_id == SID_B
    assert parsed.name == NAME_B
    assert parsed.major == MAJOR_B


def test_sid_followed_by_digit_not_truncated():
    raw = f"{QUESTION}\n答案：{NAME_E} {SID_9}{EXTRA_DIGIT}{MAJOR_E}"
    parsed = parse_application_comment(raw)
    assert parsed.student_id is None
    assert parsed.name == NAME_E
    assert parsed.major != f"{EXTRA_DIGIT}{MAJOR_E}"


def test_fourteen_digit_exam_not_truncated_as_student_id():
    raw = f"答案：{NAME_C}{EXAM_NO_14}{MAJOR_C}"
    parsed = parse_application_comment(raw)
    assert parsed.exam_no == EXAM_NO_14
    assert parsed.student_id is None
    assert parsed.name == NAME_C
    assert parsed.major == MAJOR_C


def test_existing_name_sid_major_format_regression():
    raw = f"答案：{NAME_D}{SID_D}{MAJOR_D}"
    parsed = parse_application_comment(raw)
    assert parsed.name == NAME_D
    assert parsed.student_id == SID_D
    assert parsed.major == MAJOR_D


def test_ai_merge_fills_sid_and_major_from_glued_major():
    answer = f"{NAME_A} {SID_A}{MAJOR_A}"
    parsed = ParsedApplication(
        raw=f"{QUESTION}\n答案：{answer}",
        name=NAME_A,
        major=f"{SID_A}{MAJOR_A}",
        student_id=None,
    )
    ai = AiParsedFields(
        profile="undergraduate",
        student_id=SID_A,
        major=MAJOR_A,
        evidence={
            "student_id": SID_A,
            "major": MAJOR_A,
        },
    )
    merge_ai_fields_into_undergrad_parsed(parsed, ai, answer_text=answer)
    assert parsed.student_id == SID_A
    assert parsed.major == MAJOR_A
    assert "ai_parse_merged" in parsed.parse_errors
