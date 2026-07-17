"""Tests for graduate roster-assisted parse completion (v0.4.13)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from graduate.decision import make_graduate_decision
from graduate.matcher import match_graduate
from graduate.models import GraduateStudent
from graduate.parser import parse_graduate_comment
from graduate.roster_parser import complete_graduate_parse_from_roster
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.runtime_store import RuntimeStore
from graduate.cache import GraduateStudentCache


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _student(**kwargs) -> GraduateStudent:
    base = dict(
        source_id="1",
        admission_type="博士",
        college="测试学院",
        major_code="071000",
        major_name="生物学",
        name="张三",
        key="1",
    )
    base.update(kwargs)
    s = GraduateStudent(**base)
    if not s.key or s.key == "1":
        s.key = f"{s.name}:{s.admission_type}:{s.major_code}"
    return s


def _fixture_students() -> list[GraduateStudent]:
    return [
        _student(name="张三", admission_type="博士", major_name="生物学", key="s1"),
        _student(name="张三", admission_type="硕士", major_name="生物学", key="s2"),
        _student(
            name="李明",
            admission_type="硕士",
            major_code="070200",
            major_name="物理学",
            key="s3",
        ),
        _student(
            name="李明阳",
            admission_type="博士",
            major_code="070300",
            major_name="信息科学",
            key="s4",
        ),
        _student(
            name="王博",
            admission_type="硕士",
            major_code="071001",
            major_name="生物学",
            key="s5",
        ),
    ]


def _parse_and_complete(comment: str, students: list[GraduateStudent] | None = None):
    roster = students if students is not None else _fixture_students()
    parsed = parse_graduate_comment(comment)
    return complete_graduate_parse_from_roster(parsed, roster)


def test_zhang_san_biology_phd_compact():
    parsed = _parse_and_complete("答案：张三生物学博")
    assert parsed.name == "张三"
    assert parsed.major_text == "生物学"
    assert parsed.admission_type == "博士"
    match = match_graduate(parsed, _fixture_students())
    assert match.strength == "strong"
    assert match.candidate_count == 1


def test_zhang_san_biology_master_compact():
    parsed = _parse_and_complete("张三生物学硕")
    assert parsed.admission_type == "硕士"
    match = match_graduate(parsed, _fixture_students())
    assert match.strength == "strong"
    assert match.matched_student is not None
    assert match.matched_student.admission_type == "硕士"


def test_zhang_san_biology_no_type_not_strong():
    parsed = _parse_and_complete("张三生物学")
    assert parsed.name == "张三"
    assert parsed.major_text == "生物学"
    assert parsed.admission_type is None
    match = match_graduate(parsed, _fixture_students())
    assert match.strength != "strong"


def test_major_only_no_name():
    parsed = _parse_and_complete("生物学博")
    assert parsed.name is None
    match = match_graduate(parsed, _fixture_students())
    assert match.strength != "strong"


def test_wang_bo_name_not_doctor_type():
    parsed = _parse_and_complete("王博生物学")
    assert parsed.name == "王博"
    assert parsed.admission_type is None
    match = match_graduate(parsed, _fixture_students())
    assert match.strength != "strong"


def test_li_ming_yang_longest_name_match():
    parsed = _parse_and_complete("李明阳信息科学博")
    assert parsed.name == "李明阳"
    assert parsed.major_text == "信息科学"
    assert parsed.admission_type == "博士"


def test_li_ming_short_name_match():
    parsed = _parse_and_complete("李明信息科学硕")
    assert parsed.name == "李明"
    assert parsed.major_text == "信息科学"
    assert parsed.admission_type == "硕士"


def test_two_different_names_no_completion():
    parsed = _parse_and_complete("张三生物学博李明物理学硕")
    assert parsed.name is None
    assert "multiple roster names" in parsed.parse_errors


def test_roster_parse_disabled_in_pipeline(tmp_path):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
                "grad_roster_parse_enabled": False,
            }
        )
    )
    students = _fixture_students()
    cache = GraduateStudentCache(tmp_path)
    cache.save_students(students)
    pipe = AuditPipeline(
        settings,
        MagicMock(),
        AuditLog(tmp_path / "audit.jsonl", settings),
        RuntimeStore(tmp_path / "runtime.json"),
        StudentCache(tmp_path),
        MagicMock(),
        MagicMock(),
        grad_cache=cache,
    )
    event = GroupJoinRequest(
        group_id="200",
        user_id="1",
        comment="张三生物学博",
        flag="f",
        sub_type="add",
    )
    ev = pipe._evaluate_graduate_request(event)
    assert ev.parsed.name is None
    assert ev.match.strength != "strong"


def test_spaced_labeled_input_still_strong():
    students = _fixture_students()
    parsed = parse_graduate_comment("姓名：张三 类型：博 专业：生物学")
    parsed = complete_graduate_parse_from_roster(parsed, students)
    match = match_graduate(parsed, students)
    assert match.strength == "strong"


def test_existing_name_without_span_skips_major_type():
    students = _fixture_students()
    parsed = parse_graduate_comment("生物学博")
    parsed.name = "张三"
    parsed = complete_graduate_parse_from_roster(parsed, students)
    assert parsed.major_text is None
    assert parsed.admission_type is None


def test_decision_still_manual_when_incomplete():
    parsed = _parse_and_complete("张三生物学")
    match = match_graduate(parsed, _fixture_students())
    decision = make_graduate_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"
