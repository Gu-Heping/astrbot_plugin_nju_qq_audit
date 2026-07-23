"""AI parser merge / shadow / auto-approve guard tests."""

from __future__ import annotations

import json

import pytest

from config import load_settings
from core.ai_parser.models import AiParsedFields
from core.ai_parser.service import (
    apply_ai_auto_approve_guard,
    merge_ai_fields_into_grad_parsed,
    merge_ai_fields_into_undergrad_parsed,
    maybe_run_ai_parse,
    undergrad_parse_incomplete,
)
from core.decision import make_decision
from core.matcher import match_student
from core.parser import ParsedApplication, parse_application_comment
from data_source.students import Student
from graduate.decision import make_graduate_decision
from graduate.matcher import match_graduate
from graduate.models import GraduateParsedApplication, GraduateStudent
from graduate.parser import parse_graduate_comment


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _ai_json(**kwargs) -> str:
    base = {
        "profile": "undergraduate",
        "name": None,
        "student_id": None,
        "notice_no": None,
        "major": None,
        "academy": None,
        "admission_type": None,
        "confidence": 0.95,
        "ambiguous": False,
        "warnings": [],
        "evidence": {},
    }
    base.update(kwargs)
    if not base["evidence"]:
        ev = {}
        for key in (
            "name",
            "student_id",
            "notice_no",
            "major",
            "academy",
            "admission_type",
        ):
            if base.get(key):
                ev[key] = base[key]
        base["evidence"] = ev
    return json.dumps(base, ensure_ascii=False)


def test_undergrad_merge_plus_format():
    parsed = ParsedApplication(raw="周七七+261880001+技术科学试验班")
    ai = AiParsedFields(
        profile="undergraduate",
        name="周七七",
        student_id="261880001",
        major="技术科学试验班",
        confidence=0.9,
        evidence={
            "name": "周七七",
            "student_id": "261880001",
            "major": "技术科学试验班",
        },
    )
    merge_ai_fields_into_undergrad_parsed(parsed, ai)
    assert parsed.name == "周七七"
    assert parsed.student_id == "261880001"
    assert parsed.major == "技术科学试验班"
    assert "ai_parse_merged" in parsed.parse_errors


def test_undergrad_ai_does_not_overwrite_student_id():
    parsed = ParsedApplication(
        raw="x",
        name="周七七",
        student_id="261880001",
    )
    ai = AiParsedFields(
        profile="undergraduate",
        student_id="261999999",
        evidence={"student_id": "261999999"},
    )
    merge_ai_fields_into_undergrad_parsed(parsed, ai)
    assert parsed.student_id == "261880001"


def test_grad_merge_compact():
    parsed = GraduateParsedApplication(raw="钱十一生物学博")
    ai = AiParsedFields(
        profile="graduate",
        name="钱十一",
        major="生物学",
        admission_type="博士",
        evidence={"name": "钱十一", "major": "生物学", "admission_type": "博"},
    )
    merge_ai_fields_into_grad_parsed(parsed, ai)
    assert parsed.name == "钱十一"
    assert parsed.major_text == "生物学"
    assert parsed.admission_type == "博士"


@pytest.mark.asyncio
async def test_grad_missing_evidence_dropped_via_service(monkeypatch):
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_shadow_mode": False,
                "ai_parse_base_url": "http://example.invalid/v1",
                "ai_parse_model": "test-model",
            }
        )
    )

    def fake_client(messages, _settings):
        payload = _ai_json(
            profile="graduate",
            name="不存在的人",
            major="虚构专业",
            admission_type="博士",
            evidence={
                "name": "不存在的人",
                "major": "虚构专业",
                "admission_type": "博士",
            },
        )
        return payload, "test-model"

    parsed = parse_graduate_comment("答案：钱十一生物学博")
    before = (parsed.name, parsed.major_text, parsed.admission_type)
    result = await maybe_run_ai_parse(
        settings,
        profile="graduate",
        raw_comment="答案：钱十一生物学博",
        parsed=parsed,
        incomplete=True,
        client_call=fake_client,
    )
    assert result is not None
    assert result.ok
    # fabricated fields have no evidence in answer → dropped → nothing useful merged
    assert parsed.name in {before[0], None, "钱十一"}
    if result.fields:
        assert result.fields.name is None
        assert result.fields.major is None


@pytest.mark.asyncio
async def test_shadow_mode_does_not_change_parsed_match_decision():
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
    comment = "答案：周七七+261880001+技术科学试验班"
    # Force a weak deterministic parse path by using only major-like garbage first
    parsed = parse_application_comment(comment)
    snap = (parsed.name, parsed.student_id, parsed.major)

    def fake_client(messages, _settings):
        return (
            _ai_json(
                name="周七七",
                student_id="261880001",
                major="技术科学试验班",
            ),
            "test-model",
        )

    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment=comment,
        parsed=parsed,
        incomplete=undergrad_parse_incomplete(parsed),
        client_call=fake_client,
    )
    assert (parsed.name, parsed.student_id, parsed.major) == snap
    assert "ai_parse_shadow" in parsed.parse_errors
    assert "ai_parse_merged" not in parsed.parse_errors

    students = [
        Student(
            name="周七七",
            updated_at="t",
            student_id="261880001",
            major="技术科学试验班",
        )
    ]
    # With current deterministic plus parser, this may already be strong;
    # shadow must not alter fields regardless.
    match = match_student(parsed, students)
    decision = make_decision(parsed, match, is_target_group=True)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    # shadow => not merged => guard is no-op
    assert "ai_parse_merged" not in parsed.parse_errors


