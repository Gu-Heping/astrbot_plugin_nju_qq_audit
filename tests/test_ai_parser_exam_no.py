"""AI parser exam_no validation / merge / auto-approve guard."""

from __future__ import annotations

import json

import pytest

from config import load_settings
from core.ai_parser.models import AiParsedFields
from core.ai_parser.schema import parse_ai_fields_dict
from core.ai_parser.service import (
    apply_ai_auto_approve_guard,
    merge_ai_fields_into_undergrad_parsed,
    maybe_run_ai_parse,
    undergrad_parse_incomplete,
)
from core.ai_parser.validator import validate_ai_fields
from core.decision import apply_auto_approve_flag, make_decision
from core.matcher import match_student
from core.parser import ParsedApplication
from data_source.students import Student

FICTIONAL_EXAM = "26123456000001"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_validator_keeps_exam_no():
    fields = parse_ai_fields_dict(
        {
            "profile": "undergraduate",
            "name": "张三",
            "exam_no": FICTIONAL_EXAM,
            "evidence": {"name": "张三", "exam_no": FICTIONAL_EXAM},
        },
        default_profile="undergraduate",
    )
    out = validate_ai_fields(
        fields,
        question="姓名 学号/录取号 专业",
        answer=f"张三 {FICTIONAL_EXAM}",
    )
    assert out.exam_no == FICTIONAL_EXAM


def test_exam_no_evidence_must_be_in_answer():
    fields = parse_ai_fields_dict(
        {
            "profile": "undergraduate",
            "exam_no": FICTIONAL_EXAM,
            "evidence": {"exam_no": FICTIONAL_EXAM},
        },
        default_profile="undergraduate",
    )
    out = validate_ai_fields(
        fields,
        question=f"考生号示例 {FICTIONAL_EXAM}",
        answer="张三 计算机科学与技术",
    )
    assert out.exam_no is None


def test_fourteen_digit_not_valid_student_id():
    fields = parse_ai_fields_dict(
        {
            "profile": "undergraduate",
            "student_id": FICTIONAL_EXAM,
            "evidence": {"student_id": FICTIONAL_EXAM},
        },
        default_profile="undergraduate",
    )
    out = validate_ai_fields(
        fields,
        question="姓名 学号",
        answer=f"张三 {FICTIONAL_EXAM}",
    )
    assert out.student_id is None


@pytest.mark.asyncio
async def test_ai_merged_exam_no_blocked_when_auto_approve_disallowed():
    parsed = ParsedApplication(raw="张三", name="张三")
    ai = AiParsedFields(
        profile="undergraduate",
        exam_no=FICTIONAL_EXAM,
        evidence={"exam_no": FICTIONAL_EXAM},
    )
    merge_ai_fields_into_undergrad_parsed(parsed, ai)
    assert parsed.exam_no == FICTIONAL_EXAM
    assert "ai_parse_merged" in parsed.parse_errors

    students = [
        Student(
            name="张三",
            updated_at="t",
            student_id="261880001",
            exam_no=FICTIONAL_EXAM,
            major="计算机科学与技术",
            key="k1",
        )
    ]
    match = match_student(parsed, students)
    assert match.strength == "strong"
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_auto_approve_flag(decision, "auto", match)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert decision.decision == "manual_review"
    assert decision.should_auto_approve is False


def test_ai_allow_auto_approve_true_keeps_approve_for_exam_no():
    parsed = ParsedApplication(
        raw="张三",
        name="张三",
        exam_no=FICTIONAL_EXAM,
        parse_errors=["ai_parse_merged"],
    )
    students = [
        Student(
            name="张三",
            updated_at="t",
            student_id="261880001",
            exam_no=FICTIONAL_EXAM,
            key="k1",
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


@pytest.mark.asyncio
async def test_shadow_mode_does_not_merge_exam_no():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_shadow_mode": True,
                "ai_parse_base_url": "http://example.invalid/v1",
                "ai_parse_model": "test-model",
            }
        )
    )
    comment = "答案：张三"
    parsed = ParsedApplication(raw=comment, name="张三")
    assert undergrad_parse_incomplete(parsed)

    def fake_client(messages, _settings):
        return (
            json.dumps(
                {
                    "profile": "undergraduate",
                    "name": "张三",
                    "exam_no": FICTIONAL_EXAM,
                    "confidence": 0.9,
                    "ambiguous": False,
                    "warnings": [],
                    "evidence": {"name": "张三", "exam_no": FICTIONAL_EXAM},
                },
                ensure_ascii=False,
            ),
            "test-model",
        )

    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment=comment,
        parsed=parsed,
        incomplete=True,
        client_call=fake_client,
    )
    assert parsed.exam_no is None
    assert "ai_parse_shadow" in parsed.parse_errors
    assert "ai_parse_merged" not in parsed.parse_errors


def test_to_log_dict_masks_exam_no_value():
    fields = AiParsedFields(
        profile="undergraduate",
        name="张三",
        exam_no=FICTIONAL_EXAM,
    )
    logged = fields.to_log_dict()
    assert "exam_no" in logged["fields"]
    blob = json.dumps(logged, ensure_ascii=False)
    assert FICTIONAL_EXAM not in blob
