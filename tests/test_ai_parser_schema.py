"""AI parser schema / validator unit tests."""

from __future__ import annotations

import json

import pytest

from core.ai_parser.schema import extract_json_object, parse_ai_fields_dict
from core.ai_parser.validator import validate_ai_fields


def _fields_from(payload: dict, profile: str = "undergraduate"):
    return parse_ai_fields_dict(payload, default_profile=profile)


def test_valid_json_passes():
    data = extract_json_object(
        json.dumps(
            {
                "profile": "undergraduate",
                "name": "何聿璿",
                "student_id": "261880009",
                "major": "技术科学试验班",
                "confidence": 0.9,
                "ambiguous": False,
                "warnings": [],
                "evidence": {
                    "name": "何聿璿",
                    "student_id": "261880009",
                    "major": "技术科学试验班",
                },
            },
            ensure_ascii=False,
        )
    )
    fields = validate_ai_fields(
        _fields_from(data),
        question="姓名 学号/录取号 专业",
        answer="何聿璿+261880009+技术科学试验班",
    )
    assert fields.name == "何聿璿"
    assert fields.student_id == "261880009"
    assert fields.major == "技术科学试验班"


def test_non_json_fails():
    with pytest.raises(ValueError):
        extract_json_object("not json at all")


def test_extra_fields_ignored():
    data = {
        "profile": "undergraduate",
        "name": "张三",
        "approve": True,
        "reject_reason": "x",
        "evidence": {"name": "张三"},
    }
    fields = _fields_from(data)
    assert fields.name == "张三"
    assert not hasattr(fields, "approve")


def test_evidence_not_in_text_drops_field():
    fields = _fields_from(
        {
            "name": "张三",
            "student_id": "261880009",
            "evidence": {"name": "张三", "student_id": "261880009"},
        }
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="李四+261880010+计算机",
    )
    assert fields.name is None
    assert fields.student_id is None
    assert any("evidence_missing" in w for w in fields.warnings)


def test_shuo_bo_not_normalized():
    fields = _fields_from(
        {
            "profile": "graduate",
            "admission_type": "硕/博",
            "evidence": {"admission_type": "硕/博"},
        },
        profile="graduate",
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕/博",
        answer="张三 生物学 硕/博",
    )
    assert fields.admission_type is None
    assert fields.ambiguous is True


def test_shuo_or_bo_ambiguous():
    fields = _fields_from(
        {
            "admission_type": "硕or博",
            "evidence": {"admission_type": "硕or博"},
        },
        profile="graduate",
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三生物学硕or博",
    )
    assert fields.admission_type is None
    assert fields.ambiguous is True


def test_invalid_student_id_dropped():
    fields = _fields_from(
        {
            "student_id": "12345",
            "evidence": {"student_id": "12345"},
        }
    )
    fields = validate_ai_fields(fields, question="", answer="张三12345计算机")
    assert fields.student_id is None
    assert any("invalid_student_id" in w for w in fields.warnings)


def test_non_chinese_name_dropped():
    fields = _fields_from(
        {
            "name": "John",
            "evidence": {"name": "John"},
        }
    )
    fields = validate_ai_fields(fields, question="", answer="John 261220001")
    assert fields.name is None
    assert any("not_chinese_name" in w for w in fields.warnings)
