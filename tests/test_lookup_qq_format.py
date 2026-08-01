"""Tests for compressed /audit lookup qq output format."""

from __future__ import annotations

import pytest

from admin.formatter import format_lookup_qq_result
from admin.lookup_qq import LookupQqRecord, LookupQqResult, lookup_qq_records
from config import load_settings
from data_source.students import PendingRequest
from storage.audit_log import AuditLog
from storage.requests_store import RequestsStore
from tests.test_admin_commands import DummyConfig


def _settings(**overrides):
    base = {"admin_qq_ids": "111", "target_group_ids": "796836121,2601"}
    base.update(overrides)
    return load_settings(DummyConfig(base))


def _req(
    req_id: str,
    *,
    user_id: str = "123456789",
    comment: str = "张三 261220001 计算机类",
    parsed: dict | None = None,
) -> PendingRequest:
    return PendingRequest(
        id=req_id,
        group_id="796836121",
        user_id=user_id,
        comment=comment,
        flag="secret-flag-value",
        sub_type="add",
        parsed=parsed
        or {"name": "张三", "student_id": "261220001", "major": "计算机类"},
        match={"strength": "weak"},
        decision="manual_review",
        confidence=0.5,
        reason="弱匹配，需要人工确认",
        mode="record-only",
        status="pending",
        created_at="2026-07-28T08:00:00+00:00",
        match_strength="weak",
    )


@pytest.fixture
def stores(tmp_path):
    settings = _settings()
    requests = RequestsStore(tmp_path / "requests.json")
    audit = AuditLog(tmp_path / "audit.jsonl", settings)
    return settings, requests, audit


def test_lookup_qq_format_shows_raw_application_and_parsed():
    record = LookupQqRecord(
        request_id="req-1",
        qq="123456789",
        group_id="796836121",
        created_at="2026-07-28T08:00:00+00:00",
        profile="undergraduate",
        parsed={"name": "张三", "student_id": "261220001", "major": "计算机类"},
        status="pending",
        decision="manual_review",
        reason="弱匹配",
        match_strength="weak",
        source="pending",
        application_comment="张三 261220001 计算机类",
    )
    result = LookupQqResult(qq="123456789", total=1, records=[record])
    text = format_lookup_qq_result(result, group_labels={"796836121": "测试群"})

    assert "原始申请：张三 261220001 计算机类" in text
    assert "解析：张三/261220001/计算机类" in text
    assert "结果：待处理｜匹配:弱匹配" in text
    assert "[1] 本科｜测试群(796836121)" in text


def test_lookup_qq_format_fewer_lines_than_verbose_layout():
    record = LookupQqRecord(
        request_id="req-1",
        qq="123456789",
        group_id="796836121",
        created_at="2026-07-28T08:00:00+00:00",
        profile="undergraduate",
        parsed={"name": "张三", "student_id": "261220001", "major": "计算机类"},
        status="processed",
        decision="approve",
        reason="强匹配，建议通过",
        match_strength="strong",
        source="audit",
        last_event_at="2026-07-28T09:00:00+00:00",
        application_comment="张三 261220001",
    )
    result = LookupQqResult(qq="123456789", total=1, records=[record])
    text = format_lookup_qq_result(result)

    line_count = len(text.splitlines())
    assert line_count <= 12
    assert "\n\n\n" not in text
    assert "姓名：" not in text
    assert "学号：" not in text
    assert "记录来源：" not in text


@pytest.mark.asyncio
async def test_lookup_qq_format_redacts_sensitive_fields(stores):
    settings, requests, audit = stores
    await requests.upsert(
        _req(
            "req-safe",
            parsed={
                "name": "张三",
                "student_id": "261220001",
                "_blacklist_hit": True,
                "_undergrad_exclusive_hit": True,
                "_undergrad_exclusive_group_ids": ["2601"],
            },
        )
    )
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

    assert "secret-flag-value" not in text
    assert "must-not-show" not in text
    assert "raw_event" not in text.lower()
    assert "secret-token" not in text
    assert "异常：" in text
    assert "黑名单" in text


@pytest.mark.asyncio
async def test_lookup_qq_format_integration(stores):
    _, requests, audit = stores
    await requests.upsert(_req("req-pending"))
    result = await lookup_qq_records(requests, audit, "123456789")
    text = format_lookup_qq_result(result, group_labels={"796836121": "测试群"})

    assert "历史申请：" in text
    assert "1条" in text
    assert "张三 261220001 计算机类" in text
    assert "解析：张三/261220001/计算机类" in text
    assert "796836121" in text
    assert "测试群(796836121)" in text


def test_lookup_qq_format_simplifies_onebot_reason():
    record = LookupQqRecord(
        request_id="req-1",
        qq="123456789",
        group_id="796836121",
        created_at="2026-07-28T08:00:00+00:00",
        profile="undergraduate",
        parsed={},
        status="pending",
        decision="manual_review",
        reason='{"status":"failed","retcode":1200,"message":"network error"}',
        match_strength="none",
        source="pending",
    )
    result = LookupQqResult(qq="123456789", total=1, records=[record])
    text = format_lookup_qq_result(result)

    assert "retcode" not in text.lower()
    assert "network error" not in text
    assert "原因：" in text


def test_lookup_qq_format_avoids_duplicate_group_id_in_header():
    record = LookupQqRecord(
        request_id="req-1",
        qq="1179350197",
        group_id="826811581",
        created_at="2026-07-28T08:00:00+00:00",
        profile="undergraduate",
        parsed={},
        status="processed",
        decision="approve",
        reason="已通过",
        match_strength="none",
        source="audit",
        application_comment="测试",
    )
    result = LookupQqResult(qq="1179350197", total=1, records=[record])
    text = format_lookup_qq_result(
        result,
        group_labels={"826811581": "南哪2026级本科新生咨询①群（826811581）"},
    )

    assert "826811581）(826811581)" not in text
    assert "[1] 本科｜南哪2026级本科新生咨询①群（826811581）" in text


def test_lookup_qq_format_collapses_multiline_application():
    record = LookupQqRecord(
        request_id="req-1",
        qq="1179350197",
        group_id="826811581",
        created_at="2026-07-28T08:00:00+00:00",
        profile="undergraduate",
        parsed={"major": "阈值自动拒绝测试"},
        status="pending",
        decision="manual_review",
        reason="测试",
        match_strength="none",
        source="pending",
        application_comment="问题：姓名 学号/录取号 专业\n答案：阈值自动拒绝测试",
    )
    result = LookupQqResult(qq="1179350197", total=1, records=[record])
    text = format_lookup_qq_result(result)

    assert "原始申请：问题：姓名 学号/录取号 专业 答案：阈值自动拒绝测试" in text