def test_ai_assist_strong_stays_manual_when_auto_approve_disallowed():
    parsed = ParsedApplication(
        raw="周七七+261880001+技术科学试验班",
        name="周七七",
        student_id="261880001",
        major="技术科学试验班",
        parse_errors=["ai_parse_used", "ai_parse_merged"],
    )
    students = [
        Student(
            name="周七七",
            updated_at="t",
            student_id="261880001",
            major="技术科学试验班",
        )
    ]
    match = match_student(parsed, students)
    assert match.strength == "strong"
    decision = make_decision(parsed, match, is_target_group=True)
    assert decision.decision == "approve"
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    assert decision.decision == "manual_review"
    assert "AI 辅助解析" in decision.reason


@pytest.mark.asyncio
async def test_non_shadow_merge_fills_missing_fields():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_shadow_mode": False,
                "ai_parse_base_url": "http://example.invalid/v1",
                "ai_parse_model": "test-model",
            }
        )
    )
    parsed = ParsedApplication(raw="周七七+261880001+技术科学试验班")

    def fake_client(messages, _settings):
        return (
            _ai_json(
                name="周七七",
                student_id="261880001",
                major="技术科学试验班",
            ),
            "test-model",
        )

    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment="答案：周七七+261880001+技术科学试验班",
        parsed=parsed,
        incomplete=True,
        client_call=fake_client,
    )
    assert parsed.name == "周七七"
    assert parsed.student_id == "261880001"
    assert parsed.major == "技术科学试验班"
    assert "ai_parse_merged" in parsed.parse_errors


def test_grad_ai_assist_strong_manual_guard():
    students = [
        GraduateStudent(
            source_id="1",
            admission_type="博士",
            college="生科院",
            major_code="071000",
            major_name="生物学",
            name="钱十一",
            key="k1",
        )
    ]
    parsed = GraduateParsedApplication(
        raw="钱十一生物学博",
        name="钱十一",
        major_text="生物学",
        admission_type="博士",
        parse_errors=["ai_parse_merged"],
    )
    match = match_graduate(parsed, students)
    decision = make_graduate_decision(parsed, match, is_target_group=True)
    decision = apply_ai_auto_approve_guard(
        decision, parsed, allow_auto_approve=False
    )
    if match.strength == "strong":
        assert decision.decision == "manual_review"


def test_force_overwrite_suspicious_name_major():
    raw = "问题：姓名 学号/录取号 专业\n答案：张三计算机科学与技术261220001"
    parsed = ParsedApplication(
        raw=raw,
        name="学与技术",
        student_id="261220001",
        major="张三计算机科学与技术261220001",
    )
    ai = AiParsedFields(
        profile="undergraduate",
        name="张三",
        student_id="261220001",
        major="计算机科学与技术",
        evidence={
            "name": "张三",
            "student_id": "261220001",
            "major": "计算机科学与技术",
        },
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text="张三计算机科学与技术261220001",
    )
    assert parsed.name == "张三"
    assert parsed.student_id == "261220001"
    assert parsed.major == "计算机科学与技术"


def test_non_force_merge_does_not_overwrite_existing_name():
    raw = "问题：姓名 学号/录取号 专业\n答案：张三计算机科学与技术261220001"
    parsed = ParsedApplication(
        raw=raw,
        name="学与技术",
        student_id="261220001",
        major="张三计算机科学与技术261220001",
    )
    ai = AiParsedFields(
        profile="undergraduate",
        name="张三",
        student_id="999999999",
        major="计算机科学与技术",
        evidence={
            "name": "张三",
            "student_id": "999999999",
            "major": "计算机科学与技术",
        },
    )
    merge_ai_fields_into_undergrad_parsed(parsed, ai, allow_overwrite=False)
    assert parsed.name == "学与技术"
    assert parsed.student_id == "261220001"
    # major equals glued answer (not full raw) and contains digits → still not
    # overwritten without allow_overwrite unless template-misparsed vs raw.
    assert parsed.major == "张三计算机科学与技术261220001"


def test_overwrite_rejects_ai_fields_absent_from_answer():
    answer = "张三计算机科学与技术261220001"
    parsed = ParsedApplication(
        raw=answer,
        name="学与技术",
        student_id="261220001",
        major=answer,
    )
    ai = AiParsedFields(
        profile="undergraduate",
        name="李四",
        major="软件工程",
        evidence={"name": "李四", "major": "软件工程"},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=answer,
    )
    assert parsed.name == "学与技术"
    assert parsed.major == answer


@pytest.mark.asyncio
async def test_online_ai_fallback_does_not_overwrite_without_force():
    settings = load_settings(
        DummyConfig(
            {
                "ai_parse_enabled": True,
                "ai_parse_shadow_mode": False,
                "ai_parse_base_url": "http://example.invalid/v1",
                "ai_parse_model": "test-model",
            }
        )
    )
    raw = "问题：姓名 学号/录取号 专业\n答案：张三计算机科学与技术261220001"
    parsed = ParsedApplication(
        raw=raw,
        name="学与技术",
        student_id="261220001",
        major="张三计算机科学与技术261220001",
    )

    def fake_client(messages, _settings):
        return (
            _ai_json(
                name="张三",
                student_id="261220001",
                major="计算机科学与技术",
            ),
            "test-model",
        )

    # incomplete=True simulates online fallback; allow_overwrite defaults False
    await maybe_run_ai_parse(
        settings,
        profile="undergraduate",
        raw_comment=raw,
        parsed=parsed,
        incomplete=True,
        force=False,
        allow_overwrite=False,
        client_call=fake_client,
    )
    assert parsed.name == "学与技术"
    assert parsed.student_id == "261220001"
