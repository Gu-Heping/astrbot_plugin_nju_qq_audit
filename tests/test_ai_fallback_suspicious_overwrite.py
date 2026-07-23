"""Online AI fallback may correct suspicious non-strong undergrad parses."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from config import load_settings
from core.ai_parser.models import AiParsedFields
from core.ai_parser.service import (
    merge_ai_fields_into_undergrad_parsed,
    undergrad_parse_suspicious,
)
from core.parser import ParsedApplication
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, Student
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import AuditLog
from storage.blacklist_store import BlacklistStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP = "796836121"
NAME = "张三"
SID = "261220001"
MAJOR = "计算机科学与技术"
GLUED = f"{NAME}{MAJOR}{SID}"
BAD_NAME = "学与技术"
BAD_MAJOR = GLUED
NONE_NAME = "已录取"
NONE_PERSON = "王五"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**kwargs):
    base = {
        "target_group_ids": GROUP,
        "mode": "auto",
        "admin_notify": False,
        "student_source": "mock",
        "ai_parse_enabled": True,
        "ai_parse_shadow_mode": False,
        "ai_parse_allow_auto_approve": False,
        "ai_parse_base_url": "http://example.invalid/v1",
        "ai_parse_model": "test-model",
    }
    base.update(kwargs)
    return load_settings(DummyConfig(base))


def _ai_json(**kwargs) -> str:
    base = {
        "profile": "undergraduate",
        "name": None,
        "student_id": None,
        "notice_no": None,
        "exam_no": None,
        "major": None,
        "academy": None,
        "admission_type": None,
        "confidence": 0.95,
        "ambiguous": False,
        "warnings": [],
        "evidence": {},
    }
    base.update(kwargs)
    ev = {}
    for key in ("name", "student_id", "notice_no", "exam_no", "major", "academy"):
        if base.get(key):
            ev[key] = base[key]
    base["evidence"] = ev
    return json.dumps(base, ensure_ascii=False)


def _pipeline(tmp_path, *, settings=None, students=None):
    settings = settings or _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    roster = (
        students
        if students is not None
        else [
            Student(
                key=SID,
                name=NAME,
                student_id=SID,
                notice_no="20260001",
                major=MAJOR,
                status="已确认",
                updated_at="2026-07-23T00:00:00+00:00",
            )
        ]
    )
    cache.save_students(roster)
    actions = MagicMock()
    actions.set_group_add_request = AsyncMock(
        return_value=ActionResult(ok=True, retcode=0, message="ok")
    )
    pipe = AuditPipeline(
        settings,
        requests,
        audit,
        runtime,
        cache,
        actions,
        MagicMock(),
        blacklist_store=BlacklistStore(tmp_path / "blacklist.json"),
    )
    return pipe, actions


def _event(comment: str) -> GroupJoinRequest:
    return GroupJoinRequest(
        group_id=GROUP,
        user_id="123456789",
        comment=comment,
        flag="flag-test",
        sub_type="add",
    )


def test_undergrad_parse_suspicious_glued_major_tail():
    parsed = ParsedApplication(
        raw=GLUED,
        name=BAD_NAME,
        student_id=SID,
        major=BAD_MAJOR,
    )
    assert undergrad_parse_suspicious(parsed) is True


@pytest.mark.asyncio
async def test_deterministic_strong_skips_ai(tmp_path):
    pipe, _actions = _pipeline(tmp_path)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return (_ai_json(name=NAME, student_id=SID, major=MAJOR), "test-model")

    comment = f"{NAME} {SID} {MAJOR}"
    ev = await pipe._evaluate_undergraduate_request(
        _event(comment),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.match.strength == "strong"
    assert called["n"] == 0
    assert "ai_parse_merged" not in (ev.parsed.parse_errors or [])


@pytest.mark.asyncio
async def test_non_strong_suspicious_ai_overwrite_then_rematch(tmp_path, monkeypatch):
    pipe, _actions = _pipeline(tmp_path)

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(
            raw=GLUED,
            name=BAD_NAME,
            student_id=SID,
            major=BAD_MAJOR,
        )

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return (_ai_json(name=NAME, student_id=SID, major=MAJOR), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(GLUED),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert called["n"] == 1
    assert ev.parsed.name == NAME
    assert ev.parsed.student_id == SID
    assert ev.parsed.major == MAJOR
    assert ev.match.strength == "strong"
    assert "ai_parse_merged" in ev.parsed.parse_errors
    assert "ai_parse_used" in ev.parsed.parse_errors


@pytest.mark.asyncio
async def test_ai_assisted_strong_stays_manual_by_default(tmp_path, monkeypatch):
    pipe, _actions = _pipeline(
        tmp_path,
        settings=_settings(mode="auto", ai_parse_allow_auto_approve=False),
    )

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(
            raw=GLUED,
            name=BAD_NAME,
            student_id=SID,
            major=BAD_MAJOR,
        )

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, settings):
        return (_ai_json(name=NAME, student_id=SID, major=MAJOR), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(GLUED),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.match.strength == "strong"
    assert ev.decision.decision == "manual_review"
    assert ev.decision.should_auto_approve is False
    assert "AI 辅助解析" in (ev.decision.reason or "")


@pytest.mark.asyncio
async def test_ai_assisted_strong_auto_approve_when_allowed(tmp_path, monkeypatch):
    pipe, _actions = _pipeline(
        tmp_path,
        settings=_settings(mode="auto", ai_parse_allow_auto_approve=True),
    )

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(
            raw=GLUED,
            name=BAD_NAME,
            student_id=SID,
            major=BAD_MAJOR,
        )

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, settings):
        return (_ai_json(name=NAME, student_id=SID, major=MAJOR), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(GLUED),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.match.strength == "strong"
    assert ev.decision.decision == "approve"
    assert ev.decision.should_auto_approve is True


def test_ai_does_not_overwrite_existing_student_id():
    parsed = ParsedApplication(
        raw=GLUED,
        name=BAD_NAME,
        student_id=SID,
        major=BAD_MAJOR,
    )
    ai = AiParsedFields(
        profile="undergraduate",
        name=NAME,
        student_id="261999999",
        major=MAJOR,
        evidence={"name": NAME, "student_id": "261999999", "major": MAJOR},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=GLUED,
    )
    assert parsed.student_id == SID
    assert parsed.name == NAME
    assert parsed.major == MAJOR


def test_ai_fields_absent_from_answer_not_overwritten():
    parsed = ParsedApplication(
        raw=GLUED,
        name=BAD_NAME,
        student_id=SID,
        major=BAD_MAJOR,
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
        answer_text=GLUED,
    )
    assert parsed.name == BAD_NAME
    assert parsed.major == BAD_MAJOR


@pytest.mark.asyncio
async def test_match_none_must_call_ai_and_fix_name_major(tmp_path, monkeypatch):
    students = [
        Student(
            key="k-none",
            name=NONE_PERSON,
            student_id="",
            notice_no="",
            major=MAJOR,
            status="已确认",
            updated_at="2026-07-23T00:00:00+00:00",
        )
    ]
    pipe, _actions = _pipeline(tmp_path, students=students)

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(raw=f"{NONE_NAME} {NONE_PERSON}", name=NONE_NAME, major=NONE_PERSON)

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return (_ai_json(name=NONE_PERSON), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(f"{NONE_NAME} {NONE_PERSON}"),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert called["n"] == 1
    assert ev.parsed.name == NONE_PERSON
    assert ev.parsed.major is None
    assert "match_none_before_ai" in ev.parsed.parse_errors


@pytest.mark.asyncio
async def test_match_none_ai_fill_id_then_strong_manual_when_guarded(tmp_path, monkeypatch):
    target_name = NONE_PERSON
    target_sid = SID
    comment = f"{NONE_NAME} {target_name} {target_sid}"
    pipe, _actions = _pipeline(
        tmp_path,
        settings=_settings(mode="auto", ai_parse_allow_auto_approve=False),
        students=[
            Student(
                key=target_sid,
                name=target_name,
                student_id=target_sid,
                notice_no="20260009",
                major=MAJOR,
                status="已确认",
                updated_at="2026-07-23T00:00:00+00:00",
            )
        ],
    )

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(raw=comment, name=NONE_NAME, major=target_name)

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, settings):
        return (_ai_json(name=target_name, student_id=target_sid, major=MAJOR), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(comment),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.match.strength == "strong"
    assert ev.parsed.name == target_name
    assert ev.parsed.student_id == target_sid
    # major is absent in answer, validator drops it; name+sid still rematch strong.
    assert ev.parsed.major is None
    assert "ai_parse_merged" in ev.parsed.parse_errors
    assert ev.decision.decision == "manual_review"
    assert ev.decision.should_auto_approve is False


@pytest.mark.asyncio
async def test_weak_not_suspicious_never_overwrites_existing_credential(tmp_path, monkeypatch):
    pipe, _actions = _pipeline(tmp_path)
    weak_comment = f"{NAME} {SID}"

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(raw=weak_comment, name=NAME, student_id=SID, major=None)

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return (_ai_json(name=NAME, student_id="261999999", major=MAJOR), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(weak_comment),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.parsed.student_id == SID
    assert ev.parsed.major in (None, MAJOR)
    # weak + not suspicious may skip AI by strategy; if invoked it still cannot overwrite sid.
    assert called["n"] in (0, 1)


@pytest.mark.asyncio
async def test_match_none_ai_name_outside_answer_not_overwritten(tmp_path, monkeypatch):
    comment = f"{NONE_NAME} {NONE_PERSON}"
    pipe, _actions = _pipeline(tmp_path)

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(raw=comment, name=NONE_NAME, major=NONE_PERSON)

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, settings):
        return (_ai_json(name="赵六"), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(comment),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert ev.parsed.name == NONE_NAME
    assert ev.parsed.major == NONE_PERSON
    assert "ai_parse_merged" not in ev.parsed.parse_errors
    assert "ai_parse_no_change" in ev.parsed.parse_errors


def test_ai_empty_fields_do_not_clear_major():
    raw = f"{NONE_NAME} {NONE_PERSON}"
    parsed = ParsedApplication(raw=raw, name=NONE_NAME, major=NONE_PERSON)
    parsed.parse_errors.append("match_none_before_ai")
    ai = AiParsedFields(profile="undergraduate")
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=raw,
    )
    assert parsed.name == NONE_NAME
    assert parsed.major == NONE_PERSON
    assert "ai_parse_merged" not in parsed.parse_errors
    assert "ai_parse_no_change" in parsed.parse_errors


def test_ai_noop_does_not_mark_merged():
    parsed = ParsedApplication(
        raw=f"{NAME} {SID}",
        name=NAME,
        student_id=SID,
        major=None,
    )
    ai = AiParsedFields(
        profile="undergraduate",
        name=NAME,
        student_id=SID,
        major=None,
        evidence={"name": NAME, "student_id": SID},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=f"{NAME} {SID}",
    )
    assert "ai_parse_used" in parsed.parse_errors
    assert "ai_parse_merged" not in parsed.parse_errors
    assert "ai_parse_no_change" in parsed.parse_errors


@pytest.mark.asyncio
async def test_match_none_roster_miss_ai_noop_not_merged(tmp_path, monkeypatch):
    # Rule parse already correct, but roster empty → match none; AI echoes same fields.
    pipe, _actions = _pipeline(tmp_path, students=[])
    comment = f"{NAME} {SID}"

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(raw=comment, name=NAME, student_id=SID, major=None)

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return (_ai_json(name=NAME, student_id=SID), "test-model")

    ev = await pipe._evaluate_undergraduate_request(
        _event(comment),
        allow_ai_parse=True,
        ai_client_call=fake_client,
    )
    assert called["n"] == 1
    assert ev.parsed.name == NAME
    assert ev.parsed.student_id == SID
    assert ev.parsed.major is None
    assert "ai_parse_merged" not in ev.parsed.parse_errors
    assert "ai_parse_used" in ev.parsed.parse_errors


def test_ai_status_name_fix_marks_merged():
    raw = f"{NONE_NAME} {NONE_PERSON}"
    parsed = ParsedApplication(raw=raw, name=NONE_NAME, major=NONE_PERSON)
    parsed.parse_errors.append("match_none_before_ai")
    ai = AiParsedFields(
        profile="undergraduate",
        name=NONE_PERSON,
        evidence={"name": NONE_PERSON},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=raw,
    )
    assert parsed.name == NONE_PERSON
    assert parsed.major is None
    assert "ai_parse_merged" in parsed.parse_errors
    assert "ai_parse_no_change" not in parsed.parse_errors


def test_ai_fill_student_id_marks_merged():
    raw = f"{NAME} {SID}"
    parsed = ParsedApplication(raw=raw, name=NAME, student_id=None)
    ai = AiParsedFields(
        profile="undergraduate",
        student_id=SID,
        evidence={"student_id": SID},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=raw,
    )
    assert parsed.student_id == SID
    assert "ai_parse_merged" in parsed.parse_errors


def test_ai_does_not_clear_legitimate_major():
    # Short majors that look name-like must not be wiped when AI only echoes name.
    law_major = "法学"
    raw = f"{NAME} {law_major}"
    parsed = ParsedApplication(raw=raw, name=NAME, major=law_major)
    ai = AiParsedFields(
        profile="undergraduate",
        name=NAME,
        evidence={"name": NAME},
    )
    merge_ai_fields_into_undergrad_parsed(
        parsed,
        ai,
        allow_overwrite=True,
        answer_text=raw,
    )
    assert parsed.name == NAME
    assert parsed.major == law_major
    assert "ai_parse_merged" not in parsed.parse_errors
    assert "ai_parse_no_change" in parsed.parse_errors
