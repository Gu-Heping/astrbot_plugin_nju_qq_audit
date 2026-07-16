"""Graduate audit profile: config, parser, matcher, pipeline routing."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings, validate_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest
from graduate.cache import GraduateStudentCache
from graduate.decision import make_graduate_decision
from graduate.matcher import match_graduate
from graduate.models import GraduateStudent, SENSITIVE_GRAD_FIELD_NAMES
from graduate.njutable_provider import map_row_to_graduate, strip_sensitive_grad_fields
from graduate.parser import normalize_admission_type, parse_graduate_comment
from onebot.event_extract import GroupJoinRequest
from profiles.router import resolve_profile
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore, new_request_id
from storage.runtime_store import RuntimeStore


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _grad_student(**kwargs) -> GraduateStudent:
    base = dict(
        source_id="1",
        admission_type="硕士",
        college="哲学学院",
        major_code="010101",
        major_name="马克思主义哲学",
        name="刘尚明",
        key="1",
    )
    base.update(kwargs)
    return GraduateStudent(**base)


def test_resolve_undergraduate_and_graduate():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
            }
        )
    )
    assert resolve_profile("100", settings) == "undergraduate"
    assert resolve_profile("200", settings) == "graduate"
    assert resolve_profile("999", settings) is None


def test_resolve_overlap_returns_none(caplog):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100,200",
                "grad_enabled": True,
                "grad_target_group_ids": "200,300",
            }
        )
    )
    with caplog.at_level(logging.WARNING):
        assert resolve_profile("200", settings) is None
    warnings = validate_settings(settings)
    assert any("重叠" in w for w in warnings)


def test_grad_disabled_skips_graduate_group():
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": False,
                "grad_target_group_ids": "200",
            }
        )
    )
    assert resolve_profile("200", settings) is None


def test_grad_disabled_overlap_still_routes_undergraduate():
    """When grad is off, listed grad groups must not block undergrad targets."""
    from profiles.router import configured_audit_group_ids, overlapping_group_ids

    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100,200",
                "grad_enabled": False,
                "grad_target_group_ids": "200,300",
            }
        )
    )
    assert overlapping_group_ids(settings) == frozenset()
    assert resolve_profile("200", settings) == "undergraduate"
    assert resolve_profile("100", settings) == "undergraduate"
    assert resolve_profile("300", settings) is None
    assert configured_audit_group_ids(settings) == frozenset({"100", "200"})
    warnings = validate_settings(settings)
    assert not any("重叠" in w for w in warnings)


def test_configured_audit_group_ids_includes_grad_when_enabled():
    from profiles.router import configured_audit_group_ids

    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": True,
                "grad_target_group_ids": "200",
            }
        )
    )
    assert configured_audit_group_ids(settings) == frozenset({"100", "200"})


def test_graduate_student_has_no_id_card_tail_field():
    s = _grad_student()
    data = s.to_dict()
    assert "证件号码末三位" not in data
    assert not any("证件" in k for k in data)
    loaded = GraduateStudent.from_dict(
        {**data, "证件号码末三位": "015", "id_card_tail": "015"}
    )
    assert "证件号码末三位" not in loaded.to_dict()


def test_grad_cache_files_separated(tmp_path):
    under = StudentCache(tmp_path)
    grad = GraduateStudentCache(tmp_path)
    assert under.cache_path.name == "students.cache.json"
    assert grad.cache_path.name == "grad_students.cache.json"
    assert under.sync_state_path != grad.sync_state_path
    grad.save_students([_grad_student()])
    assert under.load_students() == []
    assert len(grad.load_students()) == 1


def test_provider_strips_id_card_tail():
    settings = load_settings(DummyConfig({}))
    row = {
        "id": "1",
        "录取类型": "硕士",
        "录取学院": "哲学学院",
        "录取专业代码": "010101",
        "录取专业名称": "马克思主义哲学",
        "姓名": "刘尚明",
        "证件号码末三位": "015",
        "_short_code_id": "197391",
    }
    cleaned = strip_sensitive_grad_fields(row)
    assert "证件号码末三位" not in cleaned
    student = map_row_to_graduate(row, settings)
    assert student is not None
    assert student.name == "刘尚明"
    assert "015" not in str(student.to_dict())


@pytest.mark.parametrize(
    "raw,name,major,adm",
    [
        ("刘尚明 马克思主义哲学 硕", "刘尚明", "马克思主义哲学", "硕士"),
        ("刘尚明 硕士 马克思主义哲学", "刘尚明", "马克思主义哲学", "硕士"),
        (
            "问题：姓名 专业 硕博\n答案：刘尚明 马克思主义哲学 硕",
            "刘尚明",
            "马克思主义哲学",
            "硕士",
        ),
        ("姓名：刘尚明 专业：马克思主义哲学 类型：硕士", "刘尚明", "马克思主义哲学", "硕士"),
        ("刘尚明 010101 硕", "刘尚明", None, "硕士"),
    ],
)
def test_grad_parser_formats(raw, name, major, adm):
    parsed = parse_graduate_comment(raw)
    assert parsed.name == name
    assert parsed.admission_type == adm
    if major:
        assert parsed.major_text == major
    if "010101" in raw:
        assert "010101" in parsed.major_code_candidates


def test_grad_parser_major_code_label_not_eaten_by_major():
    parsed = parse_graduate_comment("姓名：刘尚明 专业代码：010101 类型：硕")
    assert parsed.name == "刘尚明"
    assert parsed.major_text is None or parsed.major_text != "代码"
    assert "010101" in parsed.major_code_candidates
    assert parsed.admission_type == "硕士"


def test_grad_parser_name_stops_before_major_label():
    parsed = parse_graduate_comment("姓名：张三专业：马克思主义哲学 类型：硕")
    assert parsed.name == "张三"
    assert parsed.major_text == "马克思主义哲学"
    assert parsed.admission_type == "硕士"


def test_grad_parser_major_stops_before_type_label():
    parsed = parse_graduate_comment("姓名：张三专业：马克思主义哲学类型：硕")
    assert parsed.name == "张三"
    assert parsed.major_text == "马克思主义哲学"
    assert parsed.admission_type == "硕士"


def test_grad_parser_name_stops_before_luqu_label():
    parsed = parse_graduate_comment("姓名：张三录取专业名称：马克思主义哲学 类型：硕")
    assert parsed.name == "张三"
    assert parsed.major_text == "马克思主义哲学"
    assert parsed.admission_type == "硕士"


def test_grad_parser_type_stops_before_major_label():
    parsed = parse_graduate_comment("姓名：张三 类型：硕专业：马克思主义哲学")
    assert parsed.name == "张三"
    assert parsed.admission_type == "硕士"
    assert parsed.major_text == "马克思主义哲学"


def test_grad_parser_labeled_major_with_embedded_code():
    parsed = parse_graduate_comment("姓名：刘尚明 专业：085400电子信息 类型：硕")
    assert parsed.name == "刘尚明"
    assert "085400" in parsed.major_code_candidates
    assert parsed.major_text == "电子信息"
    assert parsed.admission_type == "硕士"


def test_grad_parser_shuo_bo_placeholder_not_concrete_type():
    parsed = parse_graduate_comment("刘尚明 马克思主义哲学 硕/博")
    assert parsed.name == "刘尚明"
    assert parsed.major_text == "马克思主义哲学"
    assert parsed.admission_type is None


def test_grad_parser_does_not_force_graduate_as_master():
    assert normalize_admission_type("研究生") is None
    parsed = parse_graduate_comment("刘尚明 马克思主义哲学 研究生")
    assert parsed.admission_type is None


def test_match_strong_name_major_master_unique():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 马克思主义哲学 硕")
    match = match_graduate(parsed, students)
    assert match.strength == "strong"
    decision = make_graduate_decision(parsed, match, is_target_group=True)
    assert decision.decision == "approve"


def test_match_name_major_without_type_manual():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 马克思主义哲学")
    match = match_graduate(parsed, students)
    assert match.strength == "weak"
    decision = make_graduate_decision(parsed, match, is_target_group=True)
    assert decision.decision == "manual_review"


def test_match_name_type_without_major_manual():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 硕")
    match = match_graduate(parsed, students)
    assert match.strength == "weak"
    assert make_graduate_decision(parsed, match, is_target_group=True).decision == "manual_review"


def test_match_multi_candidate_manual():
    students = [
        _grad_student(source_id="1", key="1", major_name="马克思主义哲学"),
        _grad_student(
            source_id="2",
            key="2",
            major_code="010102",
            major_name="中国哲学",
        ),
    ]
    parsed = parse_graduate_comment("刘尚明 硕")
    match = match_graduate(parsed, students)
    assert match.candidate_count == 2
    assert match.strength == "weak"


def test_match_major_only_manual():
    students = [_grad_student()]
    parsed = parse_graduate_comment("马克思主义哲学")
    match = match_graduate(parsed, students)
    assert match.strength == "none"
    assert make_graduate_decision(parsed, match, is_target_group=True).decision == "manual_review"


def test_match_major_code():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 010101 硕")
    match = match_graduate(parsed, students)
    assert match.strength == "strong"


def test_match_conflicting_major_code_and_name_not_strong():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 010101 中国哲学 硕")
    match = match_graduate(parsed, students)
    assert match.strength != "strong"
    assert "不匹配" in match.reason or match.strength in {"none", "weak"}


def test_match_shuo_bo_placeholder_not_strong():
    students = [_grad_student()]
    parsed = parse_graduate_comment("刘尚明 马克思主义哲学 硕/博")
    match = match_graduate(parsed, students)
    assert parsed.admission_type is None
    assert match.strength != "strong"


def test_match_phd_normalization():
    students = [_grad_student(admission_type="博士", major_name="中国哲学", major_code="010102")]
    for token in ("博", "博士", "phd"):
        parsed = parse_graduate_comment(f"刘尚明 中国哲学 {token}")
        assert parsed.admission_type == "博士"
        assert match_graduate(parsed, students).strength == "strong"


def _pipeline(tmp_path: Path, *, grad_enabled=True):
    settings = load_settings(
        DummyConfig(
            {
                "target_group_ids": "100",
                "grad_enabled": grad_enabled,
                "grad_target_group_ids": "200",
                "admin_qq_ids": "1",
                "admin_notify": False,
                "mode": "auto",
                "student_source": "mock",
            }
        )
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    from data_source.mock_provider import generate_mock_students

    cache.save_students(generate_mock_students())
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_students([_grad_student()])
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, message="ok")
    )
    pipe = AuditPipeline(
        settings, requests, audit, runtime, cache, actions, None, grad_cache=grad_cache
    )
    return pipe, requests, actions


@pytest.mark.asyncio
async def test_pipeline_undergrad_unchanged_path(tmp_path):
    pipe, requests, actions = _pipeline(tmp_path)
    await pipe.runtime.set_mode("auto", "1")
    event = GroupJoinRequest(
        group_id="100",
        user_id="u1",
        comment="张三 261122001",
        flag="f-u",
        sub_type="add",
        raw_event={"time": 1000},
    )
    await pipe.handle_group_request(event)
    latest = await requests.get_by_flag("f-u")
    assert latest is not None
    assert latest.profile == "undergraduate"
    assert latest.match_strength == "strong"
    assert latest.status == "processed"
    actions.set_group_add_request.assert_awaited()


@pytest.mark.asyncio
async def test_pipeline_graduate_evaluator_and_profile(tmp_path):
    pipe, requests, actions = _pipeline(tmp_path)
    await pipe.runtime.set_mode("auto", "1")
    event = GroupJoinRequest(
        group_id="200",
        user_id="u2",
        comment="刘尚明 马克思主义哲学 硕",
        flag="f-g",
        sub_type="add",
        raw_event={"time": 1000},
    )
    await pipe.handle_group_request(event)
    latest = await requests.get_by_flag("f-g")
    assert latest is not None
    assert latest.profile == "graduate"
    assert latest.match_strength == "strong"
    assert latest.decision == "approve"
    assert latest.status == "processed"
    assert "证件" not in str(latest.parsed)
    actions.set_group_add_request.assert_awaited()


@pytest.mark.asyncio
async def test_list_view_marks_graduate_without_sensitive(tmp_path):
    from admin.ux_formatter import format_list, format_view

    pipe, requests, _ = _pipeline(tmp_path)
    req = PendingRequest(
        id=new_request_id(),
        group_id="200",
        user_id="u",
        comment="刘尚明 马克思主义哲学 硕",
        flag="fg",
        sub_type="add",
        decision="manual_review",
        confidence=0.5,
        reason="需人工",
        mode="auto",
        status="pending",
        created_at="2026-07-15T00:00:00+00:00",
        match_strength="weak",
        parsed={"name": "刘尚明", "admission_type": "硕士", "major_text": "马克思主义哲学"},
        match={"college": "哲学学院"},
        profile="graduate",
    )
    await requests.upsert(req)
    text = format_list([req], {1: req.id})
    assert "研究生" in text
    view = format_view(req, 1)
    assert "研究生" in view
    assert "录取类型" in view
    assert "证件" not in view
    assert "flag" not in view.lower() or "flag" not in view  # no flag leakage


@pytest.mark.asyncio
async def test_graduate_comment_update_reparse(tmp_path):
    pipe, requests, actions = _pipeline(tmp_path)
    await pipe.runtime.set_mode("record-only", "1")
    first = GroupJoinRequest(
        group_id="200",
        user_id="u3",
        comment="刘尚明",
        flag="f-upd",
        sub_type="add",
        raw_event={"time": 1},
    )
    await pipe.handle_group_request(first)
    second = GroupJoinRequest(
        group_id="200",
        user_id="u3",
        comment="刘尚明 马克思主义哲学 硕",
        flag="f-upd",
        sub_type="add",
        raw_event={"time": 2},
    )
    await pipe.handle_group_request(second)
    latest = await requests.get_by_flag("f-upd")
    assert latest.profile == "graduate"
    assert latest.match_strength == "strong"
    assert latest.decision == "approve"


@pytest.mark.asyncio
async def test_reconcile_covers_graduate_group(tmp_path):
    pipe, requests, _ = _pipeline(tmp_path)
    req = PendingRequest(
        id=new_request_id(),
        group_id="200",
        user_id="u-ext",
        comment="刘尚明 马克思主义哲学 硕",
        flag="f-ext",
        sub_type="add",
        decision="approve",
        confidence=0.9,
        reason="ok",
        mode="auto",
        status="pending",
        created_at="2026-07-15T00:00:00+00:00",
        match_strength="strong",
        profile="graduate",
    )
    await requests.upsert(req)
    result = await pipe.reconcile_external_join("200", "u-ext", notice_sub_type="approve")
    assert result.handled
    latest = await requests.get_by_id(req.id)
    assert latest.status != "pending"


@pytest.mark.asyncio
async def test_sweep_skips_graduate_pending(tmp_path):
    from admin.sweep import is_sweep_candidate, collect_sweep_preview

    pipe, requests, _ = _pipeline(tmp_path)
    under = PendingRequest(
        id=new_request_id(),
        group_id="100",
        user_id="u-u",
        comment="杂讯",
        flag="fu",
        sub_type="add",
        decision="manual_review",
        confidence=0.2,
        reason="需人工",
        mode="auto",
        status="pending",
        created_at="2026-07-15T00:00:00+00:00",
        match_strength="none",
        profile="undergraduate",
    )
    grad = PendingRequest(
        id=new_request_id(),
        group_id="200",
        user_id="u-g",
        comment="杂讯",
        flag="fg",
        sub_type="add",
        decision="manual_review",
        confidence=0.2,
        reason="需人工",
        mode="auto",
        status="pending",
        created_at="2026-07-15T00:00:00+00:00",
        match_strength="none",
        profile="graduate",
    )
    await requests.upsert(under)
    await requests.upsert(grad)
    assert is_sweep_candidate(under)
    assert not is_sweep_candidate(grad)
    preview = await collect_sweep_preview(pipe)
    assert under.id in {r.id for r in preview.candidates}
    assert grad.id not in {r.id for r in preview.candidates}


@pytest.mark.asyncio
async def test_rematch_can_skip_graduate_profile(tmp_path):
    pipe, requests, _ = _pipeline(tmp_path)
    grad = PendingRequest(
        id=new_request_id(),
        group_id="200",
        user_id="u-rm",
        comment="刘尚明 马克思主义哲学 硕",
        flag="frm",
        sub_type="add",
        decision="manual_review",
        confidence=0.2,
        reason="旧",
        mode="auto",
        status="pending",
        created_at="2026-07-15T00:00:00+00:00",
        match_strength="none",
        parsed={},
        match={"strength": "none"},
        profile="graduate",
    )
    await requests.upsert(grad)
    summary = await pipe.rematch_active_pending(
        source="test", profiles=frozenset({"undergraduate"})
    )
    assert summary.changed == 0
    latest = await requests.get_by_id(grad.id)
    assert latest.match_strength == "none"
    summary2 = await pipe.rematch_active_pending(source="test")
    assert summary2.changed >= 1
    latest2 = await requests.get_by_id(grad.id)
    assert latest2.match_strength == "strong"


def test_manual_review_notice_without_index_falls_back_to_list():
    from admin.ux_formatter import format_manual_review_notice

    text = format_manual_review_notice(
        index=None,
        group_id="200",
        user_id="u",
        comment="刘尚明 马克思主义哲学 硕",
        judgement="需人工",
        profile="graduate",
        parsed={"name": "刘尚明", "admission_type": "硕士", "major_text": "马克思主义哲学"},
    )
    assert "/audit view ?" not in text
    assert "/audit ok ?" not in text
    assert "/audit list" in text
    assert "类型：研究生" in text
