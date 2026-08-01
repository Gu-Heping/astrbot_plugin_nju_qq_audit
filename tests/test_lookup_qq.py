"""Tests for /audit lookup qq command."""

from __future__ import annotations

import pytest

from admin.formatter import format_lookup_qq_result
from admin.lookup_qq import (
    LookupQqResult,
    LookupQqRecord,
    lookup_qq_records,
    validate_lookup_qq,
)
from admin.permissions import can_run_command
from config import load_settings
from data_source.students import PendingRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from tests.test_admin_commands import DummyEvent


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(**overrides):
    base = {"admin_qq_ids": "111", "target_group_ids": "796836121,2601"}
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _req(
    req_id: str,
    *,
    user_id: str = "123456789",
    group_id: str = "796836121",
    status: str = "pending",
    decision: str = "manual_review",
    reason: str = "弱匹配",
    created_at: str = "2026-07-28T08:00:00+00:00",
    parsed: dict | None = None,
    profile: str = "undergraduate",
) -> PendingRequest:
    return PendingRequest(
        id=req_id,
        group_id=group_id,
        user_id=user_id,
        comment="张三 261220001",
        flag="secret-flag-value",
        sub_type="add",
        parsed=parsed or {"name": "张三", "student_id": "261220001", "major": "计算机类"},
        match={"strength": "weak"},
        decision=decision,
        confidence=0.5,
        reason=reason,
        mode="record-only",
        status=status,
        created_at=created_at,
        match_strength="weak",
        profile=profile,
    )


@pytest.fixture
def stores(tmp_path):
    settings = _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    return settings, requests, audit


@pytest.mark.asyncio
async def test_lookup_qq_pending_record(stores):
    settings, requests, audit = stores
    await requests.upsert(_req("req-pending"))
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result, group_labels={"796836121": "测试群"})
    assert "1条" in text
    assert "张三" in text
    assert "796836121" in text
    assert "待处理" in text


@pytest.mark.asyncio
async def test_lookup_qq_approved_record(stores):
    settings, requests, audit = stores
    await requests.upsert(
        _req(
            "req-approved",
            status="processed",
            decision="approve",
            reason="强匹配，建议通过",
        )
    )
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "已通过" in text


@pytest.mark.asyncio
async def test_lookup_qq_rejected_record(stores):
    settings, requests, audit = stores
    await requests.upsert(
        _req(
            "req-rejected",
            status="processed",
            decision="reject",
            reason="信息不完整",
        )
    )
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "已拒绝" in text
    assert "信息不完整" in text


@pytest.mark.asyncio
async def test_lookup_qq_multiple_groups(stores):
    settings, requests, audit = stores
    await requests.upsert(_req("req-a", group_id="796836121", created_at="2026-07-28T09:00:00+00:00"))
    await requests.upsert(_req("req-b", group_id="2601", created_at="2026-07-28T08:00:00+00:00"))
    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 2
    text = format_lookup_qq_result(result)
    assert "796836121" in text
    assert "2601" in text
    assert "[1]" in text
    assert "[2]" in text


@pytest.mark.asyncio
async def test_lookup_qq_no_records(stores):
    settings, requests, audit = stores
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result)
    assert "未找到" in text
    assert "123456789" in text


def test_lookup_qq_invalid_param():
    ok, message = validate_lookup_qq("")
    assert ok is False
    assert message == "QQ 号不能为空"

    ok, message = validate_lookup_qq("abc")
    assert ok is False
    assert message == "QQ 号必须为纯数字"

    ok, message = validate_lookup_qq("123")
    assert ok is False
    assert message == "QQ 号长度无效"


def test_lookup_qq_permission_denied_for_non_admin():
    settings = _settings(admin_qq_ids="111")
    allowed, message = can_run_command(settings, "lookup", DummyEvent("222"))
    assert allowed is False
    assert message == "无权限"


@pytest.mark.asyncio
async def test_lookup_qq_output_redacts_sensitive_fields(stores):
    settings, requests, audit = stores
    parsed = {
        "name": "张三",
        "student_id": "261220001",
        "major": "计算机类",
        "_blacklist_hit": True,
        "_undergrad_exclusive_hit": True,
        "_undergrad_exclusive_group_ids": ["2601"],
    }
    await requests.upsert(_req("req-safe", parsed=parsed))
    await audit.append(
        {
            "type": "pending_reparsed",
            "request_id": "req-safe",
            "user_id": "123456789",
            "flag": "must-not-show",
            "raw_event": {"token": "secret-token"},
        }
    )
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result, settings=settings)
    lowered = text.lower()
    assert "secret-flag-value" not in text
    assert "must-not-show" not in text
    assert "raw_event" not in lowered
    assert "secret-token" not in text
    assert "123456789" in text
    assert "黑名单" in text
    assert "多群互斥" in text


@pytest.mark.asyncio
async def test_lookup_qq_matches_applicant_qq_field(stores):
    settings, requests, audit = stores
    await requests.upsert(
        _req(
            "req-alt-field",
            user_id="999999999",
            parsed={"name": "李四", "applicant_qq": "123456789"},
        )
    )
    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 1
    assert result.records[0].request_id == "req-alt-field"


@pytest.mark.asyncio
async def test_lookup_qq_truncates_to_recent_twenty(stores):
    settings, requests, audit = stores
    for idx in range(25):
        await requests.upsert(
            _req(
                f"req-{idx:02d}",
                created_at=f"2026-07-{idx + 1:02d}T08:00:00+00:00",
            )
        )
    result = await lookup_qq_records(requests, audit, "123456789")
    assert result.total == 25
    assert len(result.records) == 20
    assert result.truncated is True
    text = format_lookup_qq_result(result)
    assert "（仅展示最近 20 条，共 25 条）" in text
