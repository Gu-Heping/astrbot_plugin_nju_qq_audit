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
    cache.save_students(
        students
        or [
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
