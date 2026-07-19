"""AI evidence must come from answer text only (v0.4.17)."""

from __future__ import annotations

from core.ai_parser.schema import parse_ai_fields_dict
from core.ai_parser.validator import validate_ai_fields


def _fields(**kwargs):
    base = {
        "profile": "graduate",
        "name": None,
        "student_id": None,
        "notice_no": None,
        "major": None,
        "academy": None,
        "admission_type": None,
        "confidence": 0.9,
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
    return parse_ai_fields_dict(base, default_profile="graduate")


def test_question_shuo_or_bo_cannot_prove_doctor():
    fields = _fields(
        admission_type="博士",
        evidence={"admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="黄昌意 计算机科学与技术",
    )
    assert fields.admission_type is None


def test_question_template_only_no_admission_type():
    fields = _fields(
        name="黄昌意",
        major="计算机科学与技术",
        admission_type="博士",
        evidence={
            "name": "黄昌意",
            "major": "计算机科学与技术",
            "admission_type": "博",
        },
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="黄昌意 计算机科学与技术",
    )
    assert fields.name == "黄昌意"
    assert fields.major == "计算机科学与技术"
    assert fields.admission_type is None


def test_answer_bo_allows_doctor():
    fields = _fields(
        name="陈俊毅",
        major="生物学",
        admission_type="博士",
        evidence={"name": "陈俊毅", "major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="陈俊毅 生物学 博",
    )
    assert fields.admission_type == "博士"


def test_answer_shuo_bo_slash_ambiguous():
    fields = _fields(admission_type="硕士", evidence={"admission_type": "硕"})
    fields = validate_ai_fields(
        fields,
        question="",
        answer="陈俊毅 生物学 硕/博",
    )
    assert fields.admission_type is None
    assert fields.ambiguous is True


def test_answer_master_slash_doctor_ambiguous():
    fields = _fields(admission_type="博士", evidence={"admission_type": "博士"})
    fields = validate_ai_fields(
        fields,
        question="",
        answer="陈俊毅 生物学 硕士/博士",
    )
    assert fields.admission_type is None
    assert fields.ambiguous is True


def test_fields_only_in_question_are_dropped():
    fields = _fields(
        name="张三",
        student_id="261880009",
        notice_no="20260001",
        major="计算机",
        evidence={
            "name": "张三",
            "student_id": "261880009",
            "notice_no": "20260001",
            "major": "计算机",
        },
    )
    fields = validate_ai_fields(
        fields,
        question="姓名：张三 学号：261880009 通知书：20260001 专业：计算机",
        answer="请审核",
    )
    assert fields.name is None
    assert fields.student_id is None
    assert fields.notice_no is None
    assert fields.major is None


def test_value_in_answer_without_evidence_kept():
    fields = _fields(
        name="何聿璿",
        student_id="261880009",
        major="技术科学试验班",
        evidence={},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 学号 专业",
        answer="何聿璿+261880009+技术科学试验班",
    )
    assert fields.name == "何聿璿"
    assert fields.student_id == "261880009"
    assert fields.major == "技术科学试验班"


def test_missing_from_answer_dropped():
    fields = _fields(
        name="李四",
        evidence={"name": "李四"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 261880009",
    )
    assert fields.name is None


def test_empty_answer_segment_rejects_question_evidence():
    fields = _fields(
        name="张三",
        student_id="261880009",
        evidence={"name": "张三", "student_id": "261880009"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名：张三 学号：261880009",
        answer="",
    )
    assert fields.name is None
    assert fields.student_id is None


def test_name_embedded_bo_not_admission_evidence():
    fields = _fields(
        name="王博",
        major="生物学",
        admission_type="博士",
        evidence={"name": "王博", "major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="王博 生物学",
    )
    assert fields.name == "王博"
    assert fields.major == "生物学"
    assert fields.admission_type is None


def test_name_ending_bo_without_validated_name_not_peeled():
    """「欧阳博」must not become doctoral evidence when AI omits name."""
    fields = _fields(
        name=None,
        major="生物学",
        admission_type="博士",
        evidence={"major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="欧阳博 生物学",
    )
    assert fields.admission_type is None


def test_compact_major_bo_after_validated_name_kept():
    """「陈俊毅生物学博」with validated name may keep admission_type=博士."""
    fields = _fields(
        name="陈俊毅",
        major="生物学",
        admission_type="博士",
        evidence={"name": "陈俊毅", "major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="陈俊毅生物学博",
    )
    assert fields.name == "陈俊毅"
    assert fields.major == "生物学"
    assert fields.admission_type == "博士"


def test_semicolon_separated_bo_kept():
    fields = _fields(
        name="陈俊毅",
        major="生物学",
        admission_type="博士",
        evidence={"name": "陈俊毅", "major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="姓名 专业 硕or博",
        answer="陈俊毅；博；生物学",
    )
    assert fields.admission_type == "博士"


def test_fullwidth_plus_separated_bo_kept():
    fields = _fields(
        name="陈俊毅",
        major="生物学",
        admission_type="博士",
        evidence={"name": "陈俊毅", "major": "生物学", "admission_type": "博"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="陈俊毅＋博＋生物学",
    )
    assert fields.admission_type == "博士"


def test_msc_alone_is_master():
    fields = _fields(
        name="张三",
        major="生物学",
        admission_type="硕士",
        evidence={"name": "张三", "major": "生物学", "admission_type": "MSc"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 生物学 MSc",
    )
    assert fields.admission_type == "硕士"


def test_msc_slash_phd_ambiguous():
    fields = _fields(
        name="张三",
        major="生物学",
        admission_type="博士",
        evidence={"name": "张三", "major": "生物学", "admission_type": "PhD"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 生物学 MSc/PhD",
    )
    assert fields.admission_type is None
    assert fields.ambiguous is True


def test_drama_msc_does_not_false_doctor_from_dr_substring():
    fields = _fields(
        name="张三",
        major="Drama",
        admission_type="硕士",
        evidence={"name": "张三", "major": "Drama", "admission_type": "MSc"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 Drama MSc",
    )
    assert fields.admission_type == "硕士"
    assert fields.ambiguous is False


def test_drama_slash_phd_not_ambiguous_placeholder():
    fields = _fields(
        name="张三",
        major="Drama",
        admission_type="博士",
        evidence={"name": "张三", "major": "Drama", "admission_type": "PhD"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 Drama/PhD",
    )
    assert fields.admission_type == "博士"
    assert fields.ambiguous is False


def test_cinema_slash_dr_not_ambiguous_placeholder():
    fields = _fields(
        name="张三",
        major="Cinema",
        admission_type="博士",
        evidence={"name": "张三", "major": "Cinema", "admission_type": "Dr"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="张三 Cinema/Dr",
    )
    assert fields.admission_type == "博士"
    assert fields.ambiguous is False


def test_name_containing_shuobo_does_not_make_doctor_ambiguous():
    fields = _fields(
        name="王硕博",
        major="生物学",
        admission_type="博士",
        evidence={"name": "王硕博", "major": "生物学", "admission_type": "博士"},
    )
    fields = validate_ai_fields(
        fields,
        question="",
        answer="王硕博 生物学 博士",
    )
    assert fields.admission_type == "博士"
    assert fields.ambiguous is False
