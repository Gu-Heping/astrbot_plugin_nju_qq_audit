"""Manual reparse for pending requests."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.modules.setdefault("astrbot", MagicMock())
sys.modules.setdefault("astrbot.api", MagicMock())
sys.modules["astrbot.api"].logger = MagicMock()

from admin.reparse import format_reparse_preview, parse_reparse_args
from config import load_settings
from core.pipeline import AuditPipeline
from data_source.student_cache import StudentCache
from data_source.students import ActionResult, PendingRequest, Student
from storage.audit_log import AuditLog
from storage.blacklist_store import BlacklistStore
from storage.requests_store import RequestsStore
from storage.runtime_store import RuntimeStore

GROUP = "796836121"


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**kwargs):
    base = {
        "target_group_ids": GROUP,
        "mode": "record-only",
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


def _pipeline(tmp_path, *, settings=None):
    settings = settings or _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    cache.save_students(
        [
            Student(
                key="261200028",
                name="高煜韬",
                student_id="261200028",
                notice_no="20260028",
                major="环境与健康实验班",
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
    return pipe, requests, actions, settings


def _pending(**kwargs) -> PendingRequest:
    defaults = dict(
        id="REQ-reparse-1",
        group_id=GROUP,
        user_id="2874068048",
        comment="问题：姓名 学号/录取号 专业\n答案：高煜韬-261200028-环境与健康实验班",
        flag="flag-reparse",
        sub_type="add",
        profile="undergraduate",
        parsed={
            "name": None,
            "student_id": "261200028",
            "major": None,
            "parse_errors": ["ai_parse_used", "ai_parse_shadow"],
        },
        match={"strength": "weak"},
        decision="manual_review",
        confidence=0.4,
        reason="仅学号匹配，缺少姓名，需人工复核",
        mode="record-only",
        status="pending",
        created_at="2026-07-23T00:00:00+00:00",
        match_strength="weak",
    )
    defaults.update(kwargs)
    return PendingRequest(**defaults)


def test_parse_reparse_args():
    assert parse_reparse_args("18", "preview") == {
        "ref": "18",
        "mode": "auto",
        "action": "preview",
    }
    assert parse_reparse_args("18", "ai", "confirm") == {
        "ref": "18",
        "mode": "ai",
        "action": "confirm",
    }
    assert parse_reparse_args("18", "rule") is None


@pytest.mark.asyncio
async def test_reparse_preview_does_not_write_store(tmp_path):
    pipe, requests, actions, _settings = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    outcome = await pipe.reparse_pending(req, mode="rule", apply=False)
    assert outcome.ok
    assert outcome.applied is False
    assert outcome.new_strength == "strong"
    assert outcome.new_parsed["name"] == "高煜韬"
    assert outcome.new_parsed["major"] == "环境与健康实验班"
    latest = await requests.get_by_id(req.id)
    assert latest.parsed.get("name") in (None, "")
    assert latest.match_strength == "weak"
    actions.set_group_add_request.assert_not_awaited()
    text = format_reparse_preview(outcome, index=18)
    assert "重解析预览 [18]" in text
    assert "高煜韬" in text
    assert "strong" in text


@pytest.mark.asyncio
async def test_reparse_confirm_updates_without_qq_action(tmp_path):
    pipe, requests, actions, _settings = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    outcome = await pipe.reparse_pending(
        req, mode="rule", apply=True, admin_user_id="admin"
    )
    assert outcome.ok
    assert outcome.applied is True
    latest = await requests.get_by_id(req.id)
    assert latest.parsed["name"] == "高煜韬"
    assert latest.parsed["student_id"] == "261200028"
    assert latest.parsed["major"] == "环境与健康实验班"
    assert latest.match_strength == "strong"
    assert latest.decision in {"approve", "manual_review"}
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_reparse_rule_does_not_call_ai(tmp_path):
    pipe, requests, _actions, _settings = _pipeline(tmp_path)
    req = _pending()
    await requests.upsert(req)
    called = {"n": 0}

    def fake_client(messages, settings):
        called["n"] += 1
        return ('{"name":"高煜韬","student_id":"261200028","major":"环境与健康实验班"}', "m")

    outcome = await pipe.reparse_pending(
        req, mode="rule", apply=False, ai_client_call=fake_client
    )
    assert outcome.ok
    assert called["n"] == 0
    assert outcome.ai_invoked is False


@pytest.mark.asyncio
async def test_reparse_ai_bypasses_old_attempt_markers(tmp_path):
    pipe, requests, _actions, _settings_obj = _pipeline(
        tmp_path,
        settings=_settings(ai_parse_shadow_mode=False),
    )
    # Old stored markers must not block a forced AI reparse.
    req = _pending(
        parsed={
            "name": None,
            "student_id": "261200028",
            "major": None,
            "parse_errors": ["ai_parse_used", "ai_parse_shadow"],
        },
    )
    await requests.upsert(req)
    called = {"n": 0}

    def fake_client(messages, _settings):
        called["n"] += 1
        # Echo fields that already appear in the answer so validator keeps them.
        return (
            '{"name":"高煜韬","student_id":"261200028","major":"环境与健康实验班","exam_no":null,"notice_no":null,"academy":null,"admission_type":null}',
            "test-model",
        )

    outcome = await pipe.reparse_pending(
        req, mode="ai", apply=True, ai_client_call=fake_client
    )
    assert outcome.ok
    assert called["n"] == 1
    assert outcome.ai_invoked is True
    latest = await requests.get_by_id(req.id)
    assert latest.parsed.get("name") == "高煜韬"
    assert latest.parsed.get("student_id") == "261200028"
    assert latest.match_strength == "strong"


@pytest.mark.asyncio
async def test_reparse_ai_overwrite_suspicious_glued_fields(tmp_path, monkeypatch):
    """Force AI reparse may overwrite suspicious name/major; never calls QQ."""
    pipe, requests, actions, _settings_obj = _pipeline(
        tmp_path,
        settings=_settings(ai_parse_shadow_mode=False),
    )
    comment = "张三计算机科学与技术261220001"
    req = _pending(
        comment=comment,
        reason="未找到匹配记录",
        parsed={
            "name": "学与技术",
            "student_id": "261220001",
            "major": "张三计算机科学与技术261220001",
            "parse_errors": [],
        },
        match={"strength": "none"},
        match_strength="none",
    )
    await requests.upsert(req)

    # Keep deterministic parse broken so overwrite path is exercised.
    from core.parser import ParsedApplication

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(
            raw=comment,
            name="学与技术",
            student_id="261220001",
            major="张三计算机科学与技术261220001",
        )

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, _settings):
        return (
            _ai_json_reparse(
                name="张三",
                student_id="261220001",
                major="计算机科学与技术",
            ),
            "test-model",
        )

    outcome = await pipe.reparse_pending(
        req, mode="ai", apply=True, ai_client_call=fake_client
    )
    assert outcome.ok
    latest = await requests.get_by_id(req.id)
    assert latest.parsed["name"] == "张三"
    assert latest.parsed["student_id"] == "261220001"
    assert latest.parsed["major"] == "计算机科学与技术"
    actions.set_group_add_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_reparse_preview_hints_when_ai_unchanged(tmp_path, monkeypatch):
    pipe, requests, _actions, _settings_obj = _pipeline(
        tmp_path,
        settings=_settings(ai_parse_shadow_mode=False),
    )
    comment = "答案：学与技术261220001"
    req = _pending(
        comment=comment,
        parsed={
            "name": "学与技术",
            "student_id": "261220001",
            "major": None,
            "parse_errors": [],
        },
    )
    await requests.upsert(req)

    from core.parser import ParsedApplication

    def fake_parse(_comment: str) -> ParsedApplication:
        return ParsedApplication(
            raw=comment,
            name="学与技术",
            student_id="261220001",
            major=None,
        )

    monkeypatch.setattr("core.pipeline.parse_application_comment", fake_parse)

    def fake_client(messages, _settings):
        # Invented major not in answer → validator drops → no field change
        return (
            _ai_json_reparse(name="张三", student_id="261220001", major="不存在的专业"),
            "test-model",
        )

    outcome = await pipe.reparse_pending(
        req, mode="ai", apply=False, ai_client_call=fake_client
    )
    assert outcome.ai_invoked is True
    text = format_reparse_preview(outcome, index=13)
    assert "AI：已调用" in text
    assert "未改变解析结果" in text


def _ai_json_reparse(**kwargs) -> str:
    import json

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


@pytest.mark.asyncio
async def test_reparse_rejects_terminal_statuses(tmp_path):
    pipe, requests, _actions, _settings = _pipeline(tmp_path)
    for status in ("processed", "dismissed", "stale"):
        req = _pending(id=f"REQ-{status}", status=status, processed_at="2026-07-23T01:00:00+00:00")
        await requests.upsert(req)
        outcome = await pipe.reparse_pending(req, mode="auto", apply=True)
        assert outcome.ok is False
        assert "pending" in outcome.message


@pytest.mark.asyncio
async def test_reparse_resolves_missing_profile_from_grad_group(tmp_path):
    from graduate.cache import GraduateStudentCache
    from graduate.models import GraduateStudent

    grad_group = "200"
    settings = _settings(
        target_group_ids=GROUP,
        grad_enabled=True,
        grad_target_group_ids=grad_group,
        ai_parse_enabled=False,
    )
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    runtime = RuntimeStore(tmp_path / "runtime.json")
    cache = StudentCache(tmp_path)
    grad_cache = GraduateStudentCache(tmp_path)
    grad_cache.save_students(
        [
            GraduateStudent(
                source_id="1",
                admission_type="博士",
                college="生命科学学院",
                major_code="071001",
                major_name="生物学",
                name="张三",
                key="张三:博士:071001",
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
        grad_cache=grad_cache,
    )
    req = PendingRequest(
        id="REQ-grad-legacy",
        group_id=grad_group,
        user_id="123456789",
        comment="张三 生物学 博",
        flag="flag-grad",
        sub_type="add",
        profile=None,  # historical missing profile
        parsed={"name": None},
        match={"strength": "none"},
        decision="manual_review",
        confidence=0.1,
        reason="信息不足",
        mode="record-only",
        status="pending",
        created_at="2026-07-23T00:00:00+00:00",
        match_strength="none",
    )
    await requests.upsert(req)
    # Pass the in-memory object with profile=None (store may coerce missing profile).
    outcome = await pipe.reparse_pending(req, mode="rule", apply=True)
    assert outcome.ok
    latest = await requests.get_by_id(req.id)
    assert latest.profile == "graduate"
    assert latest.parsed.get("name") == "张三"
    assert latest.parsed.get("admission_type") == "博士"
    assert latest.parsed.get("_profile") == "graduate"
    actions.set_group_add_request.assert_not_awaited()
    records = audit.read_all()
    assert any(
        r.get("type") == "pending_reparsed" and r.get("profile") == "graduate"
        for r in records
    )
